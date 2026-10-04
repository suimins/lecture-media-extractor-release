const HOST = "kr.lecture_script.outputs";
const tabs = new Map();
const requests = new Map();
const jobs = new Map();
let port = null;
let idleTimer = null;
let hostError = "";
let output = "다운로드/lecture";
const restored = chrome.storage.session.get("jobs").then(data => {
  for (const job of data.jobs || []) {
    if (["running", "queued", "starting"].includes(job.status)) {
      job.status = "interrupted";
      job.label = "연결이 종료됐습니다. 강의 창에서 다시 시작하세요.";
    }
    jobs.set(job.request_id, job);
  }
});

function player(url) {
  try {
    const u = new URL(url);
    return u.origin === "https://learn.hansung.ac.kr" && u.pathname === "/mod/vod/viewer.php"
      && /^\d+$/.test(u.searchParams.get("id") || "");
  } catch { return false; }
}
function mediaKind(url, mime = "") {
  try {
    const u = new URL(url);
    if (!["http:", "https:"].includes(u.protocol)) return null;
    const path = u.pathname.toLowerCase();
    if (/\.(ts|m4s|aac|vtt|srt)$/.test(path)) return null;
    if (path.endsWith(".m3u8") || /mpegurl/i.test(mime)) return "hls";
    if (path.endsWith(".mpd") || /dash\+xml/i.test(mime)) return "dash";
    if (/\.(mp4|webm|mov|mkv)$/.test(path) || /^video\//i.test(mime)) return "media";
  } catch {}
  return null;
}
function remember() {
  while (jobs.size > 100) {
    const done = [...jobs].find(([, j]) => !["running", "queued", "starting"].includes(j.status));
    if (!done) break;
    jobs.delete(done[0]);
  }
  chrome.storage.session.set({jobs: [...jobs.values()]}).catch(() => {});
}
function badge(tabId, text, color = "#245de8") {
  chrome.action.setBadgeText({tabId, text}).catch(() => {});
  chrome.action.setBadgeBackgroundColor({tabId, color}).catch(() => {});
}
function connect() {
  clearTimeout(idleTimer);
  if (port) return port;
  hostError = "";
  const connected = chrome.runtime.connectNative(HOST);
  port = connected;
  connected.onMessage.addListener(message => {
    if (message.type === "ready") { output = message.output; return; }
    if (!message.request_id) return;
    const previous = jobs.get(message.request_id) || {};
    if (message.type === "duplicate") {
      message.status = "duplicate";
    }
    const job = {...previous, ...message};
    jobs.set(message.request_id, job);
    if (job.tabId !== undefined) badge(job.tabId,
      job.status === "complete" ? "✓" : job.status === "failed" ? "!" : `${job.progress || 0}%`,
      job.status === "failed" ? "#b3261e" : "#245de8");
    remember();
    if (![...jobs.values()].some(j => ["running", "queued", "starting"].includes(j.status))) {
      idleTimer = setTimeout(() => { if (port === connected) { port = null; connected.disconnect(); } }, 60000);
    }
  });
  connected.onDisconnect.addListener(() => {
    const error = chrome.runtime.lastError?.message;
    if (port !== connected) return;
    port = null;
    hostError = error ? "로컬 처리기에 연결하지 못했습니다. 이 OS의 설치 파일을 다시 실행한 뒤 시도하세요." : "";
    for (const job of jobs.values()) {
      if (["running", "queued", "starting"].includes(job.status)) {
        job.status = "interrupted";
        job.label = hostError || "연결이 종료되었습니다. 저장된 파일로 다시 시작할 수 있습니다.";
        badge(job.tabId, "!", "#b3261e");
      }
    }
    remember();
  });
  connected.postMessage({command: "hello"});
  return connected;
}

async function scoped(tabId) {
  if (tabId < 0) return null;
  let tab;
  try { tab = await chrome.tabs.get(tabId); } catch { return null; }
  const url = tab.url || "";
  let state = tabs.get(tabId);
  if (state && state.pageUrl !== url) { clearTimeout(state.timer); tabs.delete(tabId); state = null; }
  if (!state && player(url)) {
    state = {tabId, pageUrl: url, title: tab.title || "강의", course: "", media: new Map(), started: false, manual: false, drm: false};
    tabs.set(tabId, state);
  }
  return state || null;
}
function schedule(state) {
  if (state.started || state.timer || !state.media.size) return;
  state.timer = setTimeout(() => {
    state.timer = null;
    start(state).catch(error => { hostError = error.message; badge(state.tabId, "!", "#b3261e"); });
  }, 1500);
}
function candidate(state, url, kind, headers = {}) {
  if (!kind || state.started) return;
  const previous = state.media.get(url);
  state.media.set(url, {url, kind, headers: {...previous?.headers, ...headers}});
  if (state.media.size > 80) state.media.delete(state.media.keys().next().value);
  schedule(state);
}
async function currentDocument(details) {
  if (!details.documentId) return true;
  try {
    const frame = await chrome.webNavigation.getFrame({tabId: details.tabId, frameId: details.frameId});
    return frame?.documentId === details.documentId;
  } catch { return false; }
}
async function metadata(state) {
  const tab = await chrome.tabs.get(state.tabId);
  let own = {};
  try { own = await chrome.tabs.sendMessage(state.tabId, {type: "inspect"}, {frameId: 0}); } catch {}
  state.title = own.title || state.title;
  state.course = state.course || own.course || "";
  state.drm ||= own.drm;
  if (player(state.pageUrl) && tab.openerTabId !== undefined) {
    try {
      const parent = await chrome.tabs.get(tab.openerTabId);
      const u = new URL(parent.url);
      if (u.origin === "https://learn.hansung.ac.kr" && u.pathname === "/course/view.php") {
        const result = await chrome.tabs.sendMessage(parent.id, {
          type: "inspect", lectureId: new URL(state.pageUrl).searchParams.get("id")
        }, {frameId: 0});
        state.course = state.course || result.course;
        state.title = result.title || state.title;
      }
    } catch {}
  }
}
async function start(state, retry = Boolean(state.retry)) {
  await restored;
  if (state.started || tabs.get(state.tabId) !== state) return;
  const settings = await chrome.storage.local.get({auto: true, model: "small", outputs: ["mp4", "mp3", "script"]});
  if (!state.manual && !settings.auto) return;
  if (!state.media.size) return;
  const outputs = ["mp4", "mp3", "script"].filter(kind => Array.isArray(settings.outputs) && settings.outputs.includes(kind));
  if (!outputs.length) throw new Error("MP4, MP3, 스크립트 중 하나 이상 선택하세요.");
  state.started = true; // Reserve before any async work: one source per lecture window.
  try {
    await metadata(state);
    if (tabs.get(state.tabId) !== state || (await chrome.tabs.get(state.tabId)).url !== state.pageUrl) return;
    if (state.drm) throw new Error("DRM으로 보호된 동영상은 지원하지 않습니다.");
    const choices = [...state.media.values()];
    const rank = item => item.kind === "hls" ? (/master|playlist/i.test(new URL(item.url).pathname) ? 0 : 1) : item.kind === "dash" ? 2 : 3;
    choices.sort((a, b) => rank(a) - rank(b));
    const media = choices[0];
    const stores = await chrome.cookies.getAllCookieStores();
    const storeId = stores.find(s => s.tabIds.includes(state.tabId))?.id;
    const cookieGroups = await Promise.all([media.url, state.pageUrl].map(url => chrome.cookies.getAll({url, ...(storeId ? {storeId} : {})})));
    const cookies = [...new Map(cookieGroups.flat().map(c => [`${c.domain}|${c.path}|${c.name}`, c])).values()];
    const request_id = crypto.randomUUID();
    const job = {request_id, tabId: state.tabId, title: state.title, course: state.course || "과목 미지정", outputs, status: "starting", progress: 0, label: "로컬 처리기 시작 중"};
    jobs.set(request_id, job);
    state.requestId = request_id;
    remember();
    const headers = {"Referer": state.pageUrl, ...media.headers};
    connect().postMessage({command: "start", request_id, retry, model: settings.model, outputs,
      source: {...media, title: job.title, course: job.course, page_url: state.pageUrl, headers, cookies, drm: state.drm}});
  } catch (error) {
    state.started = false;
    throw error;
  }
}

chrome.webRequest.onBeforeSendHeaders.addListener(details => {
  scoped(details.tabId).then(async state => {
    if (!state || state.started || !await currentDocument(details)) return;
    const headers = {};
    for (const h of details.requestHeaders || []) {
      if (/^(referer|origin|user-agent|authorization)$/i.test(h.name)) headers[h.name] = h.value;
    }
    requests.set(details.requestId, {state, headers});
    if (requests.size > 256) requests.delete(requests.keys().next().value);
  }).catch(() => {});
}, {urls: ["http://*/*", "https://*/*"]}, ["requestHeaders", "extraHeaders"]);
chrome.webRequest.onHeadersReceived.addListener(details => {
  const mime = details.responseHeaders?.find(h => h.name.toLowerCase() === "content-type")?.value || "";
  const kind = mediaKind(details.url, mime);
  const observed = requests.get(details.requestId);
  requests.delete(details.requestId);
  if (!kind || details.statusCode >= 400 || details.statusCode < 200) return;
  scoped(details.tabId).then(async state => {
    if (!state || (observed && observed.state !== state) || !await currentDocument(details)) return;
    candidate(state, details.url, kind, observed?.headers);
  }).catch(() => {});
}, {urls: ["http://*/*", "https://*/*"]}, ["responseHeaders"]);
chrome.webRequest.onErrorOccurred.addListener(d => requests.delete(d.requestId), {urls: ["http://*/*", "https://*/*"]});
chrome.tabs.onUpdated.addListener((tabId, change) => {
  if (change.url) {
    const state = tabs.get(tabId);
    if (state && state.pageUrl !== change.url) { clearTimeout(state.timer); tabs.delete(tabId); badge(tabId, ""); }
  }
});
chrome.tabs.onRemoved.addListener(tabId => { clearTimeout(tabs.get(tabId)?.timer); tabs.delete(tabId); });

chrome.runtime.onMessage.addListener((message, sender, respond) => {
  (async () => {
    await restored;
    const internalPage = sender.id === chrome.runtime.id && sender.url?.startsWith(chrome.runtime.getURL(""));
    if (sender.tab && !internalPage) {
      if (message.type !== "page" || sender.id !== chrome.runtime.id || !/^https?:/.test(sender.url || "")) return;
      const state = await scoped(sender.tab.id);
      if (!state || sender.url !== message.page_url || !await currentDocument({tabId: sender.tab.id, frameId: sender.frameId, documentId: sender.documentId})) return;
      state.drm ||= Boolean(message.drm);
      if (sender.frameId === 0) {
        state.title = message.title || state.title;
        state.course = state.course || message.course || "";
      }
      for (const url of (message.urls || []).slice(0, 80)) candidate(state, url, mediaKind(url));
      return {ok: true};
    }
    if (!internalPage) return;
    if (message.type === "status") {
      const state = tabs.get(message.tabId);
      return {jobs: [...jobs.values()].reverse(), hostError, output, course: state?.course || "", detected: state?.media.size || 0};
    }
    if (message.type === "open_output") { connect().postMessage({command: "open_output"}); return {ok: true}; }
    if (message.type === "cancel") {
      const job = jobs.get(message.request_id);
      if (job?.job_id && port) port.postMessage({command: "cancel", job_id: job.job_id});
      return {ok: true};
    }
    if (message.type === "start") {
      const tab = await chrome.tabs.get(message.tabId);
      if (!/^https?:/.test(tab.url || "")) throw new Error("동영상 페이지에서 실행하세요.");
      let state = await scoped(tab.id);
      const active = state?.requestId && jobs.get(state.requestId);
      if (active && ["running", "queued", "starting"].includes(active.status)) return {ok: true};
      state ||= {tabId: tab.id, pageUrl: tab.url, title: tab.title, media: new Map(), drm: false};
      state.manual = true;
      state.retry = true;
      state.started = false;
      state.course = message.course?.trim().slice(0, 200) || state.course || "";
      tabs.set(tab.id, state);
      await chrome.scripting.executeScript({target: {tabId: tab.id, allFrames: true}, files: ["content.js"]});
      await chrome.tabs.sendMessage(tab.id, {type: "scan"});
      clearTimeout(state.timer); state.timer = null;
      await start(state, true);
      return {ok: true, waiting: !state.media.size};
    }
  })().then(result => respond(result || {}), error => respond({error: error.message}));
  return true;
});
