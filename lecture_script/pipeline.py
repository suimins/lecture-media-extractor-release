from __future__ import annotations

import hashlib
import http.cookiejar
import json
import math
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import wave
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from PySide6.QtCore import QThread, Signal

from .browser import Source, is_hansung_player
from .storage import DATA, JobStore
from .runtime import configure_gpu_runtime
from .whisper_cpp import WhisperCppModel, available_devices
from .text import Segment, atomic_json, export_transcript, parse_subtitles, safe_name


class Cancelled(Exception):
    pass


def check_cancel(event):
    if event.is_set():
        raise Cancelled()


def safe_error(error) -> str:
    text = str(error)
    if any(code in text for code in ("401", "403")):
        return "접근 권한 또는 주소 만료 오류입니다. 브라우저에서 강의를 다시 열고 새로 감지된 소스로 재시도하세요."
    if any(name in text.lower() for name in ("cublas", "cudnn", "cuda driver")):
        return "GPU 실행 라이브러리를 사용할 수 없습니다. setup.cmd를 다시 실행하고 NVIDIA 드라이버를 확인하세요."
    text = re.sub(r"https?://[^\s'\"<>]+", "[미디어 주소]", text)
    text = re.sub(r"(?i)(authorization|cookie|token|signature)\s*[:=]\s*\S+", r"\1=[숨김]", text)
    return text[-700:] or type(error).__name__


def job_identity(source: Source) -> str:
    if source.local:
        path = Path(source.url).resolve()
        stat = path.stat()
        value = f"{path}|{stat.st_size}|{stat.st_mtime_ns}"
    else:
        parsed = urlsplit(source.page_url if is_hansung_player(source.page_url) else source.url)
        # Keep media IDs and quality selectors; remove only known expiring credentials.
        volatile = {"token", "access_token", "auth", "signature", "sig", "expires", "exp", "policy", "key-pair-id", "hdnts"}
        query = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True)
                 if k.lower() not in volatile and not k.lower().startswith("x-amz-")]
        value = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), ""))
    return hashlib.sha256(value.encode()).hexdigest()[:16]


def cookie_jar(cookies):
    jar = http.cookiejar.CookieJar(policy=http.cookiejar.DefaultCookiePolicy(
        strict_ns_domain=http.cookiejar.DefaultCookiePolicy.DomainStrictNonDomain))
    for item in cookies:
        domain = item["domain"]
        expiry = item.get("expires", -1)
        jar.set_cookie(http.cookiejar.Cookie(
            version=0, name=item["name"], value=item["value"], port=None, port_specified=False,
            domain=domain, domain_specified=domain.startswith("."), domain_initial_dot=domain.startswith("."),
            path=item.get("path", "/"), path_specified=True, secure=item.get("secure", False),
            expires=int(expiry) if expiry and expiry > 0 else None,
            discard=not expiry or expiry <= 0, comment=None, comment_url=None, rest={}, rfc2109=False))
    return jar


def ffmpeg_path() -> str:
    import imageio_ffmpeg
    target = DATA / "tools" / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(imageio_ffmpeg.get_ffmpeg_exe(), target)
    return str(target)


