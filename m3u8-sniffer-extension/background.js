const STATE_KEY = "snifferState";
const SETTINGS_KEY = "snifferSettings";
const MAX_CANDIDATES_PER_TAB = 30;
const MAX_SUBTITLE_CANDIDATES_PER_TAB = 30;
const AUTO_COPY_MIN_SCORE = 70;
const COURSE_CAPTURE_SETTLE_MS = 5000;
const COURSE_CAPTURE_TIMEOUT_MS = 15000;
const COURSE_CAPTURE_NEXT_DELAY_MS = 800;
const LEGACY_CLI_PREFIX = ".venv/bin/python -m app.main --run-url";

const DEFAULT_SETTINGS = {
  autoCopy: true,
  copyCliCommand: false,
  cliPrefix: ".venv\\Scripts\\python.exe -m app.main --ffmpeg ..\\tools\\ffmpeg\\bin\\ffmpeg.exe --ffprobe ..\\tools\\ffmpeg\\bin\\ffprobe.exe --run-url",
  cliOutDir: "outputs/lecture"
};

const state = {
  tabs: {},
  lastCopiedByTab: {},
  course: {
    scan: null,
    session: null
  }
};

let settings = { ...DEFAULT_SETTINGS };
let offscreenCreating = null;
const courseCaptureTimers = new Map();

const ready = initialize();

chrome.webRequest.onBeforeRequest.addListener(
  (details) => {
    if (!isCandidateUrl(details.url)) {
      return;
    }

    void ready.then(() => captureCandidate(details));
  },
  {
    urls: ["<all_urls>"]
  }
);

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message?.type === "copy-to-clipboard") {
    return false;
  }

  void ready
    .then(() => handleMessage(message, sender))
    .then(sendResponse)
    .catch((error) => sendResponse({ ok: false, error: error.message || String(error) }));
  return true;
});

chrome.tabs.onRemoved.addListener((tabId) => {
  void ready.then(() => {
    delete state.tabs[String(tabId)];
    delete state.lastCopiedByTab[String(tabId)];
    handleCourseCaptureTabRemoved(tabId);
    return persistState();
  });
});

chrome.tabs.onUpdated.addListener((tabId, changeInfo) => {
  if (changeInfo.status !== "complete") {
    return;
  }
  void ready.then(() => maybeStartViewerPlayback(tabId));
});

async function initialize() {
  await restoreState();
  await restoreSettings();
}

async function captureCandidate(details) {
  const bucket = candidateBucket(details.url);
  if (!bucket) {
    return;
  }

  const key = captureKey(details);
  const realTabId = details.tabId >= 0 ? details.tabId : null;
  const tabInfo = realTabId !== null ? await getTabInfo(realTabId) : {};
  const now = new Date().toISOString();
  const currentTabState = normalizeTabState(
    state.tabs[key] || {
      stateKey: key,
      tabId: realTabId,
      pageUrl: tabInfo.url || details.documentUrl || details.initiator || "",
      title: tabInfo.title || "",
      candidates: [],
      subtitleCandidates: [],
      bestId: null,
      bestSubtitleId: null,
      copyStatus: null,
      updatedAt: now
    }
  );

  currentTabState.pageUrl = tabInfo.url || currentTabState.pageUrl;
  currentTabState.title = tabInfo.title || currentTabState.title;
  currentTabState.updatedAt = now;
  currentTabState.stateKey = key;
  currentTabState.tabId = realTabId;

  const listName = bucket === "subtitle" ? "subtitleCandidates" : "candidates";
  const limit = bucket === "subtitle" ? MAX_SUBTITLE_CANDIDATES_PER_TAB : MAX_CANDIDATES_PER_TAB;
  const candidateList = currentTabState[listName];
  const existing = candidateList.find((candidate) => candidate.url === details.url);
  if (existing) {
    existing.seenCount += 1;
    existing.lastSeenAt = now;
    existing.requestType = details.type || existing.requestType;
  } else {
    candidateList.unshift(createCandidate(details, now, bucket));
  }

  currentTabState[listName] = dedupeCandidates(candidateList).slice(0, limit);
  state.tabs[key] = currentTabState;

  await classifyLatestCandidate(currentTabState, details.url, bucket);
  chooseBestCandidate(currentTabState, "candidates", "bestId");
  chooseBestCandidate(currentTabState, "subtitleCandidates", "bestSubtitleId");
  if (realTabId !== null) {
    await updateBadge(realTabId, currentTabState);
  }
  syncCourseSessionFromTab(currentTabState);
  await persistState();
  if (!isCourseCaptureTab(realTabId)) {
    await maybeAutoCopy(currentTabState);
  }
}

function createCandidate(details, detectedAt, bucket) {
  const urlScore = bucket === "subtitle" ? scoreSubtitleUrl(details.url) : scoreUrl(details.url);
  return {
    id: candidateId(details.url),
    bucket,
    url: details.url,
    type: urlScore.type,
    score: urlScore.score,
    reasons: urlScore.reasons,
    durationSeconds: null,
    cueCount: null,
    format: urlScore.format || null,
    sampleText: null,
    requestType: details.type || "unknown",
    initiator: details.initiator || "",
    documentUrl: details.documentUrl || "",
    detectedAt,
    lastSeenAt: detectedAt,
    seenCount: 1,
    classificationStatus: "url_only",
    classificationError: null,
    copiedAt: null
  };
}

