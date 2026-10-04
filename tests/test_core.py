import http.cookiejar
import json
import tempfile
import threading
import unittest
import urllib.request
import wave
from pathlib import Path
from types import SimpleNamespace

from lecture_script.browser import Source, media_kind
from lecture_script.pipeline import Cancelled, Options, Transcriber, cookie_jar, process_source, safe_error
from lecture_script.storage import JobStore
from lecture_script.text import Segment, export_transcript, parse_subtitles, safe_name, timestamp


class TextTests(unittest.TestCase):
    def test_vtt_cues_markup_and_hour_rollover(self):
        source = 'WEBVTT\n\nNOTE private note\nignored\n\ncue-1\n00:59.500 --> 01:02.200 align:start\n<v 교수>자료 &amp; 구조</v>\n\n'
        segments = parse_subtitles(source)
        self.assertEqual(len(segments), 1)
        self.assertEqual(segments[0].text, '자료 & 구조')
        self.assertEqual(segments[0].start, 59.5)
        self.assertEqual(timestamp(3599.9999), '01:00:00,000')

    def test_export_roundtrip_and_original_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            export_transcript(path, '강의', [Segment(0.5, 2.2, '원문')], {})
            export_transcript(path, '강의', [Segment(0.5, 2.2, '수정', '원문')], {})
            self.assertEqual(parse_subtitles((path / 'subtitles.srt').read_text(encoding='utf-8'))[0].text, '수정')
            self.assertEqual(json.loads((path / 'transcript.original.json').read_text(encoding='utf-8'))['segments'][0]['text'], '원문')

    def test_invalid_times_and_windows_names(self):
        with self.assertRaises(ValueError):
            parse_subtitles('1\n00:02,000 --> 00:01,000\n잘못된 시간')
        self.assertEqual(safe_name('CON'), '_CON')
        self.assertNotIn('/', safe_name('../위험:이름?'))
        self.assertEqual(media_kind('https://host/part.ts', 'video/mp2t'), None)
        self.assertEqual(media_kind('blob:https://host/value'), None)
        self.assertEqual(media_kind('https://host/stream?token=x', 'application/vnd.apple.mpegurl'), 'hls')

    def test_secrets_are_redacted(self):
        self.assertNotIn('secret', safe_error(ValueError('Failed https://host/video?token=secret')))


class CookieTests(unittest.TestCase):
    def test_host_only_and_secure_scope(self):
        jar = cookie_jar([{'name':'session', 'value':'secret', 'domain':'learn.example.edu', 'path':'/', 'secure':True}])
        for url, expected in [('https://learn.example.edu/file', True), ('http://learn.example.edu/file', False),
                              ('https://cdn.example.edu/file', False), ('https://child.learn.example.edu/file', False)]:
            request = urllib.request.Request(url)
            jar.add_cookie_header(request)
            self.assertEqual(request.has_header('Cookie'), expected, url)


class PipelineTests(unittest.TestCase):
    def test_subtitle_job_has_outputs_and_history(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / '수업.srt'
            source.write_text('1\n00:00:01,000 --> 00:00:03,000\n안녕하세요.\n', encoding='utf-8')
            store = JobStore(root / 'jobs.sqlite3')
            result = process_source(Source(str(source), 'local', '수업'), Options(root / 'out'),
                                    Transcriber(), threading.Event(), lambda *_: None, store)
            self.assertEqual(result['payload']['metadata']['origin'], 'existing_subtitles')
            self.assertEqual(store.recent()[0]['status'], 'complete')
            self.assertTrue((Path(result['directory']) / 'subtitles.vtt').exists())

    def test_cancel_does_not_mark_complete(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'test.vtt'
            source.write_text('WEBVTT', encoding='utf-8')
            event = threading.Event()
            event.set()
            store = JobStore(root / 'jobs.sqlite3')
            with self.assertRaises(Cancelled):
                process_source(Source(str(source), 'local', 'test'), Options(root / 'out'),
                               Transcriber(), event, lambda *_: None, store)
            self.assertEqual(store.recent()[0]['status'], 'cancelled')

    def test_chunk_boundary_and_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            audio = root / 'audio.wav'
            with wave.open(str(audio), 'wb') as output:
                output.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
                output.writeframes(b'\0\0' * (305 * 16000))
            class Model:
                count = 0
                def transcribe(self, *args, **kwargs):
                    self.count += 1
                    start, end = (299.7, 300.5) if self.count == 1 else (0.7, 1.5)
                    word = SimpleNamespace(start=start, end=end, word=' 경계 단어')
                    return iter([SimpleNamespace(start=start, end=end, text=' 경계 단어', words=[word])]), None
            engine = Transcriber()
            engine.model = Model()
            engine._load = lambda *_: None
            options = Options(root)
            result = engine.transcribe(audio, root, options, threading.Event(), lambda *_: None)
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0].start, 300)
            self.assertAlmostEqual(result[0].end, 300.5)
            count = engine.model.count
            again = engine.transcribe(audio, root, options, threading.Event(), lambda *_: None)
            self.assertEqual(result, again)
            self.assertEqual(engine.model.count, count)


if __name__ == '__main__':
    unittest.main()
