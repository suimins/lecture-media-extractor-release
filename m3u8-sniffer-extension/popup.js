let currentState = null;
let currentCourse = null;
let currentSettings = null;
let showingRecentFallback = false;
let refreshTimer = null;

const elements = {
  tabTitle: document.querySelector("#tabTitle"),
  refreshButton: document.querySelector("#refreshButton"),
  statusLabel: document.querySelector("#statusLabel"),
  statusDetail: document.querySelector("#statusDetail"),
  autoCopyToggle: document.querySelector("#autoCopyToggle"),
  courseMeta: document.querySelector("#courseMeta"),
  courseStatus: document.querySelector("#courseStatus"),
  courseList: document.querySelector("#courseList"),
  scanCourseButton: document.querySelector("#scanCourseButton"),
  startQueueButton: document.querySelector("#startQueueButton"),
  pauseQueueButton: document.querySelector("#pauseQueueButton"),
  copyManifestButton: document.querySelector("#copyManifestButton"),
  clearCourseButton: document.querySelector("#clearCourseButton"),
  bestPanel: document.querySelector("#bestPanel"),
  bestMeta: document.querySelector("#bestMeta"),
  bestUrl: document.querySelector("#bestUrl"),
  bestSubtitlePanel: document.querySelector("#bestSubtitlePanel"),
  bestSubtitleMeta: document.querySelector("#bestSubtitleMeta"),
  bestSubtitleUrl: document.querySelector("#bestSubtitleUrl"),
  copyUrlButton: document.querySelector("#copyUrlButton"),
  copyCommandButton: document.querySelector("#copyCommandButton"),
  copySubtitleButton: document.querySelector("#copySubtitleButton"),
  candidateList: document.querySelector("#candidateList"),
  subtitleList: document.querySelector("#subtitleList"),
  clearButton: document.querySelector("#clearButton")
};

document.addEventListener("DOMContentLoaded", () => {
  elements.refreshButton.addEventListener("click", loadState);
  elements.autoCopyToggle.addEventListener("change", updateAutoCopy);
  elements.scanCourseButton.addEventListener("click", scanCourse);
  elements.startQueueButton.addEventListener("click", startQueue);
  elements.pauseQueueButton.addEventListener("click", pauseQueue);
  elements.copyManifestButton.addEventListener("click", copyManifest);
  elements.clearCourseButton.addEventListener("click", clearCourse);
  elements.copyUrlButton.addEventListener("click", () => copyBest(false));
  elements.copyCommandButton.addEventListener("click", () => copyBest(true));
  elements.copySubtitleButton.addEventListener("click", copyBestSubtitle);
  elements.clearButton.addEventListener("click", clearCurrentTab);
  void loadState();
});

async function loadState() {
  const response = await chrome.runtime.sendMessage({ type: "get-state" });
  if (!response?.ok) {
    renderError(response?.error || "Could not read state.");
    return;
  }

  currentState = response.state;
  showingRecentFallback = false;
  if (!currentState && response.recentStates?.length) {
    currentState = response.recentStates[0];
    showingRecentFallback = true;
  }
  currentCourse = response.course;
  currentSettings = response.settings;
  render();
}

function render() {
  elements.autoCopyToggle.checked = Boolean(currentSettings?.autoCopy);

  if (!currentState) {
    elements.tabTitle.textContent = "Current tab";
    elements.statusLabel.textContent = "Idle";
    elements.statusDetail.textContent = "Play a lecture video to detect media.";
    elements.bestPanel.classList.add("hidden");
    elements.bestSubtitlePanel.classList.add("hidden");
    elements.candidateList.className = "candidate-list empty";
    elements.candidateList.textContent = "No m3u8 detected yet.";
    elements.subtitleList.className = "candidate-list empty";
    elements.subtitleList.textContent = "No subtitle detected yet.";
  } else {
    const titlePrefix = showingRecentFallback ? "Recent: " : "";
    elements.tabTitle.textContent = `${titlePrefix}${currentState.title || currentState.pageUrl || "Current tab"}`;
    const best = getBestCandidate();
    const bestSubtitle = getBestSubtitleCandidate();
    const playlistCount = currentState.candidates?.length || 0;
    const subtitleCount = currentState.subtitleCandidates?.length || 0;
    const count = playlistCount + subtitleCount;
    elements.statusLabel.textContent = showingRecentFallback
      ? `Recent tab has ${count} candidates`
      : `m3u8 ${playlistCount} / subtitles ${subtitleCount}`;
    elements.statusDetail.textContent = statusDetailText(currentState);

    if (best) {
      elements.bestPanel.classList.remove("hidden");
      elements.bestUrl.value = best.url;
      elements.bestMeta.textContent = `${best.type} / ${best.score}`;
    } else {
      elements.bestPanel.classList.add("hidden");
    }

    if (bestSubtitle) {
      elements.bestSubtitlePanel.classList.remove("hidden");
      elements.bestSubtitleUrl.value = bestSubtitle.url;
      elements.bestSubtitleMeta.textContent = `${bestSubtitle.type} / ${bestSubtitle.score}`;
    } else {
      elements.bestSubtitlePanel.classList.add("hidden");
    }

    renderCandidates();
    renderSubtitleCandidates();
  }

  renderCourse();
  scheduleRefreshIfRunning();
}