async function classifyLatestCandidate(tabState, url, bucket) {
  const listName = bucket === "subtitle" ? "subtitleCandidates" : "candidates";
  const candidate = tabState[listName].find((item) => item.url === url);
  if (!candidate || candidate.classificationStatus === "done") {
    return;
  }

  try {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 5000);
    const response = await fetch(url, {
      credentials: "include",
      cache: "no-store",
      signal: controller.signal
    });
    clearTimeout(timeout);
    if (!response.ok) {
      candidate.classificationStatus = "failed";
      candidate.classificationError = `HTTP ${response.status}`;
      return;
    }

    const contentType = response.headers.get("content-type") || "";
    const disposition = response.headers.get("content-disposition") || "";
    const text = await response.text();
    const contentScore = bucket === "subtitle"
      ? scoreSubtitleText(text, candidate.url, contentType, disposition)
      : scorePlaylistText(text, candidate.url);
    candidate.type = contentScore.type;
    candidate.score = clampScore(candidate.score + contentScore.score);
    candidate.reasons = uniqueStrings([...candidate.reasons, ...contentScore.reasons]);
    candidate.durationSeconds = contentScore.durationSeconds;
    candidate.cueCount = contentScore.cueCount ?? candidate.cueCount;
    candidate.format = contentScore.format || candidate.format;
    candidate.sampleText = contentScore.sampleText || candidate.sampleText;
    candidate.classificationStatus = "done";
    candidate.classificationError = null;
  } catch (error) {
    candidate.classificationStatus = "failed";
    candidate.classificationError = error.message || String(error);
  }
}

function chooseBestCandidate(tabState, listName, bestFieldName) {
  const sorted = [...(tabState[listName] || [])].sort(compareCandidates);
  tabState[bestFieldName] = sorted[0]?.id || null;
}

function compareCandidates(left, right) {
  if (right.score !== left.score) {
    return right.score - left.score;
  }
  if ((right.durationSeconds || 0) !== (left.durationSeconds || 0)) {
    return (right.durationSeconds || 0) - (left.durationSeconds || 0);
  }
  return new Date(right.lastSeenAt).getTime() - new Date(left.lastSeenAt).getTime();
}

async function maybeAutoCopy(tabState) {
  if (!settings.autoCopy || !tabState.bestId) {
    return;
  }

  const best = tabState.candidates.find((candidate) => candidate.id === tabState.bestId);
  if (!best || best.score < AUTO_COPY_MIN_SCORE || best.type === "subtitle" || best.type === "ad_or_preview") {
    tabState.copyStatus = {
      ok: false,
      reason: "Best candidate is ambiguous; manual copy required.",
      updatedAt: new Date().toISOString()
    };
    await persistState();
    return;
  }

  const tabId = String(tabState.stateKey || tabState.tabId);
  const copyText = settings.copyCliCommand ? formatCliCommand(best.url) : best.url;
  if (state.lastCopiedByTab[tabId] === copyText) {
    return;
  }

  const result = await copyToClipboard(copyText);
  tabState.copyStatus = {
    ok: result.ok,
    reason: result.ok ? "Copied best candidate." : result.error,
    copiedValueType: settings.copyCliCommand ? "cli" : "url",
    candidateId: best.id,
    updatedAt: new Date().toISOString()
  };

  if (result.ok) {
    best.copiedAt = tabState.copyStatus.updatedAt;
    state.lastCopiedByTab[tabId] = copyText;
    if (tabState.tabId !== null) {
      await updateBadge(tabState.tabId, tabState);
    }
  }
  await persistState();
}

async function handleMessage(message) {
  if (!message || typeof message.type !== "string") {
    return { ok: false, error: "Invalid message" };
  }

  if (message.type === "get-state") {
    const activeTab = await getActiveTab();
    const activeKey = activeTab ? String(activeTab.id) : null;
    const tabState = activeKey ? state.tabs[activeKey] || null : null;
    return {
      ok: true,
      state: tabState,
      recentStates: recentTabStates(activeKey),
      activeTab: activeTab ? { id: activeTab.id, title: activeTab.title, url: activeTab.url } : null,
      course: normalizeCourseState(state.course),
      settings
    };
  }

  if (message.type === "scan-course") {
    return scanActiveCourse();
  }

  if (message.type === "start-course-capture") {
    return startCourseCapture();
  }

  if (message.type === "pause-course-capture") {
    return pauseCourseCapture();
  }

  if (message.type === "clear-course-capture") {
    return clearCourseCapture();
  }

  if (message.type === "remove-course-lecture") {
    return removeCourseLecture(message);
  }

  if (message.type === "copy-candidate") {
    const tabState = state.tabs[message.stateKey || String(message.tabId)];
    const candidate = findCandidate(tabState, message.candidateId);
    if (!candidate) {
      return { ok: false, error: "Candidate not found" };
    }

    const text = message.asCommand ? formatCliCommand(candidate.url) : candidate.url;
    const result = await copyToClipboard(text);
    tabState.copyStatus = {
      ok: result.ok,
      reason: result.ok ? "Copied manually." : result.error,
      copiedValueType: message.asCommand ? "cli" : "url",
      candidateId: candidate.id,
      updatedAt: new Date().toISOString()
    };
    if (result.ok) {
      candidate.copiedAt = tabState.copyStatus.updatedAt;
    }
    await persistState();
    return { ok: result.ok, error: result.error || null, state: tabState };
  }

  if (message.type === "mark-copied") {
    const tabState = state.tabs[message.stateKey || String(message.tabId)];
    const candidate = findCandidate(tabState, message.candidateId);
    if (!candidate) {
      return { ok: false, error: "Candidate not found" };
    }

    const copiedAt = new Date().toISOString();
    candidate.copiedAt = copiedAt;
    tabState.copyStatus = {
      ok: true,
      reason: "Copied from popup.",
      copiedValueType: message.asCommand ? "cli" : "url",
      candidateId: candidate.id,
      updatedAt: copiedAt
    };
    await persistState();
    return { ok: true, state: tabState };
  }

  if (message.type === "clear-tab") {
    const key = message.stateKey || String(message.tabId);
    const tabId = typeof message.tabId === "number" ? message.tabId : null;
    delete state.tabs[key];
    delete state.lastCopiedByTab[key];
    if (tabId !== null && Number.isFinite(tabId)) {
      await updateBadge(tabId, null);
    }
    await persistState();
    return { ok: true };
  }

  if (message.type === "set-settings") {
    settings = {
      ...settings,
      ...sanitizeSettings(message.settings || {})
    };
    await chrome.storage.session.set({ [SETTINGS_KEY]: settings });
    return { ok: true, settings };
  }

  return { ok: false, error: `Unknown message type: ${message.type}` };
}

