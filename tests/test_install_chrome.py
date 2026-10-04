import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import install_chrome
from lecture_script import NATIVE_HOST_NAME


@unittest.skipUnless(os.name == "nt", "Windows native host registration")
class InstallChromeTests(unittest.TestCase):
    def test_installer_registers_the_host_used_by_the_extension(self):
        project = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix="lecture install ") as temporary:
            root = Path(temporary)
            python = root / ".venv" / "Scripts" / "python.exe"
            python.parent.mkdir(parents=True)
            python.touch()
            (root / "extension").mkdir()
            (root / "extension" / "manifest.json").write_bytes((project / "extension" / "manifest.json").read_bytes())
            registry = MagicMock()
            with patch.object(install_chrome, "ROOT", root), patch.dict(sys.modules, {"winreg": registry}), \
                    patch.object(sys, "argv", ["install_chrome.py"]):
                extension_id = install_chrome.install()
            manifest_path = Path(registry.SetValueEx.call_args.args[-1])
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["name"], NATIVE_HOST_NAME)
            self.assertTrue(registry.CreateKey.call_args.args[1].endswith("\\" + manifest["name"]))
            self.assertIn(f'const HOST = "{manifest["name"]}";', (project / "extension" / "background.js").read_text(encoding="utf-8"))
            self.assertEqual(manifest["allowed_origins"], [f"chrome-extension://{extension_id}/"])
            launcher = Path(manifest["path"]).read_text(encoding="utf-8")
            self.assertIn(f'cd /d "{root}"', launcher)
            self.assertIn(f'"{python}" -u -m lecture_script.native_host', launcher)
