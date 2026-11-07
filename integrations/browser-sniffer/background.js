import SUPPORTED_MEDIA from "./supported.js";
import { resolveSafeVariantUrl, storageSafeUrl, variantUriAtIndex } from "./hls-probe.mjs";
import { ORCHESTRATOR_CONFIG, snifferManifestProbeEndpoint, snifferPlanEndpoint } from "./orchestrator-config.js";

const CAPTURED_REQUESTS = new Map();
const RELEVANT_REQUEST_HEADERS = new Set(["user-agent", "referer", "cookie", "authorization"]);
const RELEVANT_RESPONSE_HEADERS = new Set(["content-type", "content-length", "content-range", "set-cookie"]);
const MAX_RECENT_SUBMITS = 10;
const MAX_TRACKED_REQUESTS = 200;
const REQUEST_TTL_MS = 60 * 1000;
const SESSION_KEY = "captureSessionId";
const HLS_PROBE_TIMEOUT_MS = 3000;
const HLS_PROBE_PER_REQUEST_BYTES = 512 * 1024;
const HLS_PROBE_TOTAL_BYTES = 1024 * 1024;
const HLS_PROBE_MAX_REQUESTS = 2;

function normalizeHeaders(headers, relevantNames) {
  const normalized = {};
  for (const header of headers || []) {
    const name = String(header.name || "");
    if (relevantNames.has(name.toLowerCase())) normalized[name] = header.value || "";
  }
  return normalized;
}

function headerValue(headers, name) {
  const lowered = name.toLowerCase();
  for (const [key, value] of Object.entries(headers || {})) {
    if (key.toLowerCase() === lowered) return value || "";
  }
  return "";
}

function extensionMatch(pathname, extensions) {
  return extensions.some((ext) => pathname.includes(`.${ext}`) || pathname.endsWith(ext));
}

function classifyMedia(url, responseHeaders) {
  const parsed = new URL(url);
  const pathname = parsed.pathname.toLowerCase();
  const contentType = headerValue(responseHeaders, "content-type").toLowerCase();
  for (const entry of SUPPORTED_MEDIA) {
    const exactContentType = (entry.contentTypes || []).some((candidate) => contentType.includes(candidate));
    const prefixedContentType = (entry.contentTypePrefixes || []).some((candidate) => contentType.startsWith(candidate));
    if (extensionMatch(pathname, entry.ext || []) || exactContentType || prefixedContentType) {
      const mediaKind = contentType.startsWith("audio/")
        ? "audio"
        : contentType.startsWith("video/")
          ? "video"
          : entry.mediaKind || "unknown";
      return { ...entry, mediaKind };
    }
  }
  return null;
}

function filenameFromUrl(url) {
  const parsed = new URL(url);
  if (!parsed.pathname || parsed.pathname === "/") return parsed.hostname;
  return decodeURIComponent(parsed.pathname.slice(parsed.pathname.lastIndexOf("/") + 1) || parsed.hostname);
}

function integerHeader(headers, name) {
  const value = headerValue(headers, name);
  return value && /^\d+$/.test(value) ? Number.parseInt(value, 10) : null;
}

function dedupeKey(pageUrl, url, media) {
  const parsed = new URL(url);
  return [pageUrl || "", `${parsed.protocol}//${parsed.host}${parsed.pathname}`, media.type || "", media.mediaKind || "", media.category || ""].join("|");
}

async function getTabData(tabId) {
  try { return await chrome.tabs.get(tabId); } catch { return {}; }
}

async function recordSubmit(entry) {
  const { recentSubmits } = await chrome.storage.local.get("recentSubmits");
  const next = [entry, ...(Array.isArray(recentSubmits) ? recentSubmits : [])].slice(0, MAX_RECENT_SUBMITS);
  await chrome.storage.local.set({ lastSubmit: entry, recentSubmits: next });
}

