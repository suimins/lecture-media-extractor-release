import subprocess
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import MagicMock, patch

from lecture_script import runtime
from lecture_script.pipeline import Options, Transcriber


@unittest.skipUnless(sys.platform == "win32", "Windows GPU installation")
class GpuInstallationTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.install_vulkan = self.stack.enter_context(patch("lecture_script.whisper_cpp.install_runtime"))
        self.vulkan_devices = self.stack.enter_context(patch("lecture_script.whisper_cpp.available_devices", return_value=[]))

    def test_detected_gpu_installs_runtime_in_current_python(self):
        with patch("ctranslate2.get_cuda_device_count", return_value=1), \
                patch.object(runtime.subprocess, "run") as run:
            self.assertTrue(runtime.install_gpu_runtime())
        command = run.call_args.args[0]
        self.assertEqual(command[:4], [sys.executable, "-m", "pip", "install"])
        self.assertEqual(Path(command[5]), Path(runtime.__file__).resolve().parents[1] / "requirements-gpu.txt")
        self.assertTrue(run.call_args.kwargs["check"])

    def test_cpu_machine_does_not_download_gpu_packages(self):
        with patch("ctranslate2.get_cuda_device_count", return_value=0), \
                patch.object(runtime.subprocess, "run") as run:
            self.assertFalse(runtime.install_gpu_runtime())
        run.assert_not_called()

    def test_vulkan_install_failure_still_attempts_cuda(self):
        self.install_vulkan.side_effect = OSError("Vulkan download unavailable")
        with patch("ctranslate2.get_cuda_device_count", return_value=1), \
                patch.object(runtime.subprocess, "run") as run:
            self.assertTrue(runtime.install_gpu_runtime())
        run.assert_called_once()

    def test_non_cuda_gpu_uses_vulkan_without_cuda_packages(self):
        for name in ("AMD Radeon", "Intel Arc", "Future GPU"):
            with self.subTest(name=name), patch("ctranslate2.get_cuda_device_count", return_value=0), \
                    patch.object(runtime.subprocess, "run") as run:
                self.vulkan_devices.return_value = [{"name": name}]
                self.assertTrue(runtime.install_gpu_runtime())
                run.assert_not_called()

    def test_detection_failure_keeps_cpu_available(self):
        with patch("ctranslate2.get_cuda_device_count", side_effect=RuntimeError("driver unavailable")), \
                patch.object(runtime.subprocess, "run") as run:
            self.assertFalse(runtime.install_gpu_runtime())
        run.assert_not_called()

    def test_package_install_failure_is_not_reported_as_success(self):
        with patch("ctranslate2.get_cuda_device_count", return_value=1), \
                patch.object(runtime.subprocess, "run", side_effect=subprocess.CalledProcessError(1, "pip")):
            with self.assertRaises(subprocess.CalledProcessError):
                runtime.install_gpu_runtime()


class GpuSelectionTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.directory = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        for name in ("model.bin", "config.json", "tokenizer.json"):
            (self.directory / name).touch()
        self.stack.enter_context(patch("lecture_script.pipeline.configure_gpu_runtime"))
        self.stack.enter_context(patch("faster_whisper.utils.download_model", return_value=str(self.directory)))
        self.count = self.stack.enter_context(patch("ctranslate2.get_cuda_device_count", return_value=1))
        self.devices = self.stack.enter_context(patch("lecture_script.pipeline.available_devices", return_value=[]))
        self.vulkan = self.stack.enter_context(patch("lecture_script.pipeline.WhisperCppModel"))
        self.model = MagicMock()
        self.model.transcribe.return_value = (iter(()), None)
        self.factory = self.stack.enter_context(patch("faster_whisper.WhisperModel", return_value=self.model))
        # Load native dependencies on the real OS before simulating Windows selection.
        self.stack.enter_context(patch("lecture_script.pipeline.sys.platform", "win32"))
        self.engine = Transcriber()
        self.progress = MagicMock()

    def test_auto_prefers_gpu_and_checks_real_inference_path(self):
        self.engine._load(Options(self.directory), self.progress)
        self.assertEqual(self.engine.actual_device, "cuda")
        self.assertEqual(self.factory.call_args.kwargs["device"], "cuda")
        self.assertEqual(self.factory.call_args.kwargs["compute_type"], "float16")
        self.model.transcribe.assert_called_once()
        self.assertIn("GPU (CUDA)", self.progress.call_args.args[1])

    def test_cpu_only_computer_does_not_try_gpu(self):
        self.count.return_value = 0
        self.engine._load(Options(self.directory), self.progress)
        self.assertEqual(self.engine.actual_device, "cpu")
        self.assertEqual(self.factory.call_args.kwargs["compute_type"], "int8")
        self.model.transcribe.assert_not_called()

    def test_lazy_gpu_failure_releases_model_and_reports_cpu_fallback(self):
        def fail_on_iteration():
            raise RuntimeError("cublas64_12.dll was not found")
            yield

        gpu = self.model
        gpu.transcribe.return_value = (fail_on_iteration(), None)
        cpu = MagicMock()

        def create(_path, **kwargs):
            if kwargs["device"] == "cpu":
                self.assertIsNone(self.engine.model)
                return cpu
            return gpu

        self.factory.side_effect = create
        self.engine._load(Options(self.directory), self.progress)
        self.assertIs(self.engine.model, cpu)
        self.assertEqual(self.engine.actual_device, "cpu")
        labels = [call.args[1] for call in self.progress.call_args_list]
        self.assertTrue(any("다음 장치로 전환" in label and "setup.cmd" in label for label in labels))

    def test_auto_uses_non_cuda_gpus_without_vendor_whitelist(self):
        self.count.return_value = 0
        for name in ("AMD Radeon", "Intel Arc", "Other Vulkan GPU"):
            with self.subTest(name=name):
                self.devices.return_value = [{"name": name, "index": 2}]
                self.vulkan.return_value.transcribe.return_value = (iter(()), None)
                engine = Transcriber()
                engine._load(Options(self.directory), self.progress)
                self.assertEqual(engine.actual_device, "vulkan")
                self.assertEqual(self.vulkan.call_args.args[1]["name"], name)
        self.factory.assert_not_called()

    def test_failed_cuda_tries_vulkan_before_cpu(self):
        self.factory.side_effect = RuntimeError("CUDA unavailable")
        self.devices.return_value = [{"name": "AMD Radeon", "index": 1}]
        self.vulkan.return_value.transcribe.return_value = (iter(()), None)
        self.engine._load(Options(self.directory), self.progress)
        self.assertEqual(self.engine.actual_device, "vulkan")
        self.factory.assert_called_once()

    def test_failed_vulkan_tries_other_gpu_then_cpu(self):
        self.count.return_value = 0
        self.devices.return_value = [{"name": "AMD Radeon", "index": 0}, {"name": "Intel Arc", "index": 1}]
        self.vulkan.side_effect = RuntimeError("GPU memory unavailable")
        self.engine._load(Options(self.directory), self.progress)
        self.assertEqual(self.vulkan.call_count, 2)
        self.assertEqual(self.engine.actual_device, "cpu")

    def test_explicit_cpu_skips_all_gpu_detection(self):
        self.engine._load(Options(self.directory, device="cpu"), self.progress)
        self.count.assert_not_called()
        self.devices.assert_not_called()
        self.assertEqual(self.engine.actual_device, "cpu")

    def test_mac_auto_selects_metal_and_never_probes_cuda(self):
        self.devices.return_value = [{"name": "Metal GPU 0", "index": 0, "backend": "metal"}]
        self.vulkan.return_value.transcribe.return_value = (iter(()), None)
        with patch("lecture_script.pipeline.sys.platform", "darwin"):
            self.engine._load(Options(self.directory), self.progress)
        self.count.assert_not_called()
        self.assertEqual(self.engine.actual_device, "metal")
        self.assertIn("GPU (METAL)", self.progress.call_args.args[1])

    def test_mac_without_metal_falls_back_to_cpu(self):
        with patch("lecture_script.pipeline.sys.platform", "darwin"):
            self.engine._load(Options(self.directory), self.progress)
        self.assertEqual(self.engine.actual_device, "cpu")

    def test_failed_metal_inference_falls_back_to_cpu(self):
        self.devices.return_value = [{"name": "Metal GPU 0", "index": 0, "backend": "metal"}]
        self.vulkan.side_effect = RuntimeError("Metal unavailable")
        with patch("lecture_script.pipeline.sys.platform", "darwin"):
            self.engine._load(Options(self.directory), self.progress)
        self.assertEqual(self.engine.actual_device, "cpu")

    def test_explicit_gpu_failure_is_not_silently_sent_to_cpu(self):
        self.factory.side_effect = RuntimeError("CUDA unavailable")
        with self.assertRaises(RuntimeError):
            self.engine._load(Options(self.directory, device="cuda"), self.progress)
        self.factory.assert_called_once()

    def test_successful_model_is_reused(self):
        options = Options(self.directory)
        self.engine._load(options, self.progress)
        self.engine._load(options, self.progress)
        self.factory.assert_called_once()
