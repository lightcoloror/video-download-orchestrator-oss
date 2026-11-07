// ==UserScript==
// @name         Video Orchestrator Manifest Sniffer
// @namespace    local.video_orchestrator
// @version      0.1.0
// @description  Submit HLS/DASH manifest URLs to local video-download-orchestrator planning API.
// @match        http://*/*
// @match        https://*/*
// @grant        GM_xmlhttpRequest
// @connect      127.0.0.1
// ==/UserScript==

(function () {
  const endpoint = window.VIDEO_ORCHESTRATOR_SNIFFER_ENDPOINT || "";
  const outputDir = window.VIDEO_ORCHESTRATOR_OUTPUT_DIR || "downloads";
  const seen = new Set();

  function looksLikeManifest(url) {
    const lower = String(url || "").toLowerCase();
    return lower.includes(".m3u8") || lower.includes(".mpd") || lower.includes(".ism");
  }

  function submit(url) {
    if (!endpoint || !looksLikeManifest(url) || seen.has(url)) {
      return;
    }
    seen.add(url);
    const payload = {
      url,
      page_url: location.href,
      page_title: document.title,
      initiator: location.href,
      content_type: "",
      headers: {},
      detected_at: new Date().toISOString(),
      output_dir: outputDir
    };
    if (typeof GM_xmlhttpRequest === "function") {
      GM_xmlhttpRequest({
        method: "POST",
        url: endpoint,
        headers: { "Content-Type": "application/json" },
        data: JSON.stringify(payload)
      });
    } else {
      fetch(endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      }).catch(() => {});
    }
  }

  const originalFetch = window.fetch;
  window.fetch = function (...args) {
    const url = typeof args[0] === "string" ? args[0] : args[0] && args[0].url;
    submit(url);
    return originalFetch.apply(this, args);
  };

  const originalOpen = XMLHttpRequest.prototype.open;
  XMLHttpRequest.prototype.open = function (method, url, ...rest) {
    submit(url);
    return originalOpen.call(this, method, url, ...rest);
  };
})();