async function scanActiveCourse() {
  const activeTab = await getActiveTab();
  if (!activeTab?.id) {
    return { ok: false, error: "No active tab to scan." };
  }

  if (state.course?.session?.status === "running") {
    return { ok: false, error: "Pause the running capture queue before scanning again." };
  }

  try {
    const response = await chrome.tabs.sendMessage(activeTab.id, { type: "scan-course-lectures" });
    if (!response?.ok) {
      return { ok: false, error: response?.error || "Course scan failed." };
    }

    const scan = normalizeCourseScan(response.result, activeTab);
    state.course = {
      scan,
      session: null
    };
    await persistState();
    return { ok: true, course: normalizeCourseState(state.course) };
  } catch (error) {
    return {
      ok: false,
      error: `Course scan failed. Reload the course page after installing the extension. ${error.message || String(error)}`
    };
  }
}

async function startCourseCapture() {
  const scan = state.course?.scan;
  if (!scan?.lectures?.length) {
    return { ok: false, error: "Scan a course page first." };
  }

  let session = state.course.session;
  if (!session || session.status === "done" || session.status === "cancelled") {
    session = createCourseSession(scan);
    state.course.session = session;
  }

  if (session.status === "running") {
    return { ok: true, course: normalizeCourseState(state.course) };
  }

  session.status = "running";
  session.error = null;
  session.updatedAt = new Date().toISOString();
  await persistState();
  setTimeout(() => void runNextCourseLecture(session.id), 0);
  return { ok: true, course: normalizeCourseState(state.course) };
}

async function pauseCourseCapture() {
  const session = state.course?.session;
  if (!session) {
    return { ok: true, course: normalizeCourseState(state.course) };
  }

  clearCourseTimer(session.id);
  const tabId = session.activeCaptureTabId;
  const lecture = session.lectures[session.currentIndex];
  if (lecture && (lecture.status === "opening" || lecture.status === "capturing")) {
    lecture.status = "pending";
  }
  session.status = "paused";
  session.activeCaptureTabId = null;
  session.updatedAt = new Date().toISOString();
  await persistState();
  if (Number.isFinite(tabId)) {
    await closeTabQuietly(tabId);
  }
  return { ok: true, course: normalizeCourseState(state.course) };
}

async function clearCourseCapture() {
  const session = state.course?.session;
  if (session) {
    clearCourseTimer(session.id);
    if (Number.isFinite(session.activeCaptureTabId)) {
      await closeTabQuietly(session.activeCaptureTabId);
    }
  }
  state.course = { scan: null, session: null };
  await persistState();
  return { ok: true, course: normalizeCourseState(state.course) };
}

async function removeCourseLecture(message) {
  const moduleId = String(message.moduleId || "").trim();
  if (!moduleId) {
    return { ok: false, error: "Missing lecture module id." };
  }

  const course = state.course || { scan: null, session: null };
  const session = course.session;
  if (session?.status === "running") {
    return { ok: false, error: "Pause the capture queue before removing lectures." };
  }

  let removed = false;
  if (course.scan?.lectures) {
    const before = course.scan.lectures.length;
    course.scan.lectures = course.scan.lectures.filter((lecture) => lecture.moduleId !== moduleId);
    removed = removed || course.scan.lectures.length !== before;
    reindexScanLectures(course.scan.lectures);
  }

  if (session?.lectures) {
    const removedIndex = session.lectures.findIndex((lecture) => lecture.moduleId === moduleId);
    if (removedIndex !== -1) {
      session.lectures.splice(removedIndex, 1);
      reindexSessionLectures(session.lectures);
      if (removedIndex < session.currentIndex) {
        session.currentIndex -= 1;
      }
      session.currentIndex = Math.max(0, Math.min(session.currentIndex, session.lectures.length));
      session.updatedAt = new Date().toISOString();
      removed = true;
    }
  }

  if (!removed) {
    return { ok: false, error: "Lecture not found in queue." };
  }

  state.course = course;
  await persistState();
  return { ok: true, course: normalizeCourseState(state.course) };
}

