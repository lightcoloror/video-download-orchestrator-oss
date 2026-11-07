import test from "node:test";
import assert from "node:assert/strict";

import { resolveSafeVariantUrl, storageSafeUrl, variantUriAtIndex } from "./hls-probe.mjs";

const MASTER = `#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=800000,RESOLUTION=640x360
low/index.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=2500000,RESOLUTION=1920x1080
high/index.m3u8?token=secret
`;

test("variantUriAtIndex returns only the requested master entry", () => {
  assert.equal(variantUriAtIndex(MASTER, 0), "low/index.m3u8");
  assert.equal(variantUriAtIndex(MASTER, 1), "high/index.m3u8?token=secret");
  assert.equal(variantUriAtIndex(MASTER, 2), null);
});

test("resolveSafeVariantUrl allows same-origin playlists only", () => {
  const sameOrigin = resolveSafeVariantUrl("https://cdn.example.com/master.m3u8", "high/index.m3u8");
  assert.equal(sameOrigin.status, "ok");
  assert.equal(sameOrigin.url, "https://cdn.example.com/high/index.m3u8");
  assert.equal(resolveSafeVariantUrl("https://cdn.example.com/master.m3u8", "https://other.example.com/high.m3u8").status, "cross_origin_waiting");
  assert.equal(resolveSafeVariantUrl("https://cdn.example.com/master.m3u8", "segment.ts").status, "media_resource_not_followed");
});

test("storageSafeUrl removes credentials, query, and fragments", () => {
  assert.equal(storageSafeUrl("https://user:pass@cdn.example.com/video.m3u8?token=secret#part"), "https://cdn.example.com/video.m3u8");
});