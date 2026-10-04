const $ = id => document.getElementById(id);
let tabId;
let starting = false;
let settingsSaved = Promise.resolve();
const outputKinds = ["mp4", "mp3", "script"];
function selectedOutputs() { return outputKinds.filter(kind => $(kind).checked); }
function updateSelection() {
  const outputs = selectedOutputs();
  $("start").disabled = starting || !outputs.length;
  $("model").disabled = !outputs.includes("script");
  $("selection-hint").textContent = outputs.length
    ? "선택한 파일은 저장 후 유지됩니다. 변경한 선택은 다음 작업부터 적용됩니다."
    : "MP4, MP3, 스크립트 중 하나 이상 선택하세요.";
}
async function send(message) {
  const response = await chrome.runtime.sendMessage(message);
  if (response.error) throw new Error(response.error);
  return response;
}
function notice(error) { $("notice").textContent = error.message || String(error); }
async function refresh() {
  const status = await send({type: "status", tabId});
  if (status.hostError) notice(status.hostError);
  $("output").textContent = status.output;
  if (!$("course").value && document.activeElement !== $("course")) $("course").value = status.course;
  $("jobs").replaceChildren();
  if (!status.jobs.length) $("jobs").textContent = "영상 감지 대기 중 · 강의 재생 창을 열어주세요.";
  for (const job of status.jobs.slice(0, 8)) {
    const row = document.createElement("div"); row.className = "job";
    const title = document.createElement("strong"); title.textContent = job.title;
    const label = document.createElement("small"); label.textContent = `${job.course || ""} · ${job.label}`;
    const bar = document.createElement("progress"); bar.max = 100; bar.value = job.progress || 0;
    row.append(title, label, bar);
    if (job.job_id && ["running", "queued"].includes(job.status)) {
      const cancel = document.createElement("button"); cancel.textContent = "중단";
      cancel.onclick = () => send({type: "cancel", request_id: job.request_id}).catch(notice);
      row.append(cancel);
    }
    $("jobs").append(row);
  }
}
$("auto").onchange = () => chrome.storage.local.set({auto: $("auto").checked});
$("model").onchange = () => chrome.storage.local.set({model: $("model").value});
for (const kind of outputKinds) {
  $(kind).onchange = () => {
    updateSelection();
    const outputs = selectedOutputs();
    settingsSaved = settingsSaved.catch(() => {}).then(() => chrome.storage.local.set({outputs}));
    settingsSaved.catch(notice);
  };
}
$("open").onclick = () => send({type: "open_output"}).catch(notice);
$("start").onclick = async () => {
  starting = true; updateSelection(); $("notice").textContent = "";
  try {
    await settingsSaved;
    if (!selectedOutputs().length) throw new Error("다운로드할 파일을 하나 이상 선택하세요.");
    const result = await send({type: "start", tabId, course: $("course").value});
    if (result.waiting) $("notice").textContent = "영상을 재생하면 자동으로 시작합니다. 감지되지 않으면 페이지를 새로고침한 뒤 다시 누르세요.";
    await refresh();
  } catch (error) { notice(error); }
  finally { starting = false; updateSelection(); }
};
(async () => {
  tabId = (await chrome.tabs.query({active: true, currentWindow: true}))[0]?.id;
  const settings = await chrome.storage.local.get({auto: true, model: "small", outputs: outputKinds});
  $("auto").checked = settings.auto; $("model").value = settings.model;
  for (const kind of outputKinds) $(kind).checked = Array.isArray(settings.outputs) && settings.outputs.includes(kind);
  updateSelection();
  await refresh();
  setInterval(() => refresh().catch(notice), 1000);
})().catch(notice);