function renderCourse() {
  const scan = currentCourse?.scan || null;
  const session = currentCourse?.session || null;
  const stats = currentCourse?.stats || {};
  const lectures = session?.lectures || scan?.lectures || [];
  const status = session?.status || (scan ? "scanned" : "idle");

  elements.courseMeta.textContent = status;
  if (!lectures.length) {
    elements.courseStatus.textContent = "Scan a K-MOOC or Hansung course page first.";
    elements.courseList.className = "course-list empty";
    elements.courseList.textContent = "No lectures scanned.";
  } else {
    const title = session?.courseTitle || scan?.courseTitle || scan?.title || "Course";
    elements.courseStatus.textContent = `${title} / ${stats.captured || 0} captured, ${stats.failed || 0} failed, ${lectures.length} total`;
    elements.courseList.className = "course-list";
    elements.courseList.replaceChildren(...lectures.map((lecture, index) => createCourseNode(lecture, index, session)));
  }

  const running = session?.status === "running";
  elements.startQueueButton.disabled = !lectures.length || running;
  elements.pauseQueueButton.disabled = !running;
  elements.copyManifestButton.disabled = !lectures.length;
}

function createCourseNode(lecture, index, session) {
  const node = document.createElement("article");
  const status = lecture.status || "pending";
  const active = session?.status === "running" && index === session.currentIndex;
  const running = session?.status === "running";
  node.className = `course-item ${active ? "active" : ""} ${status === "captured" ? "done" : ""} ${status === "failed" ? "failed" : ""}`;

  const top = document.createElement("div");
  top.className = "course-item-top";

  const title = document.createElement("div");
  title.className = "course-title";
  title.textContent = `${index + 1}. ${lecture.title || lecture.moduleId}`;

  const removeButton = document.createElement("button");
  removeButton.className = "remove-course-button";
  removeButton.type = "button";
  removeButton.textContent = "Remove";
  removeButton.disabled = running;
  removeButton.title = running ? "Pause the queue before removing lectures." : "Remove this lecture from the queue.";
  removeButton.addEventListener("click", () => removeCourseLecture(lecture.moduleId));

  top.append(title, removeButton);

  const meta = document.createElement("div");
  meta.className = "course-meta";
  const pieces = [
    lecture.section,
    lecture.duration,
    status,
    lecture.m3u8Url ? "m3u8" : null,
    lecture.subtitleUrl ? "subtitle" : null
  ].filter(Boolean);
  meta.textContent = pieces.join(" / ");

  node.append(top, meta);
  return node;
}

function renderCandidates() {
  const candidates = currentState?.candidates || [];
  if (!candidates.length) {
    elements.candidateList.className = "candidate-list empty";
    elements.candidateList.textContent = "No m3u8 detected yet.";
    return;
  }

  elements.candidateList.className = "candidate-list";
  elements.candidateList.replaceChildren(
    ...candidates
      .slice()
      .sort(compareCandidates)
      .map((candidate) => createCandidateNode(candidate, { bestId: currentState.bestId, allowCommand: true }))
  );
}

function renderSubtitleCandidates() {
  const candidates = currentState?.subtitleCandidates || [];
  if (!candidates.length) {
    elements.subtitleList.className = "candidate-list empty";
    elements.subtitleList.textContent = "No subtitle detected yet.";
    return;
  }

  elements.subtitleList.className = "candidate-list";
  elements.subtitleList.replaceChildren(
    ...candidates
      .slice()
      .sort(compareCandidates)
      .map((candidate) => createCandidateNode(candidate, { bestId: currentState.bestSubtitleId, allowCommand: false }))
  );
}

