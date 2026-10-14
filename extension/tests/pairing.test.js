import test from 'node:test';
import assert from 'node:assert/strict';
import {getPairingToken, savePairingToken} from '../pairing.js';

test('pairing persists across sessions, migrates old storage and can be replaced', async () => {
  const local = {}, session = {bridgeToken: 'A'.repeat(43)};
  const area = data => ({
    async get(key) { return {[key]: data[key]}; },
    async set(values) { Object.assign(data, values); },
    async remove(key) { delete data[key]; }
  });
  globalThis.chrome = {storage: {local: area(local), session: area(session)}};
  try {
    assert.equal(await getPairingToken(), 'A'.repeat(43));
    assert.equal(session.bridgeToken, undefined);
    assert.equal(local.bridgeToken, 'A'.repeat(43));
    chrome.storage.session = area({}); // 模拟浏览器重启，会话存储清空。
    assert.equal(await getPairingToken(), 'A'.repeat(43));
    await savePairingToken('B'.repeat(43));
    assert.equal(await getPairingToken(), 'B'.repeat(43));
    await assert.rejects(savePairingToken('bad'));
    assert.equal(await getPairingToken(), 'B'.repeat(43));
  } finally { delete globalThis.chrome; }
});
