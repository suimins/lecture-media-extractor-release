(() => {
  if (globalThis.__lectureScriptInstalled) return;
  globalThis.__lectureScriptInstalled = true;
  let drm = false;
  document.addEventListener("encrypted", () => { drm = true; scan(); }, true);

  function info(lectureId) {
    const coursePage = location.pathname === "/course/view.php";
    const courseLink = [...document.querySelectorAll('a[href*="/course/view.php"]')]
      .find(a => a.textContent.trim() && !/^(홈|Home|강의실|과목)$/i.test(a.textContent.trim()));
    const course = (coursePage ? document.querySelector("h1")?.textContent : courseLink?.textContent) || "";
    const lectureLink = lectureId ? [...document.querySelectorAll('a[href*="/mod/vod/view.php"]')]
      .find(a => new URL(a.href).searchParams.get("id") === lectureId) : null;
    const title = lectureLink?.querySelector(".instancename")?.textContent || lectureLink?.textContent
      || document.querySelector(".vod-title, .viewer-title, h1, h2")?.textContent || document.title;
    const urls = new Set();
    for (const video of document.querySelectorAll("video")) {
      for (const src of [video.currentSrc, video.src, ...[...video.querySelectorAll("source")].map(s => s.src)]) {
        if (/^https?:/.test(src || "")) urls.add(src);
      }
    }
    for (const entry of performance.getEntriesByType("resource")) {
      if (/\.(m3u8|mpd|mp4|webm)(?:[?#]|$)/i.test(entry.name)) urls.add(entry.name);
    }
    return {course: course.trim().slice(0, 200), title: title.trim().slice(0, 200),
      urls: [...urls].slice(-60), drm, page_url: location.href};
  }

  function scan() {
    if (!chrome.runtime?.id) return;
    chrome.runtime.sendMessage({type: "page", ...info()}).catch(() => {});
  }
  chrome.runtime.onMessage.addListener((message, _sender, respond) => {
    if (message.type === "inspect") respond(info(message.lectureId));
    if (message.type === "scan") { scan(); respond({ok: true}); }
  });
  document.addEventListener("DOMContentLoaded", scan, {once: true});
  document.addEventListener("loadedmetadata", scan, true);
  document.addEventListener("play", scan, true);
  scan();
  setInterval(scan, 5000);
})();