def download_media(source: Source, work: Path, cancel, progress, audio_only=False) -> Path:
    from yt_dlp import YoutubeDL
    check_cancel(cancel)
    prefix = "media-" + source.key

    class QuietLogger:
        def debug(self, message):
            pass
        def warning(self, message):
            pass
        def error(self, message):
            pass

    def hook(info):
        check_cancel(cancel)
        total = info.get("total_bytes") or info.get("total_bytes_estimate") or 0
        if total:
            progress(min(14, int(14 * info.get("downloaded_bytes", 0) / total)), "영상 다운로드 중")

    options = {
        "outtmpl": str(work / (prefix + ".%(ext)s")),
        "format": "bestaudio/best" if audio_only else "bestvideo*+bestaudio/best", "noplaylist": True,
        "quiet": True, "no_warnings": True, "logger": QuietLogger(),
        "http_headers": source.headers, "progress_hooks": [hook],
        "ffmpeg_location": ffmpeg_path(), "socket_timeout": 20,
        "retries": 2, "fragment_retries": 2, "concurrent_fragment_downloads": 1,
        "skip_unavailable_fragments": False, "overwrites": True,
        "allow_unplayable_formats": False, "hls_prefer_native": True,
        "cachedir": False, "writethumbnail": False, "writeinfojson": False,
    }
    with YoutubeDL(options) as downloader:
        downloader.cookiejar.set_policy(http.cookiejar.DefaultCookiePolicy(
            strict_ns_domain=http.cookiejar.DefaultCookiePolicy.DomainStrictNonDomain))
        for cookie in cookie_jar(source.cookies):
            downloader.cookiejar.set_cookie(cookie)
        info = downloader.extract_info(source.url, download=True)
        check_cancel(cancel)
        if not info or info.get("_type") == "playlist":
            raise ValueError("강의의 개별 영상 소스를 선택하세요.")
        if info.get("has_drm"):
            raise ValueError("DRM으로 보호된 영상입니다.")
        path = Path(info.get("filepath") or downloader.prepare_filename(info))
        if not path.exists():
            candidates = [p for p in work.glob(prefix + ".*")
                          if p.suffix.lower() in (".mp4", ".mkv", ".webm", ".mov", ".m4a", ".mp3", ".ogg", ".wav")]
            if len(candidates) == 1:
                path = candidates[0]
    if not path.is_file() or not path.stat().st_size:
        raise ValueError("완성된 미디어 파일을 찾을 수 없습니다.")
    return path


def read_caption(source: Source, url: str, cancel) -> str:
    import requests
    check_cancel(cancel)
    with requests.Session() as session:
        session.cookies = cookie_jar(source.cookies)
        with session.get(url, headers=source.headers, stream=True, timeout=(10, 25)) as response:
            response.raise_for_status()
            chunks = []
            size = 0
            for part in response.iter_content(65536):
                check_cancel(cancel)
                size += len(part)
                if size > 20 * 1024 * 1024:
                    raise ValueError("자막 파일이 예상 크기를 초과했습니다.")
                chunks.append(part)
            raw = b"".join(chunks)
    for encoding in ("utf-8-sig", "cp949"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("자막 인코딩을 읽을 수 없습니다. UTF-8 SRT/VTT 파일을 사용하세요.")


def extract_audio(media: Path, target: Path, cancel) -> float:
    temporary = target.with_name("audio.partial.wav")
    command = [ffmpeg_path(), "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-i", str(media),
               "-map", "0:a:0", "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(temporary)]
    with subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)) as process:
        while True:
            try:
                _, stderr = process.communicate(timeout=0.3)
                break
            except subprocess.TimeoutExpired:
                if cancel.is_set():
                    process.kill()
                    process.communicate()
                    raise Cancelled()
    if process.returncode:
        raise ValueError("오디오를 추출할 수 없습니다. 음성 트랙과 파일 손상을 확인하세요. " + stderr.decode("utf-8", "replace")[-300:])
    with wave.open(str(temporary), "rb") as audio:
        duration = audio.getnframes() / audio.getframerate()
        if duration <= 0:
            raise ValueError("오디오 길이가 0입니다.")
    temporary.replace(target)
    return duration


