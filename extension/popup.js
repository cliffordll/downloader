import {getPairingToken, getPairingPort, savePairing} from './pairing.js';
const $ = id => document.getElementById(id);
let tabId;
let initialized = false;
let captures = [];
function status(message) { $("status").textContent = message; }

async function load() {
  const [tab] = await chrome.tabs.query({active: true, currentWindow: true});
  tabId = tab?.id;
  const saved = await chrome.storage.session.get(`tab:${tabId}`);
  if (!initialized) {
    const bridgeToken = await getPairingToken();
    $("token").value = bridgeToken;
    $("port").value = await getPairingPort();
    if (!bridgeToken) document.querySelector("details").open = true;
    initialized = true;
  }
  captures = saved[`tab:${tabId}`] || [];
  render();
}

function render() {
  const keyword = $("keyword").value.trim().toLowerCase();
  const selectedType = $("media-type").value;
  const items = captures.filter(item =>
    (selectedType === "all" || item.type === selectedType) && item.url.toLowerCase().includes(keyword));
  $("count").textContent = `显示 ${items.length} / ${captures.length} 个地址`;
  $("list").replaceChildren();
  if (!items.length) {
    $("list").textContent = captures.length
      ? "没有匹配的地址，请调整关键词或类型。"
      : "尚未捕获视频地址，请刷新网页后播放视频。";
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
    send.textContent = "添加任务";
    send.title = "发送到 AVDownloader，确认后创建下载任务";
    send.onclick = async () => {
      send.disabled = true;
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 5000);
      try {
        const bridgeToken = await getPairingToken();
        if (!bridgeToken) throw new Error("请先展开“连接下载器”并保存配对码");
        const port = await getPairingPort();
        const response = await fetch(`http://127.0.0.1:${port}/capture`, {
          method: "POST", headers: {"Content-Type": "application/json", "X-AVDownloader-Token": bridgeToken},
          body: JSON.stringify(item), signal: controller.signal, credentials: "omit"
        });
        const result = await response.json();
        status(result.message);
      } catch (error) {
        status(error instanceof TypeError || error.name === "AbortError"
          ? "无法连接，请确认下载器已开启本机接收服务，并检查两端端口一致" : error.message);
      } finally { clearTimeout(timeout); send.disabled = false; }
    };
    const remove = document.createElement("button");
    remove.textContent = "删除";
    remove.title = "从当前页候选列表中移除此地址";
    const itemTabId = tabId;
    remove.onclick = async () => {
      remove.disabled = true;
      try {
        const result = await chrome.runtime.sendMessage({action: "delete", tabId: itemTabId, url: item.url});
        if (!result?.ok) throw new Error(result?.message || "删除失败，请重试");
        status("地址已从候选列表删除");
      } catch (error) { status(error.message); }
      finally { remove.disabled = false; }
    };
    actions.append(copy, send, remove);
    article.append(type, url, actions);
    $("list").append(article);
  }
}
$("pair").onclick = async () => {
  const token = $("token").value.trim();
  if (!/^[A-Za-z0-9_-]{43}$/.test(token)) return status("请粘贴下载器显示的完整配对码");
  try {
    await savePairing(token, $("port").value.trim());
    status("配对码和端口已保存，可选择视频地址发送");
  } catch (error) { status(error.message); }
};
$("refresh").onclick = () => load().catch(error => status(error.message));
$("keyword").oninput = render;
$("media-type").onchange = render;
$("clear").onclick = async () => {
  await chrome.runtime.sendMessage({action: "clear", tabId});
  await load();
};
chrome.storage.onChanged.addListener((changes, area) => {
  if (area === "session" && changes[`tab:${tabId}`]) load().catch(error => status(error.message));
});
load().catch(error => status(error.message));
