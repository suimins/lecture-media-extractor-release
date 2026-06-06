"""Subtitle track selection, extraction command building, parsing, and quality checks."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from app.core.errors import ExternalProcessError, ValidationError
from app.core.playlist import SubtitleTrack
from app.core.redaction import redact_text
from app.models.settings import FfmpegConfig, SubtitleMode, SubtitleQualityThresholds, TranscriptSegment


SubtitleOutputFormat = Literal["srt", "vtt"]
SubtitleQualityStatus = Literal["ok", "suspect", "failed"]

_TIMING_RE = re.compile(
    r"(?P<start>\d{1,2}:\d{2}:\d{2}[,.]\d{1,3})\s*-->\s*(?P<end>\d{1,2}:\d{2}:\d{2}[,.]\d{1,3})"
)
_HTML_TAG_RE = re.compile(r"<[^>]+>")


@dataclass(slots=True)
class SubtitleSelection:
    track: SubtitleTrack | None
    reason: str
    warnings: list[str] = field(default_factory=list)


@dataclass(slots=True)
class SubtitleExtractionCommand:
    args: list[str]
    redacted_args: list[str]


@dataclass(slots=True)
class SubtitleQuality:
    status: SubtitleQualityStatus
    cue_count: int
    text_chars: int
    covered_seconds: float
    coverage_ratio: float | None
    reasons: list[str] = field(default_factory=list)

    @property
    def should_fallback_to_stt(self) -> bool:
        return self.status in {"failed", "suspect"}


def select_subtitle_track(
    tracks: list[SubtitleTrack],
    *,
    mode: SubtitleMode = "auto",
    preferred_language: str | None = "ko",
    selected_track_id: str | None = None,
) -> SubtitleSelection:
    """Select the best subtitle track based on user preference and HLS metadata."""

    if mode == "skip":
        return SubtitleSelection(track=None, reason="subtitle_mode=skip")

    if not tracks:
        return SubtitleSelection(track=None, reason="no subtitle tracks")

    usable_tracks = [track for track in tracks if track.uri]
    warnings = []
    if len(usable_tracks) != len(tracks):
        warnings.append("URI 없는 자막 트랙은 제외함")

    if not usable_tracks:
        return SubtitleSelection(track=None, reason="no subtitle tracks with URI", warnings=warnings)

    if mode == "manual":
        if not selected_track_id:
            raise ValidationError("manual 자막 모드에는 selected_subtitle_track_id가 필요함")
        for track in usable_tracks:
            if track.track_id == selected_track_id:
                return SubtitleSelection(track=track, reason="manual selected", warnings=warnings)
        raise ValidationError("선택한 자막 트랙을 찾을 수 없음", detail=selected_track_id)

    if selected_track_id:
        for track in usable_tracks:
            if track.track_id == selected_track_id:
                return SubtitleSelection(track=track, reason="selected track id preferred", warnings=warnings)
        warnings.append("선택한 자막 트랙을 찾지 못해 자동 선택으로 진행")

    non_forced = [track for track in usable_tracks if not track.forced]
    candidates = non_forced or usable_tracks
    if not non_forced:
        warnings.append("forced 자막만 있어 forced-only 가능성이 있음")

    ranked = sorted(
        candidates,
        key=lambda track: _subtitle_score(track, preferred_language),
        reverse=True,
    )
    selected = ranked[0]
    return SubtitleSelection(track=selected, reason="auto selected", warnings=warnings)


def build_subtitle_extract_command(
    input_url: str,
    output_path: str | Path,
    *,
    config: FfmpegConfig | None = None,
    headers: dict[str, str] | None = None,
    output_format: SubtitleOutputFormat = "srt",
) -> SubtitleExtractionCommand:
    """Build an ffmpeg command that extracts a selected subtitle URL to SRT/VTT."""

    config = config or FfmpegConfig()
    output = str(output_path)
    codec = "srt" if output_format == "srt" else "webvtt"
    muxer = "srt" if output_format == "srt" else "webvtt"

    args = [config.ffmpeg_path, "-hide_banner", "-y"]
    if config.http_reconnect:
        args.extend(["-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5"])

    if headers:
        args.extend(["-headers", format_ffmpeg_headers(headers)])

    args.extend(config.extra_input_args)
    args.extend(["-i", input_url, "-map", "0:s:0?", "-c:s", codec, "-f", muxer, output])

    return SubtitleExtractionCommand(args=args, redacted_args=[redact_text(arg) for arg in args])


def extract_subtitle_with_ffmpeg(command: SubtitleExtractionCommand, *, timeout_seconds: float | None = None) -> None:
    """Run a prepared subtitle extraction command."""

    completed = subprocess.run(
        command.args,
        capture_output=True,
        check=False,
        text=True,
        timeout=timeout_seconds,
    )
    if completed.returncode != 0:
        stderr = redact_text(completed.stderr)
        stdout = redact_text(completed.stdout)
        detail = stderr or stdout or f"exit code {completed.returncode}"
        raise ExternalProcessError("ffmpeg 자막 추출 실패", detail=detail)


def format_ffmpeg_headers(headers: dict[str, str]) -> str:
    """Format HTTP headers for ffmpeg's `-headers` input option."""

    return "".join(f"{key}: {value}\r\n" for key, value in headers.items() if key.strip())


