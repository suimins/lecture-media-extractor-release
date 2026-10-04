import itertools
import json
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import av

from lecture_script.browser import Source
from lecture_script.native_host import Host
from lecture_script.pipeline import Options, completed_outputs, extract_audio, ffmpeg_path, process_source
from lecture_script.storage import JobStore
from lecture_script.text import Segment


class Engine:
    actual_device = "cpu"
    compute_type = "int8"

    def transcribe(self, *_args):
        return [Segment(0, 0.3, "선택한 파일 저장 시험")]


class OutputSelectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.fixture.cleanup)
        cls.video = Path(cls.fixture.name) / "source.mp4"
        subprocess.run([ffmpeg_path(), "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                        "-f", "lavfi", "-i", "color=c=blue:s=64x64:d=0.4",
                        "-f", "lavfi", "-i", "sine=frequency=440:duration=0.4", "-shortest",
                        "-c:v", "libx264", "-c:a", "aac", str(cls.video)], check=True, capture_output=True)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_all_seven_selections_save_real_formats_and_skip_unselected_work(self):
        for count in range(1, 4):
            for selected in itertools.combinations(("mp4", "mp3", "script"), count):
                with self.subTest(selected=selected):
                    output = self.root / "-".join(selected)
                    engine = Engine()
                    with patch.object(engine, "transcribe", wraps=engine.transcribe) as transcribe, \
                            patch("lecture_script.pipeline.extract_audio", wraps=extract_audio) as wav:
                        result = process_source(Source(str(self.video), "local", "Lecture", course="Course"),
                                                Options(output, outputs=selected, prefer_subtitles=False), engine,
                                                threading.Event(), lambda *_: None)
                    self.assertEqual(set(result["outputs"]), set(selected))
                    self.assertEqual(transcribe.call_count, int("script" in selected))
                    self.assertEqual(wav.call_count, int("script" in selected))
                    course = output / "Course"
                    self.assertEqual((course / "video").exists(), "mp4" in selected)
                    self.assertEqual((course / "audio").exists(), "mp3" in selected)
                    self.assertEqual((course / "script").exists(), "script" in selected)
                    self.assertFalse((course / ".work").exists())
                    self.assertFalse(list(course.rglob("*.wav")))
                    if "mp4" in selected:
                        with av.open(result["outputs"]["mp4"]) as container:
                            self.assertIn("mp4", container.format.name)
                            self.assertTrue(container.streams.video)
                            self.assertTrue(container.streams.audio)
                            self.assertIsNotNone(next(container.decode(video=0)))
                    if "mp3" in selected:
                        with av.open(result["outputs"]["mp3"]) as container:
                            self.assertEqual(container.format.name, "mp3")
                            self.assertIsNotNone(next(container.decode(audio=0)))
                    if "script" in selected:
                        self.assertTrue((Path(result["outputs"]["script"]) / "subtitles.srt").is_file())
                    self.assertTrue(self.video.is_file())

    def test_later_selections_reuse_saved_video_and_keep_previous_exports(self):
        source = Source("https://cdn.example.test/lecture.mp4?token=secret-token-fixture", "media", "Lecture", course="Course")
        def download(_source, work, *_args):
            media = work / "source.mp4"
            media.write_bytes(self.video.read_bytes())
            return media
        options = Options(self.root, outputs=("mp4",), prefer_subtitles=False)
        with patch("lecture_script.pipeline.download_media", side_effect=download) as downloader:
            first = process_source(source, options, Engine(), threading.Event(), lambda *_: None)
            mp4 = Path(first["outputs"]["mp4"])
            before = mp4.read_bytes()
            for selection in (("script",), ("mp3",)):
                options.outputs = selection
                self.assertIsNone(completed_outputs(source, options))
                process_source(source, options, Engine(), threading.Event(), lambda *_: None)
                self.assertIsNotNone(completed_outputs(source, options))
            self.assertEqual(downloader.call_count, 1)
        self.assertEqual(mp4.read_bytes(), before)
        options.outputs = ("mp4", "mp3", "script")
        result = completed_outputs(source, options)
        self.assertIsNotNone(result)
        Path(result["outputs"]["mp3"]).unlink()
        self.assertIsNone(completed_outputs(source, options))
        options.outputs = ("script",)
        self.assertIsNotNone(completed_outputs(source, options))
        manifest = next((self.root / "Course" / ".jobs").glob("*.json"))
        self.assertNotIn("secret-token-fixture", manifest.read_text())

    def test_native_host_rejects_invalid_selection_without_reserving_job(self):
        messages = []
        host = Host(messages.append, self.root, JobStore(self.root / "jobs.db"))
        self.addCleanup(host.close)
        source = {"url": "https://cdn.test/lecture.mp4", "page_url": "https://school.test/player"}
        for selection in ([], None, "mp4", ["wav"], [False], [{"mp4": True}]):
            with self.subTest(selection=selection), self.assertRaises(ValueError):
                host.handle({"command": "start", "source": source, "outputs": selection})
            self.assertFalse(host.jobs)
        self.assertFalse(messages)

    def test_native_host_passes_selection_to_pipeline(self):
        finished = threading.Event()
        messages = []
        def send(message):
            messages.append(message)
            if message["type"] in ("complete", "error"):
                finished.set()
        with patch("lecture_script.native_host.process_source", return_value={"directory": str(self.root)}) as process:
            host = Host(send, self.root, JobStore(self.root / "jobs.db"))
            try:
                host.handle({"command": "start", "outputs": ["mp3"], "source": {
                    "url": "https://cdn.test/lecture.mp4", "page_url": "https://school.test/player"}})
                self.assertTrue(finished.wait(3), messages)
                self.assertEqual(messages[-1]["type"], "complete", messages)
                self.assertEqual(process.call_args.args[1].outputs, ("mp3",))
                self.assertIn("MP3", messages[-1]["label"])
                self.assertNotIn("스크립트", messages[-1]["label"])
            finally:
                host.close()
