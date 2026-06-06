# Tools

This folder is intentionally empty in the source release.

Install ffmpeg separately and make `ffmpeg` and `ffprobe` available on `PATH`, or place portable binaries here:

```text
tools/
  ffmpeg/
    bin/
      ffmpeg.exe
      ffprobe.exe
```

The helper script `scripts/run-batch.ps1` automatically uses those portable binaries if they exist.
