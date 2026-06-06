"""End-to-end core job pipeline."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.core.audio import (
    AudioExtractionCommand,
    AudioExtractionResult,
    build_audio_extract_command,
    extract_audio_with_ffmpeg,
)
from app.core.cancel import CancellationToken
from app.core.errors import AppError, CancelledError, FfmpegCapabilityError, ValidationError
from app.core.export import write_exports
from app.core.ffmpeg import FfmpegCapabilities, run_capability_check
from app.core.playlist import PlaylistAnalysis, analyze_playlist_url
from app.core.subtitles import (
    SubtitleExtractionCommand,
    build_subtitle_extract_command,
    evaluate_subtitle_quality,
    extract_subtitle_with_ffmpeg,
    parse_subtitle_text,
    select_subtitle_track,
    should_fallback_to_stt,
    transcript_text,
)
from app.core.transcribe import TranscriptionResult, WhisperConfig, transcribe_audio
from app.models.events import JobEvent
from app.models.settings import JobConfig, JobResult, TranscriptSegment


@dataclass(slots=True)
class JobProgress:
    event: JobEvent
    message: str
    progress: float | None = None
    detail: dict[str, Any] = field(default_factory=dict)


ProgressCallback = Callable[[JobProgress], None]
EventEmitter = Callable[[JobEvent, str], None]


@dataclass(slots=True)
class JobDependencies:
    check_ffmpeg: Callable[[Any], FfmpegCapabilities] = run_capability_check
    analyze_playlist: Callable[..., PlaylistAnalysis] = analyze_playlist_url
    extract_subtitle: Callable[[SubtitleExtractionCommand], None] = extract_subtitle_with_ffmpeg
    extract_audio: Callable[..., AudioExtractionResult] = extract_audio_with_ffmpeg
    transcribe: Callable[..., TranscriptionResult] = transcribe_audio


def run_job(
    config: JobConfig,
    *,
    progress_callback: ProgressCallback | None = None,
    cancellation_token: CancellationToken | None = None,
    dependencies: JobDependencies | None = None,
) -> JobResult:
    """Run one URL-to-transcript job."""

    dependencies = dependencies or JobDependencies()
    cancellation_token = cancellation_token or CancellationToken()

    def emit(event: JobEvent, message: str, *, progress: float | None = None, detail: dict[str, Any] | None = None) -> None:
        if progress_callback:
            progress_callback(JobProgress(event=event, message=message, progress=progress, detail=detail or {}))

    try:
        emit(JobEvent.JOB_STARTED, "작업 시작", progress=0.0)
        _validate_config(config)
        output_dir = Path(config.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        cancellation_token.raise_if_cancelled()
        emit(JobEvent.CHECKING_FFMPEG_CAPABILITIES, "ffmpeg 기능 확인", progress=0.03)
        capabilities = dependencies.check_ffmpeg(config.ffmpeg)
        if not capabilities.ok:
            raise FfmpegCapabilityError("ffmpeg 기능 확인 실패", detail=_capability_detail(capabilities))

        cancellation_token.raise_if_cancelled()
        emit(JobEvent.FETCHING_PLAYLIST, "m3u8 playlist 다운로드", progress=0.08)
        analysis = dependencies.analyze_playlist(config.url, headers=config.headers, retry=config.retry)
        warnings = list(analysis.warnings)

        emit(
            JobEvent.ANALYZING_PLAYLIST,
            "m3u8 playlist 분석",
            progress=0.12,
            detail={"kind": analysis.kind, "playlist_type": analysis.playlist_type},
        )
        if analysis.kind == "media":
            emit(JobEvent.MEDIA_PLAYLIST_INPUT_DETECTED, "media playlist 입력 감지", progress=0.14)

        subtitle_result = _try_subtitle_route(config, analysis, output_dir, dependencies, emit, cancellation_token, warnings)
        if subtitle_result is not None:
            emit(JobEvent.EXPORTING, "결과 파일 저장", progress=0.95)
            write_exports(subtitle_result, config)
            emit(JobEvent.JOB_DONE, "작업 완료", progress=1.0, detail={"method": subtitle_result.method})
            return subtitle_result

        stt_result = _run_stt_route(config, analysis, output_dir, dependencies, emit, cancellation_token, warnings)
        emit(JobEvent.EXPORTING, "결과 파일 저장", progress=0.95)
        write_exports(stt_result, config)
        emit(JobEvent.JOB_DONE, "작업 완료", progress=1.0, detail={"method": stt_result.method})
        return stt_result

    except CancelledError:
        emit(JobEvent.JOB_CANCELLED, "작업 취소", progress=None)
        raise
    except AppError:
        emit(JobEvent.JOB_ERROR, "작업 실패", progress=None)
        raise


def _try_subtitle_route(
    config: JobConfig,
    analysis: PlaylistAnalysis,
    output_dir: Path,
    dependencies: JobDependencies,
    emit: ProgressCallback,
    cancellation_token: CancellationToken,
    warnings: list[str],
) -> JobResult | None:
    if config.subtitle_mode == "skip" or not analysis.subtitle_tracks:
        emit(JobEvent.SUBTITLE_NOT_FOUND, "사용 가능한 자막 트랙 없음", progress=0.18)
        return None

    emit(JobEvent.SUBTITLE_FOUND, "자막 트랙 발견", progress=0.18, detail={"count": len(analysis.subtitle_tracks)})
    emit(JobEvent.SELECTING_SUBTITLE_TRACK, "자막 트랙 선택", progress=0.20)
    selection = select_subtitle_track(
        analysis.subtitle_tracks,
        mode=config.subtitle_mode,
        preferred_language=config.preferred_subtitle_language,
        selected_track_id=config.selected_subtitle_track_id,
    )
    warnings.extend(selection.warnings)
    if selection.track is None or not selection.track.uri:
        warnings.append(selection.reason)
        return None

    cancellation_token.raise_if_cancelled()
    subtitle_path = output_dir / "source_subtitle.srt"
    command = build_subtitle_extract_command(
        selection.track.uri,
        subtitle_path,
        config=config.ffmpeg,
        headers=config.headers,
        output_format="srt",
    )
    emit(
        JobEvent.EXTRACTING_SUBTITLE_WITH_FFMPEG,
        "ffmpeg 자막 추출",
        progress=0.28,
        detail={"track_id": selection.track.track_id, "command": command.redacted_args},
    )
    dependencies.extract_subtitle(command)

    cancellation_token.raise_if_cancelled()
    emit(JobEvent.VALIDATING_SUBTITLE_OUTPUT, "자막 품질 검증", progress=0.42)
    subtitle_text = subtitle_path.read_text(encoding="utf-8", errors="replace")
    segments = parse_subtitle_text(subtitle_text, source_format="srt")
    quality = evaluate_subtitle_quality(
        segments,
        duration_seconds=analysis.duration_seconds,
        thresholds=config.subtitle_quality,
        forced=selection.track.forced,
    )

    if should_fallback_to_stt(quality, allow_stt_fallback=config.allow_stt_fallback):
        warnings.extend(f"subtitle quality: {reason}" for reason in quality.reasons)
        emit(JobEvent.FALLING_BACK_TO_STT, "자막 품질 문제로 STT 폴백", progress=0.48, detail={"quality": quality.status})
        return None

    text = transcript_text(segments)
    return JobResult(
        source_url=config.url,
        method="subtitle",
        input_playlist_kind=analysis.kind,
        playlist_type=analysis.playlist_type,
        duration_seconds=analysis.duration_seconds,
        subtitle_url=selection.track.uri,
        selected_subtitle_track_id=selection.track.track_id,
        subtitle_quality_status=quality.status,
        audio_path=None,
        transcript_text=text,
        segments=segments,
        output_files=[],
        warnings=warnings,
    )


def _run_stt_route(
    config: JobConfig,
    analysis: PlaylistAnalysis,
    output_dir: Path,
    dependencies: JobDependencies,
    emit: ProgressCallback,
    cancellation_token: CancellationToken,
    warnings: list[str],
) -> JobResult:
    cancellation_token.raise_if_cancelled()
    audio_path = output_dir / "audio.wav"
    command = build_audio_extract_command(config.url, audio_path, config=config.ffmpeg, headers=config.headers)
    emit(JobEvent.EXTRACTING_AUDIO, "오디오 추출", progress=0.52, detail={"command": command.redacted_args})

    def on_audio_progress(audio_progress) -> None:
        progress = 0.52
        if audio_progress.progress_ratio is not None:
            progress = 0.52 + audio_progress.progress_ratio * 0.22
        emit(
            JobEvent.EXTRACTING_AUDIO,
            "오디오 추출 중",
            progress=progress,
            detail={
                "out_time_seconds": audio_progress.out_time_seconds,
                "progress_ratio": audio_progress.progress_ratio,
                "speed": audio_progress.speed,
            },
        )

    dependencies.extract_audio(
        command,
        duration_seconds=analysis.duration_seconds,
        cancellation_token=cancellation_token,
        progress_callback=on_audio_progress,
    )

    cancellation_token.raise_if_cancelled()
    emit(JobEvent.TRANSCRIBING, "음성 인식", progress=0.76)
    whisper_config = WhisperConfig(
        model_size=config.whisper_model,
        language=config.language,
        device=config.whisper_device,
        compute_type=config.whisper_compute_type,
        beam_size=config.whisper_beam_size,
        download_root=config.whisper_download_root,
    )

    def on_stt_progress(stt_progress) -> None:
        progress = 0.76
        if stt_progress.progress_ratio is not None:
            progress = 0.76 + stt_progress.progress_ratio * 0.16
        emit(
            JobEvent.TRANSCRIBING,
            "음성 인식 중",
            progress=progress,
            detail={
                "processed_seconds": stt_progress.processed_seconds,
                "eta_seconds": stt_progress.eta_seconds,
                "segment_count": stt_progress.segment_count,
            },
        )

    transcription = dependencies.transcribe(
        audio_path,
        config=whisper_config,
        duration_seconds=analysis.duration_seconds,
        cancellation_token=cancellation_token,
        progress_callback=on_stt_progress,
    )

    audio_result_path = str(audio_path) if config.keep_audio else None
    if not config.keep_audio:
        audio_path.unlink(missing_ok=True)

    return JobResult(
        source_url=config.url,
        method="stt",
        input_playlist_kind=analysis.kind,
        playlist_type=analysis.playlist_type,
        duration_seconds=transcription.duration_seconds or analysis.duration_seconds,
        subtitle_url=None,
        selected_subtitle_track_id=None,
        subtitle_quality_status=None,
        audio_path=audio_result_path,
        transcript_text=transcription.text,
        segments=transcription.segments,
        output_files=[],
        warnings=warnings,
    )


def _validate_config(config: JobConfig) -> None:
    if not config.url.strip():
        raise ValidationError("m3u8 URL이 비어 있음")
    if not config.output_dir.strip():
        raise ValidationError("출력 폴더가 비어 있음")


def _capability_detail(capabilities: FfmpegCapabilities) -> str:
    parts = list(capabilities.errors)
    if capabilities.missing_protocols:
        parts.append(f"missing protocols: {', '.join(sorted(capabilities.missing_protocols))}")
    if capabilities.missing_formats:
        parts.append(f"missing formats: {', '.join(sorted(capabilities.missing_formats))}")
    return "; ".join(parts) or "unknown ffmpeg capability failure"
