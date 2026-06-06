"""Minimal CLI entry point for early core validation."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any
import sys

from app.core.batch import BatchItem, parse_urls_file, run_batch
from app.core.ffmpeg import run_capability_check
from app.core.job import JobProgress, run_job
from app.core.playlist import analyze_playlist_text
from app.core.redaction import REDACTED, SENSITIVE_HEADER_NAMES, redact_url
from app.models.settings import FfmpegConfig, JobConfig


def main() -> int:
    parser = argparse.ArgumentParser(description="m3u8 transcript app core utilities")
    parser.add_argument("--check-ffmpeg", action="store_true", help="check ffmpeg/ffprobe capabilities")
    parser.add_argument("--analyze-file", type=Path, help="analyze a local m3u8 playlist file")
    parser.add_argument("--run-url", help="run URL-to-transcript core pipeline")
    parser.add_argument("--run-batch", type=Path, help="run URL-to-transcript jobs from a newline-separated URL file")
    parser.add_argument("--out", default="outputs", help="output directory for jobs")
    parser.add_argument("--headers-json", type=Path, help="JSON file containing HTTP headers")
    parser.add_argument("--user-agent", help="HTTP User-Agent header")
    parser.add_argument("--referer", help="HTTP Referer header")
    parser.add_argument("--cookie", help="HTTP Cookie header")
    parser.add_argument("--subtitle-mode", choices=["auto", "manual", "skip"], default="auto")
    parser.add_argument("--subtitle-track-id", help="manual subtitle track id")
    parser.add_argument("--subtitle-language", default="ko", help="preferred subtitle language")
    parser.add_argument("--whisper-model", default="medium", help="faster-whisper model size")
    parser.add_argument(
        "--whisper-download-root",
        default="model-cache/whisper-models",
        help="local faster-whisper model cache directory",
    )
    parser.add_argument("--whisper-device", default="cpu", help="faster-whisper device, e.g. cpu or cuda")
    parser.add_argument("--whisper-compute-type", default="int8", help="faster-whisper compute type")
    parser.add_argument("--whisper-beam-size", type=int, default=5, help="faster-whisper beam size")
    parser.add_argument("--language", default="ko", help="STT language; use auto for detection")
    parser.add_argument("--keep-audio", action="store_true", help="keep extracted WAV audio")
    parser.add_argument("--no-stt-fallback", action="store_true", help="do not fallback from bad subtitles to STT")
    parser.add_argument("--no-skip-existing", action="store_true", help="re-run existing batch items")
    parser.add_argument("--stop-on-error", action="store_true", help="stop batch processing on first failed URL")
    parser.add_argument("--ffmpeg", default="ffmpeg", help="ffmpeg executable path")
    parser.add_argument("--ffprobe", default="ffprobe", help="ffprobe executable path")
    args = parser.parse_args()

    if args.check_ffmpeg:
        capabilities = run_capability_check(FfmpegConfig(ffmpeg_path=args.ffmpeg, ffprobe_path=args.ffprobe))
        print(json.dumps(_jsonable(capabilities), ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if capabilities.ok else 1

    if args.analyze_file:
        text = args.analyze_file.read_text(encoding="utf-8")
        analysis = analyze_playlist_text(text, source_url=str(args.analyze_file))
        print(json.dumps(_jsonable(analysis), ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    if args.run_url:
        headers = _load_headers(args)
        config = _build_job_config(args, url=args.run_url, output_dir=args.out, headers=headers)
        result = run_job(config, progress_callback=_print_progress)
        print(json.dumps(_jsonable(result), ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    if args.run_batch:
        headers = _load_headers(args)
        urls = parse_urls_file(args.run_batch)
        if not urls:
            print(f"URL file is empty: {args.run_batch}", file=sys.stderr)
            return 1
        config = _build_job_config(args, url="", output_dir=args.out, headers=headers)
        result = run_batch(
            urls,
            base_config=config,
            skip_existing=not args.no_skip_existing,
            continue_on_error=not args.stop_on_error,
            progress_callback=_print_batch_progress,
        )
        print(json.dumps(_jsonable(result), ensure_ascii=False, indent=2, sort_keys=True))
        return 1 if result.failed_count else 0

    print("m3u8 transcript app core is installed. Use --check-ffmpeg, --analyze-file, --run-url, or --run-batch.")
    return 0


def _load_headers(args: argparse.Namespace) -> dict[str, str]:
    headers: dict[str, str] = {}
    if args.headers_json:
        loaded = json.loads(args.headers_json.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            raise SystemExit("--headers-json must contain a JSON object")
        headers.update({str(key): str(value) for key, value in loaded.items()})
    if args.user_agent:
        headers["User-Agent"] = args.user_agent
    if args.referer:
        headers["Referer"] = args.referer
    if args.cookie:
        headers["Cookie"] = args.cookie
    return headers


def _build_job_config(args: argparse.Namespace, *, url: str, output_dir: str, headers: dict[str, str]) -> JobConfig:
    return JobConfig(
        url=url,
        output_dir=output_dir,
        headers=headers,
        subtitle_mode=args.subtitle_mode,
        selected_subtitle_track_id=args.subtitle_track_id,
        whisper_model=args.whisper_model,
        whisper_download_root=args.whisper_download_root,
        whisper_device=args.whisper_device,
        whisper_compute_type=args.whisper_compute_type,
        whisper_beam_size=args.whisper_beam_size,
        language=args.language,
        preferred_subtitle_language=args.subtitle_language,
        allow_stt_fallback=not args.no_stt_fallback,
        ffmpeg=FfmpegConfig(ffmpeg_path=args.ffmpeg, ffprobe_path=args.ffprobe),
        keep_audio=args.keep_audio,
    )


def _print_progress(progress: JobProgress) -> None:
    detail = f" {json.dumps(_jsonable(progress.detail), ensure_ascii=False, sort_keys=True)}" if progress.detail else ""
    percent = f" {progress.progress * 100:5.1f}%" if progress.progress is not None else ""
    print(f"[{progress.event}]{percent} {progress.message}{detail}", file=sys.stderr)


def _print_batch_progress(item: BatchItem, progress: JobProgress | None) -> None:
    prefix = f"[batch {item.index:03d}]"
    if progress is None:
        print(f"{prefix} {item.status} {redact_url(item.url)}", file=sys.stderr)
        return
    detail = f" {json.dumps(_jsonable(progress.detail), ensure_ascii=False, sort_keys=True)}" if progress.detail else ""
    percent = f" {progress.progress * 100:5.1f}%" if progress.progress is not None else ""
    print(f"{prefix} [{progress.event}]{percent} {progress.message}{detail}", file=sys.stderr)


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            key_text = str(key)
            if key_text.lower() in SENSITIVE_HEADER_NAMES:
                result[key] = REDACTED
            elif key_text.lower() in {"source_url", "subtitle_url", "url"} and isinstance(item, str):
                result[key] = redact_url(item)
            else:
                result[key] = _jsonable(item)
        return result
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, set):
        return sorted(value)
    return value


if __name__ == "__main__":
    raise SystemExit(main())
