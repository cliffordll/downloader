import test from "node:test";
import assert from "node:assert/strict";

test("captures per tab, survives worker restart, clears navigation and never stores Cookie", async () => {
  const events = {};
  const state = {};
  function event(name) {
    return {addListener(callback) { events[name] = callback; }};
  }
  globalThis.chrome = {
    webRequest: Object.fromEntries(["onBeforeSendHeaders", "onHeadersReceived", "onCompleted", "onErrorOccurred"].map(name => [name, event(name)])),
    tabs: {onRemoved: event("removed"), onUpdated: event("updated")},
    storage: {session: {
      async get(key) { return {[key]: state[key]}; },
      async set(values) { Object.assign(state, values); },
      async remove(key) { delete state[key]; }
    }},
    action: {async setBadgeText() {}},
    runtime: {id: "test", onMessage: event("message")}
  };
  const flush = () => new Promise(resolve => setImmediate(resolve));
  await import("../background.js?worker=1");
  events.onBeforeSendHeaders({tabId: 1, requestId: "1", requestHeaders: [
    {name: "Referer", value: "https://example.com/watch"}, {name: "Cookie", value: "secret"}
  ]});
  const response = {tabId: 1, requestId: "1", statusCode: 200, url: "https://cdn.test/stream?token=1",
    responseHeaders: [{name: "Content-Type", value: "application/vnd.apple.mpegurl"}]};
  events.onHeadersReceived(response);
  events.onCompleted({requestId: "1"});
  await flush();
  assert.equal(state["tab:1"][0].referer, "https://example.com/watch");
  assert.ok(!JSON.stringify(state).includes("secret"));
  events.onHeadersReceived({...response, tabId: -1});
  events.onHeadersReceived({...response, tabId: 2, statusCode: 403});
  await flush();
  assert.equal(state["tab:2"], undefined);
  await import("../background.js?worker=2");
  assert.equal(state["tab:1"].length, 1);
  events.onHeadersReceived({...response, tabId: 2});
  await flush();
  events.updated(1, {status: "loading"});
  await flush();
  assert.equal(state["tab:1"], undefined);
  assert.equal(state["tab:2"].length, 1);
  events.removed(2);
  await flush();
  assert.equal(state["tab:2"], undefined);
  delete globalThis.chrome;
});