function reindexScanLectures(lectures) {
  lectures.forEach((lecture, index) => {
    lecture.sourceIndex = index;
  });
}

function reindexSessionLectures(lectures) {
  lectures.forEach((lecture, index) => {
    lecture.index = index;
    lecture.sourceIndex = index;
  });
}

function normalizeCourseScan(result, activeTab) {
  const seen = new Set();
  const lectures = [];
  for (const item of result?.lectures || []) {
    const moduleId = String(item.moduleId || "").trim();
    const viewerUrl = String(item.viewerUrl || "").trim();
    if (!moduleId || !viewerUrl || seen.has(moduleId)) {
      continue;
    }
    seen.add(moduleId);
    lectures.push({
      moduleId,
      section: String(item.section || "").trim(),
      title: String(item.title || `module-${moduleId}`).trim(),
      duration: String(item.duration || "").trim(),
      viewerUrl,
      viewUrl: String(item.viewUrl || "").trim(),
      sourceIndex: lectures.length
    });
  }

  return {
    site: String(result?.site || "unknown"),
    courseTitle: String(result?.courseTitle || activeTab.title || "").trim(),
    pageUrl: String(result?.pageUrl || activeTab.url || "").trim(),
    title: String(result?.title || activeTab.title || "").trim(),
    sourceTabId: activeTab.id,
    scannedAt: result?.scannedAt || new Date().toISOString(),
    lectures
  };
}

function createCourseSession(scan) {
  const now = new Date().toISOString();
  return {
    id: `course-${Date.now()}`,
    status: "idle",
    site: scan.site,
    courseTitle: scan.courseTitle,
    pageUrl: scan.pageUrl,
    sourceTabId: scan.sourceTabId,
    currentIndex: 0,
    activeCaptureTabId: null,
    startedAt: now,
    updatedAt: now,
    finishedAt: null,
    error: null,
    lectures: scan.lectures.map((lecture, index) => ({
      ...lecture,
      index,
      status: "pending",
      m3u8Url: null,
      subtitleUrl: null,
      playlistCandidate: null,
      subtitleCandidate: null,
      captureTabId: null,
      startedAt: null,
      capturedAt: null,
      error: null
    }))
  };
}

async function runNextCourseLecture(sessionId) {
  const session = state.course?.session;
  if (!session || session.id !== sessionId || session.status !== "running") {
    return;
  }

  if (session.currentIndex >= session.lectures.length) {
    session.status = "done";
    session.activeCaptureTabId = null;
    session.finishedAt = new Date().toISOString();
    session.updatedAt = session.finishedAt;
    await persistState();
    return;
  }

  const lecture = session.lectures[session.currentIndex];
  const now = new Date().toISOString();
  lecture.status = "opening";
  lecture.startedAt = now;
  lecture.error = null;
  session.updatedAt = now;
  await persistState();

  try {
    const tab = await chrome.tabs.create({
      url: lecture.viewerUrl,
      active: false,
      openerTabId: Number.isFinite(session.sourceTabId) ? session.sourceTabId : undefined
    });
    lecture.captureTabId = tab.id;
    lecture.status = "capturing";
    session.activeCaptureTabId = tab.id;
    session.updatedAt = new Date().toISOString();
    await persistState();
    scheduleCourseFinalize(session.id, tab.id, COURSE_CAPTURE_TIMEOUT_MS, "timeout");
    setTimeout(() => void maybeStartViewerPlayback(tab.id), 1200);
  } catch (error) {
    lecture.status = "failed";
    lecture.error = error.message || String(error);
    lecture.capturedAt = new Date().toISOString();
    session.currentIndex += 1;
    session.updatedAt = lecture.capturedAt;
    await persistState();
    setTimeout(() => void runNextCourseLecture(session.id), COURSE_CAPTURE_NEXT_DELAY_MS);
  }
}

function syncCourseSessionFromTab(tabState) {
  const session = state.course?.session;
  if (!session || session.status !== "running" || session.activeCaptureTabId !== tabState.tabId) {
    return;
  }

  const lecture = session.lectures[session.currentIndex];
  if (!lecture) {
    return;
  }

  const playlist = bestPlayableCandidate(tabState);
  const subtitle = bestSubtitleCandidate(tabState);
  if (playlist) {
    lecture.m3u8Url = playlist.url;
    lecture.playlistCandidate = summarizeCandidate(playlist);
  }
  if (subtitle) {
    lecture.subtitleUrl = subtitle.url;
    lecture.subtitleCandidate = summarizeCandidate(subtitle);
  }
  lecture.status = playlist ? "capturing" : lecture.status;
  lecture.error = null;
  session.updatedAt = new Date().toISOString();

  scheduleCourseFinalize(
    session.id,
    tabState.tabId,
    playlist ? COURSE_CAPTURE_SETTLE_MS : COURSE_CAPTURE_TIMEOUT_MS,
    playlist ? "settled" : "timeout"
  );
}