function createCandidateNode(candidate, options) {
  const node = document.createElement("article");
  node.className = candidate.id === options.bestId ? "candidate best-candidate" : "candidate";

  const top = document.createElement("div");
  top.className = "candidate-top";

  const title = document.createElement("div");
  title.className = "candidate-title";
  title.textContent = candidate.id === options.bestId ? `Best / ${candidate.type}` : candidate.type;

  const score = document.createElement("div");
  score.className = "candidate-score";
  score.textContent = String(candidate.score);
  top.append(title, score);

  const url = document.createElement("span");
  url.className = "candidate-url";
  url.textContent = candidate.url;

  const meta = document.createElement("div");
  meta.className = "candidate-meta";
  meta.append(
    metaText(`${candidate.requestType}`),
    metaText(`seen ${candidate.seenCount}`),
    metaText(candidate.classificationStatus)
  );
  if (candidate.durationSeconds) {
    meta.append(metaText(formatDuration(candidate.durationSeconds)));
  }
  if (candidate.cueCount) {
    meta.append(metaText(`${candidate.cueCount} cues`));
  }
  if (candidate.format) {
    meta.append(metaText(candidate.format));
  }

  const copyUrl = document.createElement("button");
  copyUrl.className = "mini-button";
  copyUrl.type = "button";
  copyUrl.textContent = options.allowCommand ? "Copy URL" : "Copy Subtitle URL";
  copyUrl.addEventListener("click", () => copyCandidate(candidate.id, false));
  meta.append(copyUrl);

  if (options.allowCommand) {
    const copyCommand = document.createElement("button");
    copyCommand.className = "mini-button";
    copyCommand.type = "button";
    copyCommand.textContent = "Copy CLI";
    copyCommand.addEventListener("click", () => copyCandidate(candidate.id, true));
    meta.append(copyCommand);
  }

  node.append(top, url, meta);
  if (candidate.sampleText) {
    const sample = document.createElement("p");
    sample.className = "candidate-sample";
    sample.textContent = candidate.sampleText;
    node.append(sample);
  }
  return node;
}

function metaText(text) {
  const span = document.createElement("span");
  span.textContent = text;
  return span;
}

async function scanCourse() {
  const response = await chrome.runtime.sendMessage({ type: "scan-course" });
  if (!response?.ok) {
    renderError(friendlyCourseError(response?.error || "Course scan failed."));
    return;
  }
  currentCourse = response.course;
  render();
}

async function startQueue() {
  const response = await chrome.runtime.sendMessage({ type: "start-course-capture" });
  if (!response?.ok) {
    renderError(response?.error || "Could not start queue.");
    return;
  }
  currentCourse = response.course;
  render();
}

async function pauseQueue() {
  const response = await chrome.runtime.sendMessage({ type: "pause-course-capture" });
  if (response?.ok) {
    currentCourse = response.course;
    render();
  }
}

async function clearCourse() {
  const response = await chrome.runtime.sendMessage({ type: "clear-course-capture" });
  if (response?.ok) {
    currentCourse = response.course;
    render();
  }
}

async function removeCourseLecture(moduleId) {
  const response = await chrome.runtime.sendMessage({ type: "remove-course-lecture", moduleId });
  if (!response?.ok) {
    renderError(response?.error || "Could not remove lecture.");
    return;
  }
  currentCourse = response.course;
  render();
}

async function copyManifest() {
  const manifest = buildManifest();
  if (!manifest) {
    return;
  }
  const result = await writeClipboard(JSON.stringify(manifest, null, 2));
  if (!result.ok) {
    renderError(result.error || "Could not copy JSON.");
    return;
  }
  elements.courseStatus.textContent = "Capture JSON copied.";
}

function buildManifest() {
  const scan = currentCourse?.scan || null;
  const session = currentCourse?.session || null;
  if (!scan && !session) {
    return null;
  }
  return {
    schema: "m3u8-sniffer-course-capture-v1",
    exportedAt: new Date().toISOString(),
    scan,
    session,
    lectures: session?.lectures || scan?.lectures || []
  };
}

function scheduleRefreshIfRunning() {
  if (refreshTimer) {
    clearTimeout(refreshTimer);
    refreshTimer = null;
  }
  if (currentCourse?.session?.status === "running") {
    refreshTimer = setTimeout(() => void loadState(), 1500);
  }
}

