import json
import tempfile
import unittest
from pathlib import Path

from app.core.export import format_srt, format_srt_timestamp, write_exports
from app.models.settings import JobConfig, JobResult, TranscriptSegment


class ExportTests(unittest.TestCase):
    def test_format_srt_timestamp(self) -> None:
        self.assertEqual(format_srt_timestamp(0), "00:00:00,000")
        self.assertEqual(format_srt_timestamp(62.345), "00:01:02,345")
        self.assertEqual(format_srt_timestamp(3661.2), "01:01:01,200")

    def test_format_srt(self) -> None:
        text = format_srt(
            [
                TranscriptSegment(start=1.0, end=2.5, text="hello"),
                TranscriptSegment(start=3.0, end=4.0, text="world"),
            ]
        )

        self.assertIn("1\n00:00:01,000 --> 00:00:02,500\nhello", text)
        self.assertIn("2\n00:00:03,000 --> 00:00:04,000\nworld", text)

    def test_write_exports_creates_files_and_redacts_json_urls(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            config = JobConfig(url="https://cdn.example.com/master.m3u8?token=secret", output_dir=tmp_dir)
            result = JobResult(
                source_url=config.url,
                method="subtitle",
                input_playlist_kind="master",
                playlist_type="vod",
                duration_seconds=2.0,
                subtitle_url="https://cdn.example.com/subs.m3u8?auth_key=subtitle-secret",
                selected_subtitle_track_id="subs:ko:Korean",
                subtitle_quality_status="ok",
                audio_path=None,
                transcript_text="hello",
                segments=[TranscriptSegment(start=0, end=1, text="hello")],
                output_files=[],
            )

            files = write_exports(result, config)

            self.assertEqual(len(files), 4)
            self.assertTrue((Path(tmp_dir) / "transcript.txt").exists())
            self.assertTrue((Path(tmp_dir) / "transcript.md").exists())
            self.assertTrue((Path(tmp_dir) / "transcript.srt").exists())
            metadata = json.loads((Path(tmp_dir) / "transcript.json").read_text(encoding="utf-8"))
            self.assertNotIn("secret", metadata["source_url"])
            self.assertNotIn("subtitle-secret", metadata["subtitle_url"])
            self.assertEqual(result.output_files, files)


if __name__ == "__main__":
    unittest.main()
