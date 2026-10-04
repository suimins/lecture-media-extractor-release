import json
import os
import shlex
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import install_chrome
from lecture_script import NATIVE_HOST_NAME, runtime, whisper_cpp


class PlatformTests(unittest.TestCase):
    def test_mac_and_linux_register_user_host_with_safe_launcher(self):
        project = Path(install_chrome.__file__).parent
        for system in ("darwin", "linux"):
            with self.subTest(system=system), tempfile.TemporaryDirectory() as directory:
                home = Path(directory)
                root = home / "project with 'quote and spaces"
                python = root / ".venv" / "bin" / "python"
                python.parent.mkdir(parents=True)
                python.touch()
                (root / "extension").mkdir()
                (root / "extension" / "manifest.json").write_bytes((project / "extension" / "manifest.json").read_bytes())
                with patch.object(install_chrome, "ROOT", root), patch.object(sys, "platform", system), \
                        patch.object(Path, "home", return_value=home), patch.dict(os.environ, {"XDG_CONFIG_HOME": str(home / ".config")}), \
                        patch.object(sys, "argv", ["install_chrome.py"]):
                    extension_id = install_chrome.install()
                    manifest = install_chrome.manifest_directory() / f"{NATIVE_HOST_NAME}.json"
                expected = home / ("Library/Application Support/Google/Chrome" if system == "darwin" else ".config/google-chrome")
                self.assertEqual(manifest.parent, expected / "NativeMessagingHosts")
                payload = json.loads(manifest.read_text(encoding="utf-8"))
                self.assertEqual(payload["allowed_origins"], [f"chrome-extension://{extension_id}/"])
                launcher = Path(payload["path"])
                self.assertTrue(launcher.is_absolute())
                source = launcher.read_text(encoding="utf-8")
                self.assertIn(shlex.quote(str(python)), source)
                self.assertIn(shlex.quote(str(root)), source)
                self.assertNotIn(b"\r\n", launcher.read_bytes())
                if os.name != "nt":
                    self.assertTrue(launcher.stat().st_mode & 0o100)

    def test_open_folder_uses_platform_command_and_isolates_protocol_streams(self):
        for system, command in (("darwin", "open"), ("linux", "xdg-open")):
            with self.subTest(system=system), patch.object(sys, "platform", system), \
                    patch.object(runtime.subprocess, "Popen") as launch:
                runtime.open_path("folder with spaces")
                self.assertEqual(launch.call_args.args[0], [command, "folder with spaces"])
                self.assertEqual(launch.call_args.kwargs["stdout"], runtime.subprocess.DEVNULL)

    def test_mac_installation_does_not_install_cuda_packages(self):
        with patch.object(sys, "platform", "darwin"), \
                patch("lecture_script.whisper_cpp.install_runtime") as install, \
                patch("lecture_script.whisper_cpp.available_devices", return_value=[{"name": "Metal GPU 0"}]), \
                patch.object(runtime.subprocess, "run") as run:
            self.assertTrue(runtime.install_gpu_runtime())
            install.assert_called_once()
            run.assert_not_called()
