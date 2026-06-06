# M3U8 Sniffer Extension

Chrome/Edge Manifest V3 extension that detects `.m3u8` playlist requests and subtitle requests from the current tab.

## Install

1. Open `chrome://extensions`.
2. Enable `Developer mode`.
3. Click `Load unpacked`.
4. Select this folder: `m3u8-sniffer-extension`.

Edge uses `edge://extensions` with the same flow.

After changing extension files, click the extension reload button in `chrome://extensions` or `edge://extensions`, then reload the lecture/course page so the new content script is injected.

## Usage

1. Open the lecture page.
2. Start video playback.
3. The extension badge shows the number of detected media/subtitle candidates.
4. Click the extension icon.
5. Use the copied best m3u8 URL, or click `Copy URL` / `Copy CLI Command`.
6. If a subtitle request is detected, use `Copy Subtitle URL`.

The extension attempts automatic clipboard copy only for strong m3u8 candidates. Subtitle candidates are shown separately and require manual copy so they do not overwrite the media URL.

If playback opens in a new tab or popup where the extension popup is unavailable, open the extension from the original tab. The popup falls back to the most recent detection from another tab.

## Course Queue Capture

For K-MOOC and Hansung e-class course pages, use `Scan Course` in the popup to collect `li.activity.vod.modtype_vod` lecture items. The scanner stores each module id, section, title, duration, and `viewer.php?id=...` URL.

After scanning, `Start Queue` opens each viewer URL in sequence, lets the existing network sniffer capture the best m3u8 and subtitle candidates, closes the capture tab, and moves to the next lecture. `Copy JSON` copies the collected manifest for batch processing.

Hansung pages can show the current week twice, so queue items are deduplicated by Moodle module id.

Use `Remove` on scanned lectures to exclude videos before starting the queue. If the queue is already running, pause it before removing items.

## Why Candidates Exist

One video playback can emit multiple HLS playlists:

- master playlist
- variant media playlists for different qualities
- audio-only playlists
- subtitle playlists
- preview or ad playlists
- repeated requests for the same playlist

The best URL is selected by score. Master playlists are preferred because they can expose subtitle tracks to the Python transcript pipeline.

## Subtitle Detection

The extension also watches for common subtitle URLs:

- `.vtt`, `.srt`, `.ttml`, `.dfxp`, `.smi`, `.sami`
- `subtitle`, `caption`, `transcript`, `timedtext`, `texttrack`
- API endpoints such as `subtitle.php?uuid=...&language=ko`

When possible, it fetches the response and classifies the body as WebVTT, SRT, TTML, JSON, or HTML. WebVTT responses show cue count and a short sample line in the popup.

## Privacy Notes

- Detected URLs are stored in `chrome.storage.session`, not permanent local storage.
- The extension does not send data to a remote server.
- Cookies and authorization headers are not copied.
- `<all_urls>` host permission is required because lecture/video CDN domains vary.
- Candidate classification may fetch detected playlist/subtitle URLs in the browser with the current session credentials so the popup can score them. The response body is only used locally for classification.

## Limitations

- DRM-protected streams are still unsupported by the transcript app.
- Some signed URLs may expire.
- Playlist classification may fail when a CDN blocks extension-side fetches. In that case, URL-pattern scoring is still used.
- If automatic clipboard copy fails, use the popup buttons.
- Chrome internal pages, extension pages, and some browser-controlled viewer pages may not expose a usable active tab UI. Network detection can still work if the underlying HLS requests are ordinary HTTP/HTTPS requests.
- Subtitle detection does not read POST request bodies. It works best for GET-based subtitle URLs.
