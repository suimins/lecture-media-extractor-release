chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (!message || message.type !== "copy-to-clipboard") {
    return false;
  }

  void navigator.clipboard.writeText(message.text)
    .then(() => sendResponse({ ok: true }))
    .catch((error) => sendResponse({ ok: false, error: error.message || String(error) }));
  return true;
});
