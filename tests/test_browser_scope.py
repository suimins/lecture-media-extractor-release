"""Real browser regression tests: never crawl another page or reuse an old capture."""
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from PySide6.QtCore import QCoreApplication
from playwright.sync_api import sync_playwright

from lecture_script.browser import BROWSER_INIT_SCRIPT, BrowserWorker


class Handler(BaseHTTPRequestHandler):
    late_started = threading.Event()
    late_release = threading.Event()
    def log_message(self, *_):
        pass

    def do_GET(self):
        if self.path == '/player-redirect':
            self.send_response(302)
            self.send_header('Location', '/player')
            self.end_headers()
            return
        if self.path == '/late.mp4':
            self.late_started.set()
            self.late_release.wait(timeout=5)
        if self.path.endswith('.mp4'):
            content, mime = b'test-media-response', 'video/mp4'
        elif self.path in ('/player', '/player2'):
            name = 'popup' if self.path == '/player' else 'popup2'
            content = (f'<html><title>Player {name}</title><video src="/{name}.mp4" preload="none"></video>'
                       f'<script>fetch("/{name}-startup.mp4")</script></html>').encode()
            mime = 'text/html'
        else:
            content = ('''<html><title>Current lecture</title>
              <video src="/current.mp4" preload="none"></video>
              <a href="/archive/2022/old.mp4">Old year</a>
              <a href="/course/view.php?id=old">Previous semester</a>
              <button id="open-player" onclick="window.open('/player','lecture-player','width=600,height=400')">강의</button>
              <button id="next-player" onclick="window.open('/player2','lecture-player','width=600,height=400')">다음 강의</button>
              <button id="blank-player" onclick="const p=window.open('','blank-player','width=600,height=400');setTimeout(()=>p.location='/player',50)">빈 창</button>
              <button id="redirect-player" onclick="window.open('/player-redirect','redirect-player','width=600,height=400')">리다이렉트</button>
              </html>''' if self.path == '/current' else '<html><title>Other page</title><video src="/old.mp4" preload="none"></video></html>').encode()
            mime = 'text/html'
        self.send_response(200)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(content)))
        self.end_headers()
        self.wfile.write(content)


class ScopeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f'http://127.0.0.1:{cls.server.server_port}'
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(channel='chromium', headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        self.context = self.browser.new_context()
        self.context.add_init_script(BROWSER_INIT_SCRIPT)
        self.worker = BrowserWorker()
        self.worker.context = self.context
        self.context.expose_binding('__lectureFocus', self.worker._focused)
        self.context.on('page', self.worker._attach)
        self.context.on('request', self.worker._request)
        self.context.on('response', self.worker._response)
        self.page = self.context.new_page()
        self.page.goto(self.base + '/current')
        self.worker.active_page = self.page

    def tearDown(self):
        self.context.close()

    def install_hansung_fixture(self):
        """Observed viewer.php + blob/HLS shape, with every school request mocked."""
        def respond(route):
            parsed = urlsplit(route.request.url)
            if parsed.path.endswith('.m3u8'):
                route.fulfill(status=200, content_type='application/vnd.apple.mpegurl', body='#EXTM3U\n#EXT-X-ENDLIST\n')
            elif parsed.path.endswith('.ts'):
                route.fulfill(status=200, content_type='video/mp2t', body='fixture')
            elif parsed.path == '/mod/vod/viewer.php':
                lecture_id = parse_qs(parsed.query)['id'][0]
                route.fulfill(status=200, content_type='text/html; charset=utf-8', body=f'''<html><title>강의 {lecture_id}</title>
                  <p>출석처리 기간이 아닙니다.</p><video></video>
                  <a href="/hls/archive/2022/index.m3u8">지난 연도 강의</a>
                  <script>
                    document.querySelector('video').src = URL.createObjectURL(new MediaSource());
                    window.ready = Promise.all([
                      fetch('/hls/{lecture_id}/mp4/{lecture_id}.mp4/index.m3u8').then(r => r.text()),
                      fetch('/hls/{lecture_id}/part.ts').then(r => r.text())
                    ]);
                  </script></html>''')
            else:
                route.fulfill(status=200, content_type='text/html; charset=utf-8', body='''<html><title>현재 과목</title>
                  <a id="lecture" href="/mod/vod/view.php?id=12"
                     onclick="window.open('https://learn.hansung.ac.kr/mod/vod/viewer.php?id=12', '', 'width=1920,height=1088,toolbar=no,location=no,menubar=no,copyhistory=no,status=no,directories=no,scrollbars=yes,resizable=yes'); return false;">강의</a>
                  <a href="/mod/vod/view.php?id=12" onclick="window.open('/mod/vod/viewer.php?id=12', '')">강의 중복 표시</a>
                  <a href="/mod/vod/view.php?id=13" onclick="window.open('/mod/vod/viewer.php?id=13', '')">두 번째 강의</a>
                  <a style="display:none" href="/mod/vod/view.php?id=14" onclick="window.open('/mod/vod/viewer.php?id=14', '')">숨긴 강의</a>
                  <a href="/mod/vod/view.php?id=15" onclick="window.open('/mod/vod/viewer.php?id=99', '')">ID 불일치</a>
                  <a href="/course/view.php?id=old">지난 학기</a></html>''')
        self.context.route('https://learn.hansung.ac.kr/**', respond)

    def test_course_lists_visible_lectures_without_opening_or_crawling(self):
        self.install_hansung_fixture()
        self.page.goto('https://learn.hansung.ac.kr/course/view.php?id=1')
        requested = []
        self.context.on('request', lambda request: requested.append(request.url))
        self.worker.arm()
        self.assertEqual({s.url for s in self.worker.sources.values()}, {
            'https://learn.hansung.ac.kr/mod/vod/viewer.php?id=12',
            'https://learn.hansung.ac.kr/mod/vod/viewer.php?id=13'})
        self.assertTrue(all(s.kind == 'lecture' for s in self.worker.sources.values()))
        self.assertEqual(len(self.context.pages), 1)
        self.assertFalse(any('viewer.php' in url or 'id=old' in url for url in requested))

    def test_selected_course_lecture_resolves_one_player_and_rejects_old_list(self):
        self.install_hansung_fixture()
        self.page.goto('https://learn.hansung.ac.kr/course/view.php?id=1')
        self.worker.arm()
        selected = next(iter(self.worker.sources.values()))
        prepared = []
        self.worker.prepared.connect(prepared.append)
        self.worker._prepare([selected])
        self.assertEqual(len(prepared), 1)
        self.assertEqual(len(prepared[0]), 1)
        self.assertEqual(prepared[0][0].kind, 'hls')
        self.assertIn('/12.mp4/index.m3u8', prepared[0][0].url)
        self.assertEqual(len(self.context.pages), 2)
        self.assertEqual(prepared[0][0].title, '강의')
        self.worker._focused({'page': self.page, 'frame': self.page.main_frame})
        self.worker.arm()
        with self.assertRaises(ValueError):
            self.worker._prepare([selected])

    def test_course_cannot_open_multiple_lectures_at_once(self):
        self.install_hansung_fixture()
        self.page.goto('https://learn.hansung.ac.kr/course/view.php?id=1')
        self.worker.arm()
        with self.assertRaises(ValueError):
            self.worker._prepare(list(self.worker.sources.values()))
        self.assertEqual(len(self.context.pages), 1)

    def test_already_open_hansung_blob_player_recovers_only_its_manifest(self):
        self.install_hansung_fixture()
        background = self.context.new_page()
        background.goto('https://learn.hansung.ac.kr/mod/vod/viewer.php?id=99')
        background.evaluate('window.ready')
        self.page.goto('https://learn.hansung.ac.kr/mod/vod/viewer.php?id=12')
        self.page.evaluate('window.ready')
        self.assertFalse(self.worker.sources)
        self.worker._focused({'page': self.page, 'frame': self.page.main_frame})
        self.worker.arm()
        sources = list(self.worker.sources.values())
        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0].url, 'https://learn.hansung.ac.kr/hls/12/mp4/12.mp4/index.m3u8')
        self.assertEqual(sources[0].kind, 'hls')
        self.assertFalse(sources[0].drm, 'An attendance-period notice is not a media protection error')
        self.worker._prepare(sources)

    def test_hansung_unnamed_onclick_popup_uses_viewer_not_link_target(self):
        self.install_hansung_fixture()
        self.page.goto('https://learn.hansung.ac.kr/course/view.php?id=1')
        self.worker.arm()
        with self.page.expect_popup() as pending:
            self.page.click('#lecture')
        popup = pending.value
        popup.wait_for_load_state('domcontentloaded')
        popup.evaluate('window.ready')
        popup.wait_for_timeout(100)
        self.assertIs(self.worker.scope_page, popup)
        self.assertEqual(self.worker.scope_url, 'https://learn.hansung.ac.kr/mod/vod/viewer.php?id=12')
        self.assertEqual({source.kind for source in self.worker.sources.values()}, {'hls'})
        self.assertEqual(len(self.worker.sources), 1)

    def test_changed_player_id_cannot_recover_previous_document_manifest(self):
        self.install_hansung_fixture()
        self.page.goto('https://learn.hansung.ac.kr/mod/vod/viewer.php?id=12')
        self.page.evaluate('window.ready')
        self.page.evaluate("history.pushState({}, '', '?id=99')")
        self.worker.arm()
        self.assertFalse(self.worker.sources)

    def test_duplicate_playback_notice_clears_and_prevents_preparation(self):
        self.install_hansung_fixture()
        self.page.goto('https://learn.hansung.ac.kr/mod/vod/viewer.php?id=12')
        self.page.evaluate('window.ready')
        self.worker.arm()
        selected = list(self.worker.sources.values())
        self.assertTrue(selected)
        self.page.evaluate("document.body.textContent = '다중 동영상 플레이가 감지되었습니다. 현재 창의 동영상 플레이를 중단합니다.'")
        problems, prepared = [], []
        self.worker.problem.connect(problems.append)
        self.worker.prepared.connect(prepared.append)
        self.worker._prepare(selected)
        self.assertFalse(self.worker.sources)
        self.assertFalse(prepared)
        self.assertTrue(any('중복 재생' in message for message in problems))

    def test_explicit_start_no_links_no_background_page(self):
        self.page.evaluate("fetch('/current.mp4')")
        self.assertFalse(self.worker.sources, 'Capture must be off until explicitly armed')
        background = self.context.new_page()
        background.goto(self.base + '/old')
        self.page.bring_to_front()
        # Headless pages all report visible/focused; deliver the foreground event explicitly.
        self.worker._focused({'page': self.page, 'frame': self.page.main_frame})
        self.worker.arm()
        self.assertEqual({s.url for s in self.worker.sources.values()}, {self.base + '/current.mp4'})
        background.evaluate("fetch('/old.mp4')")
        self.assertNotIn(self.base + '/old.mp4', {s.url for s in self.worker.sources.values()})
        self.assertFalse(any('archive' in s.url for s in self.worker.sources.values()))
        self.page.evaluate("fetch('/fresh.mp4')")
        self.page.wait_for_timeout(150)
        self.assertIn(self.base + '/fresh.mp4', {s.url for s in self.worker.sources.values()})

    def test_navigation_clears_and_requires_rearming(self):
        self.worker.arm()
        old_source = next(iter(self.worker.sources.values()))
        self.page.goto(self.base + '/old')
        self.assertIsNone(self.worker.scope_page)
        self.assertFalse(self.worker.sources)
        self.page.evaluate("fetch('/old.mp4')")
        self.assertFalse(self.worker.sources)
        self.worker.arm()
        with self.assertRaises(ValueError):
            self.worker._prepare([old_source])

    def test_old_request_is_not_accepted_after_rearm(self):
        Handler.late_started.clear()
        Handler.late_release.clear()
        self.worker.arm()
        self.page.evaluate("void fetch('/late.mp4')")
        self.page.wait_for_timeout(100)
        self.assertTrue(Handler.late_started.is_set())
        self.worker.arm()
        Handler.late_release.set()
        self.page.wait_for_timeout(200)
        self.assertNotIn(self.base + '/late.mp4', {s.url for s in self.worker.sources.values()})

    def test_tab_switch_clears_and_subframes_are_in_scope(self):
        background = self.context.new_page()
        background.goto(self.base + '/other')
        self.worker._focused({'page': self.page, 'frame': self.page.main_frame})
        self.worker.arm()
        self.page.evaluate("() => {const f=document.createElement('iframe'); f.src='/embedded'; document.body.append(f)}")
        self.page.wait_for_timeout(100)
        self.worker.scan()
        self.assertIn(self.base + '/old.mp4', {s.url for s in self.worker.sources.values()}, 'A video actually embedded in the current page is in scope')
        self.worker._focused({'page': background, 'frame': background.main_frame})
        self.assertIsNone(self.worker.scope_page)
        self.assertFalse(self.worker.sources)

    def test_drm_page_is_flagged_before_prepare(self):
        self.page.evaluate('window.__lectureDRM = true')
        self.worker.arm()
        self.assertTrue(all(source.drm for source in self.worker.sources.values()))

    def test_rearm_while_response_callback_waits_drops_old_source(self):
        from types import SimpleNamespace
        self.worker.arm()
        class Request:
            frame = self.page.main_frame
            def all_headers(inner):
                self.worker.arm()
                return {}
        request = Request()
        self.worker.tracked_requests.add(request)
        response = SimpleNamespace(request=request, status=200, url=self.base + '/stale.mp4', headers={'content-type':'video/mp4'})
        self.worker._response(response)
        self.assertNotIn(self.base + '/stale.mp4', {source.url for source in self.worker.sources.values()})

    def test_user_opened_player_is_adopted_and_parent_requests_are_excluded(self):
        self.worker.arm()
        with self.page.expect_popup() as pending:
            self.page.click('#open-player')
        popup = pending.value
        popup.wait_for_load_state('domcontentloaded')
        popup.wait_for_timeout(150)
        self.assertIs(self.worker.scope_page, popup)
        self.assertFalse(self.worker.popup_loading)
        urls = {source.url for source in self.worker.sources.values()}
        self.assertIn(self.base + '/popup.mp4', urls)
        self.assertIn(self.base + '/popup-startup.mp4', urls)
        self.assertNotIn(self.base + '/current.mp4', urls)
        self.page.evaluate("fetch('/parent-old.mp4')")
        popup.wait_for_timeout(100)
        self.assertNotIn(self.base + '/parent-old.mp4', {source.url for source in self.worker.sources.values()})
        self.worker._focused({'page': popup, 'frame': popup.main_frame})
        self.worker._prepare(list(self.worker.sources.values()))

    def test_blank_and_redirect_player_initial_navigation_is_allowed(self):
        for selector in ('#blank-player', '#redirect-player'):
            self.worker._focused({'page': self.page, 'frame': self.page.main_frame})
            self.worker.arm()
            with self.page.expect_popup() as pending:
                self.page.click(selector)
            popup = pending.value
            popup.wait_for_url(self.base + '/player')
            popup.wait_for_load_state('domcontentloaded')
            popup.wait_for_timeout(150)
            self.assertIs(self.worker.scope_page, popup, selector)
            self.assertIn(self.base + '/popup.mp4', {source.url for source in self.worker.sources.values()})
            popup.close()

    def test_reused_player_window_captures_only_new_lecture(self):
        self.worker.arm()
        with self.page.expect_popup() as pending:
            self.page.click('#open-player')
        popup = pending.value
        popup.wait_for_load_state('domcontentloaded')
        popup.wait_for_timeout(100)
        self.worker._focused({'page': self.page, 'frame': self.page.main_frame})
        self.worker.arm()
        self.page.click('#next-player')
        popup.wait_for_url(self.base + '/player2')
        popup.wait_for_timeout(150)
        self.assertIs(self.worker.scope_page, popup)
        urls = {source.url for source in self.worker.sources.values()}
        self.assertIn(self.base + '/popup2.mp4', urls)
        self.assertNotIn(self.base + '/popup.mp4', urls)

    def test_popup_without_new_user_gesture_is_rejected(self):
        self.worker.arm()
        with self.page.expect_popup() as pending:
            self.page.evaluate("window.open('/player','script-popup')")
        pending.value.wait_for_load_state('domcontentloaded')
        self.assertIsNone(self.worker.scope_page)
        self.assertFalse(self.worker.sources)

    def test_parent_navigation_invalidates_player(self):
        self.worker.arm()
        with self.page.expect_popup() as pending:
            self.page.click('#open-player')
        popup = pending.value
        popup.wait_for_load_state('domcontentloaded')
        popup.wait_for_timeout(100)
        self.page.goto(self.base + '/old')
        self.assertIsNone(self.worker.scope_page)
        self.assertFalse(self.worker.sources)

    def test_nested_popup_is_not_followed(self):
        self.worker.arm()
        with self.page.expect_popup() as pending:
            self.page.click('#open-player')
        popup = pending.value
        popup.wait_for_load_state('domcontentloaded')
        popup.wait_for_timeout(100)
        popup.evaluate("document.body.insertAdjacentHTML('beforeend', `<button id='nested' onclick=\"window.open('/old','nested-player')\">Nested</button>`)")
        with popup.expect_popup() as next_popup:
            popup.click('#nested')
        next_popup.value.wait_for_load_state('domcontentloaded')
        self.assertIsNone(self.worker.scope_page)
        self.assertFalse(self.worker.sources)

    def test_reused_window_focus_does_not_capture_its_old_document(self):
        self.worker.arm()
        with self.page.expect_popup() as pending:
            self.page.click('#open-player')
        popup = pending.value
        popup.wait_for_load_state('domcontentloaded')
        popup.wait_for_timeout(100)
        self.worker._focused({'page': self.page, 'frame': self.page.main_frame})
        self.worker.arm()
        self.page.click('body', position={'x': 5, 'y': 5})
        self.worker._focused({'page': popup, 'frame': popup.main_frame})
        self.assertIs(self.worker.scope_page, popup)
        self.assertTrue(self.worker.popup_waiting_navigation)
        self.assertFalse(self.worker.sources)
        self.worker.scan()
        self.assertFalse(self.worker.sources, 'The old document must not leak into the new capture')
        popup.goto(self.base + '/player2')
        popup.wait_for_timeout(100)
        self.assertIn(self.base + '/popup2.mp4', {source.url for source in self.worker.sources.values()})


if __name__ == '__main__':
    unittest.main()