async function finalizeCourseLecture(sessionId, tabId, reason) {
  const session = state.course?.session;
  if (!session || session.id !== sessionId || session.activeCaptureTabId !== tabId) {
    return;
  }

  clearCourseTimer(session.id);
  const lecture = session.lectures[session.currentIndex];
  if (!lecture) {
    return;
  }

  const tabState = state.tabs[String(tabId)];
  const playlist = tabState ? bestPlayableCandidate(tabState) : null;
  const subtitle = tabState ? bestSubtitleCandidate(tabState) : null;
  if (playlist) {
    lecture.m3u8Url = playlist.url;
    lecture.playlistCandidate = summarizeCandidate(playlist);
  }
  if (subtitle) {
    lecture.subtitleUrl = subtitle.url;
    lecture.subtitleCandidate = summarizeCandidate(subtitle);
  }

  const capturedAt = new Date().toISOString();
  lecture.capturedAt = capturedAt;
  if (lecture.m3u8Url) {
    lecture.status = "captured";
    lecture.error = null;
  } else if (lecture.subtitleUrl) {
    lecture.status = "partial";
    lecture.error = "Subtitle detected, but no m3u8 candidate was captured.";
  } else {
    lecture.status = "failed";
    lecture.error = reason === "timeout" ? "No m3u8 candidate detected before timeout." : "No m3u8 candidate detected.";
  }

  session.currentIndex += 1;
  session.activeCaptureTabId = null;
  session.updatedAt = capturedAt;
  await persistState();
  await closeTabQuietly(tabId);

  if (session.status === "running") {
    setTimeout(() => void runNextCourseLecture(session.id), COURSE_CAPTURE_NEXT_DELAY_MS);
  }
}

function handleCourseCaptureTabRemoved(tabId) {
  const session = state.course?.session;
  if (!session || session.status !== "running" || session.activeCaptureTabId !== tabId) {
    return;
  }

  clearCourseTimer(session.id);
  const lecture = session.lectures[session.currentIndex];
  if (lecture) {
    const hasCapture = Boolean(lecture.m3u8Url);
    lecture.status = hasCapture ? "captured" : "failed";
    lecture.error = hasCapture ? null : "Capture tab was closed before an m3u8 candidate was detected.";
    lecture.capturedAt = new Date().toISOString();
  }
  session.currentIndex += 1;
  session.activeCaptureTabId = null;
  session.updatedAt = new Date().toISOString();
  setTimeout(() => void runNextCourseLecture(session.id), COURSE_CAPTURE_NEXT_DELAY_MS);
}

function scheduleCourseFinalize(sessionId, tabId, delayMs, reason) {
  clearCourseTimer(sessionId);
  const timer = setTimeout(() => {
    courseCaptureTimers.delete(sessionId);
    void finalizeCourseLecture(sessionId, tabId, reason);
  }, delayMs);
  courseCaptureTimers.set(sessionId, timer);
}

function clearCourseTimer(sessionId) {
  const timer = courseCaptureTimers.get(sessionId);
  if (timer) {
    clearTimeout(timer);
    courseCaptureTimers.delete(sessionId);
  }
}

function isCourseCaptureTab(tabId) {
  return Number.isFinite(tabId)
    && state.course?.session?.status === "running"
    && state.course.session.activeCaptureTabId === tabId;
}

async function maybeStartViewerPlayback(tabId) {
  if (!isCourseCaptureTab(tabId)) {
    return;
  }

  for (const delay of [0, 1000, 2500]) {
    setTimeout(() => {
      chrome.tabs.sendMessage(tabId, { type: "start-viewer-playback" }).catch(() => {});
    }, delay);
  }
}

function bestPlayableCandidate(tabState) {
  const candidates = [...(normalizeTabState(tabState).candidates || [])].sort(compareCandidates);
  return candidates.find((candidate) => (
    candidate.score >= 50
    && candidate.type !== "subtitle"
    && candidate.type !== "ad_or_preview"
  )) || null;
}

function bestSubtitleCandidate(tabState) {
  const candidates = [...(normalizeTabState(tabState).subtitleCandidates || [])].sort(compareCandidates);
  return candidates.find((candidate) => candidate.score >= 45) || null;
}

function summarizeCandidate(candidate) {
  if (!candidate) {
    return null;
  }
  return {
    id: candidate.id,
    url: candidate.url,
    type: candidate.type,
    score: candidate.score,
    durationSeconds: candidate.durationSeconds,
    cueCount: candidate.cueCount,
    format: candidate.format,
    detectedAt: candidate.detectedAt,
    lastSeenAt: candidate.lastSeenAt,
    reasons: candidate.reasons || []
  };
}

function normalizeCourseState(course) {
  const normalized = course || { scan: null, session: null };
  return {
    scan: normalized.scan || null,
    session: normalized.session || null,
    stats: courseStats(normalized.session, normalized.scan)
  };
}

function courseStats(session, scan) {
  const lectures = session?.lectures || scan?.lectures || [];
  return {
    total: lectures.length,
    captured: lectures.filter((lecture) => lecture.status === "captured").length,
    partial: lectures.filter((lecture) => lecture.status === "partial").length,
    failed: lectures.filter((lecture) => lecture.status === "failed").length,
    pending: lectures.filter((lecture) => !lecture.status || lecture.status === "pending").length,
    running: lectures.filter((lecture) => lecture.status === "opening" || lecture.status === "capturing").length
  };
}

