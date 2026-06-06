"""Local speech-to-text transcription with faster-whisper."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.core.cancel import CancellationToken
from app.models.settings import TranscriptSegment


@dataclass(slots=True)
class WhisperConfig:
    model_size: str = "medium"
    language: str | None = "ko"
    device: str = "cpu"
    compute_type: str = "int8"
    beam_size: int = 5
    vad_filter: bool = True
    download_root: str | None = None


@dataclass(slots=True)
class TranscriptionProgress:
    processed_seconds: float
    total_seconds: float | None
    progress_ratio: float | None
    elapsed_seconds: float
    eta_seconds: float | None
    segment_count: int
    current_text: str


@dataclass(slots=True)
class TranscriptionResult:
    text: str
    segments: list[TranscriptSegment]
    detected_language: str | None
    language_probability: float | None
    duration_seconds: float | None
    progress_events: list[TranscriptionProgress] = field(default_factory=list)


ProgressCallback = Callable[[TranscriptionProgress], None]
Clock = Callable[[], float]


def normalize_language(language: str | None) -> str | None:
    """Convert GUI language option to faster-whisper language parameter."""

    if language is None:
        return None
    normalized = language.strip().lower()
    if not normalized or normalized in {"auto", "detect", "none"}:
        return None
    return normalized


def create_whisper_model(config: WhisperConfig):
    """Instantiate faster-whisper lazily so tests and non-STT paths do not import it."""

    from faster_whisper import WhisperModel

    kwargs: dict[str, Any] = {
        "device": config.device,
        "compute_type": config.compute_type,
    }
    if config.download_root:
        kwargs["download_root"] = config.download_root
    return WhisperModel(config.model_size, **kwargs)


def transcribe_audio(
    audio_path: str | Path,
    *,
    config: WhisperConfig | None = None,
    model: Any | None = None,
    duration_seconds: float | None = None,
    cancellation_token: CancellationToken | None = None,
    progress_callback: ProgressCallback | None = None,
    clock: Clock = time.monotonic,
) -> TranscriptionResult:
    """Transcribe an audio file and emit progress events per returned segment."""

    config = config or WhisperConfig()
    cancellation_token = cancellation_token or CancellationToken()
    cancellation_token.raise_if_cancelled()

    if model is None:
        model = create_whisper_model(config)
    cancellation_token.raise_if_cancelled()

    language = normalize_language(config.language)
    raw_segments, info = model.transcribe(
        str(audio_path),
        language=language,
        vad_filter=config.vad_filter,
        beam_size=config.beam_size,
    )

    total_seconds = duration_seconds or _info_value(info, "duration")
    eta = _RollingEta(clock)
    segments: list[TranscriptSegment] = []
    progress_events: list[TranscriptionProgress] = []
    max_processed = 0.0

    for raw_segment in _iter_segments(raw_segments):
        cancellation_token.raise_if_cancelled()
        segment = _to_transcript_segment(raw_segment)
        max_processed = max(max_processed, segment.end)
        if segment.text:
            segments.append(segment)

        eta_value, elapsed = eta.update(max_processed, total_seconds)
        progress_ratio = None
        if total_seconds and total_seconds > 0:
            progress_ratio = min(1.0, max(0.0, max_processed / total_seconds))

        progress = TranscriptionProgress(
            processed_seconds=max_processed,
            total_seconds=total_seconds,
            progress_ratio=progress_ratio,
            elapsed_seconds=elapsed,
            eta_seconds=eta_value,
            segment_count=len(segments),
            current_text=segment.text,
        )
        progress_events.append(progress)
        if progress_callback:
            progress_callback(progress)

    return TranscriptionResult(
        text="\n".join(segment.text for segment in segments),
        segments=segments,
        detected_language=_info_value(info, "language"),
        language_probability=_info_value(info, "language_probability"),
        duration_seconds=total_seconds,
        progress_events=progress_events,
    )


class _RollingEta:
    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        self._started_at: float | None = None
        self._baseline_processed: float = 0.0

    def update(self, processed_seconds: float, total_seconds: float | None) -> tuple[float | None, float]:
        now = self._clock()
        if self._started_at is None:
            self._started_at = now
            self._baseline_processed = processed_seconds
            return None, 0.0

        elapsed = max(0.0, now - self._started_at)
        processed_delta = max(0.0, processed_seconds - self._baseline_processed)
        if not total_seconds or total_seconds <= 0 or elapsed <= 0 or processed_delta <= 0:
            return None, elapsed

        rate = processed_delta / elapsed
        remaining = max(0.0, total_seconds - processed_seconds)
        if rate <= 0:
            return None, elapsed
        return remaining / rate, elapsed


def _iter_segments(raw_segments: Iterable[Any]) -> Iterable[Any]:
    return raw_segments


def _to_transcript_segment(raw_segment: Any) -> TranscriptSegment:
    start = float(_segment_value(raw_segment, "start", 0.0) or 0.0)
    end = float(_segment_value(raw_segment, "end", start) or start)
    text = str(_segment_value(raw_segment, "text", "") or "").strip()
    return TranscriptSegment(start=start, end=end, text=text)


def _segment_value(raw_segment: Any, key: str, default: Any = None) -> Any:
    if isinstance(raw_segment, dict):
        return raw_segment.get(key, default)
    return getattr(raw_segment, key, default)


def _info_value(info: Any, key: str) -> Any:
    if info is None:
        return None
    if isinstance(info, dict):
        return info.get(key)
    return getattr(info, key, None)

