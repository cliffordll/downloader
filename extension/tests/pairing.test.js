import test from 'node:test';
import assert from 'node:assert/strict';
import {getPairingToken, savePairingToken, getPairingPort, savePairing, validatePort} from '../pairing.js';

test('pairing persists across sessions, migrates old storage and can be replaced', async () => {
  const local = {}, session = {bridgeToken: 'A'.repeat(43)};
  const area = data => ({
    async get(key) { return {[key]: data[key]}; },
    async set(values) { Object.assign(data, values); },
    async remove(key) { delete data[key]; }
  });
  globalThis.chrome = {storage: {local: area(local), session: area(session)}};
  try {
    assert.equal(await getPairingPort(), 17839);
    assert.equal(await getPairingToken(), 'A'.repeat(43));
    assert.equal(session.bridgeToken, undefined);
    assert.equal(local.bridgeToken, 'A'.repeat(43));
    chrome.storage.session = area({}); // 模拟浏览器重启，会话存储清空。
    assert.equal(await getPairingToken(), 'A'.repeat(43));
    await savePairingToken('B'.repeat(43));
    assert.equal(await getPairingToken(), 'B'.repeat(43));
    await savePairing('C'.repeat(43), '17840');
    assert.equal(await getPairingPort(), 17840);
    assert.equal(await getPairingToken(), 'C'.repeat(43));
    await assert.rejects(savePairing('D'.repeat(43), 0));
    assert.equal(await getPairingToken(), 'C'.repeat(43));
    assert.equal(await getPairingPort(), 17840);
    await assert.rejects(savePairingToken('bad'));
    assert.equal(await getPairingToken(), 'C'.repeat(43));
  } finally { delete globalThis.chrome; }
});

test('port validation rejects malformed and out of range values', () => {
  for (const value of [0, 65536, -1, true, null, '', '1.5', 1.5, '1e3', '17840/path'])
    assert.throws(() => validatePort(value));
  assert.equal(validatePort('65535'), 65535);
});