async function closeTabQuietly(tabId) {
  try {
    await chrome.tabs.remove(tabId);
  } catch {
    // The tab may have already been closed by the user or the browser.
  }
}

function sanitizeSettings(nextSettings) {
  const sanitized = {};
  if (typeof nextSettings.autoCopy === "boolean") {
    sanitized.autoCopy = nextSettings.autoCopy;
  }
  if (typeof nextSettings.copyCliCommand === "boolean") {
    sanitized.copyCliCommand = nextSettings.copyCliCommand;
  }
  if (typeof nextSettings.cliPrefix === "string" && nextSettings.cliPrefix.trim()) {
    sanitized.cliPrefix = nextSettings.cliPrefix.trim();
  }
  if (typeof nextSettings.cliOutDir === "string" && nextSettings.cliOutDir.trim()) {
    sanitized.cliOutDir = nextSettings.cliOutDir.trim();
  }
  return sanitized;
}

function isM3u8Url(rawUrl) {
  try {
    const url = new URL(rawUrl);
    return url.protocol === "http:" || url.protocol === "https:"
      ? url.href.toLowerCase().includes(".m3u8")
      : false;
  } catch {
    return false;
  }
}

function isCandidateUrl(rawUrl) {
  return isM3u8Url(rawUrl) || isSubtitleUrl(rawUrl);
}

function isSubtitleUrl(rawUrl) {
  try {
    const url = new URL(rawUrl);
    if (url.protocol !== "http:" && url.protocol !== "https:") {
      return false;
    }
    const text = `${url.pathname}?${url.searchParams.toString()}`.toLowerCase();
    return (
      /\.(vtt|srt|ttml|dfxp|smi|sami)(\?|$)/.test(text)
      || /(^|\/|_|-|\.)(subtitle|subtitles|caption|captions|transcript|timedtext|texttrack)(\/|_|-|\.|\?|=|$)/.test(text)
      || /(^|[?&])(subtitle|subtitles|caption|captions|transcript|timedtext|texttrack)=/.test(text)
    );
  } catch {
    return false;
  }
}

function candidateBucket(rawUrl) {
  if (isM3u8Url(rawUrl)) {
    return "playlist";
  }
  if (isSubtitleUrl(rawUrl)) {
    return "subtitle";
  }
  return null;
}

function captureKey(details) {
  if (details.tabId >= 0) {
    return String(details.tabId);
  }

  const originHint = details.documentUrl || details.initiator || "detached";
  return `global:${candidateId(originHint)}`;
}

function recentTabStates(excludeKey) {
  return Object.values(state.tabs)
    .map(normalizeTabState)
    .filter((tabState) => candidateTotal(tabState) > 0 && tabState.stateKey !== excludeKey)
    .sort((left, right) => new Date(right.updatedAt).getTime() - new Date(left.updatedAt).getTime())
    .slice(0, 5);
}

function normalizeTabState(tabState) {
  tabState.candidates = tabState.candidates || [];
  tabState.subtitleCandidates = tabState.subtitleCandidates || [];
  tabState.bestId = tabState.bestId || null;
  tabState.bestSubtitleId = tabState.bestSubtitleId || null;
  return tabState;
}

function candidateTotal(tabState) {
  return (tabState?.candidates?.length || 0) + (tabState?.subtitleCandidates?.length || 0);
}

function findCandidate(tabState, candidateId) {
  if (!tabState) {
    return null;
  }
  const normalized = normalizeTabState(tabState);
  return (
    normalized.candidates.find((item) => item.id === candidateId)
    || normalized.subtitleCandidates.find((item) => item.id === candidateId)
    || null
  );
}

function scoreUrl(rawUrl) {
  const url = rawUrl.toLowerCase();
  let score = 40;
  let type = "unknown";
  const reasons = ["url contains .m3u8"];

  if (url.includes("master")) {
    score += 30;
    type = "master";
    reasons.push("master-like URL");
  }
  if (url.includes("index") || url.includes("playlist")) {
    score += 8;
    reasons.push("playlist-like URL");
  }
  if (url.includes("chunklist") || url.includes("media")) {
    score += 5;
    type = type === "unknown" ? "media" : type;
    reasons.push("media-like URL");
  }
  if (/(subtitle|subtitles|caption|captions|vtt|webvtt|cc)(\/|_|-|\.|\?)/.test(url)) {
    score -= 60;
    type = "subtitle";
    reasons.push("subtitle-like URL");
  }
  if (/(^|\/|_|-|\.)(ad|ads|preroll|preview|sample)(\/|_|-|\.|\?)/.test(url)) {
    score -= 80;
    type = "ad_or_preview";
    reasons.push("ad/preview-like URL");
  }

  return { score: clampScore(score), type, reasons };
}

