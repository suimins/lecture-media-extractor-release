import tempfile
import unittest
import wave
from pathlib import Path

from app.core.audio import (
    analyze_hls_encryption,
    build_audio_extract_command,
    extract_audio_with_ffmpeg,
    make_audio_progress,
    parse_progress_line,
)
from app.core.cancel import CancellationToken
from app.core.errors import CancelledError
from app.models.settings import FfmpegConfig


class HlsEncryptionTests(unittest.TestCase):
    def test_analyze_hls_encryption_detects_none(self) -> None:
        info = analyze_hls_encryption("#EXTM3U\n#EXTINF:1,\nseg.ts\n")

        self.assertEqual(info.status, "none")
        self.assertFalse(info.keys)

    def test_analyze_hls_encryption_detects_aes_128_identity(self) -> None:
        playlist = '#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI="key.bin"\n#EXTINF:1,\nseg.ts\n'

        info = analyze_hls_encryption(playlist)

        self.assertEqual(info.status, "aes_128")
        self.assertTrue(info.has_aes_128)
        self.assertFalse(info.drm_likely)

    def test_analyze_hls_encryption_flags_sample_aes_as_drm_likely(self) -> None:
        playlist = '#EXTM3U\n#EXT-X-KEY:METHOD=SAMPLE-AES,KEYFORMAT="com.apple.streamingkeydelivery",URI="skd://x"\n'

        info = analyze_hls_encryption(playlist)

        self.assertEqual(info.status, "drm_likely")
        self.assertTrue(info.drm_likely)
        self.assertTrue(info.warnings)

    def test_analyze_hls_encryption_flags_non_identity_keyformat(self) -> None:
        playlist = '#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,KEYFORMAT="com.widevine",URI="license"\n'

        info = analyze_hls_encryption(playlist)

        self.assertEqual(info.status, "drm_likely")


class AudioCommandTests(unittest.TestCase):
    def test_build_audio_extract_command_outputs_stt_ready_wav(self) -> None:
        command = build_audio_extract_command(
            "https://cdn.example.com/media.m3u8?token=secret",
            "audio.wav",
            config=FfmpegConfig(ffmpeg_path="ffmpeg"),
            headers={"Cookie": "session=abc", "Referer": "https://example.com"},
        )

        self.assertIn("-ac", command.args)
        self.assertIn("1", command.args)
        self.assertIn("-ar", command.args)
        self.assertIn("16000", command.args)
        self.assertIn("-c:a", command.args)
        self.assertIn("pcm_s16le", command.args)
        self.assertIn("-progress", command.args)
        self.assertIn("pipe:1", command.args)
        self.assertIn("-headers", command.args)
        self.assertNotIn("token=secret", " ".join(command.redacted_args))
        self.assertNotIn("session=abc", " ".join(command.redacted_args))


class AudioProgressTests(unittest.TestCase):
    def test_parse_progress_line(self) -> None:
        self.assertEqual(parse_progress_line("out_time_us=2500000\n"), ("out_time_us", "2500000"))
        self.assertIsNone(parse_progress_line("not-progress"))

    def test_make_audio_progress_uses_out_time_us(self) -> None:
        progress = make_audio_progress({"out_time_us": "2500000", "speed": "1.2x", "progress": "continue"}, duration_seconds=10)

        self.assertEqual(progress.out_time_seconds, 2.5)
        self.assertEqual(progress.progress_ratio, 0.25)
        self.assertEqual(progress.speed, "1.2x")
        self.assertEqual(progress.state, "continue")

    def test_make_audio_progress_parses_out_time_string(self) -> None:
        progress = make_audio_progress({"out_time": "00:01:02.500"}, duration_seconds=125)

        self.assertEqual(progress.out_time_seconds, 62.5)
        self.assertEqual(progress.progress_ratio, 0.5)


class AudioExtractionIntegrationTests(unittest.TestCase):
    def test_extract_audio_with_ffmpeg_creates_16khz_mono_wav_from_lavfi(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            output = Path(tmp_dir) / "audio.wav"
            command = build_audio_extract_command(
                "sine=frequency=1000:duration=0.2",
                output,
                config=FfmpegConfig(extra_input_args=["-f", "lavfi"], http_reconnect=False),
            )

            events = []
            result = extract_audio_with_ffmpeg(command, duration_seconds=0.2, progress_callback=events.append)

            self.assertEqual(result.output_path, str(output))
            self.assertTrue(output.exists())
            self.assertGreater(output.stat().st_size, 44)
            self.assertTrue(events)
            with wave.open(str(output), "rb") as wav:
                self.assertEqual(wav.getnchannels(), 1)
                self.assertEqual(wav.getframerate(), 16000)
                self.assertEqual(wav.getsampwidth(), 2)

    def test_extract_audio_with_ffmpeg_respects_pre_cancelled_token(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            output = Path(tmp_dir) / "audio.wav"
            command = build_audio_extract_command(
                "sine=frequency=1000:duration=2",
                output,
                config=FfmpegConfig(extra_input_args=["-f", "lavfi"], http_reconnect=False),
            )
            token = CancellationToken()
            token.cancel()

            with self.assertRaises(CancelledError):
                extract_audio_with_ffmpeg(command, duration_seconds=2, cancellation_token=token)


if __name__ == "__main__":
    unittest.main()
