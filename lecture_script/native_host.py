"""Chrome Native Messaging bridge. Credentials live only in process memory."""
from __future__ import annotations

import json
import os
import queue
import struct
import sys
import threading
from urllib.parse import urlsplit

from .browser import Source, media_kind
from .pipeline import Cancelled, Options, Transcriber, completed_outputs, job_identity, process_source, safe_error, validate_outputs
from .storage import DATA, DEFAULT_OUTPUT, JobStore
from .runtime import open_path

MAX_MESSAGE = 1024 * 1024


def read_message(stream):
    header = stream.read(4)
    if not header:
        return None
    if len(header) != 4:
        raise ValueError("Incomplete message header")
    size = struct.unpack("=I", header)[0]
    if size > MAX_MESSAGE or size == 0:
        raise ValueError("Invalid message size")
    data = bytearray()
    while len(data) < size:
        part = stream.read(size - len(data))
        if not part:
            raise ValueError("Incomplete message")
        data.extend(part)
    value = json.loads(data)
    if not isinstance(value, dict):
        raise ValueError("Expected an object")
    return value


def write_message(stream, message):
    data = json.dumps(message, ensure_ascii=False).encode("utf-8")
    if len(data) > MAX_MESSAGE:
        raise ValueError("Response too large")
    stream.write(struct.pack("=I", len(data)) + data)
    stream.flush()


def http_url(value):
    if not isinstance(value, str) or len(value) > 16384 or any(ord(c) < 32 for c in value):
        raise ValueError("잘못된 영상 주소입니다.")
    parsed = urlsplit(value)
    if parsed.scheme not in ("https", "http") or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("HTTP/HTTPS 영상 주소만 사용할 수 있습니다.")
    return value


def source_from_message(message):
    raw = message.get("source")
    if not isinstance(raw, dict):
        raise ValueError("영상 정보를 받지 못했습니다.")
    url, page_url = http_url(raw.get("url")), http_url(raw.get("page_url"))
    kind = raw.get("kind") or media_kind(url)
    if kind not in ("media", "hls", "dash"):
        raise ValueError("동영상 소스만 처리할 수 있습니다.")
    headers = {}
    for name, value in raw.get("headers", {}).items():
        if name.lower() not in ("referer", "origin", "user-agent", "authorization"):
            continue
        if not isinstance(value, str) or len(value) > 16384 or "\r" in value or "\n" in value:
            raise ValueError("잘못된 요청 헤더입니다.")
        headers[name] = value
    hosts = {urlsplit(url).hostname, urlsplit(page_url).hostname}
    cookies = []
    for cookie in raw.get("cookies", [])[:500]:
        domain = str(cookie.get("domain", ""))
        if not domain or not any(h == domain.lstrip(".") or (domain.startswith(".") and h.endswith(domain)) for h in hosts):
            continue
        cookies.append({"name": str(cookie["name"]), "value": str(cookie["value"]), "domain": domain,
                        "path": str(cookie.get("path", "/")), "secure": bool(cookie.get("secure")),
                        "expires": cookie.get("expirationDate", cookie.get("expires", -1))})
    return Source(url, kind, str(raw.get("title") or "강의")[:200], page_url, headers, cookies,
                  drm=bool(raw.get("drm")), course=str(raw.get("course") or "과목 미지정")[:200])