function scoreSubtitleUrl(rawUrl) {
  const url = rawUrl.toLowerCase();
  let score = 35;
  let type = "subtitle";
  let format = null;
  const reasons = ["subtitle-like URL"];

  if (/\.vtt(\?|$)/.test(url) || url.includes("webvtt")) {
    score += 45;
    type = "webvtt";
    format = "vtt";
    reasons.push("WebVTT-like URL");
  }
  if (/\.srt(\?|$)/.test(url)) {
    score += 40;
    type = "srt";
    format = "srt";
    reasons.push("SRT-like URL");
  }
  if (/\.(ttml|dfxp)(\?|$)/.test(url)) {
    score += 35;
    type = "ttml";
    format = "ttml";
    reasons.push("TTML-like URL");
  }
  if (/(subtitle|subtitles|caption|captions|timedtext|transcript|texttrack)/.test(url)) {
    score += 25;
    reasons.push("caption endpoint keyword");
  }
  if (/(language|lang)=(ko|kor|kr|ko-kr)/.test(url)) {
    score += 10;
    reasons.push("Korean language parameter");
  }
  if (/(^|\/|_|-|\.)(ad|ads|preroll|preview|sample)(\/|_|-|\.|\?)/.test(url)) {
    score -= 60;
    reasons.push("ad/preview-like URL");
  }

  return { score: clampScore(score), type, format, reasons };
}

function scorePlaylistText(text, rawUrl) {
  const lowerText = text.toLowerCase();
  const urlScore = scoreUrl(rawUrl);
  let score = 0;
  let type = urlScore.type;
  const reasons = [];
  let durationSeconds = null;

  if (!text.trim().startsWith("#EXTM3U")) {
    return {
      score: -70,
      type: "unknown",
      reasons: ["response is not an HLS playlist"],
      durationSeconds
    };
  }

  if (text.includes("#EXT-X-STREAM-INF")) {
    score += 60;
    type = "master";
    reasons.push("master playlist tag");
  }
  if (text.includes("#EXT-X-MEDIA:TYPE=SUBTITLES")) {
    score += 20;
    type = "master";
    reasons.push("subtitle media group available");
  }
  if (text.includes("#EXTINF:")) {
    score += 30;
    type = type === "master" ? type : "media";
    reasons.push("media playlist segments");
    durationSeconds = estimateDuration(text);
  }
  if (text.includes("#EXT-X-ENDLIST")) {
    score += 8;
    reasons.push("VOD playlist");
  }
  if (lowerText.includes(".vtt") || lowerText.includes("webvtt") || lowerText.includes(".ttml")) {
    score -= 45;
    type = "subtitle";
    reasons.push("subtitle segment references");
  }
  if (durationSeconds !== null && durationSeconds < 30) {
    score -= 35;
    reasons.push("very short playlist duration");
  }
  if (durationSeconds !== null && durationSeconds >= 60) {
    score += 10;
    reasons.push("lecture-length candidate");
  }

  return { score, type, reasons, durationSeconds };
}

function scoreSubtitleText(text, rawUrl, contentType, disposition) {
  const normalizedText = stripBom(text).trimStart();
  const lowerText = normalizedText.toLowerCase();
  const lowerContentType = contentType.toLowerCase();
  const lowerDisposition = disposition.toLowerCase();
  const urlScore = scoreSubtitleUrl(rawUrl);
  let score = 0;
  let type = urlScore.type;
  let format = urlScore.format;
  const reasons = [];
  let cueCount = null;
  let sampleText = null;

  if (lowerDisposition.includes(".vtt")) {
    score += 25;
    type = "webvtt";
    format = "vtt";
    reasons.push("filename is .vtt");
  }
  if (lowerContentType.includes("vtt") || lowerContentType.includes("webvtt")) {
    score += 25;
    type = "webvtt";
    format = "vtt";
    reasons.push("WebVTT content type");
  }
  if (normalizedText.startsWith("WEBVTT")) {
    score += 75;
    type = "webvtt";
    format = "vtt";
    cueCount = countCueTimestamps(normalizedText);
    sampleText = subtitleSampleText(normalizedText);
    reasons.push("WEBVTT header");
  } else if (/\d{2}:\d{2}:\d{2},\d{3}\s+-->\s+\d{2}:\d{2}:\d{2},\d{3}/.test(normalizedText)) {
    score += 70;
    type = "srt";
    format = "srt";
    cueCount = countCueTimestamps(normalizedText);
    sampleText = subtitleSampleText(normalizedText);
    reasons.push("SRT timestamp cues");
  } else if (lowerText.includes("<tt") && lowerText.includes("</tt>")) {
    score += 55;
    type = "ttml";
    format = "ttml";
    sampleText = subtitleSampleText(normalizedText);
    reasons.push("TTML XML body");
  } else if (looksLikeJson(normalizedText, lowerContentType)) {
    score += 35;
    type = "json";
    format = "json";
    sampleText = subtitleSampleText(normalizedText);
    reasons.push("JSON-like subtitle response");
  } else if (lowerText.includes("<html") || lowerText.includes("<!doctype html")) {
    score -= 80;
    type = "html";
    reasons.push("HTML response, probably not subtitles");
  } else if (normalizedText.includes("-->")) {
    score += 30;
    cueCount = countCueTimestamps(normalizedText);
    sampleText = subtitleSampleText(normalizedText);
    reasons.push("subtitle timestamp arrow");
  } else {
    score -= 20;
    reasons.push("subtitle response not recognized");
  }

  if (cueCount !== null && cueCount >= 5) {
    score += 15;
    reasons.push("multiple subtitle cues");
  }

  return {
    score,
    type,
    format,
    reasons,
    durationSeconds: null,
    cueCount,
    sampleText
  };
}