def export_media(media: Path, target: Path, kind: str, cancel):
    """Write a real MP4/MP3 to a temporary file, then publish atomically."""
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.stem + ".partial" + target.suffix)
    codecs = (["-map", "0:v:0", "-map", "0:a:0?", "-c", "copy", "-movflags", "+faststart"]
              if kind == "mp4" else ["-map", "0:a:0", "-vn", "-c:a", "libmp3lame", "-q:a", "2"])
    for attempt in range(2 if kind == "mp4" else 1):
        check_cancel(cancel)
        command = [ffmpeg_path(), "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-i", str(media),
                   *codecs, str(temporary)]
        with subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)) as process:
            while True:
                try:
                    _, stderr = process.communicate(timeout=0.3)
                    break
                except subprocess.TimeoutExpired:
                    if cancel.is_set():
                        process.kill()
                        process.communicate()
                        raise Cancelled()
        if process.returncode == 0:
            check_cancel(cancel)
            if not temporary.is_file() or not temporary.stat().st_size:
                raise ValueError(f"{kind.upper()} 파일이 생성되지 않았습니다.")
            temporary.replace(target)
            return
        # If stream copying cannot create MP4, encode compatible video/audio.
        codecs = ["-map", "0:v:0", "-map", "0:a:0?", "-c:v", "libx264", "-preset", "veryfast",
                  "-crf", "20", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart"]
    raise ValueError(f"{kind.upper()} 변환 실패: " + stderr.decode("utf-8", "replace")[-300:])


@dataclass
class Options:
    output: Path
    model: str = "small"
    device: str = "auto"
    language: str = "ko"
    prefer_subtitles: bool = True
    vocabulary: str = ""
    action: str = "transcribe"
    outputs: tuple[str, ...] = ("script",)


def validate_outputs(outputs):
    if (not isinstance(outputs, (list, tuple)) or not outputs
            or any(not isinstance(item, str) or item not in ("mp4", "mp3", "script") for item in outputs)):
        raise ValueError("MP4, MP3, 스크립트 중 하나 이상 선택하세요.")
    return tuple(kind for kind in ("mp4", "mp3", "script") if kind in outputs)


def saved_outputs(course: Path, identity: str) -> dict:
    """Return only complete, nonempty exports belonging to this course."""
    files = {}
    try:
        record = json.loads((course / ".jobs" / f"{identity}.json").read_text(encoding="utf-8"))
        if record["identity"] != identity:
            return files
        for kind, folder in (("mp4", "video"), ("mp3", "audio"), ("script", "script")):
            if kind not in record["files"]:
                continue
            path = Path(record["files"][kind])
            if path.resolve().parent != (course / folder).resolve():
                continue
            if kind == "script":
                if not all((path / name).is_file() and (path / name).stat().st_size for name in (
                        "transcript.txt", "transcript.md", "subtitles.srt", "subtitles.vtt",
                        "transcript.json", "transcript.original.json")):
                    continue
                payload = json.loads((path / "transcript.json").read_text(encoding="utf-8"))
                if not payload.get("segments"):
                    continue
            elif path.suffix != f".{kind}" or not path.is_file() or not path.stat().st_size:
                continue
            files[kind] = str(path.resolve())
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        pass
    return files


def completed_outputs(source, options):
    course = options.output / safe_name(source.course or "과목 미지정")
    files = saved_outputs(course, job_identity(source))
    if not all(kind in files for kind in options.outputs):
        return None
    return {"directory": files["script"] if "script" in options.outputs else str(course),
            "course_directory": str(course), "outputs": {kind: files[kind] for kind in options.outputs}}


def save_audio(source: Source, options: Options, cancel, progress, store=None):
    """Stage one: durable WAV output, no speech model loaded or downloaded."""
    if source.drm or source.kind in ('lecture', 'subtitle'):
        raise ValueError("재생 가능한 영상 또는 음성 파일을 선택하세요.")
    if source.local and Path(source.url).suffix.lower() in ('.srt', '.vtt'):
        raise ValueError("자막에는 음원이 없습니다. 영상 또는 음성 파일을 선택하세요.")
    identity = 'audio-' + job_identity(source)
    directory = options.output / f"{safe_name(source.title)}_{identity[:14]}"
    work = directory / '.work'
    work.mkdir(parents=True, exist_ok=True)
    media = None
    if store:
        store.update(identity, source.title, 'running', directory)
    try:
        check_cancel(cancel)
        progress(0, '음원 저장 준비 중 · 음성 모델은 사용하지 않습니다')
        media = Path(source.url) if source.local else download_media(source, work, cancel, progress, audio_only=True)
        progress(20, '영상에서 음원 추출 중')
        audio = work / 'audio.wav'
        duration = extract_audio(media, audio, cancel)
        check_cancel(cancel)
        saved = directory / 'audio.wav'
        audio.replace(saved)
        result = {'mode': 'audio', 'title': source.title, 'directory': str(directory),
                  'audio_path': str(saved.resolve()), 'duration': duration,
                  'format': 'PCM WAV / 16000 Hz / mono'}
        atomic_json(directory / 'audio.json', result)
        if media and not source.local:
            media.unlink(missing_ok=True)
        if store:
            store.update(identity, source.title, 'audio_ready', directory)
        progress(100, '음원 저장 완료 · 저장한 음원으로 나중에 스크립트를 만들 수 있습니다')
        return result
    except Exception as error:
        if store:
            store.update(identity, source.title, 'cancelled' if isinstance(error, Cancelled) else 'failed', directory,
                         '' if isinstance(error, Cancelled) else safe_error(error))
        raise


class Transcriber:
    def __init__(self):
        self.model = None
        self.loaded_key = None
        self.actual_device = None
        self.compute_type = None

    def transcribe(self, audio_path, work, options, cancel, progress):
        from importlib.metadata import version
        check_cancel(cancel)
        digest = hashlib.sha256()
        with audio_path.open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                check_cancel(cancel)
                digest.update(block)
        config = {"audio_sha256": digest.hexdigest(), "model": options.model, "device": options.device,
                  "language": options.language, "vocabulary": options.vocabulary,
                  "engine": version("faster-whisper"), "chunk_seconds": 300, "pipeline_version": 2}
        cache_key = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()
        checkpoint_path = work / "checkpoint.json"
        checkpoint = {"key": cache_key, "chunks": {}}
        if checkpoint_path.exists():
            try:
                saved = json.loads(checkpoint_path.read_text(encoding="utf-8"))
                if saved.get("key") == cache_key:
                    checkpoint = saved
            except (ValueError, OSError):
                pass
        with wave.open(str(audio_path), "rb") as audio:
            rate, frames = audio.getframerate(), audio.getnframes()
            duration = frames / rate
            count = math.ceil(duration / 300)
            result = []
            for index in range(count):
                check_cancel(cancel)
                if str(index) in checkpoint["chunks"]:
                    result.extend(Segment(**s) for s in checkpoint["chunks"][str(index)])
                    progress(25 + int(70 * (index + 1) / count), f"저장된 구간 복원 · {index + 1}/{count}")
                    continue
                self._load(options, progress)
                check_cancel(cancel)
                core_start, core_end = index * 300, min((index + 1) * 300, duration)
                offset, stop = max(0, core_start - 1), min(duration, core_end + 1)
                audio.setpos(round(offset * rate))
                chunk_path = work / "chunk.wav"
                with wave.open(str(chunk_path), "wb") as chunk:
                    chunk.setparams(audio.getparams())
                    chunk.writeframes(audio.readframes(round((stop - offset) * rate)))
                backend_options = {"cancel": cancel} if self.actual_device in ("vulkan", "metal") else {}
                iterator, _ = self.model.transcribe(str(chunk_path), language=None if options.language == "auto" else options.language,
                    task="transcribe", vad_filter=True, word_timestamps=True, condition_on_previous_text=False,
                    initial_prompt=options.vocabulary or None, beam_size=5, **backend_options)
                items = []
                for segment in iterator:
                    check_cancel(cancel)
                    if segment.words:
                        words = [word for word in segment.words
                                 if core_start <= offset + (word.start + word.end) / 2 < core_end]
                        if not words:
                            continue
                        start, end = max(core_start, offset + words[0].start), min(core_end, offset + words[-1].end)
                        text = "".join(word.word for word in words).strip()
                    else:
                        if not core_start <= offset + (segment.start + segment.end) / 2 < core_end:
                            continue
                        start, end = max(core_start, offset + segment.start), min(core_end, offset + segment.end)
                        text = segment.text.strip()
                    if text and end > start:
                        items.append(Segment(start, end, text))
                    device_label = f"GPU ({self.actual_device.upper()})" if self.actual_device in ("cuda", "vulkan", "metal") else "CPU"
                    progress(25 + min(69, int(70 * (offset + segment.end) / duration)), f"음성 인식 · {device_label} · {index + 1}/{count} 구간")
                checkpoint["chunks"][str(index)] = [asdict(s) for s in items]
                atomic_json(checkpoint_path, checkpoint)
                result.extend(items)
                chunk_path.unlink(missing_ok=True)
        return result

    def _load(self, options, progress):
        key = (options.model, options.device)
        if self.loaded_key == key:
            return
        progress(25, f"음성 모델 준비 · {options.model} (첫 실행은 모델 다운로드가 필요합니다)")
        configure_gpu_runtime()
        import ctranslate2
        from faster_whisper import WhisperModel
        from faster_whisper.utils import download_model
        from huggingface_hub.errors import LocalEntryNotFoundError
        if options.device not in ("auto", "cuda", "vulkan", "metal", "cpu"):
            raise ValueError("지원하지 않는 전사 장치입니다.")
        candidates = []
        if options.device == "cuda" or (options.device == "auto" and sys.platform == "win32"):
            try:
                if options.device == "cuda" or ctranslate2.get_cuda_device_count():
                    candidates.append(("cuda", None))
            except RuntimeError as error:
                progress(25, "CUDA 감지 실패 · 다른 장치를 확인합니다 · " + safe_error(error))
        if options.device in ("auto", "vulkan", "metal"):
            candidates.extend((device.get("backend", "vulkan"), device) for device in available_devices()
                              if options.device == "auto" or device.get("backend", "vulkan") == options.device)
        if options.device in ("auto", "cpu"):
            candidates.append(("cpu", None))
        if not candidates:
            raise RuntimeError("사용 가능한 GPU가 없습니다. 설치 프로그램과 그래픽 드라이버를 확인하세요.")
        # Drop the previous model before loading another large model on the same GPU.
        self.model = None
        self.loaded_key = None
        for device, adapter in candidates:
            try:
                if device in ("vulkan", "metal"):
                    progress(25, f"GPU 준비 · {adapter['name']} · {device.upper()} · {options.model}")
                    self.model = WhisperCppModel(options.model, adapter)
                    compute_type = "float16"
                else:
                    try:
                        model_path = download_model(options.model, cache_dir=str(DATA / "models"), local_files_only=True)
                        if not all((Path(model_path) / name).is_file() for name in ("model.bin", "config.json", "tokenizer.json")):
                            model_path = options.model
                    except LocalEntryNotFoundError:
                        model_path = options.model
                    compute_type = "int8" if device == "cpu" else "float16"
                    self.model = WhisperModel(model_path, device=device, compute_type=compute_type,
                        download_root=str(DATA / "models"), cpu_threads=min(8, os.cpu_count() or 4))
                if device != "cpu":
                    # Confirm inference, not just device enumeration or model construction.
                    import numpy as np
                    probe, _ = self.model.transcribe(np.zeros(16000, dtype=np.float32), language="ko", vad_filter=False, beam_size=1)
                    list(probe)
            except Exception as error:
                self.model = None
                if options.device != "auto" or device == "cpu":
                    raise
                progress(25, f"{device.upper()} 초기화 실패 · 다음 장치로 전환 · " + safe_error(error))
                continue
            self.actual_device = device
            self.compute_type = compute_type
            label = f"GPU ({device.upper()})" if device != "cpu" else "CPU"
            if adapter:
                label += f" · {adapter['name']}"
            progress(25, f"음성 모델 준비 완료 · {label} · {options.model}")
            self.loaded_key = key
            return


def process_source(source, options, transcriber, cancel, progress, store=None):
    """Save the selected exports and clean up only intermediate files."""
    from PySide6.QtCore import QLockFile
    options.outputs = validate_outputs(options.outputs)
    if source.drm:
        raise ValueError("DRM으로 보호된 영상은 지원하지 않습니다.")
    identity = job_identity(source)
    course = options.output / safe_name(source.course or "과목 미지정")
    stem = f"{safe_name(source.title)}_{identity[:8]}"
    directory = course / "script" / stem
    work = course / ".work" / identity
    work.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(options.output / ".pipeline.lock"))
    lock.setStaleLockTime(0)
    progress(0, "다른 강의 처리 완료를 기다리는 중")
    while not lock.tryLock(0):
        check_cancel(cancel)
        if lock.error() == QLockFile.LockError.PermissionError:
            raise ValueError("다운로드 폴더에 쓸 수 없습니다. 폴더 권한을 확인하세요.")
        cancel.wait(0.25)
    try:
        return _process_pipeline(source, options, transcriber, cancel, progress, store,
                                 identity, course, stem, directory, work)
    finally:
        lock.unlock()


