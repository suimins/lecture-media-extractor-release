import unittest
from types import SimpleNamespace

from app.core.cancel import CancellationToken
from app.core.errors import CancelledError
from app.core.transcribe import WhisperConfig, normalize_language, transcribe_audio


class FakeModel:
    def __init__(self, segments, info=None):
        self.segments = segments
        self.info = info or SimpleNamespace(language="ko", language_probability=0.95, duration=30.0)
        self.calls = []

    def transcribe(self, audio_path, **kwargs):
        self.calls.append((audio_path, kwargs))
        return iter(self.segments), self.info


class StepClock:
    def __init__(self, values):
        self.values = list(values)
        self.index = 0

    def __call__(self):
        if self.index >= len(self.values):
            return self.values[-1]
        value = self.values[self.index]
        self.index += 1
        return value


class TranscribeTests(unittest.TestCase):
    def test_normalize_language(self) -> None:
        self.assertEqual(normalize_language("ko"), "ko")
        self.assertEqual(normalize_language(" KO "), "ko")
        self.assertIsNone(normalize_language(None))
        self.assertIsNone(normalize_language("auto"))
        self.assertIsNone(normalize_language("detect"))

    def test_transcribe_audio_passes_korean_language_and_returns_segments(self) -> None:
        model = FakeModel(
            [
                SimpleNamespace(start=0.0, end=5.0, text=" 안녕하세요 "),
                SimpleNamespace(start=5.0, end=10.0, text="두 번째 문장"),
            ],
            SimpleNamespace(language="ko", language_probability=0.98, duration=10.0),
        )

        result = transcribe_audio("audio.wav", model=model, config=WhisperConfig(language="ko"))

        self.assertEqual(model.calls[0][0], "audio.wav")
        self.assertEqual(model.calls[0][1]["language"], "ko")
        self.assertTrue(model.calls[0][1]["vad_filter"])
        self.assertEqual(model.calls[0][1]["beam_size"], 5)
        self.assertEqual(result.detected_language, "ko")
        self.assertEqual(result.language_probability, 0.98)
        self.assertEqual(result.duration_seconds, 10.0)
        self.assertEqual(len(result.segments), 2)
        self.assertEqual(result.segments[0].text, "안녕하세요")
        self.assertEqual(result.text, "안녕하세요\n두 번째 문장")

    def test_auto_language_passes_none(self) -> None:
        model = FakeModel([{"start": 0, "end": 1, "text": "hello"}])

        transcribe_audio("audio.wav", model=model, config=WhisperConfig(language="auto"))

        self.assertIsNone(model.calls[0][1]["language"])

    def test_duration_argument_overrides_info_duration(self) -> None:
        model = FakeModel([{"start": 0, "end": 1, "text": "hello"}], {"language": "en", "duration": 99})

        result = transcribe_audio("audio.wav", model=model, duration_seconds=12)

        self.assertEqual(result.duration_seconds, 12)

    def test_progress_uses_rolling_eta_after_first_segment(self) -> None:
        model = FakeModel(
            [
                {"start": 0, "end": 10, "text": "first"},
                {"start": 10, "end": 20, "text": "second"},
                {"start": 20, "end": 30, "text": "third"},
            ],
            {"language": "en", "language_probability": 0.9, "duration": 40.0},
        )
        events = []
        clock = StepClock([100.0, 101.0, 102.0])

        result = transcribe_audio("audio.wav", model=model, progress_callback=events.append, clock=clock)

        self.assertEqual(len(events), 3)
        self.assertIsNone(events[0].eta_seconds)
        self.assertEqual(events[1].progress_ratio, 0.5)
        self.assertAlmostEqual(events[1].eta_seconds, 2.0)
        self.assertAlmostEqual(events[2].eta_seconds, 1.0)
        self.assertEqual(result.progress_events, events)

    def test_empty_text_segment_reports_progress_but_not_transcript_text(self) -> None:
        model = FakeModel(
            [
                {"start": 0, "end": 1, "text": "   "},
                {"start": 1, "end": 2, "text": "real"},
            ]
        )

        result = transcribe_audio("audio.wav", model=model)

        self.assertEqual(len(result.progress_events), 2)
        self.assertEqual(len(result.segments), 1)
        self.assertEqual(result.text, "real")

    def test_cancellation_before_start_raises(self) -> None:
        token = CancellationToken()
        token.cancel()

        with self.assertRaises(CancelledError):
            transcribe_audio("audio.wav", model=FakeModel([]), cancellation_token=token)

    def test_cancellation_between_segments_raises(self) -> None:
        token = CancellationToken()
        model = FakeModel(
            [
                {"start": 0, "end": 1, "text": "first"},
                {"start": 1, "end": 2, "text": "second"},
            ]
        )

        def cancel_after_first(_progress):
            token.cancel()

        with self.assertRaises(CancelledError):
            transcribe_audio("audio.wav", model=model, cancellation_token=token, progress_callback=cancel_after_first)


if __name__ == "__main__":
    unittest.main()

