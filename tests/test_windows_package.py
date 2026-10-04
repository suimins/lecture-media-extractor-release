import os
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path

from scripts.build_windows_zip import build


@unittest.skipUnless(os.name == 'nt', 'Windows ZIP installer')
class WindowsPackageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='lecture package ')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        archive = self.root / 'share.zip'
        build(archive)
        with zipfile.ZipFile(archive) as zipped:
            self.assertIsNone(zipped.testzip())
            self.assertFalse(any(set(Path(name).parts) & {'.venv', 'data', 'work', '__pycache__', '.git'}
                                 for name in zipped.namelist()))
            zipped.extractall(self.root / "share with 'quote and spaces")
        self.source = self.root / "share with 'quote and spaces" / 'Lecture-Script-Windows'
        self.target = self.root / 'installed app'

    def run_installer(self):
        return subprocess.run(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass',
                               '-File', str(self.source / 'install-windows.ps1'),
                               '-InstallDir', str(self.target), '-NoOpen'], capture_output=True, timeout=30)

    def test_shared_zip_installs_app_and_preserves_existing_runtime_data(self):
        (self.target / 'data').mkdir(parents=True)
        (self.target / 'data' / 'keep.bin').write_bytes(b'existing model')
        (self.target / '.venv').mkdir()
        (self.target / '.venv' / 'keep.txt').write_text('existing environment')
        # Exercise copying and orchestration without changing this computer's host registration.
        (self.source / 'setup.ps1').write_text(
            'param([switch]$SkipGpuSetup)\n'
            'Set-Location -LiteralPath $PSScriptRoot\n'
            'Set-Content -LiteralPath (Join-Path $PSScriptRoot "setup-ran.txt") -Value "ready"\n')
        result = self.run_installer()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.target / 'setup-ran.txt').is_file())
        self.assertTrue((self.target / 'lecture_script' / 'native_host.py').is_file())
        self.assertTrue((self.target / 'extension' / 'manifest.json').is_file())
        self.assertEqual((self.target / 'data' / 'keep.bin').read_bytes(), b'existing model')
        self.assertEqual((self.target / '.venv' / 'keep.txt').read_text(), 'existing environment')
        self.assertIn(b'Setup complete.', result.stdout)

    def test_setup_failure_is_not_reported_as_success(self):
        (self.source / 'setup.ps1').write_text("throw 'fixture install failed'\n")
        result = self.run_installer()
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn(b'Setup complete.', result.stdout)
