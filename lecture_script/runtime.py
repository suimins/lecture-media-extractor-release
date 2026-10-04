"""OS integration and installation of the available GPU runtimes."""
import os
import subprocess
import sys
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path

_DLL_HANDLES = []
_CONFIGURED = False


def configure_gpu_runtime():
    global _CONFIGURED
    if _CONFIGURED or os.name != "nt":
        return
    directories = set()
    for package in ("nvidia-cublas-cu12", "nvidia-cudnn-cu12", "nvidia-cuda-runtime-cu12", "nvidia-cuda-nvrtc-cu12"):
        try:
            installed = distribution(package)
        except PackageNotFoundError:
            continue
        for file in installed.files or []:
            if str(file).lower().endswith(".dll"):
                directories.add(str(installed.locate_file(file).resolve().parent))
    for directory in sorted(directories):
        _DLL_HANDLES.append(os.add_dll_directory(directory))
    if directories:
        # Process-local only; LoadLibrary calls in native libraries also consult PATH.
        os.environ["PATH"] = os.pathsep.join(sorted(directories)) + os.pathsep + os.environ.get("PATH", "")
    _CONFIGURED = True


def install_gpu_runtime():
    """Provision native Metal/Vulkan and the CUDA fast path where supported."""
    from .whisper_cpp import install_runtime, available_devices
    backend = "Metal" if sys.platform == "darwin" else "Vulkan"
    print(f"Installing native {backend} GPU runtime...", flush=True)
    try:
        install_runtime()
    except Exception as error:
        if sys.platform != "win32":
            raise
        print(f"Vulkan setup failed; still checking CUDA: {error}", flush=True)
    devices = available_devices()
    for device in devices:
        print(f"{backend} GPU: {device['name']}")
    if sys.platform != "win32":
        print(f"{backend} GPU ready." if devices else "No supported GPU detected. CPU is available.")
        return bool(devices)
    import ctranslate2
    try:
        count = ctranslate2.get_cuda_device_count()
    except RuntimeError as error:
        print(f"CUDA unavailable; Vulkan and CPU remain available: {error}")
        count = 0
    if not count:
        print("Using Vulkan GPU automatically." if devices else "No supported GPU detected. Using CPU.")
        return bool(devices)
    print(f"Detected {count} NVIDIA CUDA GPU(s). Installing GPU runtime...", flush=True)
    requirements = Path(__file__).resolve().parents[1] / "requirements-gpu.txt"
    subprocess.run([sys.executable, "-m", "pip", "install", "-r", str(requirements),
                    "--disable-pip-version-check"], check=True)
    print("GPU runtimes installed. Transcription selects an available GPU automatically.")
    return True


def open_path(path):
    """Open a local folder/document without exposing Chrome's protocol streams."""
    if sys.platform == "win32":
        os.startfile(path)
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(path)],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


if __name__ == "__main__":
    install_gpu_runtime()
