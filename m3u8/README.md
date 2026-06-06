# m3u8 Transcript CLI

This folder contains the Python transcript pipeline.

## Setup

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

After installation, `m3u8-transcript` is equivalent to `python -m app.main`.

## Commands

Check ffmpeg:

```powershell
python -m app.main --check-ffmpeg
# or
m3u8-transcript --check-ffmpeg
```

Run one URL:

```powershell
python -m app.main --run-url "https://example.com/path/index.m3u8" --out outputs\one --whisper-model medium
```

Run a batch:

```powershell
python -m app.main --run-batch ..\examples\urls.example.txt --out ..\outputs\course --whisper-model medium
```

CUDA example:

```powershell
python -m app.main --run-batch ..\examples\urls.example.txt `
  --out ..\outputs\course `
  --whisper-model large-v3 `
  --whisper-device cuda `
  --whisper-compute-type int8_float16 `
  --whisper-beam-size 5 `
  --language ko
```

## Important Options

- `--subtitle-mode auto|manual|skip`: use subtitle tracks when available, or force STT.
- `--subtitle-language ko`: preferred subtitle language.
- `--whisper-model medium`: faster-whisper model name or local model path.
- `--whisper-download-root model-cache/whisper-models`: model cache directory.
- `--whisper-device cpu|cuda`: transcription device.
- `--whisper-compute-type int8|int8_float16|float16`: compute type.
- `--whisper-beam-size 5`: decoding beam size.
- `--keep-audio`: keep extracted WAV files.
- `--ffmpeg` and `--ffprobe`: explicit binary paths when not on `PATH`.

## Tests

```powershell
python -m unittest discover -s tests -v
```