function stripBom(text) {
  return text.replace(/^\uFEFF/, "");
}

function countCueTimestamps(text) {
  const matches = text.match(/\d{2}:\d{2}:\d{2}[.,]\d{3}\s+-->\s+\d{2}:\d{2}:\d{2}[.,]\d{3}/g);
  return matches ? matches.length : 0;
}

function subtitleSampleText(text) {
  const lines = stripBom(text)
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter((line) => line && line !== "WEBVTT" && !/^\d+$/.test(line) && !line.includes("-->"));
  return lines.slice(0, 2).join(" ").slice(0, 180) || null;
}

function looksLikeJson(text, contentType) {
  return contentType.includes("json") || text.startsWith("{") || text.startsWith("[");
}

function estimateDuration(text) {
  let total = 0;
  let found = false;
  for (const line of text.split(/\r?\n/)) {
    const match = line.match(/^#EXTINF:([0-9.]+)/);
    if (!match) {
      continue;
    }
    const value = Number.parseFloat(match[1]);
    if (Number.isFinite(value)) {
      total += value;
      found = true;
    }
  }
  return found ? total : null;
}

function dedupeCandidates(candidates) {
  const seen = new Set();
  const result = [];
  for (const candidate of candidates) {
    if (seen.has(candidate.url)) {
      continue;
    }
    seen.add(candidate.url);
    result.push(candidate);
  }
  return result;
}

function uniqueStrings(values) {
  return [...new Set(values.filter(Boolean))];
}

function clampScore(score) {
  return Math.max(0, Math.min(120, Math.round(score)));
}

function candidateId(url) {
  let hash = 2166136261;
  for (let index = 0; index < url.length; index += 1) {
    hash ^= url.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return `m3u8-${(hash >>> 0).toString(16)}`;
}

async function copyToClipboard(text) {
  try {
    await ensureOffscreenDocument();
    const response = await chrome.runtime.sendMessage({ type: "copy-to-clipboard", text });
    if (response?.ok) {
      return { ok: true };
    }
    return { ok: false, error: response?.error || "Clipboard write failed" };
  } catch (error) {
    return { ok: false, error: error.message || String(error) };
  }
}

async function ensureOffscreenDocument() {
  const offscreenUrl = chrome.runtime.getURL("offscreen.html");
  const contexts = await chrome.runtime.getContexts({
    contextTypes: ["OFFSCREEN_DOCUMENT"],
    documentUrls: [offscreenUrl]
  });
  if (contexts.length > 0) {
    return;
  }

  if (offscreenCreating) {
    await offscreenCreating;
    return;
  }

  offscreenCreating = chrome.offscreen.createDocument({
    url: "offscreen.html",
    reasons: ["CLIPBOARD"],
    justification: "Copy detected m3u8 URLs to the clipboard."
  });

  try {
    await offscreenCreating;
  } finally {
    offscreenCreating = null;
  }
}

function formatCliCommand(url) {
  return `${settings.cliPrefix} ${shellQuote(url)} --out ${shellQuote(settings.cliOutDir)}`;
}

function shellQuote(value) {
  return `"${String(value).replaceAll("\\", "\\\\").replaceAll('"', '\\"')}"`;
}

async function getActiveTab() {
  const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
  return tabs[0] || null;
}

async function getTabInfo(tabId) {
  try {
    return await chrome.tabs.get(tabId);
  } catch {
    return {};
  }
}

async function updateBadge(tabId, tabState) {
  if (!Number.isFinite(tabId) || tabId < 0) {
    return;
  }

  const count = candidateTotal(normalizeTabState(tabState || {}));
  await chrome.action.setBadgeBackgroundColor({ tabId, color: "#2563eb" });
  await chrome.action.setBadgeText({ tabId, text: count ? String(count) : "" });
}

async function restoreState() {
  const stored = await chrome.storage.session.get(STATE_KEY);
  if (stored[STATE_KEY]?.tabs) {
    state.tabs = stored[STATE_KEY].tabs;
    for (const tabState of Object.values(state.tabs)) {
      normalizeTabState(tabState);
    }
  }
  if (stored[STATE_KEY]?.lastCopiedByTab) {
    state.lastCopiedByTab = stored[STATE_KEY].lastCopiedByTab;
  }
  if (stored[STATE_KEY]?.course) {
    state.course = {
      scan: stored[STATE_KEY].course.scan || null,
      session: stored[STATE_KEY].course.session || null
    };
    if (state.course.session?.status === "running") {
      state.course.session.status = "paused";
      state.course.session.activeCaptureTabId = null;
    }
  }
}

async function restoreSettings() {
  const stored = await chrome.storage.session.get(SETTINGS_KEY);
  settings = {
    ...DEFAULT_SETTINGS,
    ...(stored[SETTINGS_KEY] || {})
  };
  if (settings.cliPrefix === LEGACY_CLI_PREFIX) {
    settings.cliPrefix = DEFAULT_SETTINGS.cliPrefix;
    await chrome.storage.session.set({ [SETTINGS_KEY]: settings });
  }
}

async function persistState() {
  await chrome.storage.session.set({
    [STATE_KEY]: {
      tabs: state.tabs,
      lastCopiedByTab: state.lastCopiedByTab,
      course: state.course
    }
  });
}
