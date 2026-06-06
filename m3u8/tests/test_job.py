import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from app.core.audio import AudioExtractionResult
from app.core.ffmpeg import FfmpegCapabilities
from app.core.job import JobDependencies, run_job
from app.core.playlist import PlaylistAnalysis, SubtitleTrack
from app.core.transcribe import TranscriptionResult
from app.models.events import JobEvent
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


def subtitle_track() -> SubtitleTrack:
    return SubtitleTrack(
        track_id="subs:ko:Korean",
        group_id="subs",
        name="Korean",
        language="ko",
        uri="https://cdn.example.com/subs.m3u8",
        default=True,
        autoselect=True,
        forced=False,
        raw_attributes={},
    )


def analysis_with_subtitles(duration_seconds=60.0) -> PlaylistAnalysis:
    return PlaylistAnalysis(
        source_url="https://cdn.example.com/master.m3u8",
        kind="master",
        playlist_type="vod",
        is_m3u8=True,
        has_endlist=True,
        target_duration=10,
        duration_seconds=duration_seconds,
        subtitle_tracks=[subtitle_track()],
    )


def analysis_without_subtitles(duration_seconds=60.0) -> PlaylistAnalysis:
    return PlaylistAnalysis(
        source_url="https://cdn.example.com/media.m3u8",
        kind="media",
        playlist_type="vod",
        is_m3u8=True,
        has_endlist=True,
        target_duration=10,
        duration_seconds=duration_seconds,
        subtitle_tracks=[],
    )


class JobPipelineTests(unittest.TestCase):
    def test_run_job_uses_subtitle_route_when_quality_is_ok(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            events = []

            def fake_extract_subtitle(command):
                Path(command.args[-1]).write_text(
                    "\n\n".join(
                        f"{index}\n00:00:{index:02d},000 --> 00:00:{index + 1:02d},000\n"
                        f"충분히 긴 자막 문장입니다 {index}. 강의 내용을 텍스트로 확인할 수 있도록 문장을 넉넉하게 넣습니다."
                        for index in range(1, 15)
                    ),
                    encoding="utf-8",
                )

            def fail_audio(*_args, **_kwargs):
                raise AssertionError("audio route should not run")

            dependencies = JobDependencies(
                check_ffmpeg=ok_capabilities,
                analyze_playlist=lambda *_args, **_kwargs: analysis_with_subtitles(),
                extract_subtitle=fake_extract_subtitle,
                extract_audio=fail_audio,
            )
            config = JobConfig(url="https://cdn.example.com/master.m3u8?token=secret", output_dir=tmp_dir)

            result = run_job(config, dependencies=dependencies, progress_callback=events.append)

            self.assertEqual(result.method, "subtitle")
            self.assertEqual(result.selected_subtitle_track_id, "subs:ko:Korean")
            self.assertEqual(result.subtitle_quality_status, "ok")
            self.assertEqual(len(result.output_files), 4)
            self.assertTrue((Path(tmp_dir) / "transcript.txt").exists())
            self.assertIn(JobEvent.EXTRACTING_SUBTITLE_WITH_FFMPEG, [event.event for event in events])
            self.assertIn(JobEvent.JOB_DONE, [event.event for event in events])

    def test_run_job_falls_back_to_stt_when_subtitle_quality_is_bad(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            calls = SimpleNamespace(audio=False, transcribe=False)

            def fake_extract_subtitle(command):
                Path(command.args[-1]).write_text("", encoding="utf-8")

            def fake_extract_audio(command, **_kwargs):
                calls.audio = True
                Path(command.output_path).write_bytes(b"wav")
                return AudioExtractionResult(output_path=command.output_path, stderr="")

            def fake_transcribe(audio_path, **_kwargs):
                calls.transcribe = True
                self.assertTrue(Path(audio_path).exists())
                return TranscriptionResult(
                    text="stt text",
                    segments=[TranscriptSegment(start=0, end=1, text="stt text")],
                    detected_language="ko",
                    language_probability=0.9,
                    duration_seconds=60,
                )

            dependencies = JobDependencies(
                check_ffmpeg=ok_capabilities,
                analyze_playlist=lambda *_args, **_kwargs: analysis_with_subtitles(),
                extract_subtitle=fake_extract_subtitle,
                extract_audio=fake_extract_audio,
                transcribe=fake_transcribe,
            )
            config = JobConfig(url="https://cdn.example.com/master.m3u8", output_dir=tmp_dir)

            result = run_job(config, dependencies=dependencies)

            self.assertTrue(calls.audio)
            self.assertTrue(calls.transcribe)
            self.assertEqual(result.method, "stt")
            self.assertEqual(result.transcript_text, "stt text")
            self.assertFalse((Path(tmp_dir) / "audio.wav").exists())

    def test_run_job_uses_stt_when_no_subtitles(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            def fake_extract_audio(command, **_kwargs):
                Path(command.output_path).write_bytes(b"wav")
                return AudioExtractionResult(output_path=command.output_path, stderr="")

            def fake_transcribe(_audio_path, **_kwargs):
                return TranscriptionResult(
                    text="only stt",
                    segments=[TranscriptSegment(start=0, end=1, text="only stt")],
                    detected_language="ko",
                    language_probability=0.9,
                    duration_seconds=60,
                )

            dependencies = JobDependencies(
                check_ffmpeg=ok_capabilities,
                analyze_playlist=lambda *_args, **_kwargs: analysis_without_subtitles(),
                extract_audio=fake_extract_audio,
                transcribe=fake_transcribe,
            )
            config = JobConfig(url="https://cdn.example.com/media.m3u8", output_dir=tmp_dir, keep_audio=True)

            result = run_job(config, dependencies=dependencies)

            self.assertEqual(result.method, "stt")
            self.assertEqual(result.audio_path, str(Path(tmp_dir) / "audio.wav"))
            self.assertTrue(Path(result.audio_path).exists())


if __name__ == "__main__":
    unittest.main()
