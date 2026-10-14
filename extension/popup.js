import {getPairingToken, savePairingToken} from './pairing.js';
const $ = id => document.getElementById(id);
let tabId;
let initialized = false;
function status(message) { $("status").textContent = message; }

async function load() {
  const [tab] = await chrome.tabs.query({active: true, currentWindow: true});
  tabId = tab?.id;
  const saved = await chrome.storage.session.get(`tab:${tabId}`);
  if (!initialized) {
    const bridgeToken = await getPairingToken();
    $("token").value = bridgeToken;
    if (!bridgeToken) document.querySelector("details").open = true;
    initialized = true;
  }
  const items = saved[`tab:${tabId}`] || [];
  $("list").replaceChildren();
  if (!items.length) {
    $("list").textContent = "尚未捕获视频地址，请刷新网页后播放视频。";
    return;
  }
  for (const item of [...items].reverse()) {
    const article = document.createElement("article");
    const type = document.createElement("strong");
    type.textContent = item.type.toUpperCase();
    const url = document.createElement("div");
    url.className = "url";
    url.textContent = item.url;
    const actions = document.createElement("div");
    actions.className = "actions";
    const copy = document.createElement("button");
    copy.textContent = "复制地址";
    copy.onclick = async () => {
      try { await navigator.clipboard.writeText(item.url); status("地址已复制"); }
      catch { status("复制失败，请选中地址手动复制"); }
    };
    const send = document.createElement("button");
    send.className = "send";
    send.textContent = "添加到 AVDownloader";
    send.onclick = async () => {
      send.disabled = true;
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 5000);
      try {
        const bridgeToken = await getPairingToken();
        if (!bridgeToken) throw new Error("请先展开“连接下载器”并保存配对码");
        const response = await fetch("http://127.0.0.1:17839/capture", {
          method: "POST", headers: {"Content-Type": "application/json", "X-AVDownloader-Token": bridgeToken},
          body: JSON.stringify(item), signal: controller.signal, credentials: "omit"
        });
        const result = await response.json();
        status(result.message);
      } catch (error) {
        status(error instanceof TypeError || error.name === "AbortError"
          ? "无法连接，请启动 AVDownloader 并打开“文件 → 浏览器扩展配对”" : error.message);
      } finally { clearTimeout(timeout); send.disabled = false; }
    };
    actions.append(copy, send);
    article.append(type, url, actions);
    $("list").append(article);
  }
}
$("pair").onclick = async () => {
  const token = $("token").value.trim();
  if (!/^[A-Za-z0-9_-]{43}$/.test(token)) return status("请粘贴下载器显示的完整配对码");
  await savePairingToken(token);
  status("配对码已保存，可选择视频地址发送");
};
$("refresh").onclick = () => load().catch(error => status(error.message));
$("clear").onclick = async () => {
  await chrome.runtime.sendMessage({action: "clear", tabId});
  await load();
};
chrome.storage.onChanged.addListener((changes, area) => {
  if (area === "session" && changes[`tab:${tabId}`]) load().catch(error => status(error.message));
});
load().catch(error => status(error.message));
