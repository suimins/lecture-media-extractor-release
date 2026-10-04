import tempfile
import time
import unittest
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


class ExtensionSelectionTests(unittest.TestCase):
    def test_popup_persists_selection_and_passes_it_to_native_host(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as profile, sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(
                profile, channel="chromium", headless=True,
                args=[f"--disable-extensions-except={root / 'extension'}", f"--load-extension={root / 'extension'}"])
            try:
                context.route("https://learn.hansung.ac.kr/**", lambda route: route.fulfill(
                    content_type="text/html; charset=utf-8", body='<meta charset="utf-8"><title>시험 강의</title><video preload="none" src="https://media.example.test/lecture.mp4"></video>'))
                context.route("https://media.example.test/**", lambda route: route.fulfill(body="fixture"))
                worker = context.service_workers[0] if context.service_workers else context.wait_for_event("serviceworker")
                # Exercise the real extension while isolating it from installed native hosts.
                worker.evaluate("""() => {
                    globalThis.nativeRequests = [];
                    chrome.runtime.connectNative = () => {
                        const listeners = [];
                        return {
                            onMessage: {addListener: fn => listeners.push(fn)},
                            onDisconnect: {addListener: () => {}}, disconnect: () => {},
                            postMessage: message => {
                                nativeRequests.push(message);
                                queueMicrotask(() => listeners.forEach(fn => fn(message.command === 'hello'
                                    ? {type: 'ready', output: 'Downloads/lecture'}
                                    : {type: 'complete', status: 'complete', progress: 100,
                                       request_id: message.request_id, label: '선택한 파일 저장 완료'})));
                            }
                        };
                    };
                }""")
                player = context.new_page()
                player.goto("https://learn.hansung.ac.kr/mod/vod/viewer.php?id=99001")

                def starts():
                    return worker.evaluate("nativeRequests.filter(m => m.command === 'start')")

                deadline = time.monotonic() + 8
                while not starts() and time.monotonic() < deadline:
                    player.wait_for_timeout(100)
                self.assertTrue(starts())
                self.assertEqual(starts()[0]["outputs"], ["mp4", "mp3", "script"])
                player_id = worker.evaluate("[...tabs.keys()][0]")
                popup_url = worker.url.rsplit("/", 1)[0] + "/popup.html"

                def open_popup():
                    popup = context.new_page()
                    popup.goto(popup_url)
                    expect(popup.locator("#output")).to_have_text("Downloads/lecture")
                    # An action popup uses the player tab; this fixture opens its HTML as a tab.
                    popup.evaluate("id => { tabId = id; }", player_id)
                    return popup

                popup = open_popup()
                for kind in ("mp4", "mp3", "script"):
                    self.assertTrue(popup.locator(f"#{kind}").is_checked())
                popup.locator("#mp4").uncheck()
                popup.locator("#script").uncheck()
                self.assertTrue(popup.locator("#model").is_disabled())
                popup.locator("#start").click()
                deadline = time.monotonic() + 5
                while len(starts()) < 2 and time.monotonic() < deadline:
                    popup.wait_for_timeout(100)
                self.assertEqual(starts()[-1]["outputs"], ["mp3"])
                popup.close()

                popup = open_popup()
                self.assertFalse(popup.locator("#mp4").is_checked())
                self.assertTrue(popup.locator("#mp3").is_checked())
                self.assertFalse(popup.locator("#script").is_checked())
                screenshot = root / "work" / "qa" / "popup-selection.png"
                screenshot.parent.mkdir(parents=True, exist_ok=True)
                popup.set_viewport_size({"width": 390, "height": 850})
                popup.screenshot(path=str(screenshot), full_page=True)
                popup.locator("#mp3").uncheck()
                self.assertTrue(popup.locator("#start").is_disabled())
                self.assertIn("하나 이상", popup.locator("#selection-hint").inner_text())
                popup.evaluate("() => settingsSaved")
                previous = len(starts())
                player.goto("https://learn.hansung.ac.kr/mod/vod/viewer.php?id=99002")
                player.wait_for_timeout(2200)
                self.assertEqual(len(starts()), previous)
            finally:
                context.close()
