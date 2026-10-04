from __future__ import annotations

import hashlib
import queue
import re
import threading
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from PySide6.QtCore import QThread, Signal

HOME_URL = "https://learn.hansung.ac.kr/login.php"
BROWSER_INIT_SCRIPT = """window.__lectureDRM = false;
    window.__lectureDocumentURL = location.href;
    window.__lectureGestureSeq = 0;
    window.__lectureGestureTime = 0;
    document.addEventListener('encrypted', () => window.__lectureDRM = true, true);
    const rememberGesture = event => {
        if (!event.isTrusted) return;
        if (event.type === 'keydown' && !['Enter', ' '].includes(event.key)) return;
        if (event.type === 'pointerdown' && event.button !== 0) return;
        window.__lectureGestureSeq++;
        window.__lectureGestureTime = Date.now();
    };
    for (const type of ['pointerdown', 'click', 'keydown']) {
        document.addEventListener(type, rememberGesture, true);
    }
    window.addEventListener('focus', () => window.__lectureFocus?.());
"""


def is_hansung_player(url: str) -> bool:
    parsed = urlsplit(url)
    ids = parse_qs(parsed.query).get("id", [])
    return (parsed.scheme == "https" and parsed.netloc == "learn.hansung.ac.kr"
            and parsed.path == "/mod/vod/viewer.php"
            and len(ids) == 1 and ids[0].isascii() and ids[0].isdigit())


def is_hansung_course(url: str) -> bool:
    parsed = urlsplit(url)
    ids = parse_qs(parsed.query).get('id', [])
    return (parsed.scheme == 'https' and parsed.netloc == 'learn.hansung.ac.kr'
            and parsed.path == '/course/view.php'
            and len(ids) == 1 and ids[0].isascii() and ids[0].isdigit())


def lecture_link(href: str, onclick: str) -> str | None:
    """Read a single observed link; never execute onclick or discover other courses."""
    parsed = urlsplit(href)
    if parsed.scheme != 'https' or parsed.netloc != 'learn.hansung.ac.kr' or parsed.path != '/mod/vod/view.php':
        return None
    ids = parse_qs(parsed.query).get('id', [])
    if len(ids) != 1 or not ids[0].isascii() or not ids[0].isdigit():
        return None
    match = re.search(r'''window\.open\(\s*(['"])([^'"]+)\1''', onclick)
    if not match:
        return None
    target = match[2]
    if target.startswith('/'):
        target = 'https://learn.hansung.ac.kr' + target
    if not is_hansung_player(target) or parse_qs(urlsplit(target).query)['id'] != ids:
        return None
    return 'https://learn.hansung.ac.kr/mod/vod/viewer.php?id=' + ids[0]


def dom_headers(page_url: str, media_url: str):
    page, media = urlsplit(page_url), urlsplit(media_url)
    if page.scheme == "https" and media.scheme == "http":
        return {}
    referer = page_url.split("#")[0] if (page.scheme, page.netloc) == (media.scheme, media.netloc) else f"{page.scheme}://{page.netloc}/"
    return {"Referer": referer}


def media_kind(url: str, content_type: str = "") -> str | None:
    path = urlsplit(url).path.lower()
    mime = content_type.split(";")[0].strip().lower()
    if not url.startswith(("http://", "https://")):
        return None
    if path.endswith((".ts", ".m4s", ".aac")):
        return None  # A stream fragment is not a complete lecture.
    if path.endswith((".vtt", ".srt")) or mime in ("text/vtt", "application/x-subrip"):
        return "subtitle"
    if path.endswith(".m3u8") or mime in ("application/vnd.apple.mpegurl", "application/x-mpegurl", "audio/mpegurl"):
        return "hls"
    if path.endswith(".mpd") or mime == "application/dash+xml":
        return "dash"
    if path.endswith((".mp4", ".webm", ".mov", ".mkv", ".mp3", ".m4a", ".wav", ".ogg", ".flac")) or mime.startswith(("video/", "audio/")):
        return "media"
    return None


