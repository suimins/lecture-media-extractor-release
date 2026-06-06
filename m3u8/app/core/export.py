"""Transcript export helpers."""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from app.core.redaction import REDACTED, SENSITIVE_HEADER_NAMES, redact_url
from app.models.settings import JobConfig, JobResult, TranscriptSegment


def write_exports(result: JobResult, config: JobConfig, *, base_name: str = "transcript") -> list[str]:
    """Write configured transcript exports and return output file paths."""

    output_dir = Path(config.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_files: list[str] = []

    if config.export_txt:
        path = output_dir / f"{base_name}.txt"
        path.write_text(result.transcript_text, encoding="utf-8")
        output_files.append(str(path))

    if config.export_md:
        path = output_dir / f"{base_name}.md"
        path.write_text(format_markdown(result), encoding="utf-8")
        output_files.append(str(path))

    if config.export_srt:
        path = output_dir / f"{base_name}.srt"
        path.write_text(format_srt(result.segments), encoding="utf-8")
        output_files.append(str(path))

    if config.export_json:
        path = output_dir / f"{base_name}.json"
        path.write_text(json.dumps(job_result_metadata(result), ensure_ascii=False, indent=2), encoding="utf-8")
        output_files.append(str(path))

    result.output_files[:] = output_files
    return output_files


def format_markdown(result: JobResult) -> str:
    lines = [
        "# Transcript",
        "",
        f"- Method: `{result.method}`",
        f"- Source: `{redact_url(result.source_url)}`",
    ]
    if result.duration_seconds is not None:
        lines.append(f"- Duration: `{result.duration_seconds:.2f}s`")
    if result.selected_subtitle_track_id:
        lines.append(f"- Subtitle track: `{result.selected_subtitle_track_id}`")
    if result.subtitle_quality_status:
        lines.append(f"- Subtitle quality: `{result.subtitle_quality_status}`")
    if result.warnings:
        lines.extend(["", "## Warnings", ""])
        lines.extend(f"- {warning}" for warning in result.warnings)
    lines.extend(["", "## Text", "", result.transcript_text])
    return "\n".join(lines).rstrip() + "\n"


def format_srt(segments: list[TranscriptSegment]) -> str:
    blocks = []
    for index, segment in enumerate(segments, start=1):
        text = segment.text.strip()
        if not text:
            continue
        blocks.append(
            "\n".join(
                [
                    str(index),
                    f"{format_srt_timestamp(segment.start)} --> {format_srt_timestamp(segment.end)}",
                    text,
                ]
            )
        )
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def format_srt_timestamp(seconds: float) -> str:
    clamped = max(0.0, seconds)
    milliseconds_total = int(round(clamped * 1000))
    milliseconds = milliseconds_total % 1000
    total_seconds = milliseconds_total // 1000
    sec = total_seconds % 60
    minutes_total = total_seconds // 60
    minute = minutes_total % 60
    hour = minutes_total // 60
    return f"{hour:02d}:{minute:02d}:{sec:02d},{milliseconds:03d}"


def job_result_metadata(result: JobResult) -> dict[str, Any]:
    return _jsonable(result)


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            key_text = str(key).lower()
            if key_text in SENSITIVE_HEADER_NAMES:
                result[key] = REDACTED
            elif key_text in {"source_url", "subtitle_url", "url"} and isinstance(item, str):
                result[key] = redact_url(item)
            else:
                result[key] = _jsonable(item)
        return result
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, set):
        return sorted(value)
    return value
