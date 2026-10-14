const validToken = token => typeof token === "string" && /^[A-Za-z0-9_-]{43}$/.test(token);

export async function getPairingToken() {
  const {bridgeToken} = await chrome.storage.local.get("bridgeToken");
  if (validToken(bridgeToken)) return bridgeToken;
  // 升级时把原会话内的配对码迁移到持久存储。
  const session = await chrome.storage.session.get("bridgeToken");
  if (!validToken(session.bridgeToken)) return "";
  await savePairingToken(session.bridgeToken);
  return session.bridgeToken;
}

export async function savePairingToken(token) {
  if (!validToken(token)) throw new Error("请粘贴下载器显示的完整配对码");
  await chrome.storage.local.set({bridgeToken: token});
  await chrome.storage.session.remove("bridgeToken");
}
