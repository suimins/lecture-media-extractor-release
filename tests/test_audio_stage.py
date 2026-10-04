import json
import tempfile
import threading
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from lecture_script.browser import Source
from lecture_script.pipeline import BatchWorker, Cancelled, Options, save_audio
from lecture_script.storage import JobStore


class AudioStageTests(unittest.TestCase):
    def test_installed_transcription_decoder_accepts_real_wav(self):
        from faster_whisper.audio import decode_audio

        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "speech.wav"
            with wave.open(str(source), "wb") as audio:
                audio.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
                audio.writeframes(b"\0\0" * 16000)
            samples = decode_audio(str(source), sampling_rate=16000)
            self.assertEqual(samples.shape, (16000,))
            self.assertTrue((samples == 0).all())

    def test_audio_batch_saves_reopenable_file_without_loading_stt(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = root / 'source.wav'
            with wave.open(str(original), 'wb') as audio:
                audio.setparams((2, 2, 44100, 0, 'NONE', 'not compressed'))
                audio.writeframes(b'\0\0' * 2 * 44100)
            before = original.read_bytes()
            store = JobStore(root / 'jobs.sqlite3')
            worker = BatchWorker([Source(str(original), 'local', 'lecture')], Options(root / 'results', action='audio'), store)
            results, errors = [], []
            worker.result.connect(results.append)
            worker.problem.connect(errors.append)
            with patch('lecture_script.pipeline.Transcriber', side_effect=AssertionError('Audio stage must not load STT')):
                worker.run()
            self.assertFalse(errors)
            self.assertEqual(len(results), 1)
            self.assertEqual(original.read_bytes(), before)
            result = results[0]
            with wave.open(result['audio_path']) as audio:
                self.assertEqual((audio.getframerate(), audio.getnchannels()), (16000, 1))
                self.assertGreater(audio.getnframes(), 15000)
            restored = JobStore(store.path).recent()[0]
            self.assertEqual(restored['status'], 'audio_ready')
            record = json.loads((Path(restored['output_dir']) / 'audio.json').read_text(encoding='utf-8'))
            self.assertTrue(Path(record['audio_path']).exists())
            self.assertFalse((Path(restored['output_dir']) / 'transcript.json').exists())

    def test_cancelled_audio_job_does_not_publish_a_result(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source.wav'
            source.write_bytes(b'fixture')
            event = threading.Event()
            event.set()
            store = JobStore(root / 'jobs.sqlite3')
            with self.assertRaises(Cancelled):
                save_audio(Source(str(source), 'local'), Options(root, action='audio'), event, lambda *_: None, store)
            self.assertEqual(store.recent()[0]['status'], 'cancelled')
            self.assertFalse(list(root.rglob('audio.json')))

    def test_caption_cannot_be_exported_as_audio(self):
        with self.assertRaises(ValueError):
            save_audio(Source('caption.srt', 'local'), Options(Path('.'), action='audio'), threading.Event(), lambda *_: None)