@dataclass
class Source:
    url: str = field(repr=False)
    kind: str = "media"
    title: str = "강의"
    page_url: str = field(default="", repr=False)
    headers: dict = field(default_factory=dict, repr=False)
    cookies: list = field(default_factory=list, repr=False)
    subtitle_url: str = field(default="", repr=False)
    drm: bool = False
    capture_epoch: int = 0
    course: str = "과목 미지정"

    @property
    def key(self):
        return hashlib.sha256(self.url.encode()).hexdigest()[:16]

    @property
    def local(self):
        return self.kind == "local"

    @property
    def display_location(self):
        return str(Path(self.url).parent) if self.local else urlsplit(self.url).hostname or ""


class BrowserWorker(QThread):
    status = Signal(str)
    source_found = Signal(object)
    capture_reset = Signal()
    prepared = Signal(object)
    problem = Signal(str)

    def __init__(self):
        super().__init__()
        self.commands = queue.Queue()
        self.context = None
        self.active_page = None
        self.sources: dict[str, Source] = {}
        self.scope_page = None
        self.scope_url = ""
        self.epoch = 0
        self.tracked_requests = set()
        self.gesture_baseline = 0
        self.follow_popup = False
        self.popup_parent = None
        self.popup_parent_url = ""
        self.popup_loading = False
        self.popup_waiting_navigation = False
        self.popup_deadline = 0.0
        self.checking_popup = None
        self.popup_requests = set()
        self.popup_responses = []
        self.cancel_resolution = threading.Event()
        self.owned_player = None

    def submit(self, command, payload=None):
        self.commands.put((command, payload))

    def run(self):
        from playwright.sync_api import sync_playwright
        try:
            with sync_playwright() as playwright:
                browser = None
                for channel in ("msedge", "chrome", None):
                    try:
                        browser = playwright.chromium.launch(channel=channel, headless=False)
                        break
                    except Exception:
                        continue
                if browser is None:
                    raise RuntimeError("Edge 또는 Chrome을 설치하세요. 대안: .venv\\Scripts\\python -m playwright install chromium")
                self.context = browser.new_context(accept_downloads=False)
                self.context.add_init_script(BROWSER_INIT_SCRIPT)
                self.context.expose_binding("__lectureFocus", self._focused)
                self.context.on("page", self._attach)
                self.context.on("request", self._request)
                self.context.on("requestfailed", lambda request: self.tracked_requests.discard(request))
                self.context.on("response", self._response)
                self.active_page = self.context.new_page()
                self.active_page.goto(HOME_URL, wait_until="domcontentloaded", timeout=30000)
                self.status.emit("브라우저에서 로그인한 뒤 원하는 강의를 열어 주세요.")
                while browser.is_connected() and self.context.pages:
                    try:
                        command, payload = self.commands.get_nowait()
                    except queue.Empty:
                        self.context.pages[-1].wait_for_timeout(100)
                        continue
                    try:
                        if command == "stop":
                            break
                        if command == "navigate":
                            page = self._page()
                            page.goto(payload, wait_until="domcontentloaded", timeout=30000)
                            page.bring_to_front()
                        elif command == "arm":
                            self.arm()
                        elif command == "pause":
                            self.disarm()
                        elif command == "prepare":
                            self._prepare(payload)
                    except (ValueError, RuntimeError) as error:
                        # Our actionable messages do not contain session URLs.
                        self.problem.emit(str(error))
                    except Exception as error:
                        # Do not emit Playwright errors containing session-bearing URLs.
                        self.problem.emit(f"브라우저 작업을 완료하지 못했습니다 ({type(error).__name__}). 페이지를 확인하고 다시 시도하세요.")
                browser.close()
        except Exception as error:
            self.problem.emit(str(error) if isinstance(error, RuntimeError) else f"브라우저 연결 실패 ({type(error).__name__})")
        finally:
            self.disarm()
            self.context = None
            self.status.emit("브라우저 연결이 종료되었습니다. 발견된 주소는 만료될 수 있습니다.")

    def _attach(self, page):
        self.active_page = page
        page.on("framenavigated", lambda frame: self._navigated(page, frame))
        page.on("domcontentloaded", lambda: self._loaded(page))
        page.on("close", lambda: self.disarm() if page in (self.scope_page, self.popup_parent) else None)
        if self.scope_page is not None and page != self.scope_page and not self._adopt_popup(page):
            self.disarm()
            self.status.emit("관련 없는 새 창이 열려 감지를 멈췄습니다. 원하는 페이지에서 다시 시작하세요.")

    def _adopt_popup(self, page, wait_for_navigation=False):
        """Only follow the armed page's direct popup after a fresh real user gesture."""
        if self.checking_popup is not None:
            return page == self.checking_popup
        parent, parent_url, epoch = self.scope_page, self.scope_url, self.epoch
        if parent is None or page == parent or not self.follow_popup:
            return False
        self.checking_popup = page
        self.popup_requests.clear()
        self.popup_responses.clear()
        try:
            if page.opener() != parent or parent.is_closed() or parent.url != parent_url:
                return False
            gesture = parent.evaluate("""() => ({
                sequence: window.__lectureGestureSeq || 0,
                age: Date.now() - (window.__lectureGestureTime || 0)
            })""")
            if (epoch != self.epoch or parent.url != parent_url or
                    gesture["sequence"] <= self.gesture_baseline or not 0 <= gesture["age"] <= 10000):
                return False
            self.disarm()
            self.scope_page, self.scope_url = page, page.url
            self.active_page = page
            self.popup_parent, self.popup_parent_url = parent, parent_url
            self.popup_loading = True
            self.popup_waiting_navigation = wait_for_navigation
            self.popup_deadline = time.monotonic() + 15
            pending_responses = list(self.popup_responses)
            if not wait_for_navigation:
                self.tracked_requests.update(self.popup_requests)
            self.popup_requests.clear()
            self.popup_responses.clear()
            if not wait_for_navigation:
                for response in pending_responses:
                    self._response(response)
            self.status.emit("현재 페이지에서 연 재생 창을 연결했습니다. 새 창에서 영상을 재생하세요.")
            # The popup event may arrive before or after its initial navigation/DOM load.
            if not wait_for_navigation and page.url.startswith(("http://", "https://")) and page.evaluate("document.readyState") != "loading":
                self._loaded(page)
            return True
        except Exception:
            return False
        finally:
            self.checking_popup = None
            self.popup_requests.clear()
            self.popup_responses.clear()

    def _loaded(self, page):
        if page != self.scope_page or not self.popup_loading or self.popup_waiting_navigation:
            return
        if not page.url.startswith(("http://", "https://")):
            return  # window.open('') followed by assigning the player URL.
        self.popup_loading = False
        self.scope_url = page.url
        self.scan(recover_startup=True)

    def _focused(self, source):
        page = source["page"]
        if source["frame"] != page.main_frame:
            return
        self.active_page = page
        if self.scope_page is not None and page != self.scope_page:
            if self.popup_loading and page == self.popup_parent:
                return
            if self._adopt_popup(page, wait_for_navigation=True):
                return
            self.disarm()
            self.status.emit("탭이 변경되어 감지를 멈췄습니다. 원하는 페이지에서 감지를 다시 시작하세요.")

    def _navigated(self, page, frame):
        if frame != page.main_frame:
            return
        if page == self.popup_parent:
            self.disarm()
            self.status.emit("원래 강의 페이지가 변경되어 재생 창 감지를 멈췄습니다.")
        elif page == self.scope_page and self.popup_loading:
            if time.monotonic() > self.popup_deadline:
                self.disarm()
                self.status.emit("재생 창 연결 대기 시간이 지났습니다. 원하는 재생 창에서 감지를 다시 시작하세요.")
                return
            self._reset_sources()
            self.scope_url = page.url
            self.popup_waiting_navigation = False
        elif page == self.scope_page:
            self.disarm()
            self.status.emit("페이지가 변경되어 이전 영상 목록을 비웠습니다. 현재 페이지 감지를 다시 시작하세요.")
        elif self.scope_page is not None:
            # LMS players often reuse an existing window with the same window.open target name.
            self._adopt_popup(page)

    def _page(self):
        pages = [p for p in self.context.pages if not p.is_closed()]
        focused, visible = [], []
        for page in pages:
            try:
                state = page.evaluate("({focus:document.hasFocus(),visible:document.visibilityState === 'visible'})")
                if state["focus"]:
                    focused.append(page)
                if state["visible"]:
                    visible.append(page)
            except Exception:
                pass
        if len(focused) == 1:
            self.active_page = focused[0]
            return focused[0]
        if len(visible) == 1:
            self.active_page = visible[0]
            return visible[0]
        if self.active_page in pages:
            return self.active_page
        return pages[-1]

    def _emit_source(self, source):
        if self.scope_page is None or self.popup_waiting_navigation or source.page_url != self.scope_url:
            return
        source.capture_epoch = self.epoch
        existing = self.sources.get(source.key)
        if existing and not source.subtitle_url:
            return
        self.sources[source.key] = source
        self.source_found.emit(source)

    def disarm(self):
        self.scope_page = None
        self.scope_url = ""
        self.follow_popup = False
        self.popup_parent = None
        self.popup_parent_url = ""
        self.popup_loading = False
        self.popup_waiting_navigation = False
        self.popup_deadline = 0.0
        self.gesture_baseline = 0
        self._reset_sources()

    def _reset_sources(self):
        self.epoch += 1
        self.sources.clear()
        self.tracked_requests.clear()
        self.capture_reset.emit()

    def arm(self):
        page = self._page()
        self.disarm()
        self.scope_page, self.scope_url = page, page.url
        epoch = self.epoch
        baseline = page.evaluate("window.__lectureGestureSeq || 0")
        if epoch != self.epoch or page.url != self.scope_url:
            return
        self.gesture_baseline = baseline
        self.follow_popup = True
        self.scan(recover_startup=True)

    def _request(self, request):
        try:
            if not self.popup_waiting_navigation and self.scope_page is not None and request.frame.page == self.scope_page and self.scope_page.url == self.scope_url:
                self.tracked_requests.add(request)
            elif self.checking_popup is not None and request.frame.page == self.checking_popup:
                self.popup_requests.add(request)
        except Exception:
            pass

    def _response(self, response):
        try:
            epoch = self.epoch
            if response.request not in self.tracked_requests:
                if response.request in self.popup_requests:
                    self.popup_responses.append(response)
                return
            self.tracked_requests.discard(response.request)
            if response.status not in (200, 206):
                return
            kind = media_kind(response.url, response.headers.get("content-type", ""))
            if not kind:
                return
            frame = response.request.frame
            page = frame.page
            page_url = page.url
            if page != self.scope_page or page.url != self.scope_url:
                return
            headers = response.request.all_headers()
            safe_headers = {key: headers[key] for key in ("referer", "user-agent") if key in headers}
            title = page.title() or "강의"
            if epoch != self.epoch or page != self.scope_page or page.url != page_url:
                return
            self._emit_source(Source(response.url, kind, title, page_url, safe_headers))
        except Exception:
            pass  # An unrelated request or a closed popup should not abort capture.

    def scan(self, recover_startup=False):
        page = self.scope_page
        if page is None or self.popup_waiting_navigation or page.is_closed() or page.url != self.scope_url:
            return False
        epoch, page_url = self.epoch, self.scope_url
        title = page.title()
        if is_hansung_course(page_url):
            headings = page.locator('h1').all_text_contents()
            title = next((heading.strip() for heading in headings if heading.strip()), title)
        if epoch != self.epoch:
            return False
        drm = False
        for frame in page.frames:
            try:
                data = frame.evaluate("""() => ({
                    drm: !!window.__lectureDRM,
                    documentURL: window.__lectureDocumentURL || '',
                    text: document.body?.innerText || '',
                    resources: performance.getEntriesByType('resource').map(entry => entry.name),
                    media: [...document.querySelectorAll('video,audio')].map(v => ({
                        url: v.currentSrc || v.src || v.querySelector('source')?.src || '',
                        subtitle: [...v.querySelectorAll('track[src]')].find(t => /^(ko(?:-|$)|kor$)/i.test(t.srclang))?.src || ''
                    })),
                    tracks: [...document.querySelectorAll('track[src]')].map(t => ({url:t.src,title:t.label || t.srclang || '자막'}))
                })""")
                if epoch != self.epoch or page != self.scope_page or page.url != page_url:
                    return False
                drm |= data["drm"]
                if frame == page.main_frame and is_hansung_course(page_url):
                    links = frame.locator('a[href*="/mod/vod/view.php"]').evaluate_all("""links => links
                        .filter(a => a.getClientRects().length && getComputedStyle(a).visibility !== 'hidden')
                        .map(a => ({href:a.href, onclick:a.getAttribute('onclick') || '',
                            title:(a.querySelector('.instancename') || a).textContent.trim()}))""")
                    if epoch != self.epoch or page.url != page_url:
                        return False
                    for item in links:
                        target = lecture_link(item['href'], item['onclick'])
                        if target:
                            self._emit_source(Source(target, 'lecture', item['title'] or '강의', page_url))
                hansung_player = is_hansung_player(frame.url)
                if hansung_player and "다중 동영상 플레이가 감지" in data["text"] and not data["media"]:
                    self._reset_sources()
                    message = "eClass가 중복 재생을 차단했습니다. 다른 재생 창을 닫고 원하는 강의 창 하나에서 다시 감지하세요."
                    self.status.emit(message)
                    self.problem.emit(message)
                    return False
                # Hansung JW Player loads an HLS manifest before capture can be armed,
                # then exposes only a blob URL. Recover completed manifests from this
                # exact document on explicit arm, without visiting any lecture links.
                ready_blob_player = (hansung_player and data["documentURL"] == frame.url
                                     and any(item["url"].startswith("blob:") for item in data["media"]))
                fresh_popup = self.popup_parent is not None and not self.popup_loading
                if recover_startup and (fresh_popup or ready_blob_player):
                    for url in data["resources"]:
                        kind = media_kind(url)
                        if kind and (fresh_popup or kind in ("hls", "dash")):
                            self._emit_source(Source(url, kind, title, page_url, dom_headers(frame.url, url)))
                for item in data["media"]:
                    if media_kind(item["url"]):
                        self._emit_source(Source(item["url"], media_kind(item["url"]), title, page_url,
                                                dom_headers(frame.url, item["url"]), subtitle_url=item["subtitle"]))
                for item in data["tracks"]:
                    if media_kind(item["url"]):
                        self._emit_source(Source(item["url"], "subtitle", f"{title} · {item['title']}", page_url, dom_headers(frame.url, item["url"])))
            except Exception:
                continue
        if drm:
            self.problem.emit("이 페이지에서 DRM 재생이 감지되었습니다. 보호된 영상은 처리하지 않습니다.")
            for key, source in list(self.sources.items()):
                if source.page_url == page.url:
                    source.drm = True
                    self.source_found.emit(source)
        lecture_count = sum(s.kind == 'lecture' for s in self.sources.values())
        if is_hansung_course(page_url):
            guide = "하나를 선택하고 '음원만 저장'을 누르세요." if lecture_count else "주차별 학습 활동의 동영상이 표시되었는지 확인하세요."
            self.status.emit(f"현재 과목: {title} · 강의 {lecture_count}개. {guide}")
        elif '/login/' in urlsplit(page_url).path:
            self.status.emit("로그인 페이지입니다. 이 앱이 연 브라우저에서 로그인하고 과목의 주차별 강의 페이지를 연 뒤 다시 불러오세요.")
        elif self.popup_parent is not None:
            self.status.emit(f"연결된 재생 창만 감지 중: {title} · 이 창에서 영상을 재생하세요.")
        else:
            self.status.emit(f"현재 페이지 감지 중: {title} · 강의를 클릭하면 열린 재생 창도 연결합니다.")
        return True

    def _prepare(self, selected):
        if self.scope_page is None or self._page() != self.scope_page or self.scope_page.url != self.scope_url:
            raise ValueError("현재 페이지 감지를 다시 시작하세요.")
        if any(source.kind == 'lecture' for source in selected):
            if len(selected) != 1:
                raise ValueError("과목 목록의 강의는 한 번에 하나만 선택하세요. 선택한 재생 창 하나에서 음원을 가져옵니다.")
            self._resolve_lecture(selected[0])
            return
        if not self.scan():
            return
        cookies = self.context.cookies()
        result = []
        for source in selected:
            if not source.local and (source.key not in self.sources or source.capture_epoch != self.epoch):
                raise ValueError("이전 페이지의 영상 소스입니다. 현재 페이지에서 다시 감지하세요.")
            current = self.sources.get(source.key, source)
            result.append(replace(current, cookies=cookies) if not current.local else current)
        self.prepared.emit(result)

    def _resolve_lecture(self, selected):
        if selected.key not in self.sources or selected.capture_epoch != self.epoch or selected.page_url != self.scope_url:
            raise ValueError("현재 과목에서 강의 목록을 다시 불러오세요.")
        parent, parent_url = self.scope_page, self.scope_url
        if not is_hansung_course(parent_url) or not is_hansung_player(selected.url):
            raise ValueError("현재 과목에 표시된 강의 링크만 열 수 있습니다.")
        if self.cancel_resolution.is_set():
            raise ValueError("강의 연결을 중단했습니다.")
        self.disarm()
        if self.owned_player is not None and not self.owned_player.is_closed():
            self.owned_player.close()
        player = self.context.new_page()
        self.owned_player = player
        self.scope_page, self.scope_url = player, player.url
        self.popup_parent, self.popup_parent_url = parent, parent_url
        self.popup_loading = True
        self.popup_deadline = time.monotonic() + 35
        self.status.emit(f"선택한 강의 여는 중: {selected.title}")
        try:
            player.bring_to_front()
            player.goto(selected.url, wait_until='domcontentloaded', timeout=30000)
            deadline = time.monotonic() + 25
            while time.monotonic() < deadline:
                if self.cancel_resolution.is_set():
                    raise ValueError("강의 연결을 중단했습니다.")
                if player.is_closed() or self.scope_page != player or parent.url != parent_url:
                    raise ValueError("강의 연결 중 페이지가 변경되었습니다. 현재 과목에서 다시 불러오세요.")
                if not self.scan(recover_startup=True):
                    return
                candidates = [s for s in self.sources.values() if s.kind in ('hls', 'dash', 'media')]
                if candidates:
                    candidate = min(candidates, key=lambda s: {'hls': 0, 'dash': 1, 'media': 2}[s.kind])
                    if candidate.drm:
                        raise ValueError("DRM으로 보호된 영상은 처리할 수 없습니다.")
                    cookies = self.context.cookies()
                    if self.scope_page != player or parent.url != parent_url:
                        raise ValueError("페이지가 변경되었습니다. 강의를 다시 선택하세요.")
                    self.prepared.emit([replace(candidate, title=selected.title, cookies=cookies)])
                    self.status.emit(f"영상 연결 완료: {selected.title} · 선택한 작업을 시작합니다.")
                    return
                player.wait_for_timeout(200)
            raise ValueError("영상 주소를 찾지 못했습니다. 열린 강의 창에서 재생 버튼을 누른 뒤 '현재 페이지 강의 불러오기'를 누르세요.")
        finally:
            self.cancel_resolution.clear()
