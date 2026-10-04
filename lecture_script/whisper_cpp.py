"""Shared whisper.cpp adapter: Metal on macOS, Vulkan on Windows/Linux."""
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import wave
import zipfile
from pathlib import Path
from types import SimpleNamespace
from urllib.request import urlopen

from .storage import DATA

# Community build of upstream whisper.cpp v1.8.4; source/build provenance is in README.
RELEASE_URL = "https://github.com/jiang1997/whisper.cpp-release/releases/download/v1.8.4.1/"
SOURCE_COMMIT = "9386f239401074690479731c1e41683fbbeac557"
SOURCE_URL = f"https://codeload.github.com/ggml-org/whisper.cpp/tar.gz/{SOURCE_COMMIT}"
SOURCE_SHA256 = "0cc49e22729edd3cd2e7727522b456f459e1860fe65e975b39ce1a194857f9d5"
PACKAGES = {
    "win32": ("whisper-1.8.4-windows-x64.zip", "4b1b36343feb55ec3deace6a7dd18cc217f43a55e4ecce76ccd4ee3595c0b642"),
    "linux": ("whisper-1.8.4-linux-x64.tar.gz", "2ae2366da557189abf25310ba004bd6361d4558f8d373fb61cc89cbf0c2a0885"),
}
RUNTIME_DIRECTORY = DATA / "tools"
MODEL_NAMES = {"tiny": "tiny", "small": "small", "medium": "medium",
               "large-v3": "large-v3", "turbo": "large-v3-turbo"}
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def runtime_directory():
    if sys.platform == "win32" and platform.machine().lower() in ("amd64", "x86_64"):
        return RUNTIME_DIRECTORY / "whisper-vulkan" / "whisper-1.8.4-windows-x64"
    if sys.platform == "linux" and platform.machine().lower() in ("amd64", "x86_64"):
        return RUNTIME_DIRECTORY / "whisper-1.8.4-linux-x64"
    if sys.platform == "darwin":
        return RUNTIME_DIRECTORY / f"whisper-metal-1.8.4-{platform.machine().lower()}"
    return None


def executable():
    directory = runtime_directory()
    return directory / ("whisper-cli.exe" if sys.platform == "win32" else "whisper-cli") if directory else None


def download_verified(url, digest):
    archive = tempfile.TemporaryFile()
    try:
        with urlopen(url, timeout=60) as response:
            actual = hashlib.sha256()
            while block := response.read(1024 * 1024):
                archive.write(block)
                actual.update(block)
        if actual.hexdigest() != digest:
            raise RuntimeError("GPU runtime checksum mismatch")
        archive.seek(0)
        return archive
    except BaseException:
        archive.close()
        raise


def install_runtime():
    """Provision a native engine for this OS/architecture without emulation."""
    target = executable()
    if target is None:
        print("No packaged GPU engine for this platform; CPU remains available.")
        return
    if target.is_file():
        return
    RUNTIME_DIRECTORY.mkdir(parents=True, exist_ok=True)
    if sys.platform == "darwin":
        # Build upstream Metal on BOTH Apple Silicon and Intel Macs. The published
        # community Intel Mac binary disables Metal, so it cannot satisfy auto GPU.
        subprocess.run(["xcrun", "--find", "clang"], check=True, capture_output=True)
        from cmake import CMAKE_BIN_DIR
        cmake = str(Path(CMAKE_BIN_DIR) / "cmake")
        with download_verified(SOURCE_URL, SOURCE_SHA256) as archive, \
                tempfile.TemporaryDirectory(prefix="lecture-metal-") as temporary:
            root = Path(temporary)
            with tarfile.open(fileobj=archive, mode="r:gz") as source:
                source.extractall(root, filter="data")
            build = root / "build"
            subprocess.run([cmake, "-S", str(root / f"whisper.cpp-{SOURCE_COMMIT}"), "-B", str(build),
                "-DCMAKE_BUILD_TYPE=Release", "-DBUILD_SHARED_LIBS=OFF", "-DGGML_METAL=ON",
                "-DGGML_METAL_EMBED_LIBRARY=ON", "-DGGML_NATIVE=OFF", "-DGGML_BLAS=OFF",
                "-DWHISPER_COREML=OFF", "-DWHISPER_BUILD_TESTS=OFF"], check=True)
            subprocess.run([cmake, "--build", str(build), "--target", "whisper-cli", "--config", "Release",
                            "-j", str(min(8, os.cpu_count() or 4))], check=True)
            target.parent.mkdir(parents=True, exist_ok=True)
            partial = target.with_name("whisper-cli.partial")
            shutil.copy2(build / "bin" / "whisper-cli", partial)
            partial.chmod(0o755)
            partial.replace(target)
        return
    filename, digest = PACKAGES[sys.platform]
    with download_verified(RELEASE_URL + filename, digest) as archive:
        if sys.platform == "win32":
            with zipfile.ZipFile(archive) as zipped:
                target.parent.mkdir(parents=True, exist_ok=True)
                partial = target.with_suffix(".partial")
                partial.write_bytes(zipped.read("whisper-1.8.4-windows-x64/whisper-cli.exe"))
                partial.replace(target)
        else:
            with tarfile.open(fileobj=archive, mode="r:gz") as source:
                source.extractall(RUNTIME_DIRECTORY, filter="data")
            target.chmod(0o755)