def parse_subtitle_text(text: str, *, source_format: SubtitleOutputFormat | None = None) -> list[TranscriptSegment]:
    """Parse SRT or WebVTT subtitle text into transcript segments."""

    if source_format == "srt":
        return parse_srt(text)
    if source_format == "vtt":
        return parse_vtt(text)

    stripped = text.lstrip("\ufeff\n\r\t ")
    if stripped.upper().startswith("WEBVTT"):
        return parse_vtt(text)
    return parse_srt(text)


def parse_srt(text: str) -> list[TranscriptSegment]:
    """Parse SubRip text."""

    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        return []

    segments: list[TranscriptSegment] = []
    for block in re.split(r"\n{2,}", normalized):
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if not lines:
            continue
        if lines[0].isdigit():
            lines = lines[1:]
        if not lines:
            continue

        match = _TIMING_RE.search(lines[0])
        if not match:
            continue

        body = " ".join(_clean_subtitle_line(line) for line in lines[1:]).strip()
        if body:
            segments.append(
                TranscriptSegment(
                    start=parse_timestamp(match.group("start")),
                    end=parse_timestamp(match.group("end")),
                    text=body,
                )
            )
    return segments


def parse_vtt(text: str) -> list[TranscriptSegment]:
    """Parse WebVTT text."""

    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    blocks = re.split(r"\n{2,}", normalized.strip())
    segments: list[TranscriptSegment] = []

    for block in blocks:
        lines = [line.strip("\ufeff ") for line in block.splitlines() if line.strip()]
        if not lines:
            continue
        if lines[0].upper().startswith(("WEBVTT", "NOTE", "STYLE", "REGION")):
            continue

        timing_index = next((index for index, line in enumerate(lines) if "-->" in line), None)
        if timing_index is None:
            continue

        match = _TIMING_RE.search(lines[timing_index])
        if not match:
            continue

        body_lines = lines[timing_index + 1 :]
        body = " ".join(_clean_subtitle_line(line) for line in body_lines).strip()
        if body:
            segments.append(
                TranscriptSegment(
                    start=parse_timestamp(match.group("start")),
                    end=parse_timestamp(match.group("end")),
                    text=body,
                )
            )
    return segments


def parse_timestamp(value: str) -> float:
    """Convert HH:MM:SS.mmm or HH:MM:SS,mmm to seconds."""

    timestamp, _, milliseconds = value.replace(",", ".").partition(".")
    hours_text, minutes_text, seconds_text = timestamp.split(":")
    milliseconds = (milliseconds + "000")[:3]
    return int(hours_text) * 3600 + int(minutes_text) * 60 + int(seconds_text) + int(milliseconds) / 1000


