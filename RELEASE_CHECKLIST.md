# Release Checklist

Before publishing:

- Confirm no real lecture URLs, cookies, tokens, model files, ffmpeg binaries, transcripts, or logs are staged.
- Run tests from `m3u8`.
- Run `python -m unittest discover -s tests -v` from the root after installing `requirements.txt` and the Playwright Chromium test browser.
- Check `extension/*.js` syntax and load the automatic extension from `extension`.
- On Windows, verify `setup.cmd` installs dependencies and registers the native host, then check automatic processing in Chrome.
- Build the Windows ZIP with `python scripts/build_windows_zip.py`; extract it and run `install-windows.ps1 -NoOpen` in a clean Windows runner. Verify the private Python, pip dependencies, registered launcher and public-audio transcription from the installed directory. Check the guide's folder path and the explicit manual Chrome extension step.
- On Apple Silicon and Intel macOS runners, build the Metal engine, run the POSIX installer and native host handshake, transcribe the public fixture, and run the root tests. Distinguish actual GPU execution from CPU fallback on runners without GPU access.
- On Linux x64, verify the Vulkan package, native host registration, and CPU/GPU selection.
- Load the browser extension once from `m3u8-sniffer-extension`.
- Remove generated caches such as `__pycache__` if creating a manual zip.
- Create the zip from a clean git checkout or GitHub release archive.

Suggested local checks:

```powershell
rg -n "PRIVATE_CDN|YOUR_LOCAL_PATH|sk-|BEGIN PRIVATE|Bearer [A-Za-z0-9._-]{20,}" . --glob "!RELEASE_CHECKLIST.md"
git status
```