def parse_devices(log):
    devices = []
    for match in re.finditer(r"ggml_vulkan:\s*(\d+) = ([^\r\n|]+)\| uma: ([01])", log):
        name = match[2].strip()
        if any(software in name.lower() for software in ("llvmpipe", "lavapipe", "swiftshader", "software")):
            continue
        devices.append({"index": int(match[1]), "name": name, "integrated": match[3] == "1", "backend": "vulkan"})
    for match in re.finditer(r"ggml_metal_device_init:\s*GPU name:\s*MTL(\d+)", log):
        devices.append({"index": int(match[1]), "name": f"Metal GPU {match[1]}", "integrated": True, "backend": "metal"})
    # Prefer dedicated graphics, irrespective of vendor. Keep the CLI's device IDs.
    return sorted(devices, key=lambda device: (device["integrated"], device["index"]))


def available_devices():
    cli = executable()
    if cli is None or not cli.is_file():
        return []
    try:
        with tempfile.TemporaryDirectory(prefix="lecture-gpu-probe-") as directory:
            root = Path(directory)
            # This pinned CLI enumerates its actual backends before reading model data.
            # An empty model intentionally stops the probe before loading any weights.
            model = root / "probe.bin"
            model.touch()
            audio = root / "probe.wav"
            with wave.open(str(audio), "wb") as output:
                output.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
                output.writeframes(b"\0\0" * 160)
            result = subprocess.run([str(executable()), "-m", str(model), "-f", str(audio)],
                                    capture_output=True, timeout=20, creationflags=CREATE_NO_WINDOW)
            return parse_devices(result.stderr.decode("utf-8", "replace"))
    except subprocess.TimeoutExpired as error:
        # Some Metal drivers enumerate the GPU before a slow initialization step.
        # Keep that discovery; _load still verifies an actual inference before use.
        return parse_devices((error.stderr or b"").decode("utf-8", "replace"))
    except OSError:
        return []


class WhisperCppModel:
    def __init__(self, model, device):
        from huggingface_hub import hf_hub_download
        self.device = device
        self.path = hf_hub_download("ggerganov/whisper.cpp", f"ggml-{MODEL_NAMES[model]}.bin",
                                    cache_dir=str(DATA / "models"))
        self.vad_path = hf_hub_download("ggml-org/whisper-vad", "ggml-silero-v5.1.2.bin",
                                       cache_dir=str(DATA / "models"))

    def transcribe(self, audio, *, language="ko", initial_prompt=None, beam_size=5,
                   vad_filter=True, cancel=None, **_kwargs):
        """Match the segment interface used by the shared checkpoint/export pipeline."""
        def segments():
            from .pipeline import Cancelled
            with tempfile.TemporaryDirectory(prefix="lecture-vulkan-") as directory:
                root = Path(directory)
                if not isinstance(audio, (str, Path)):
                    import numpy as np
                    samples = (np.clip(audio, -1, 1) * 32767).astype("<i2")
                    audio_path = root / "probe.wav"
                    with wave.open(str(audio_path), "wb") as output:
                        output.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
                        output.writeframes(samples.tobytes())
                else:
                    audio_path = Path(audio)
                output_path = root / "transcript"
                command = [str(executable()), "-m", str(self.path), "-f", str(audio_path),
                           "-dev", str(self.device["index"]), "-l", language or "auto",
                           "-bs", str(beam_size), "-t", str(min(8, os.cpu_count() or 4)),
                           "-mc", "0", "-sow", "-ml", "1", "-oj", "-of", str(output_path)]
                if vad_filter:
                    command += ["--vad", "-vm", str(self.vad_path)]
                if initial_prompt:
                    command += ["--prompt", initial_prompt]
                if cancel and cancel.is_set():
                    raise Cancelled()
                process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                           creationflags=CREATE_NO_WINDOW)
                try:
                    while True:
                        if cancel and cancel.is_set():
                            raise Cancelled()
                        try:
                            _, stderr = process.communicate(timeout=0.2)
                            break
                        except subprocess.TimeoutExpired:
                            continue
                finally:
                    if process.poll() is None:
                        process.kill()
                        process.communicate()
                log = stderr.decode("utf-8", "replace")
                if process.returncode:
                    raise RuntimeError("GPU 전사 실패: " + log[-1000:])
                backend = "MTL" if self.device.get("backend") == "metal" else "Vulkan"
                expected = f"{backend}{self.device['index']} backend"
                if f"using {expected}" not in log or f"failed to initialize {expected}" in log:
                    raise RuntimeError("GPU가 실제로 활성화되지 않았습니다.")
                payload = json.loads(output_path.with_suffix(".json").read_text(encoding="utf-8"))
                words = []
                for item in payload["transcription"]:
                    start, end = item["offsets"]["from"] / 1000, item["offsets"]["to"] / 1000
                    if end > start and item["text"].strip():
                        words.append(SimpleNamespace(start=start, end=end, word=item["text"]))
                        text = "".join(word.word for word in words)
                        if len(text) >= 80 or text.rstrip().endswith((".", "?", "!", "。")):
                            yield SimpleNamespace(start=words[0].start, end=end, text=text, words=words)
                            words = []
                if words:
                    yield SimpleNamespace(start=words[0].start, end=words[-1].end,
                                          text="".join(word.word for word in words), words=words)
        return segments(), None