function getBestCandidate() {
  return currentState?.candidates?.find((candidate) => candidate.id === currentState.bestId) || null;
}

function getBestSubtitleCandidate() {
  return currentState?.subtitleCandidates?.find((candidate) => candidate.id === currentState.bestSubtitleId) || null;
}

async function copyBest(asCommand) {
  const best = getBestCandidate();
  if (!best) {
    return;
  }
  await copyCandidate(best.id, asCommand);
}

async function copyBestSubtitle() {
  const best = getBestSubtitleCandidate();
  if (!best) {
    return;
  }
  await copyCandidate(best.id, false);
}

async function copyCandidate(candidateId, asCommand) {
  const candidate = findCandidate(candidateId);
  if (!candidate) {
    renderError("Candidate not found.");
    return;
  }

  const text = asCommand ? formatCliCommand(candidate.url) : candidate.url;
  const copyResult = await writeClipboard(text);
  if (!copyResult.ok) {
    renderError(copyResult.error || "Copy failed.");
    return;
  }

  const response = await chrome.runtime.sendMessage({
    type: "mark-copied",
    stateKey: currentState.stateKey,
    tabId: currentState.tabId,
    candidateId,
    asCommand
  });

  if (!response?.ok) {
    currentState.copyStatus = {
      ok: true,
      reason: "Copied to clipboard.",
      candidateId,
      copiedValueType: asCommand ? "cli" : "url",
      updatedAt: new Date().toISOString()
    };
    candidate.copiedAt = currentState.copyStatus.updatedAt;
    render();
    return;
  }

  currentState = response.state;
  render();
}

function findCandidate(candidateId) {
  return (
    currentState?.candidates?.find((item) => item.id === candidateId)
    || currentState?.subtitleCandidates?.find((item) => item.id === candidateId)
    || null
  );
}

async function writeClipboard(text) {
  try {
    await navigator.clipboard.writeText(text);
    return { ok: true };
  } catch (error) {
    return fallbackClipboardWrite(text, error);
  }
}

function fallbackClipboardWrite(text, originalError) {
  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.setAttribute("readonly", "readonly");
  textarea.style.position = "fixed";
  textarea.style.top = "-1000px";
  textarea.style.left = "-1000px";
  document.body.append(textarea);
  textarea.focus();
  textarea.select();

  try {
    const ok = document.execCommand("copy");
    return ok ? { ok: true } : { ok: false, error: "document copy command failed" };
  } catch (error) {
    return { ok: false, error: error.message || originalError?.message || String(error) };
  } finally {
    textarea.remove();
  }
}

function formatCliCommand(url) {
  const prefix = currentSettings?.cliPrefix || ".venv\\Scripts\\python.exe -m app.main --ffmpeg ..\\tools\\ffmpeg\\bin\\ffmpeg.exe --ffprobe ..\\tools\\ffmpeg\\bin\\ffprobe.exe --run-url";
  const outDir = currentSettings?.cliOutDir || "outputs/lecture";
  return `${prefix} ${shellQuote(url)} --out ${shellQuote(outDir)}`;
}

function shellQuote(value) {
  return `"${String(value).replaceAll("\\", "\\\\").replaceAll('"', '\\"')}"`;
}

async function updateAutoCopy() {
  const response = await chrome.runtime.sendMessage({
    type: "set-settings",
    settings: { autoCopy: elements.autoCopyToggle.checked }
  });
  if (response?.ok) {
    currentSettings = response.settings;
  }
}

async function clearCurrentTab() {
  if (!currentState) {
    return;
  }
  await chrome.runtime.sendMessage({ type: "clear-tab", stateKey: currentState.stateKey, tabId: currentState.tabId });
  await loadState();
}

function renderError(message) {
  elements.statusLabel.textContent = "Error";
  elements.statusDetail.textContent = message;
}

function friendlyCourseError(message) {
  if (String(message).includes("Unknown message type: scan-course")) {
    return "Reload the extension, then reload the course page. The popup is newer than the background worker.";
  }
  return message;
}

function statusDetailText(tabState) {
  if (!tabState.copyStatus) {
    return "Detected candidates are kept in this browser session.";
  }
  return tabState.copyStatus.ok ? "Copied to clipboard." : tabState.copyStatus.reason;
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

function formatDuration(seconds) {
  const rounded = Math.round(seconds);
  const minutes = Math.floor(rounded / 60);
  const rest = rounded % 60;
  return `${minutes}:${String(rest).padStart(2, "0")}`;
}
