from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl
from urllib.parse import urlencode
from urllib.parse import urlparse
from urllib.parse import urlunparse

from video_orchestrator.router import classify_url
from video_orchestrator.runtime.redaction import redact_url
from video_orchestrator.runtime.site_adapters import match_site_adapter


URL_RE = re.compile(r"(?:https?://|magnet:\?)[^\s<>'\"，。；、）)\]]+", re.IGNORECASE)
URL_START_RE = re.compile(r"https?://", re.IGNORECASE)
_WECHAT_CHANNELS_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")
TRACKING_QUERY_KEYS = {
    "feature",
    "html_redirect",
    "index",
    "list",
    "pp",
    "si",
    "start_radio",
    "t",
    "utm_campaign",
    "utm_content",
    "utm_medium",
    "utm_source",
    "utm_term",
}


def load_and_clean_batch_spec(spec_path: Path) -> dict[str, Any]:
    text = spec_path.read_text(encoding="utf-8")
    try:
        raw = json.loads(text)
    except json.JSONDecodeError:
        return clean_batch_input(text)
    return clean_batch_input(raw)


def clean_batch_input(raw: Any) -> dict[str, Any]:
    candidates = _candidate_items(raw)
    cleaned = []
    skipped = []
    seen = set()

    for index, candidate in enumerate(candidates, start=1):
        url = str(candidate.get("url") or "").strip()
        if not url:
            skipped.append({"index": index, "reason": "empty_url"})
            continue
        normalized_url, reason = normalize_batch_url(url)
        if normalized_url is None:
            skipped.append({"index": index, "url": url, "reason": reason})
            continue
        dedupe_key = _dedupe_key(normalized_url)
        if dedupe_key in seen:
            skipped.append({"index": index, "url": normalized_url, "reason": "duplicate"})
            continue
        seen.add(dedupe_key)
        item = {"url": normalized_url}
        if candidate.get("backend"):
            item["backend"] = candidate.get("backend")
        item["source_index"] = index
        item["media_kind"] = classify_url(normalized_url).media_kind
        # 视频号分享链接识别后总是记录清洗原因（标记平台归属）；其余平台仅在 URL 实际变化时记录
        if normalized_url != url or reason == "wechat_channels_share_link":
            if normalized_url != url:
                item["source_url"] = _audit_source_url(url, reason)
            item["cleaning"] = reason
        cleaned.append(item)

    return {
        "service": "video-download-orchestrator",
        "status": "ok" if cleaned else "empty",
        "will_download": False,
        "contract": "agent_batch_clean_v1",
        "input_count": len(candidates),
        "item_count": len(cleaned),
        "skipped_count": len(skipped),
        "items": cleaned,
        "skipped": skipped,
        "platforms": platform_clean_summary(cleaned),
        "media_kinds": media_kind_summary(cleaned),
        "defaults": {
            "youtube": "single_video_no_playlist",
            "dedupe": "canonical_url",
            "archive_sidecars": "yt-dlp writes info.json, description and subtitles when used",
        },
    }


def media_kind_summary(items: list[dict[str, Any]]) -> dict[str, int]:
    summary: dict[str, int] = {}
    for item in items:
        media_kind = str(item.get("media_kind") or "unknown")
        summary[media_kind] = summary.get(media_kind, 0) + 1
    return summary


def platform_clean_summary(items: list[dict[str, Any]]) -> dict[str, Any]:
    """统计清洗后命中站点适配器的链接，作为 MediaGo 智能清洗摘要的输入。

    只做识别与计划；识别成功不代表自动下载。
    """
    summary: dict[str, Any] = {}
    for item in items:
        url = str(item.get("url") or "")
        adapter = match_site_adapter(url)
        if adapter is None:
            continue
        entry = summary.setdefault(
            adapter.name,
            {"kind": adapter.kind, "count": 0, "planning_only": "planning_only" in adapter.capabilities},
        )
        entry["count"] = int(entry.get("count") or 0) + 1
    return summary