class Host:
    def __init__(self, send, output=None, store=None):
        self.send = send
        self.output = output or DEFAULT_OUTPUT
        self.store = store or JobStore()
        self.pending = queue.Queue()
        self.jobs = {}
        self.guard = threading.Lock()
        self.worker = threading.Thread(target=self.run, daemon=True)
        self.worker.start()

    def handle(self, message):
        command = message.get("command")
        request_id = str(message.get("request_id", ""))[:100]
        if command == "hello":
            self.send({"type": "ready", "output": str(self.output)})
        elif command == "start":
            source = source_from_message(message)
            outputs = validate_outputs(message.get("outputs", ["mp4", "mp3", "script"]))
            identity = job_identity(source)
            with self.guard:
                if identity in self.jobs:
                    self.send({"type": "duplicate", "request_id": request_id, "job_id": identity,
                               "label": "이미 처리 중인 강의입니다."})
                    return
                cancel = threading.Event()
                self.jobs[identity] = cancel
            model = message.get("model", "small")
            if model not in ("tiny", "small", "medium", "large-v3", "turbo"):
                model = "small"
            options = Options(self.output, model=model, prefer_subtitles=False, outputs=outputs)
            self.send({"type": "progress", "request_id": request_id, "job_id": identity,
                       "status": "queued", "progress": 0, "label": "처리 대기 중",
                       "title": source.title, "course": source.course, "outputs": list(outputs)})
            self.pending.put((source, options, cancel, identity, request_id, bool(message.get("retry"))))
        elif command == "cancel":
            with self.guard:
                cancel = self.jobs.get(message.get("job_id"))
                if cancel:
                    cancel.set()
        elif command == "open_output":
            self.output.mkdir(parents=True, exist_ok=True)
            open_path(self.output)
        else:
            raise ValueError("지원하지 않는 요청입니다.")

    def run(self):
        engine = Transcriber()
        while True:
            item = self.pending.get()
            if item is None:
                return
            source, options, cancel, identity, request_id, retry = item
            base = {"request_id": request_id, "job_id": identity, "title": source.title, "course": source.course}
            def report(value, label):
                self.send({**base, "type": "progress", "status": "running", "progress": value, "label": label})
            try:
                if cancel.is_set():
                    raise Cancelled()
                # Refreshing the same lecture must not download it again across Chrome sessions.
                result = completed_outputs(source, options) if not retry else None
                if result is None:
                    result = process_source(source, options, engine, cancel, report, self.store)
                label = "선택한 파일 저장 완료 · " + " · ".join("스크립트" if kind == "script" else kind.upper() for kind in options.outputs)
                self.store.update(identity, source.title, "complete", result["directory"], progress=100, detail=label)
                self.send({**base, "type": "complete", "status": "complete", "progress": 100,
                           "label": label, "directory": result["directory"]})
            except Cancelled:
                self.send({**base, "type": "cancelled", "status": "cancelled", "label": "중단됨 · 저장된 영상과 음성은 유지됩니다."})
            except Exception as error:
                self.send({**base, "type": "error", "status": "failed", "label": safe_error(error)})
            finally:
                with self.guard:
                    self.jobs.pop(identity, None)

    def close(self):
        with self.guard:
            for cancel in self.jobs.values():
                cancel.set()
        self.pending.put(None)
        self.worker.join(timeout=10)


def main():
    if os.name == "nt":
        import msvcrt
        msvcrt.setmode(sys.stdin.fileno(), os.O_BINARY)
        msvcrt.setmode(sys.stdout.fileno(), os.O_BINARY)
    # Native libraries and Python logging must not corrupt Chrome's framed stdout.
    output = os.fdopen(os.dup(sys.stdout.fileno()), "wb", buffering=0)
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    sys.stdout = sys.stderr
    # On Windows, NumPy's DLL initialization can inspect CRT stdin handles.
    # Import before another thread blocks reading Chrome's pipe (CRT handle lock).
    import numpy  # noqa: F401
    guard = threading.Lock()
    def send(message):
        with guard:
            try:
                write_message(output, message)
            except (OSError, ValueError):
                pass
    host = Host(send)
    try:
        while (message := read_message(sys.stdin.buffer)) is not None:
            try:
                host.handle(message)
            except Exception as error:
                send({"type": "error", "status": "failed", "request_id": str(message.get("request_id", ""))[:100],
                      "label": safe_error(error)})
    finally:
        host.close()
        output.close()


if __name__ == "__main__":
    main()
