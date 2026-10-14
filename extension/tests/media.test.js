import test from "node:test";
import assert from "node:assert/strict";
import {mediaType, mergeCapture} from "../media.js";

test("recognizes signed URLs and extensionless media MIME responses", () => {
  assert.equal(mediaType("https://cdn.test/a.M3U8?sign=123"), "m3u8");
  assert.equal(mediaType("https://cdn.test/a?x=1", "application/vnd.apple.mpegurl; charset=utf-8"), "m3u8");
  assert.equal(mediaType("https://cdn.test/stream", "video/mp4"), "mp4");
  assert.equal(mediaType("https://cdn.test/a.mp4?token=1"), "mp4");
  for (const url of ["blob:https://test/a.mp4", "file:///a.mp4", "https://test/a.ts", "https://test/?url=a.m3u8", "bad"])
    assert.equal(mediaType(url), null);
});
test("deduplicates exact URLs, keeps signed variants and bounds storage", () => {
  const items = Array.from({length: 100}, (_, i) => ({url: `https://test/a.mp4?token=${i}`}));
  const updated = mergeCapture(items, {url: items[0].url, referer: "https://test/"});
  assert.equal(updated.length, 100);
  assert.equal(updated.at(-1).referer, "https://test/");
  const more = mergeCapture(items, {url: "https://test/a.mp4?token=100"});
  assert.equal(more.length, 100);
  assert.equal(more[0].url, items[1].url);
});
