import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from app.core.audio import AudioExtractionResult
from app.core.batch import make_output_dir, parse_urls_file, run_batch, should_skip_output
from app.core.errors import PlaylistError
from app.core.ffmpeg import FfmpegCapabilities
from app.core.job import JobDependencies
from app.core.playlist import PlaylistAnalysis
from app.core.transcribe import TranscriptionResult
from app.models.settings import JobConfig, TranscriptSegment


def ok_capabilities(_config) -> FfmpegCapabilities:
    return FfmpegCapabilities(
        ffmpeg_path="ffmpeg",
        ffprobe_path="ffprobe",
        ffmpeg_available=True,
        ffprobe_available=True,
        protocols={"file", "http", "https", "tls", "crypto"},
        formats={"hls", "webvtt", "srt", "mov"},
    )


def analysis_without_subtitles(url: str = "https://cdn.example.com/media.m3u8") -> PlaylistAnalysis:
    return PlaylistAnalysis(
        source_url=url,
        kind="media",
        playlist_type="vod",
        is_m3u8=True,
        has_endlist=True,
        target_duration=10,
        duration_seconds=60,
        subtitle_tracks=[],
    )


def fake_extract_audio(command, **_kwargs):
    Path(command.output_path).write_bytes(b"wav")
    return AudioExtractionResult(output_path=command.output_path, stderr="")


def fake_transcribe(_audio_path, **_kwargs):
    return TranscriptionResult(
        text="batch text",
        segments=[TranscriptSegment(start=0, end=1, text="batch text")],
        detected_language="ko",
        language_probability=0.9,
        duration_seconds=60,
    )


class BatchTests(unittest.TestCase):
    def test_parse_urls_file_ignores_blanks_and_comments(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "urls.txt"
            path.write_text(
                "\n".join(
                    [
                        "# lecture list",
                        "",
                        " https://cdn.example.com/a.m3u8 ",
                        "https://cdn.example.com/b.m3u8",
                    ]
                ),
                encoding="utf-8",
            )

            self.assertEqual(
                parse_urls_file(path),
                ["https://cdn.example.com/a.m3u8", "https://cdn.example.com/b.m3u8"],
            )

    def test_make_output_dir_is_stable_and_readable(self) -> None:
        first = make_output_dir("out", 3, "https://cdn.example.com/path/index.m3u8?token=secret")
        second = make_output_dir("out", 3, "https://cdn.example.com/path/index.m3u8?token=secret")

        self.assertEqual(first, second)
        self.assertEqual(first.parent, Path("out"))
        self.assertTrue(first.name.startswith("003_cdn.example.com_"))

    def test_should_skip_output_requires_txt_and_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            output_dir = Path(tmp_dir)

            self.assertFalse(should_skip_output(output_dir))
            (output_dir / "transcript.txt").write_text("text", encoding="utf-8")
            self.assertFalse(should_skip_output(output_dir))
            (output_dir / "transcript.json").write_text("{}", encoding="utf-8")
            self.assertTrue(should_skip_output(output_dir))

    def test_run_batch_processes_urls_and_writes_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            urls = ["https://cdn.example.com/one.m3u8", "https://cdn.example.com/two.m3u8"]
            calls = SimpleNamespace(analyzed=[])

            def analyze(url, **_kwargs):
                calls.analyzed.append(url)
                return analysis_without_subtitles(url)

            dependencies = JobDependencies(
                check_ffmpeg=ok_capabilities,
                analyze_playlist=analyze,
                extract_audio=fake_extract_audio,
                transcribe=fake_transcribe,
            )
            config = JobConfig(url="", output_dir=tmp_dir, keep_audio=True)

            result = run_batch(urls, base_config=config, dependencies=dependencies)

            self.assertEqual(result.total_count, 2)
            self.assertEqual(result.completed_count, 2)
            self.assertEqual(result.pending_count, 0)
            self.assertEqual(result.failed_count, 0)
            self.assertEqual(calls.analyzed, urls)
            self.assertTrue((Path(result.items[0].output_dir) / "transcript.txt").exists())
            summary = json.loads((Path(tmp_dir) / "batch_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["total_count"], 2)
            self.assertEqual(summary["completed_count"], 2)
            self.assertEqual(summary["items"][0]["status"], "done")

    def test_run_batch_skips_existing_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            url = "https://cdn.example.com/existing.m3u8"
            output_dir = make_output_dir(tmp_dir, 1, url)
            output_dir.mkdir(parents=True)
            (output_dir / "transcript.txt").write_text("done", encoding="utf-8")
            (output_dir / "transcript.json").write_text("{}", encoding="utf-8")

            def fail_analyze(*_args, **_kwargs):
                raise AssertionError("existing item should be skipped")

            dependencies = JobDependencies(
                check_ffmpeg=ok_capabilities,
                analyze_playlist=fail_analyze,
                extract_audio=fake_extract_audio,
                transcribe=fake_transcribe,
            )
            config = JobConfig(url="", output_dir=tmp_dir)

            result = run_batch([url], base_config=config, dependencies=dependencies)

            self.assertEqual(result.skipped_count, 1)
            self.assertEqual(result.items[0].status, "skipped")

    def test_run_batch_records_failure_and_continues(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            urls = ["https://cdn.example.com/bad.m3u8", "https://cdn.example.com/good.m3u8"]

            def analyze(url, **_kwargs):
                if "bad" in url:
                    raise PlaylistError(
                        "m3u8 playlist 다운로드 실패",
                        detail="403 Forbidden for https://cdn.example.com/bad.m3u8?auth_key=secret",
                    )
                return analysis_without_subtitles(url)

            dependencies = JobDependencies(
                check_ffmpeg=ok_capabilities,
                analyze_playlist=analyze,
                extract_audio=fake_extract_audio,
                transcribe=fake_transcribe,
            )
            config = JobConfig(url="", output_dir=tmp_dir)

            result = run_batch(urls, base_config=config, dependencies=dependencies)

            self.assertEqual(result.failed_count, 1)
            self.assertEqual(result.completed_count, 1)
            self.assertEqual(result.items[0].status, "failed")
            self.assertIn("403 Forbidden", result.items[0].error)
            self.assertNotIn("secret", result.items[0].error)
            errors = (Path(tmp_dir) / "errors.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(errors), 1)
            self.assertIn("403 Forbidden", errors[0])
            self.assertNotIn("secret", errors[0])

    def test_run_batch_can_stop_on_first_error_and_write_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            dependencies = JobDependencies(
                check_ffmpeg=ok_capabilities,
                analyze_playlist=lambda *_args, **_kwargs: (_ for _ in ()).throw(PlaylistError("bad url")),
                extract_audio=fake_extract_audio,
                transcribe=fake_transcribe,
            )
            config = JobConfig(url="", output_dir=tmp_dir)

            result = run_batch(
                ["https://cdn.example.com/bad.m3u8", "https://cdn.example.com/never.m3u8"],
                base_config=config,
                continue_on_error=False,
                dependencies=dependencies,
            )

            self.assertEqual(result.failed_count, 1)
            self.assertEqual(result.pending_count, 1)
            self.assertTrue((Path(tmp_dir) / "batch_summary.json").exists())


if __name__ == "__main__":
    unittest.main()