def remove_intermediates(source, output, work):
    """Delete only this job's working files; final exports are never targets."""
    root = output.resolve()
    original = Path(source.url).resolve() if source.local else None
    paths = [path for path in work.iterdir() if path.is_file() or path.is_symlink()]
    # Validate every target before deleting any file; never follow a path outside output.
    for path in [work, *paths]:
        resolved = path.resolve()
        if not resolved.is_relative_to(root) or not path.parent.resolve().is_relative_to(root) or resolved == original:
            raise ValueError("원본 파일 또는 저장 폴더 밖의 파일은 정리할 수 없습니다.")
    for path in paths:
        path.unlink(missing_ok=True)
    # Only remove empty directories; other lectures' files must remain untouched.
    for folder in (work, work.parent):
        try:
            folder.rmdir()
        except OSError:
            pass


def _process_pipeline(source, options, transcriber, cancel, progress, store,
                      identity, course, stem, directory, work):
    stage = "downloading"
    result_directory = directory if "script" in options.outputs else course
    last_report = (0.0, None, None)
    def report(value, label):
        nonlocal last_report
        now = time.monotonic()
        if value == last_report[1] and stage == last_report[2] and now - last_report[0] < 0.3:
            return
        last_report = (now, value, stage)
        if store:
            store.update(identity, source.title, stage, result_directory, progress=value, detail=label)
        progress(value, label)

    if store:
        store.update(identity, source.title, "running", result_directory)
    try:
        check_cancel(cancel)
        files = saved_outputs(course, identity)
        payload, segments, media, audio, duration = None, None, None, None, None
        origin = "speech_recognition"
        is_caption = source.kind == "subtitle" or (source.local and Path(source.url).suffix.lower() in (".srt", ".vtt"))
        if is_caption:
            if options.outputs != ("script",):
                raise ValueError("자막 파일에서는 스크립트만 저장할 수 있습니다.")
            stage = "transcribing"
            content = Path(source.url).read_text(encoding="utf-8-sig") if source.local else read_caption(source, source.url, cancel)
            segments, origin = parse_subtitles(content), "existing_subtitles"
        else:
            record_path = work / "pipeline.json"
            saved = {}
            try:
                saved = json.loads(record_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pass
            recorded_media = Path(saved.get("media_path", ""))
            if (saved.get("identity") == identity and recorded_media.is_file()
                    and recorded_media.resolve().parent == work.resolve()
                    and recorded_media.stat().st_size == saved.get("media_size")):
                media = recorded_media
                report(14, "이전에 내려받은 파일 사용 중")
            elif "mp4" in files:
                media = Path(files["mp4"])
                report(14, "저장된 MP4 다시 사용 중")
            else:
                report(0, "미디어 다운로드 준비 중")
                if source.local:
                    original = Path(source.url)
                    media = work / ("source" + original.suffix)
                    temporary = media.with_suffix(media.suffix + ".partial")
                    shutil.copy2(original, temporary)
                    temporary.replace(media)
                else:
                    media = download_media(source, work, cancel, report, "mp4" not in options.outputs)
                check_cancel(cancel)
            saved.update(identity=identity, media_path=str(media.resolve()), media_size=media.stat().st_size)
            atomic_json(record_path, saved)

            if "mp4" in options.outputs:
                stage = "exporting"
                report(16, "MP4 영상 저장 중")
                target = course / "video" / (stem + ".mp4")
                if media.resolve() != target.resolve():
                    export_media(media, target, "mp4", cancel)
                files["mp4"] = str(target.resolve())
            if "mp3" in options.outputs:
                stage = "exporting"
                report(20, "MP3 음성 저장 중")
                target = course / "audio" / (stem + ".mp3")
                export_media(media, target, "mp3", cancel)
                files["mp3"] = str(target.resolve())
            if "script" in options.outputs:
                stage = "extracting"
                report(23, "스크립트 생성을 위한 음원 추출 중")
                audio = work / "audio.wav"
                if saved.get("audio_size") == (audio.stat().st_size if audio.exists() else -1):
                    with wave.open(str(audio), "rb") as wave_file:
                        duration = wave_file.getnframes() / wave_file.getframerate()
                else:
                    duration = extract_audio(media, audio, cancel)
                    check_cancel(cancel)
                    saved.update(audio_size=audio.stat().st_size, duration=duration)
                    atomic_json(record_path, saved)
                stage = "transcribing"
                report(25, "음성 스크립트 준비 중")
                if options.prefer_subtitles:
                    try:
                        if source.local:
                            for extension in (".srt", ".vtt"):
                                sidecar = Path(source.url).with_suffix(extension)
                                if sidecar.exists():
                                    segments = parse_subtitles(sidecar.read_text(encoding="utf-8-sig")) or None
                                    break
                        elif source.subtitle_url:
                            segments = parse_subtitles(read_caption(source, source.subtitle_url, cancel)) or None
                        if segments and max(s.end for s in segments) > duration + 5:
                            segments = None
                        if segments:
                            origin = "existing_subtitles"
                    except Cancelled:
                        raise
                    except Exception:
                        report(25, "기존 자막을 읽지 못해 음성 인식으로 전환합니다")
                if segments is None:
                    segments = transcriber.transcribe(audio, work, options, cancel, report)
        check_cancel(cancel)
        if "script" in options.outputs:
            if not segments:
                raise ValueError("인식한 음성이 없습니다. 재생 가능한 음성이 있는 파일인지 확인하세요.")
            metadata = {"origin": origin, "course": source.course,
                        "model": options.model if origin == "speech_recognition" else None,
                        "device": (transcriber.actual_device or "checkpoint") if origin == "speech_recognition" else None,
                        "compute_type": transcriber.compute_type if origin == "speech_recognition" else None,
                        "language": options.language, "media_path": files.get("mp4", ""),
                        "audio_path": files.get("mp3", ""), "duration": duration,
                        "source_host": source.display_location if not source.local else "local"}
            report(95, "스크립트 파일 저장 중")
            payload = export_transcript(directory, source.title, segments, metadata)
            files["script"] = str(directory.resolve())
        check_cancel(cancel)
        stage = "cleaning"
        report(98, "선택한 파일 저장 완료 · 임시 파일 정리 중")
        remove_intermediates(source, options.output, work)
        atomic_json(course / ".jobs" / f"{identity}.json", {"identity": identity, "files": files})
        label = "선택한 파일 저장 완료 · " + " · ".join("스크립트" if kind == "script" else kind.upper() for kind in options.outputs)
        if store:
            store.update(identity, source.title, "complete", result_directory, progress=100, detail=label)
        progress(100, label)
        return {"directory": str(result_directory), "course_directory": str(course), "payload": payload,
                "outputs": {kind: files[kind] for kind in options.outputs}}
    except Exception as error:
        if store:
            store.update(identity, source.title, "cancelled" if isinstance(error, Cancelled) else "failed", result_directory,
                         "" if isinstance(error, Cancelled) else safe_error(error))
        raise


class BatchWorker(QThread):
    progress = Signal(int, str)
    result = Signal(object)
    problem = Signal(str)

    def __init__(self, sources, options, store):
        super().__init__()
        self.sources, self.options, self.store = sources, options, store
        self.cancel = threading.Event()

    def run(self):
        transcriber = Transcriber() if self.options.action == 'transcribe' else None
        for index, source in enumerate(self.sources):
            if self.cancel.is_set():
                break
            prefix = f"[{index + 1}/{len(self.sources)}] {source.title} · "
            try:
                report = lambda value, label: self.progress.emit(value, prefix + label)
                if self.options.action == 'audio':
                    result = save_audio(source, self.options, self.cancel, report, self.store)
                else:
                    result = process_source(source, self.options, transcriber, self.cancel, report, self.store)
                self.result.emit(result)
            except Cancelled:
                self.progress.emit(0, "작업을 중단했습니다. 같은 파일을 다시 추가하면 완료한 전사 구간을 복원합니다.")
                break
            except Exception as error:
                self.problem.emit(f"{source.title}: {safe_error(error)}")