def normalize_batch_url(url: str) -> tuple[str | None, str]:
    parsed = urlparse(url.strip())
    if parsed.scheme.lower() == "magnet":
        topics = [value for key, value in parse_qsl(parsed.query, keep_blank_values=True) if key.lower() == "xt"]
        if not any(re.fullmatch(r"urn:(?:btih|btmh):[^\s&]+", topic, re.IGNORECASE) for topic in topics):
            return None, "invalid_magnet_uri"
        return urlunparse(parsed._replace(scheme="magnet", fragment="")), "magnet_uri"
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None, "not_http_url"
    host = parsed.netloc.lower()
    if host.startswith("www."):
        host_without_www = host[4:]
    else:
        host_without_www = host
    if host_without_www in {"youtube.com", "m.youtube.com"} and parsed.path == "/redirect":
        return None, "youtube_redirect_external"
    if host_without_www == "youtu.be":
        video_id = parsed.path.strip("/").split("/")[0]
        if not video_id:
            return None, "missing_youtube_video_id"
        return f"https://www.youtube.com/watch?v={video_id}", "youtube_short_url"
    if host_without_www in {"youtube.com", "m.youtube.com"} and parsed.path == "/watch":
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        video_id = query.get("v")
        if not video_id:
            return None, "missing_youtube_video_id"
        return f"https://www.youtube.com/watch?v={video_id}", "youtube_single_video"
    if host_without_www == "weixin.qq.com" and parsed.path.startswith("/sph/"):
        video_id = parsed.path[len("/sph/") :].strip("/")
        if not video_id:
            return None, "missing_wechat_channels_id"
        if "/" in video_id or not _WECHAT_CHANNELS_ID_RE.match(video_id):
            return None, "invalid_wechat_channels_id"
        # 规范化：只保留规范短链路径，不展开或保留任何查询参数（含 token 等敏感参数）
        return f"https://weixin.qq.com/sph/{video_id}", "wechat_channels_share_link"
    cleaned_query = _clean_query(parsed.query)
    cleaned = parsed._replace(query=cleaned_query, fragment="")
    return urlunparse(cleaned), "normalized"


def _audit_source_url(url: str, reason: str) -> str:
    """清洗审计用的原始地址（脱敏后）。

    - 视频号分享短链是纯路径形态，规范化后不应保留任何查询参数
      （含 poc_token 等敏感参数），审计字段同样整体丢弃 query 与 fragment；
    - 其他平台保留非敏感查询参数，仅对敏感参数做值级脱敏。
    """
    if reason == "wechat_channels_share_link":
        parsed = urlparse(url)
        return urlunparse(parsed._replace(query="", fragment=""))
    return redact_url(url)


def _candidate_items(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, dict) and isinstance(raw.get("items"), list):
        return [_item_from_value(item) for item in raw["items"]]
    if isinstance(raw, list):
        return [_item_from_value(item) for item in raw]
    if isinstance(raw, str):
        return [{"url": url} for url in _extract_text_urls(raw)]
    return []


def _extract_text_urls(text: str) -> list[str]:
    urls: list[str] = []
    for match in URL_RE.finditer(text):
        candidate = match.group(0)
        starts = [nested.start() for nested in URL_START_RE.finditer(candidate)]
        if len(starts) <= 1:
            urls.append(candidate)
            continue

        # Some copied rich text loses the separator between adjacent links. Split
        # only path-like URLs; a literal URL inside a query may be intentional.
        boundaries = [0]
        for position in starts[1:]:
            prefix = candidate[boundaries[-1] : position]
            if "?" not in prefix and "#" not in prefix:
                boundaries.append(position)
        boundaries.append(len(candidate))
        urls.extend(candidate[start:end] for start, end in zip(boundaries, boundaries[1:]) if start < end)
    return urls


def _item_from_value(item: Any) -> dict[str, Any]:
    if isinstance(item, str):
        return {"url": item}
    if isinstance(item, dict):
        return {"url": item.get("url"), "backend": item.get("backend")}
    return {"url": ""}


def _clean_query(query: str) -> str:
    pairs = [(key, value) for key, value in parse_qsl(query, keep_blank_values=True) if key.lower() not in TRACKING_QUERY_KEYS]
    return urlencode(pairs, doseq=True)


def _dedupe_key(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme.lower() == "magnet":
        for key, value in parse_qsl(parsed.query, keep_blank_values=True):
            if key.lower() == "xt" and re.fullmatch(r"urn:(?:btih|btmh):[^\s&]+", value, re.IGNORECASE):
                return f"magnet:{value.casefold()}"
    host = parsed.netloc.lower()
    path = parsed.path.rstrip("/")
    query = urlencode(sorted(parse_qsl(parsed.query, keep_blank_values=True)))
    return urlunparse(parsed._replace(netloc=host, path=path, query=query, fragment=""))
