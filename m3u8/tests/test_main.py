import io
import sys
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from app import main as cli
from app.core.ffmpeg import FfmpegCapabilities


class MainCliTests(unittest.TestCase):
    def test_check_ffmpeg_uses_cli_paths(self) -> None:
        seen = {}

        def fake_check(config):
            seen["ffmpeg_path"] = config.ffmpeg_path
            seen["ffprobe_path"] = config.ffprobe_path
            return FfmpegCapabilities(
                ffmpeg_path=config.ffmpeg_path,
                ffprobe_path=config.ffprobe_path,
                ffmpeg_available=True,
                ffprobe_available=True,
            )

        argv = [
            "app.main",
            "--check-ffmpeg",
            "--ffmpeg",
            "..\\tools\\ffmpeg\\bin\\ffmpeg.exe",
            "--ffprobe",
            "..\\tools\\ffmpeg\\bin\\ffprobe.exe",
        ]
        with (
            patch.object(sys, "argv", argv),
            patch.object(cli, "run_capability_check", fake_check),
            redirect_stdout(io.StringIO()),
        ):
            code = cli.main()

        self.assertEqual(code, 0)
        self.assertEqual(seen["ffmpeg_path"], "..\\tools\\ffmpeg\\bin\\ffmpeg.exe")
        self.assertEqual(seen["ffprobe_path"], "..\\tools\\ffmpeg\\bin\\ffprobe.exe")


if __name__ == "__main__":
    unittest.main()