def transcript_text(segments: list[TranscriptSegment]) -> str:
    """Return a readable transcript body."""

    return "\n".join(segment.text for segment in segments if segment.text.strip())


def evaluate_subtitle_quality(
    segments: list[TranscriptSegment],
    *,
    duration_seconds: float | None,
    thresholds: SubtitleQualityThresholds | None = None,
    forced: bool = False,
) -> SubtitleQuality:
    """Evaluate whether extracted subtitles are useful enough to avoid STT fallback."""

    thresholds = thresholds or SubtitleQualityThresholds()
    cue_count = len(segments)
    text_chars = len(_plain_text(transcript_text(segments)))
    covered_seconds = _covered_seconds(segments)
    coverage_ratio = covered_seconds / duration_seconds if duration_seconds and duration_seconds > 0 else None

    reasons: list[str] = []
    status: SubtitleQualityStatus = "ok"

    if cue_count == 0:
        reasons.append("cue_count == 0")
        status = "failed"
    elif cue_count < thresholds.min_cue_count:
        reasons.append(f"cue_count < {thresholds.min_cue_count}")
        status = "suspect"

    if text_chars == 0:
        reasons.append("plain_text_chars == 0")
        status = "failed"
    elif text_chars < thresholds.min_text_chars:
        reasons.append(f"plain_text_chars < {thresholds.min_text_chars}")
        if status != "failed":
            status = "suspect"

    if duration_seconds and duration_seconds > 0:
        duration_minutes = duration_seconds / 60
        min_chars = duration_minutes * thresholds.min_chars_per_minute
        if text_chars < min_chars:
            reasons.append(f"plain_text_chars < duration_minutes * {thresholds.min_chars_per_minute}")
            if status != "failed":
                status = "suspect"
        if coverage_ratio is not None and coverage_ratio < thresholds.min_coverage_ratio:
            reasons.append(f"coverage_ratio < {thresholds.min_coverage_ratio:.2f}")
            if status != "failed":
                status = "suspect"
        if forced and coverage_ratio is not None and coverage_ratio < thresholds.forced_only_max_coverage_ratio:
            reasons.append("forced-only subtitle likely")
            if status != "failed":
                status = "suspect"

    return SubtitleQuality(
        status=status,
        cue_count=cue_count,
        text_chars=text_chars,
        covered_seconds=covered_seconds,
        coverage_ratio=coverage_ratio,
        reasons=reasons,
    )


def should_fallback_to_stt(quality: SubtitleQuality, *, allow_stt_fallback: bool) -> bool:
    return allow_stt_fallback and quality.should_fallback_to_stt


def _subtitle_score(track: SubtitleTrack, preferred_language: str | None) -> int:
    score = 0
    language = (track.language or "").lower()
    preferred = (preferred_language or "").lower()
    if preferred and language == preferred:
        score += 100
    elif preferred and language.startswith(f"{preferred}-"):
        score += 80
    if track.default:
        score += 20
    if track.autoselect:
        score += 10
    if not track.forced:
        score += 5
    return score


def _clean_subtitle_line(line: str) -> str:
    return _HTML_TAG_RE.sub("", line).replace("&nbsp;", " ").strip()


def _plain_text(text: str) -> str:
    return re.sub(r"\s+", "", text)


def _covered_seconds(segments: list[TranscriptSegment]) -> float:
    intervals = sorted((segment.start, segment.end) for segment in segments if segment.end > segment.start)
    if not intervals:
        return 0.0

    total = 0.0
    current_start, current_end = intervals[0]
    for start, end in intervals[1:]:
        if start <= current_end:
            current_end = max(current_end, end)
            continue
        total += current_end - current_start
        current_start, current_end = start, end
    total += current_end - current_start
    return total

