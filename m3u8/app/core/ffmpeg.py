"""ffmpeg capability checks and output parsers."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field

from app.models.settings import FfmpegConfig


REQUIRED_PROTOCOLS = {"file", "http", "https", "tls", "crypto"}
REQUIRED_FORMATS = {"hls", "webvtt", "srt", "mov"}


@dataclass(slots=True)
class CommandResult:
    args: list[str]
    returncode: int
    stdout: str
    stderr: str


@dataclass(slots=True)
class FfmpegCapabilities:
    ffmpeg_path: str
    ffprobe_path: str
    ffmpeg_available: bool
    ffprobe_available: bool
    protocols: set[str] = field(default_factory=set)
    formats: set[str] = field(default_factory=set)
    missing_protocols: set[str] = field(default_factory=set)
    missing_formats: set[str] = field(default_factory=set)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return (
            self.ffmpeg_available
            and self.ffprobe_available
            and not self.missing_protocols
            and not self.missing_formats
            and not self.errors
        )


def run_capability_check(config: FfmpegConfig, *, timeout_seconds: float = 10.0) -> FfmpegCapabilities:
    """Check whether ffmpeg/ffprobe expose the protocol and format support we need."""

    capabilities = FfmpegCapabilities(
        ffmpeg_path=config.ffmpeg_path,
        ffprobe_path=config.ffprobe_path,
        ffmpeg_available=False,
        ffprobe_available=False,
    )

    ffmpeg_version = _run_command([config.ffmpeg_path, "-version"], timeout_seconds=timeout_seconds)
    capabilities.ffmpeg_available = ffmpeg_version.returncode == 0
    if not capabilities.ffmpeg_available:
        capabilities.errors.append(_command_error("ffmpeg", ffmpeg_version))

    ffprobe_version = _run_command([config.ffprobe_path, "-version"], timeout_seconds=timeout_seconds)
    capabilities.ffprobe_available = ffprobe_version.returncode == 0
    if not capabilities.ffprobe_available:
        capabilities.errors.append(_command_error("ffprobe", ffprobe_version))

    if capabilities.ffmpeg_available:
        protocols = _run_command([config.ffmpeg_path, "-protocols"], timeout_seconds=timeout_seconds)
        if protocols.returncode == 0:
            capabilities.protocols = parse_protocols(protocols.stdout)
        else:
            capabilities.errors.append(_command_error("ffmpeg -protocols", protocols))

        formats = _run_command([config.ffmpeg_path, "-formats"], timeout_seconds=timeout_seconds)
        if formats.returncode == 0:
            capabilities.formats = parse_formats(formats.stdout)
        else:
            capabilities.errors.append(_command_error("ffmpeg -formats", formats))

    capabilities.missing_protocols = REQUIRED_PROTOCOLS - capabilities.protocols
    capabilities.missing_formats = _missing_formats(capabilities.formats)
    return capabilities


def parse_protocols(output: str) -> set[str]:
    """Parse `ffmpeg -protocols` output into protocol names."""

    protocols: set[str] = set()
    in_section = False
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line in {"Input:", "Output:"}:
            in_section = True
            continue
        if line.endswith(":"):
            in_section = False
            continue
        if in_section:
            protocols.update(part.strip() for part in line.split() if part.strip())
    return protocols


def parse_formats(output: str) -> set[str]:
    """Parse `ffmpeg -formats` output into format names.

    ffmpeg format rows look like ` DE mov,mp4,m4a ...`; this returns each comma-separated alias.
    """

    formats: set[str] = set()
    for raw_line in output.splitlines():
        if len(raw_line) < 5:
            continue

        flags = raw_line[:4]
        if not any(char in "DE" for char in flags):
            continue
        if any(char not in " DE.d" for char in flags):
            continue

        rest = raw_line[4:].strip()
        if not rest or rest.startswith("=") or rest.startswith("---"):
            continue

        names = rest.split(maxsplit=1)[0]
        for name in names.split(","):
            clean = name.strip()
            if clean:
                formats.add(clean)
    return formats


def _missing_formats(formats: set[str]) -> set[str]:
    missing = set(REQUIRED_FORMATS)
    if "subrip" in formats:
        missing.discard("srt")
    return {name for name in missing if name not in formats}


def _run_command(args: list[str], *, timeout_seconds: float) -> CommandResult:
    try:
        completed = subprocess.run(
            args,
            capture_output=True,
            check=False,
            text=True,
            timeout=timeout_seconds,
        )
    except FileNotFoundError as exc:
        return CommandResult(args=args, returncode=127, stdout="", stderr=str(exc))
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout if isinstance(exc.stdout, str) else ""
        stderr = exc.stderr if isinstance(exc.stderr, str) else ""
        return CommandResult(args=args, returncode=124, stdout=stdout, stderr=stderr or "command timed out")

    return CommandResult(
        args=args,
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def _command_error(label: str, result: CommandResult) -> str:
    detail = result.stderr.strip() or result.stdout.strip() or f"exit code {result.returncode}"
    return f"{label} unavailable: {detail}"
