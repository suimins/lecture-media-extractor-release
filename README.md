# Lecture Media Extractor

Lecture Media Extractor is a small toolkit for collecting lecture media URLs and turning HLS lectures into study-friendly transcripts.

Use it only with lectures or media you are allowed to access and process. DRM-protected streams are not supported.

It has two parts:

- `m3u8-sniffer-extension`: Chrome/Edge extension that detects lecture `.m3u8` playlist and subtitle URLs.
- `m3u8`: Python CLI pipeline that uses subtitles when available, or extracts audio with ffmpeg and transcribes it with faster-whisper.

The release folder intentionally excludes Whisper models, virtual environments, ffmpeg binaries, logs, generated transcripts, and private/signed lecture URLs.

## Folder Layout

```text
lecture-media-extractor-release/
  m3u8/                       Python transcript CLI
  m3u8-sniffer-extension/     Chrome/Edge Manifest V3 extension
  examples/                   Safe placeholder examples
  scripts/                    Convenience scripts
  tools/                      Optional local tools, not committed
```

## Requirements

- Python 3.11 or newer
- ffmpeg and ffprobe on `PATH`, or explicit paths passed with `--ffmpeg` and `--ffprobe`
- Chrome or Edge for the URL sniffer extension
- Enough disk space for model cache and transcript outputs

CPU transcription works by default. CUDA/GPU transcription can be faster, but it depends on local NVIDIA driver/CUDA/cuDNN compatibility.

## Install Python App

From the repository root:

```powershell
cd .\m3u8
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
python -m app.main --check-ffmpeg
```

After installation, the CLI is also available as:

```powershell
m3u8-transcript --check-ffmpeg
```

If ffmpeg is not on `PATH`, install it separately and pass the binary paths:

```powershell
python -m app.main --check-ffmpeg --ffmpeg "C:\path\to\ffmpeg.exe" --ffprobe "C:\path\to\ffprobe.exe"
```

## Install Browser Extension

1. Open `chrome://extensions` or `edge://extensions`.
2. Enable developer mode.
3. Click `Load unpacked`.
4. Select `m3u8-sniffer-extension`.

Open a lecture page, start playback, then use the extension popup to copy a media URL or scan a supported course page queue.

## Run One URL

```powershell
cd .\m3u8
.\.venv\Scripts\Activate.ps1
python -m app.main --run-url "https://example.com/path/index.m3u8" --out ..\outputs\one --whisper-model medium
```

Useful options:

```powershell
python -m app.main --run-url "https://example.com/path/index.m3u8" `
  --out ..\outputs\one `
  --whisper-model large-v3 `
  --whisper-device cuda `
  --whisper-compute-type int8_float16 `
  --whisper-beam-size 5 `
  --language ko
```

## Run Batch

Put one m3u8 URL per line in a text file. Lines starting with `#` are ignored.

```powershell
.\scripts\run-batch.ps1 -Urls .\examples\urls.example.txt -Out .\outputs\course -Model medium
```

For CUDA:

```powershell
.\scripts\run-batch.ps1 -Urls .\examples\urls.example.txt -Out .\outputs\course -Model large-v3 -Device cuda -ComputeType int8_float16 -BeamSize 5
```

Each item writes:

- `transcript.txt`
- `transcript.md`
- `transcript.srt`
- `transcript.json`

The batch summary is written to `batch_summary.json`.

## Model Notes

Models are not included in this release. faster-whisper downloads or uses the requested model under `m3u8/model-cache/whisper-models` by default.

Common choices:

- `tiny`: very fast, useful for smoke tests
- `medium`: balanced
- `large-v3`: better quality, heavier

For Korean technical lectures, `large-v3` with later LLM cleanup usually gives the best study notes, especially for domain terms.

## Privacy And Sharing

Do not commit:

- real signed CDN URLs
- cookies or authorization headers
- generated transcripts from private lectures
- downloaded models
- local ffmpeg binaries

Generated metadata redacts common signed URL query parameters, but private transcripts and raw captured manifests should still be treated as private.

The browser extension uses broad `<all_urls>` permissions because lecture players and CDN hosts vary. It stores detections in browser session storage and does not send them to a remote server.

Before publishing to GitHub, run:

```powershell
git status
git diff --stat
```

Then check that only source, docs, examples, and scripts are staged.

## License

MIT. See `LICENSE`.
