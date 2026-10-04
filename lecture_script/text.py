from __future__ import annotations

import html
import json
import math
import re
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class Segment:
    start: float
    end: float
    text: str
    original: str | None = None


def safe_name(value: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value).strip(" .")[:70]
    if not name:
        name = "lecture"
    if name.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(10)), *(f"LPT{i}" for i in range(10))}:
        name = "_" + name
    return name


def timestamp(seconds: float, separator: str = ",") -> str:
    ms = max(0, round(seconds * 1000))
    hours, ms = divmod(ms, 3600000)
    minutes, ms = divmod(ms, 60000)
    seconds, ms = divmod(ms, 1000)
    return f"{hours:02}:{minutes:02}:{seconds:02}{separator}{ms:03}"


def parse_time(value: str) -> float:
    fields = value.replace(",", ".").split(":")
    if len(fields) not in (2, 3):
        raise ValueError("잘못된 자막 시간 형식입니다.")
    total = 0.0
    for field in fields:
        total = total * 60 + float(field)
    return total


def validate_segments(segments: list[Segment]) -> list[Segment]:
    clean = []
    for segment in segments:
        if not math.isfinite(segment.start) or not math.isfinite(segment.end):
            raise ValueError("자막에 유효하지 않은 시간이 있습니다.")
        if segment.start < 0 or segment.end <= segment.start:
            raise ValueError("자막의 시작·종료 시간을 확인하세요.")
        if segment.text.strip():
            clean.append(segment)
    return sorted(clean, key=lambda s: (s.start, s.end))


def parse_subtitles(content: str) -> list[Segment]:
    content = content.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    result = []
    for block in re.split(r"\n\s*\n", content):
        lines = block.strip().splitlines()
        if not lines or lines[0].startswith(("NOTE", "STYLE", "REGION")):
            continue
        for index, line in enumerate(lines):
            match = re.match(r"\s*([\d:.,]+)\s+-->\s+([\d:.,]+)", line)
            if not match:
                continue
            # Remove WebVTT formatting and inline timestamps; preserve spoken words.
            text = html.unescape(re.sub(r"<[^>]+>", "", "\n".join(lines[index + 1:]))).strip()
            if text:
                result.append(Segment(parse_time(match[1]), parse_time(match[2]), text))
            break
    return validate_segments(result)


def atomic_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def export_transcript(directory: Path, title: str, segments: list[Segment], metadata: dict) -> dict:
    segments = validate_segments(segments)
    directory.mkdir(parents=True, exist_ok=True)
    srt, vtt = [], ["WEBVTT\n"]
    markdown = [f"# {title}\n"]
    plain = []
    for number, item in enumerate(segments, 1):
        body = item.text.strip()
        plain.append(body)
        srt.append(f"{number}\n{timestamp(item.start)} --> {timestamp(item.end)}\n{body}\n")
        vtt.append(f"{timestamp(item.start, '.')} --> {timestamp(item.end, '.')}\n{body}\n")
        markdown.append(f"**[{timestamp(item.start, '.')}]** {body}\n")
    outputs = {"transcript.txt": "\n\n".join(plain), "transcript.md": "\n".join(markdown),
               "subtitles.srt": "\n".join(srt), "subtitles.vtt": "\n".join(vtt)}
    for name, content in outputs.items():
        path = directory / name
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(content + "\n", encoding="utf-8")
        temporary.replace(path)
    payload = {"schema_version": 1, "title": title, "metadata": metadata,
               "segments": [asdict(s) for s in segments]}
    atomic_json(directory / "transcript.json", payload)
    # Preserve the first machine-generated result when the user edits later.
    if not (directory / "transcript.original.json").exists():
        atomic_json(directory / "transcript.original.json", payload)
    return payload