function newSessionId() {
  return `br-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
}

async function getCaptureSessionId() {
  const stored = await chrome.storage.local.get(SESSION_KEY);
  if (stored[SESSION_KEY]) return stored[SESSION_KEY];
  const captureSessionId = newSessionId();
  await chrome.storage.local.set({ [SESSION_KEY]: captureSessionId });
  return captureSessionId;
}

function isOwnExtensionRequest(details) {
  return String(details.initiator || "").startsWith(chrome.runtime.getURL(""));
}

function pruneCapturedRequests(now = Date.now()) {
  for (const [requestId, state] of CAPTURED_REQUESTS.entries()) {
    if (now - Number(state.seenAt || 0) > REQUEST_TTL_MS) CAPTURED_REQUESTS.delete(requestId);
  }
  while (CAPTURED_REQUESTS.size >= MAX_TRACKED_REQUESTS) {
    CAPTURED_REQUESTS.delete(CAPTURED_REQUESTS.keys().next().value);
  }
}

async function readTextWithLimit(response, maxBytes) {
  const reader = response.body && response.body.getReader ? response.body.getReader() : null;
  if (!reader) {
    const text = await response.text();
    const bytesRead = new TextEncoder().encode(text).byteLength;
    if (bytesRead > maxBytes) throw new Error("playlist_too_large");
    return { text, bytesRead };
  }
  const chunks = [];
  let total = 0;
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    total += value.byteLength;
    if (total > maxBytes) {
      await reader.cancel();
      throw new Error("playlist_too_large");
    }
    chunks.push(value);
  }
  const merged = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    merged.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return { text: new TextDecoder().decode(merged), bytesRead: total };
}

async function fetchPlaylist(url, budget) {
  if (budget.requestCount >= HLS_PROBE_MAX_REQUESTS) throw new Error("request_budget_exceeded");
  const remaining = HLS_PROBE_TOTAL_BYTES - budget.totalBytes;
  if (remaining <= 0) throw new Error("byte_budget_exceeded");
  budget.requestCount += 1;
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), HLS_PROBE_TIMEOUT_MS);
  try {
    const response = await fetch(url, { cache: "no-store", credentials: "include", signal: controller.signal });
    if (!response.ok) throw new Error(`http_${response.status}`);
    const result = await readTextWithLimit(response, Math.min(HLS_PROBE_PER_REQUEST_BYTES, remaining));
    if (!result.text.trimStart().startsWith("#EXTM3U")) throw new Error("not_hls_playlist");
    budget.totalBytes += result.bytesRead;
    return result;
  } finally {
    clearTimeout(timeout);
  }
}

async function postManifestProbe(captureId, body) {
  const response = await fetch(snifferManifestProbeEndpoint(captureId), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body)
  });
  const payload = await response.json().catch(() => ({}));
  return { response, payload };
}

async function recordProbeResult({ captureId, captureSessionId, pageUrl, status, budget }) {
  await recordSubmit({
    ok: status === "completed" || status === "master_parsed",
    kind: "manifest_probe",
    capture_id: captureId,
    capture_session_id: captureSessionId,
    page_url: storageSafeUrl(pageUrl),
    status,
    request_count: budget.requestCount,
    total_bytes: budget.totalBytes,
    submitted_at: new Date().toISOString()
  });
}

async function postProbeFailure(captureId, depth, error, budget) {
  const errorCode = error && error.name === "AbortError" ? "timeout" : String(error && error.message ? error.message : "fetch_failed");
  try {
    return await postManifestProbe(captureId, {
      probe_status: errorCode === "playlist_too_large" ? "too_large" : "fetch_failed",
      error_code: errorCode,
      probe_depth: depth
    });
  } catch {
    return null;
  }
}

async function probeHlsPlaylist(captureId, masterUrl, captureSessionId, pageUrl) {
  const budget = { requestCount: 0, totalBytes: 0 };
  let master;
  try {
    master = await fetchPlaylist(masterUrl, budget);
  } catch (error) {
    await postProbeFailure(captureId, 0, error, budget);
    await recordProbeResult({ captureId, captureSessionId, pageUrl, status: "fetch_failed", budget });
    return;
  }

  let masterResult;
  try {
    masterResult = await postManifestProbe(captureId, { probe_status: "ok", playlist_text: master.text, probe_depth: 0 });
  } catch {
    return;
  }
  const followUp = masterResult.payload && masterResult.payload.follow_up;
  if (!masterResult.response.ok || !followUp || followUp.kind !== "variant_playlist") {
    await recordProbeResult({ captureId, captureSessionId, pageUrl, status: "completed", budget });
    return;
  }

  const variantUri = variantUriAtIndex(master.text, Number(followUp.variant_index));
  if (!variantUri) {
    await postManifestProbe(captureId, { probe_status: "parse_failed", error_code: "variant_uri_not_found", probe_depth: 1 }).catch(() => null);
    await recordProbeResult({ captureId, captureSessionId, pageUrl, status: "variant_uri_not_found", budget });
    return;
  }
  const resolved = resolveSafeVariantUrl(masterUrl, variantUri);
  if (resolved.status !== "ok") {
    const probeStatus = resolved.status === "cross_origin_waiting" ? "cross_origin_waiting" : "unsafe_variant_blocked";
    await postManifestProbe(captureId, { probe_status: probeStatus, error_code: resolved.status, probe_depth: 1 }).catch(() => null);
    await recordProbeResult({ captureId, captureSessionId, pageUrl, status: probeStatus, budget });
    return;
  }

  let variant;
  try {
    variant = await fetchPlaylist(resolved.url, budget);
  } catch (error) {
    await postProbeFailure(captureId, 1, error, budget);
    await recordProbeResult({ captureId, captureSessionId, pageUrl, status: "variant_fetch_failed", budget });
    return;
  }
  const result = await postManifestProbe(captureId, { probe_status: "ok", playlist_text: variant.text, probe_depth: 1 }).catch(() => null);
  await recordProbeResult({
    captureId,
    captureSessionId,
    pageUrl,
    status: result && result.response.ok ? "completed" : "variant_submit_failed",
    budget
  });
}

async function submit(details, media, responseHeaders) {
  if (details.tabId < 0 || isOwnExtensionRequest(details)) return;
  const captured = CAPTURED_REQUESTS.get(details.requestId) || {};
  const tab = await getTabData(details.tabId);
  const requestHeaders = captured.requestHeaders || {};
  const contentType = headerValue(responseHeaders, "content-type");
  const pageUrl = tab.url || details.documentUrl || details.initiator;
  const captureSessionId = await getCaptureSessionId();
  const payload = {
    url: details.url,
    capture_session_id: captureSessionId,
    request_id: details.requestId,
    tab_id: details.tabId,
    frame_id: details.frameId,
    resource_type: details.type,
    method: details.method,
    status_code: details.statusCode,
    redirect_count: Number(captured.redirectCount || 0),
    media_type: media.type,
    media_kind: media.mediaKind || "unknown",
    category: media.category,
    filename: filenameFromUrl(details.url),
    page_url: pageUrl,
    page_title: tab.title || "",
    initiator: details.initiator || "",
    document_url: details.documentUrl || "",
    origin_url: details.originUrl || "",
    content_type: contentType,
    headers: { ...requestHeaders, ...responseHeaders },
    request_headers: requestHeaders,
    response_headers: responseHeaders,
    content_length: integerHeader(responseHeaders, "content-length"),
    content_range: headerValue(responseHeaders, "content-range") || null,
    dedupe_key: dedupeKey(pageUrl, details.url, media),
    detected_at: new Date().toISOString(),
    output_dir: ORCHESTRATOR_CONFIG.outputDir
  };

  try {
    const response = await fetch(snifferPlanEndpoint(), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    const responsePayload = await response.json().catch(() => ({}));
    await recordSubmit({
      ok: response.ok,
      status: response.status,
      kind: "capture",
      capture_id: responsePayload.capture_id || null,
      endpoint: snifferPlanEndpoint(),
      url: storageSafeUrl(details.url),
      capture_session_id: captureSessionId,
      page_url: storageSafeUrl(payload.page_url),
      media_type: payload.media_type,
      media_kind: payload.media_kind,
      category: payload.category,
      resource_type: payload.resource_type,
      content_length: payload.content_length,
      content_range: payload.content_range,
      redirect_count: payload.redirect_count,
      submitted_at: new Date().toISOString()
    });
    if (response.ok && media.type === "HLS" && responsePayload.capture_id) {
      void probeHlsPlaylist(responsePayload.capture_id, details.url, captureSessionId, pageUrl);
    }
  } catch (error) {
    await recordSubmit({
      ok: false,
      status: 0,
      kind: "capture",
      endpoint: snifferPlanEndpoint(),
      url: storageSafeUrl(details.url),
      capture_session_id: captureSessionId,
      page_url: storageSafeUrl(payload.page_url),
      media_type: payload.media_type,
      category: payload.category,
      redirect_count: payload.redirect_count,
      submitted_at: new Date().toISOString(),
      error: String(error && error.message ? error.message : error)
    });
  }
}

chrome.webRequest.onBeforeSendHeaders.addListener(
  (details) => {
    if (details.tabId < 0 || isOwnExtensionRequest(details)) return;
    pruneCapturedRequests();
    const existing = CAPTURED_REQUESTS.get(details.requestId) || {};
    CAPTURED_REQUESTS.set(details.requestId, {
      requestHeaders: normalizeHeaders(details.requestHeaders, RELEVANT_REQUEST_HEADERS),
      redirectCount: Number(existing.redirectCount || 0),
      seenAt: Date.now()
    });
  },
  { urls: ["http://*/*", "https://*/*"] },
  ["requestHeaders", "extraHeaders"]
);

chrome.webRequest.onBeforeRedirect.addListener(
  (details) => {
    if (details.tabId < 0 || isOwnExtensionRequest(details)) return;
    const existing = CAPTURED_REQUESTS.get(details.requestId) || { requestHeaders: {}, seenAt: Date.now() };
    existing.redirectCount = Number(existing.redirectCount || 0) + 1;
    existing.seenAt = Date.now();
    CAPTURED_REQUESTS.set(details.requestId, existing);
  },
  { urls: ["http://*/*", "https://*/*"] }
);

chrome.webRequest.onHeadersReceived.addListener(
  async (details) => {
    if (details.tabId < 0 || isOwnExtensionRequest(details)) return;
    const responseHeaders = normalizeHeaders(details.responseHeaders, RELEVANT_RESPONSE_HEADERS);
    const media = classifyMedia(details.url, responseHeaders);
    if (!media) return;
    await submit(details, media, responseHeaders);
    CAPTURED_REQUESTS.delete(details.requestId);
  },
  { urls: ["http://*/*", "https://*/*"], types: ["xmlhttprequest", "media", "other"] },
  ["responseHeaders", "extraHeaders"]
);

function clearRequest(details) {
  CAPTURED_REQUESTS.delete(details.requestId);
}

chrome.webRequest.onCompleted.addListener(clearRequest, { urls: ["http://*/*", "https://*/*"] });
chrome.webRequest.onErrorOccurred.addListener(clearRequest, { urls: ["http://*/*", "https://*/*"] });
