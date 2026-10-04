"""Real installer/host/transcription smoke test; optional installed project path."""
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from urllib.request import urlretrieve

ROOT = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import install_chrome
from lecture_script import NATIVE_HOST_NAME
from lecture_script.browser import Source
from lecture_script.native_host import read_message, write_message
from lecture_script.pipeline import Options, Transcriber, process_source
from lecture_script.whisper_cpp import SOURCE_COMMIT, available_devices, executable


def main():
    cli = executable()
    assert cli and cli.is_file(), "Platform GPU engine was not installed/built"
    subprocess.run([str(cli), "--help"], check=True, capture_output=True, timeout=30)
    manifest_path = install_chrome.manifest_directory() / f"{NATIVE_HOST_NAME}.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    request = io.BytesIO()
    write_message(request, {"command": "hello"})
    command = [os.environ["COMSPEC"], "/d", "/c", manifest["path"]] if sys.platform == "win32" else [manifest["path"]]
    result = subprocess.run(command, input=request.getvalue(), capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    assert read_message(io.BytesIO(result.stdout))["type"] == "ready"
    print("Installed Chrome native host handshake passed", flush=True)

    devices = available_devices()
    print("Detected GPU backends:", devices, flush=True)
    with tempfile.TemporaryDirectory(prefix="lecture platform ") as temporary:
        root = Path(temporary)
        audio = root / "sample.wav"
        urlretrieve(f"https://raw.githubusercontent.com/ggml-org/whisper.cpp/{SOURCE_COMMIT}/samples/jfk.wav", audio)
        engine = Transcriber()
        options = Options(root / "output", model="tiny", language="en", prefer_subtitles=False)
        result = process_source(Source(str(audio), "local", "Public speech fixture"), options, engine,
                                threading.Event(), lambda n, text: print(n, text, flush=True))
        text = " ".join(segment["text"] for segment in result["payload"]["segments"]).lower()
        assert "country" in text, text
        if devices:
            expected = {device["backend"] for device in devices}
            if sys.platform == "win32":
                expected.add("cuda")
            assert engine.actual_device in expected, engine.actual_device
        else:
            # A cold driver may become available between discovery and transcription.
            allowed = {"cpu", "metal"} if sys.platform == "darwin" else {"cpu", "vulkan", "cuda"}
            assert engine.actual_device in allowed, engine.actual_device
        print(f"Real transcription/export passed: {engine.actual_device}", flush=True)
        if engine.actual_device == "cpu":
            print("No usable GPU was selected in this run; CPU fallback was verified.", flush=True)


if __name__ == "__main__":
    main()
