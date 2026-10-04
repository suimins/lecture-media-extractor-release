"""Register a per-user Chrome native host on Windows, macOS, or Linux."""
import base64
import hashlib
import json
import os
import shlex
import sys
from pathlib import Path
from lecture_script import NATIVE_HOST_NAME
from lecture_script.runtime import open_path

ROOT = Path(__file__).resolve().parent


def manifest_directory():
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Google" / "Chrome" / "NativeMessagingHosts"
    if sys.platform.startswith("linux"):
        config = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
        if not config.is_absolute():
            config = Path.home() / ".config"
        return config / "google-chrome" / "NativeMessagingHosts"
    if sys.platform == "win32":
        return ROOT / "data" / "chrome"
    raise RuntimeError(f"Chrome native messaging is not configured for {sys.platform}.")


def install():
    windows = sys.platform == "win32"
    python = ROOT / ".venv" / ("Scripts/python.exe" if windows else "bin/python")
    if not python.is_file():
        raise RuntimeError("Run setup.cmd (Windows), setup.command (macOS), or setup.sh (Linux) first.")
    extension = json.loads((ROOT / "extension" / "manifest.json").read_text(encoding="utf-8"))
    digest = hashlib.sha256(base64.b64decode(extension["key"])).hexdigest()[:32]
    extension_id = "".join(chr(ord("a") + int(char, 16)) for char in digest)
    directory = ROOT / "data" / "chrome"
    directory.mkdir(parents=True, exist_ok=True)
    launcher = directory / ("native-host.cmd" if windows else "native-host.sh")
    if windows:
        launcher.write_text('@echo off\nchcp 65001 >nul\nsetlocal\n'
            f'cd /d "{ROOT}"\n"{python}" -u -m lecture_script.native_host %*\n', encoding="utf-8")
    else:
        launcher.write_text('#!/bin/sh\n'
            f'cd {shlex.quote(str(ROOT))} || exit 1\n'
            f'exec {shlex.quote(str(python))} -u -m lecture_script.native_host "$@"\n',
            encoding="utf-8", newline="\n")
        launcher.chmod(0o755)
    manifest = manifest_directory() / f"{NATIVE_HOST_NAME}.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps({
        "name": NATIVE_HOST_NAME, "description": "Lecture Script selected MP4/MP3/transcript exports",
        "path": str(launcher), "type": "stdio", "allowed_origins": [f"chrome-extension://{extension_id}/"]
    }, indent=2), encoding="utf-8")
    if windows:
        import winreg
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"Software\Google\Chrome\NativeMessagingHosts\{NATIVE_HOST_NAME}") as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, str(manifest))
    print(f"Native host installed. Extension ID: {extension_id}")
    print(f"Chrome > chrome://extensions > Developer mode > Load unpacked > {ROOT / 'extension'}")
    if "--open-guide" in sys.argv:
        open_path(ROOT / "install-guide.html")
    return extension_id


if __name__ == "__main__":
    install()
