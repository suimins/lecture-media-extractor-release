import io
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from lecture_script import whisper_cpp as vulkan
from lecture_script.pipeline import Cancelled


class VulkanTests(unittest.TestCase):
    def test_devices_prefer_dedicated_gpus_and_keep_real_indices(self):
        devices = vulkan.parse_devices('''ggml_vulkan: Found 4 Vulkan devices:
ggml_vulkan: 0 = Intel Graphics (Intel) | uma: 1 | fp16: 1
ggml_vulkan: 1 = AMD Radeon (AMD) | uma: 0 | fp16: 1
ggml_vulkan: 2 = New Vendor GPU | uma: 0 | fp16: 1
ggml_vulkan: 3 = llvmpipe software | uma: 1 | fp16: 0''')
        self.assertEqual([d["index"] for d in devices], [1, 2, 0])
        self.assertFalse(devices[0]["integrated"])
        self.assertTrue(devices[-1]["integrated"])

    def test_bad_archive_hash_is_rejected_before_extraction(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(vulkan.sys, "platform", "win32"), \
                patch.object(vulkan.platform, "machine", return_value="AMD64"), \
                patch.object(vulkan, "RUNTIME_DIRECTORY", Path(directory)), \
                patch.object(vulkan, "urlopen", return_value=io.BytesIO(b"invalid archive")):
            with self.assertRaisesRegex(RuntimeError, "checksum"):
                vulkan.install_runtime()
            self.assertFalse(vulkan.executable().exists())

    def model(self):
        with patch("huggingface_hub.hf_hub_download", return_value="fixture-model.bin"):
            return vulkan.WhisperCppModel("tiny", {"index": 1, "name": "AMD Radeon", "backend": "vulkan"})

    def test_real_gpu_confirmation_and_millisecond_timestamp_conversion(self):
        calls = []
        def launch(command, **_kwargs):
            calls.append(command)
            path = Path(command[command.index("-of") + 1]).with_suffix(".json")
            path.write_text(json.dumps({"transcription": [
                {"offsets": {"from": 1250, "to": 2750}, "text": " 자료 구조"}]}), encoding="utf-8")
            process = MagicMock(returncode=0)
            process.poll.return_value = 0
            process.communicate.return_value = (None, b"whisper_backend_init_gpu: using Vulkan1 backend")
            return process
        with patch.object(vulkan.subprocess, "Popen", side_effect=launch):
            iterator, _ = self.model().transcribe("sample.wav", language="ko")
            segments = list(iterator)
        self.assertEqual((segments[0].start, segments[0].end, segments[0].text), (1.25, 2.75, " 자료 구조"))
        self.assertEqual(segments[0].words[0].word, " 자료 구조")
        self.assertEqual(calls[0][calls[0].index("-dev") + 1], "1")
        self.assertEqual(calls[0][calls[0].index("-l") + 1], "ko")

    def test_silent_cpu_fallback_is_not_reported_as_gpu(self):
        process = MagicMock(returncode=0)
        process.poll.return_value = 0
        process.communicate.return_value = (None, b"using CPU backend")
        with patch.object(vulkan.subprocess, "Popen", return_value=process):
            iterator, _ = self.model().transcribe("sample.wav")
            with self.assertRaisesRegex(RuntimeError, "실제로 활성화"):
                list(iterator)

    def test_failed_backend_initialization_is_not_reported_as_gpu(self):
        process = MagicMock(returncode=0)
        process.poll.return_value = 0
        process.communicate.return_value = (None, b"using Vulkan1 backend\nfailed to initialize Vulkan1 backend")
        with patch.object(vulkan.subprocess, "Popen", return_value=process):
            iterator, _ = self.model().transcribe("sample.wav")
            with self.assertRaisesRegex(RuntimeError, "실제로 활성화"):
                list(iterator)

    def test_metal_device_logs_and_native_architecture(self):
        devices = vulkan.parse_devices("ggml_metal_device_init: GPU name:   MTL0\n")
        self.assertEqual(devices[0]["backend"], "metal")
        self.assertEqual(devices[0]["index"], 0)
        for architecture in ("arm64", "x86_64"):
            with self.subTest(architecture=architecture), patch.object(vulkan.sys, "platform", "darwin"), \
                    patch.object(vulkan.platform, "machine", return_value=architecture):
                self.assertIn(architecture, str(vulkan.executable()))
                self.assertEqual(vulkan.executable().name, "whisper-cli")

    def test_unknown_architecture_does_not_execute_an_incompatible_binary(self):
        with patch.object(vulkan.sys, "platform", "linux"), \
                patch.object(vulkan.platform, "machine", return_value="riscv64"), \
                patch.object(vulkan.subprocess, "run") as run:
            self.assertIsNone(vulkan.executable())
            self.assertEqual(vulkan.available_devices(), [])
            run.assert_not_called()

    def test_slow_probe_preserves_devices_already_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            cli = Path(directory) / "whisper-cli"
            cli.touch()
            for log, expected in ((b"ggml_metal_device_init: GPU name: MTL0\n", ["metal"]), (None, [])):
                with self.subTest(log=log), patch.object(vulkan, "executable", return_value=cli), \
                        patch.object(vulkan.subprocess, "run", side_effect=vulkan.subprocess.TimeoutExpired(
                            "whisper-cli", 20, stderr=log)):
                    self.assertEqual([device["backend"] for device in vulkan.available_devices()], expected)

    def test_cancelled_job_does_not_start_process(self):
        cancel = threading.Event()
        cancel.set()
        with patch.object(vulkan.subprocess, "Popen") as launch:
            iterator, _ = self.model().transcribe("sample.wav", cancel=cancel)
            with self.assertRaises(Cancelled):
                list(iterator)
            launch.assert_not_called()

    def test_cancellation_stops_a_running_gpu_process(self):
        cancel = threading.Event()
        process = MagicMock()
        process.poll.return_value = None
        def communicate(**kwargs):
            if kwargs:
                cancel.set()
                raise vulkan.subprocess.TimeoutExpired("whisper-cli", 0.2)
            return None, b""
        process.communicate.side_effect = communicate
        with patch.object(vulkan.subprocess, "Popen", return_value=process):
            iterator, _ = self.model().transcribe("sample.wav", cancel=cancel)
            with self.assertRaises(Cancelled):
                list(iterator)
        process.kill.assert_called_once()
