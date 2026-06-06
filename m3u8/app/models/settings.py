"""Configuration and result models for jobs."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


SubtitleMode = Literal["auto", "manual", "skip"]
PlaylistKind = Literal["master", "media", "unknown"]
PlaylistType = Literal["vod", "event", "live_or_open", "unknown"]


@dataclass(slots=True)
class RetryConfig:
    timeout_seconds: float = 15.0
    max_attempts: int = 3
    backoff_seconds: float = 1.5
    retry_status_codes: tuple[int, ...] = (408, 429, 500, 502, 503, 504)


@dataclass(slots=True)
class SubtitleQualityThresholds:
    min_cue_count: int = 5
    min_text_chars: int = 200
    min_chars_per_minute: int = 20
    min_coverage_ratio: float = 0.10
    forced_only_max_coverage_ratio: float = 0.05


@dataclass(slots=True)
class FfmpegConfig:
    ffmpeg_path: str = "ffmpeg"
    ffprobe_path: str = "ffprobe"
    extra_input_args: list[str] = field(default_factory=list)
    http_reconnect: bool = True


@dataclass(slots=True)
class LoggingConfig:
    redact_sensitive_values: bool = True
    save_debug_log: bool = True
    include_ffmpeg_stderr: bool = True


@dataclass(slots=True)
class BatchConfig:
    urls_file: str
    output_dir: str
    skip_existing: bool = True
    keep_audio: bool = False
    continue_on_error: bool = True


@dataclass(slots=True)
class JobConfig:
    url: str
    output_dir: str
    headers: dict[str, str] = field(default_factory=dict)
    subtitle_mode: SubtitleMode = "auto"
    selected_subtitle_track_id: str | None = None
    whisper_model: str = "medium"
    whisper_download_root: str | None = "model-cache/whisper-models"
    whisper_device: str = "cpu"
    whisper_compute_type: str = "int8"
    whisper_beam_size: int = 5
    language: str | None = "ko"
    preferred_subtitle_language: str | None = "ko"
    allow_stt_fallback: bool = True
    subtitle_quality: SubtitleQualityThresholds = field(default_factory=SubtitleQualityThresholds)
    retry: RetryConfig = field(default_factory=RetryConfig)
    ffmpeg: FfmpegConfig = field(default_factory=FfmpegConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    keep_audio: bool = False
    export_txt: bool = True
    export_md: bool = True
    export_srt: bool = True
    export_json: bool = True


@dataclass(slots=True)
class TranscriptSegment:
    start: float
    end: float
    text: str


@dataclass(slots=True)
class JobResult:
    source_url: str
    method: str
    input_playlist_kind: PlaylistKind
    playlist_type: PlaylistType
    duration_seconds: float | None
    subtitle_url: str | None
    selected_subtitle_track_id: str | None
    subtitle_quality_status: str | None
    audio_path: str | None
    transcript_text: str
    segments: list[TranscriptSegment]
    output_files: list[str]
    warnings: list[str] = field(default_factory=list)
