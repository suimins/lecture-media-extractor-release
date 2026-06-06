"""Audio extraction and HLS encryption checks."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from queue import Empty, Queue
from threading import Thread
from typing import Literal

from app.core.cancel import CancellationToken
from app.core.errors import CancelledError, ExternalProcessError
from app.core.playlist import parse_attribute_list
from app.core.redaction import redact_text
from app.core.subtitles import format_ffmpeg_headers
from app.models.settings import FfmpegConfig


HlsEncryptionStatus = Literal["none", "aes_128", "drm_likely", "unknown"]


@dataclass(slots=True)
class HlsKeyInfo:
    method: str
    uri: str | None
    keyformat: str | None
    raw_attributes: dict[str, str]


@dataclass(slots=True)
class HlsEncryptionInfo:
    status: HlsEncryptionStatus
    keys: list[HlsKeyInfo] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def has_aes_128(self) -> bool:
        return any(key.method == "AES-128" for key in self.keys)

    @property
    def drm_likely(self) -> bool:
        return self.status == "drm_likely"


@dataclass(slots=True)
class AudioExtractionCommand:
    args: list[str]
    redacted_args: list[str]
    output_path: str


@dataclass(slots=True)
class AudioProgress:
    out_time_seconds: float | None
    progress_ratio: float | None
    speed: str | None
    state: str | None
    raw: dict[str, str]


@dataclass(slots=True)
class AudioExtractionResult:
    output_path: str
    stderr: str
    progress_events: list[AudioProgress] = field(default_factory=list)


ProgressCallback = Callable[[AudioProgress], None]


def analyze_hls_encryption(playlist_text: str) -> HlsEncryptionInfo:
    """Classify HLS EXT-X-KEY usage for user-facing messaging."""

    keys: list[HlsKeyInfo] = []
    warnings: list[str] = []
    drm_likely = False
    aes_128 = False
    unknown = False

    for raw_line in playlist_text.splitlines():
        line = raw_line.strip()
        if not line.startswith("#EXT-X-KEY:"):
            continue

        attrs = parse_attribute_list(line.split(":", 1)[1])
        method = attrs.get("METHOD", "").upper()
        if not method or method == "NONE":
            continue

        keyformat = attrs.get("KEYFORMAT")
        normalized_keyformat = (keyformat or "identity").strip('"').lower()
        key_info = HlsKeyInfo(
            method=method,
            uri=attrs.get("URI"),
            keyformat=keyformat,
            raw_attributes=attrs,
        )
        keys.append(key_info)

        if method == "AES-128" and normalized_keyformat == "identity":
            aes_128 = True
        elif method == "SAMPLE-AES" or normalized_keyformat != "identity":
            drm_likely = True
            warnings.append(f"DRM 가능성: METHOD={method}, KEYFORMAT={keyformat or 'identity'}")
        else:
            unknown = True
            warnings.append(f"알 수 없는 HLS encryption method: {method}")

    if drm_likely:
        status: HlsEncryptionStatus = "drm_likely"
    elif aes_128:
        status = "aes_128"
    elif unknown:
        status = "unknown"
    else:
        status = "none"

    return HlsEncryptionInfo(status=status, keys=keys, warnings=warnings)


def build_audio_extract_command(
    input_url: str,
    output_path: str | Path,
    *,
    config: FfmpegConfig | None = None,
    headers: dict[str, str] | None = None,
) -> AudioExtractionCommand:
    """Build ffmpeg command for STT-ready 16kHz mono WAV extraction."""

    config = config or FfmpegConfig()
    output = str(output_path)
    args = [config.ffmpeg_path, "-hide_banner", "-y"]
    if config.http_reconnect:
        args.extend(["-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5"])
    if headers:
        args.extend(["-headers", format_ffmpeg_headers(headers)])

    args.extend(config.extra_input_args)
    args.extend(
        [
            "-i",
            input_url,
            "-vn",
            "-sn",
            "-dn",
            "-map",
            "0:a:0?",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            "-f",
            "wav",
            "-progress",
            "pipe:1",
            "-nostats",
            output,
        ]
    )
    return AudioExtractionCommand(args=args, redacted_args=[redact_text(arg) for arg in args], output_path=output)


def parse_progress_line(line: str) -> tuple[str, str] | None:
    """Parse a single ffmpeg `-progress` key=value line."""

    stripped = line.strip()
    if not stripped or "=" not in stripped:
        return None
    key, value = stripped.split("=", 1)
    return key, value


def make_audio_progress(raw: dict[str, str], *, duration_seconds: float | None = None) -> AudioProgress:
    out_time_seconds = _parse_out_time(raw)
    progress_ratio = None
    if duration_seconds and duration_seconds > 0 and out_time_seconds is not None:
        progress_ratio = min(1.0, max(0.0, out_time_seconds / duration_seconds))
    return AudioProgress(
        out_time_seconds=out_time_seconds,
        progress_ratio=progress_ratio,
        speed=raw.get("speed"),
        state=raw.get("progress"),
        raw=dict(raw),
    )


def extract_audio_with_ffmpeg(
    command: AudioExtractionCommand,
    *,
    duration_seconds: float | None = None,
    cancellation_token: CancellationToken | None = None,
    progress_callback: ProgressCallback | None = None,
) -> AudioExtractionResult:
    """Run ffmpeg while reading progress stdout and log stderr concurrently."""

    cancellation_token = cancellation_token or CancellationToken()
    process = subprocess.Popen(
        command.args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )

    queue: Queue[tuple[str, str | None]] = Queue()
    stdout_thread = Thread(target=_enqueue_lines, args=("stdout", process.stdout, queue), daemon=True)
    stderr_thread = Thread(target=_enqueue_lines, args=("stderr", process.stderr, queue), daemon=True)
    stdout_thread.start()
    stderr_thread.start()

    progress_state: dict[str, str] = {}
    progress_events: list[AudioProgress] = []
    stderr_lines: list[str] = []

    try:
        while True:
            if cancellation_token.cancelled and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)
                raise CancelledError("오디오 추출이 취소됨")

            try:
                stream_name, line = queue.get(timeout=0.1)
            except Empty:
                if process.poll() is not None and not stdout_thread.is_alive() and not stderr_thread.is_alive():
                    break
                continue

            if line is None:
                continue

            if stream_name == "stderr":
                stderr_lines.append(redact_text(line.rstrip("\n")))
                continue

            parsed = parse_progress_line(line)
            if parsed is None:
                continue
            key, value = parsed
            progress_state[key] = value
            if key == "progress":
                progress = make_audio_progress(progress_state, duration_seconds=duration_seconds)
                progress_events.append(progress)
                if progress_callback:
                    progress_callback(progress)
                if value == "end":
                    progress_state = {}

        returncode = process.wait()
    finally:
        stdout_thread.join(timeout=1)
        stderr_thread.join(timeout=1)

    stderr = "\n".join(line for line in stderr_lines if line)
    if returncode != 0:
        raise ExternalProcessError("ffmpeg 오디오 추출 실패", detail=stderr or f"exit code {returncode}")

    return AudioExtractionResult(output_path=command.output_path, stderr=stderr, progress_events=progress_events)


def _enqueue_lines(stream_name: str, stream, queue: Queue[tuple[str, str | None]]) -> None:
    if stream is None:
        queue.put((stream_name, None))
        return
    try:
        for line in stream:
            queue.put((stream_name, line))
    finally:
        stream.close()
        queue.put((stream_name, None))


def _parse_out_time(raw: dict[str, str]) -> float | None:
    for key in ("out_time_us", "out_time_ms"):
        value = raw.get(key)
        if value is None:
            continue
        try:
            return int(value) / 1_000_000
        except ValueError:
            pass

    value = raw.get("out_time")
    if value:
        return _parse_ffmpeg_time(value)
    return None


def _parse_ffmpeg_time(value: str) -> float | None:
    try:
        hours_text, minutes_text, seconds_text = value.split(":")
        return int(hours_text) * 3600 + int(minutes_text) * 60 + float(seconds_text)
    except ValueError:
        return None
