import {mediaType, mergeCapture} from "./media.js";

const filter = {urls: ["http://*/*", "https://*/*"]};
const referers = new Map();
let writes = Promise.resolve();
function enqueue(work) {
  writes = writes.then(work).catch(error => console.warn("AVDownloader 捕获更新失败", error.message));
}
const tabKey = id => `tab:${id}`;

chrome.webRequest.onBeforeSendHeaders.addListener(details => {
  if (details.tabId < 0) return;
  const referer = (details.requestHeaders || []).find(h => h.name.toLowerCase() === "referer")?.value || "";
  if (referers.size >= 1000) referers.delete(referers.keys().next().value);
  referers.set(details.requestId, referer);
}, filter, ["requestHeaders", "extraHeaders"]);

chrome.webRequest.onHeadersReceived.addListener(details => {
  if (details.tabId < 0 || details.statusCode < 200 || details.statusCode >= 300) return;
  const mime = (details.responseHeaders || []).find(h => h.name.toLowerCase() === "content-type")?.value;
  const type = mediaType(details.url, mime);
  if (!type) return;
  const capture = {url: details.url, type, referer: referers.get(details.requestId) || ""};
  enqueue(async () => {
    const key = tabKey(details.tabId);
    const stored = await chrome.storage.session.get(key);
    const items = mergeCapture(stored[key] || [], capture);
    await chrome.storage.session.set({[key]: items});
    await chrome.action.setBadgeText({tabId: details.tabId, text: String(items.length)});
  });
}, filter, ["responseHeaders"]);

chrome.webRequest.onCompleted.addListener(d => referers.delete(d.requestId), filter);
chrome.webRequest.onErrorOccurred.addListener(d => referers.delete(d.requestId), filter);
chrome.tabs.onRemoved.addListener(id => enqueue(() => chrome.storage.session.remove(tabKey(id))));
chrome.tabs.onUpdated.addListener((id, change) => {
  if (change.status === "loading") enqueue(async () => {
    await chrome.storage.session.remove(tabKey(id));
    await chrome.action.setBadgeText({tabId: id, text: ""});
  });
});

chrome.runtime.onMessage.addListener((message, sender, respond) => {
  if (sender.id !== chrome.runtime.id) return;
  if (message.action === "delete" && Number.isInteger(message.tabId) && typeof message.url === "string") {
    enqueue(async () => {
      try {
        const key = tabKey(message.tabId);
        const stored = await chrome.storage.session.get(key);
        const items = (stored[key] || []).filter(item => item.url !== message.url);
        await chrome.storage.session.set({[key]: items});
        await chrome.action.setBadgeText({tabId: message.tabId, text: items.length ? String(items.length) : ""});
        respond({ok: true});
      } catch {
        respond({ok: false, message: "删除失败，请重试"});
      }
    });
    return true;
  }
  if (message.action === "clear" && Number.isInteger(message.tabId)) {
    enqueue(async () => {
      await chrome.storage.session.remove(tabKey(message.tabId));
      await chrome.action.setBadgeText({tabId: message.tabId, text: ""});
      respond({ok: true});
    });
    return true;
  }
});
