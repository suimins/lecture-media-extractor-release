chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message?.type === "scan-course-lectures") {
    sendResponse({ ok: true, result: scanCourseLectures() });
    return false;
  }

  if (message?.type === "start-viewer-playback") {
    void startViewerPlayback().then(sendResponse);
    return true;
  }

  return false;
});

function scanCourseLectures() {
  const lectureMap = new Map();
  const items = [...document.querySelectorAll('li.activity.vod.modtype_vod[id^="module-"]')];

  items.forEach((item, sourceIndex) => {
    const moduleId = (item.id || "").replace(/^module-/, "");
    if (!moduleId) {
      return;
    }

    const link = item.querySelector('a[href*="/mod/vod/view.php"]') || item.querySelector("a[href]");
    const viewUrl = link?.href || "";
    const viewerUrl = getViewerUrl(link, viewUrl);
    if (!viewerUrl) {
      return;
    }

    const lecture = {
      moduleId,
      section: getSectionTitle(item),
      title: getLectureTitle(item, moduleId),
      duration: getLectureDuration(item),
      viewerUrl,
      viewUrl,
      sourceIndex
    };

    lectureMap.set(moduleId, lecture);
  });

  const lectures = [...lectureMap.values()].sort((left, right) => left.sourceIndex - right.sourceIndex);
  return {
    site: detectSite(),
    courseTitle: getCourseTitle(),
    pageUrl: location.href,
    title: document.title || "",
    scannedAt: new Date().toISOString(),
    lectures
  };
}

function getViewerUrl(link, viewUrl) {
  const onclick = link?.getAttribute("onclick") || "";
  const popupMatch = onclick.match(/window\.open\(['"]([^'"]+\/mod\/vod\/viewer\.php\?id=\d+[^'"]*)['"]/);
  if (popupMatch) {
    return new URL(popupMatch[1], location.href).href;
  }

  if (viewUrl && /\/mod\/vod\/view\.php\?id=\d+/.test(viewUrl)) {
    return viewUrl.replace("/mod/vod/view.php", "/mod/vod/viewer.php");
  }

  return "";
}

function getLectureTitle(item, moduleId) {
  const titleNode = item.querySelector(".instancename");
  if (!titleNode) {
    return `module-${moduleId}`;
  }

  const clone = titleNode.cloneNode(true);
  clone.querySelectorAll(".accesshide").forEach((node) => node.remove());
  const title = normalizeText(clone.textContent).replace(/\s*(동영상|파일)\s*$/, "").trim();
  return title || `module-${moduleId}`;
}

function getSectionTitle(item) {
  const section = item.closest("li.section");
  if (!section) {
    return "";
  }

  const aria = normalizeText(section.getAttribute("aria-label") || "");
  if (aria) {
    return aria;
  }

  const hidden = normalizeText(section.querySelector(".hidden.sectionname")?.textContent || "");
  if (hidden) {
    return hidden;
  }

  return normalizeText(section.querySelector("h3.sectionname")?.textContent || "");
}

function getLectureDuration(item) {
  const text = normalizeText(item.textContent || "");
  const matches = text.match(/\d{1,2}:\d{2}(?::\d{2})?/g);
  return matches?.at(-1) || "";
}

function detectSite() {
  const host = location.hostname.toLowerCase();
  if (host.includes("kmooc.kr")) {
    return "kmooc";
  }
  if (host.includes("hansung.ac.kr")) {
    return "hansung";
  }
  if (document.body?.className?.toString().includes("coursemos")) {
    return "coursemos";
  }
  return host || "unknown";
}

function getCourseTitle() {
  const candidates = [
    document.querySelector("h1")?.textContent,
    document.querySelector(".course-title")?.textContent,
    document.querySelector(".page-header-headings")?.textContent,
    document.title
  ];
  return normalizeText(candidates.find((value) => normalizeText(value || "")) || "");
}

async function startViewerPlayback() {
  const attempts = [];

  for (const video of document.querySelectorAll("video")) {
    try {
      video.muted = true;
      await video.play();
      attempts.push("video.play ok");
    } catch (error) {
      attempts.push(`video.play failed: ${error.message || String(error)}`);
    }
  }

  const selectors = [
    ".vjs-big-play-button",
    ".vjs-play-control",
    "button[aria-label*='Play']",
    "button[aria-label*='재생']",
    "button[title*='Play']",
    "button[title*='재생']",
    "[role='button'][aria-label*='Play']",
    "[role='button'][aria-label*='재생']"
  ];

  for (const selector of selectors) {
    const button = document.querySelector(selector);
    if (!button) {
      continue;
    }
    try {
      button.click();
      attempts.push(`clicked ${selector}`);
      break;
    } catch (error) {
      attempts.push(`click failed ${selector}: ${error.message || String(error)}`);
    }
  }

  return { ok: true, attempts };
}

function normalizeText(value) {
  return String(value || "").replace(/\s+/g, " ").trim();
}
