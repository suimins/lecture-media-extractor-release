"""Batch runner for processing many m3u8 URLs from a text file."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from app.core.errors import AppError
from app.core.job import JobDependencies, JobProgress, run_job
from app.core.redaction import redact_text, redact_url
from app.models.settings import JobConfig, JobResult


@dataclass(slots=True)
class BatchItem:
    index: int
    url: str
    output_dir: str
    status: str = "pending"
    result: JobResult | None = None
    error: str | None = None


@dataclass(slots=True)
class BatchResult:
    items: list[BatchItem] = field(default_factory=list)

    @property
    def total_count(self) -> int:
        return len(self.items)

    @property
    def completed_count(self) -> int:
        return sum(1 for item in self.items if item.status == "done")

    @property
    def skipped_count(self) -> int:
        return sum(1 for item in self.items if item.status == "skipped")

    @property
    def failed_count(self) -> int:
        return sum(1 for item in self.items if item.status == "failed")

    @property
    def pending_count(self) -> int:
        return sum(1 for item in self.items if item.status == "pending")


BatchProgressCallback = Callable[[BatchItem, JobProgress | None], None]


def parse_urls_file(path: str | Path) -> list[str]:
    """Parse newline-separated URL list, ignoring blanks and comments."""

    urls: list[str] = []
    for raw_line in Path(path).read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        urls.append(line)
    return urls


def make_output_dir(base_dir: str | Path, index: int, url: str) -> Path:
    """Create a stable, filesystem-friendly per-URL output directory path."""

    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:10]
    host_hint = _slug(url.split("://", 1)[-1].split("/", 1)[0] or "url")
    return Path(base_dir) / f"{index:03d}_{host_hint}_{digest}"


def should_skip_output(output_dir: str | Path) -> bool:
    """Return whether a previous transcript export appears complete enough to skip."""

    path = Path(output_dir)
    return (path / "transcript.json").exists() and (path / "transcript.txt").exists()


def run_batch(
    urls: list[str],
    *,
    base_config: JobConfig,
    skip_existing: bool = True,
    continue_on_error: bool = True,
    progress_callback: BatchProgressCallback | None = None,
    dependencies: JobDependencies | None = None,
) -> BatchResult:
    """Run many URL jobs sequentially with resume/skip support."""

    base_output_dir = Path(base_config.output_dir)
    base_output_dir.mkdir(parents=True, exist_ok=True)
    result = BatchResult(
        items=[
            BatchItem(index=index, url=url, output_dir=str(make_output_dir(base_output_dir, index, url)))
            for index, url in enumerate(urls, start=1)
        ]
    )
    errors_path = base_output_dir / "errors.jsonl"

    for item in result.items:
        item_output_dir = Path(item.output_dir)

        if skip_existing and should_skip_output(item_output_dir):
            item.status = "skipped"
            if progress_callback:
                progress_callback(item, None)
            continue

        job_config = _copy_job_config(base_config, url=item.url, output_dir=str(item_output_dir))
        try:
            def on_job_progress(progress: JobProgress) -> None:
                if progress_callback:
                    progress_callback(item, progress)

            item.result = run_job(job_config, progress_callback=on_job_progress, dependencies=dependencies)
            item.status = "done"
            if progress_callback:
                progress_callback(item, None)
        except Exception as exc:
            item.status = "failed"
            item.error = _error_message(exc)
            _append_error(errors_path, item)
            if progress_callback:
                progress_callback(item, None)
            if not continue_on_error:
                break

    write_batch_summary(result, base_output_dir / "batch_summary.json")
    return result


def write_batch_summary(result: BatchResult, path: str | Path) -> None:
    """Write a redacted batch summary JSON."""

    payload = {
        "total_count": result.total_count,
        "completed_count": result.completed_count,
        "skipped_count": result.skipped_count,
        "failed_count": result.failed_count,
        "pending_count": result.pending_count,
        "items": [_item_summary(item) for item in result.items],
    }
    Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _copy_job_config(config: JobConfig, *, url: str, output_dir: str) -> JobConfig:
    return JobConfig(
        url=url,
        output_dir=output_dir,
        headers=dict(config.headers),
        subtitle_mode=config.subtitle_mode,
        selected_subtitle_track_id=config.selected_subtitle_track_id,
        whisper_model=config.whisper_model,
        whisper_download_root=config.whisper_download_root,
        whisper_device=config.whisper_device,
        whisper_compute_type=config.whisper_compute_type,
        whisper_beam_size=config.whisper_beam_size,
        language=config.language,
        preferred_subtitle_language=config.preferred_subtitle_language,
        allow_stt_fallback=config.allow_stt_fallback,
        subtitle_quality=config.subtitle_quality,
        retry=config.retry,
        ffmpeg=config.ffmpeg,
        logging=config.logging,
        keep_audio=config.keep_audio,
        export_txt=config.export_txt,
        export_md=config.export_md,
        export_srt=config.export_srt,
        export_json=config.export_json,
    )


def _append_error(path: Path, item: BatchItem) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "index": item.index,
        "url": redact_url(item.url),
        "output_dir": item.output_dir,
        "error": item.error,
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def _item_summary(item: BatchItem) -> dict[str, Any]:
    return {
        "index": item.index,
        "url": redact_url(item.url),
        "output_dir": item.output_dir,
        "status": item.status,
        "error": item.error,
        "method": item.result.method if item.result else None,
        "output_files": item.result.output_files if item.result else [],
    }


def _error_message(exc: Exception) -> str:
    if isinstance(exc, AppError):
        message = f"{exc.message}: {exc.detail}" if exc.detail else exc.message
        return redact_text(message)
    return redact_text(str(exc))


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", value).strip("-._")
    return slug[:40] or "url"
