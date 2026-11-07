from __future__ import annotations

import html
import json
import re
from collections.abc import Callable
from typing import Any
from urllib.parse import urljoin

import requests

from video_orchestrator.runtime.config import site_probe_config
from video_orchestrator.runtime.redaction import redact_headers
from video_orchestrator.runtime.redaction import redact_url
from video_orchestrator.runtime.site_adapters import site_adapter_diagnostic
from video_orchestrator.runtime.url_safety import UnsafeUrlError
from video_orchestrator.runtime.url_safety import validate_adapter_probe_url
from video_orchestrator.runtime.url_safety import validate_loopback_proxy_url


FetchText = Callable[[str, dict[str, str], int], tuple[str, str | None, str]]
_ALLOWED_REQUEST_HEADERS = {"accept", "accept-language", "user-agent"}
_ALLOWED_CONTENT_TYPES = (
    "text/",
    "application/json",
    "application/ld+json",
    "application/xhtml+xml",
    "application/javascript",
)
_SECURITY_MODE = "trusted_local_proxy_allowlist"


def probe_public_page(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout_seconds: int = 10,
    fetch_text: FetchText | None = None,
) -> dict[str, Any]:
    site_adapter = site_adapter_diagnostic(url)
    request_headers = dict(headers or {})
    if not site_adapter.get("matched"):
        return _probe_result(
            url=url,
            site_adapter=site_adapter,
            probe_status="unsupported",
            page_type="unknown",
            request_headers=request_headers,
            next_action="没有匹配的平台适配器。请使用普通下载或页面嗅探。",
            security_mode="not_applicable",
            proxy_used=False,
        )
    if "public_page_probe" not in site_adapter.get("capabilities", []):
        return _probe_result(
            url=url,
            site_adapter=site_adapter,
            probe_status="unsupported",
            page_type=str(site_adapter.get("kind") or "unknown"),
            request_headers=request_headers,
            next_action="该站点适配器暂不支持公开页面解析。请使用普通下载或页面嗅探。",
            security_mode="not_applicable",
            proxy_used=False,
        )

    security = _base_security(request_headers)
    try:
        if fetch_text is not None:
            page_text, content_type, final_url = fetch_text(url, request_headers, timeout_seconds)
        else:
            page_text, content_type, final_url, security = _requests_fetch_text(
                url,
                request_headers,
                timeout_seconds,
                allowed_host_suffixes=tuple(site_adapter.get("probe_host_suffixes") or ()),
            )
    except UnsafeUrlError as exc:
        security_details = exc.details if isinstance(exc.details, dict) else {}
        return _probe_result(
            url=url,
            site_adapter=site_adapter,
            probe_status="blocked",
            page_type=str(site_adapter.get("kind") or "unknown"),
            request_headers=request_headers,
            block_reason=exc.reason,
            blocked_reason=exc.reason,
            next_action="公开页面地址不安全或不可访问，已停止探测。请使用允许的平台公开页面链接或浏览器嗅探。",
            **security,
            **security_details,
        )
    except requests.RequestException as exc:
        return _probe_result(
            url=url,
            site_adapter=site_adapter,
            probe_status="fetch_failed",
            page_type=str(site_adapter.get("kind") or "unknown"),
            request_headers=request_headers,
            error_type=type(exc).__name__,
            next_action="公开页面请求失败。请确认本地代理可用；仍失败时使用页面嗅探。",
            **security,
        )

    extracted = extract_public_page_metadata(page_text, adapter_name=str(site_adapter.get("name") or ""), page_url=final_url or url)
    probe_status = "ok" if extracted.get("has_public_video_signal") else "needs_browser_sniffing"
    next_action = (
        "已从公开页面提取到基础视频线索。优先使用普通下载；失败后再用页面嗅探。"
        if probe_status == "ok"
        else "公开页面没有足够视频线索。请优先尝试普通下载；失败后使用页面嗅探并播放视频。"
    )
    return _probe_result(
        url=url,
        site_adapter=site_adapter,
        probe_status=probe_status,
        page_type=str(site_adapter.get("kind") or "unknown"),
        request_headers=request_headers,
        content_type=content_type,
        final_url=final_url,
        next_action=next_action,
        **security,
        **extracted,
    )


def extract_public_page_metadata(page_text: str, *, adapter_name: str, page_url: str) -> dict[str, Any]:
    if adapter_name == "bilibili":
        return _extract_bilibili_metadata(page_text, page_url)
    if adapter_name == "xiaohongshu":
        return _extract_generic_platform_metadata(page_text, page_url, platform="xiaohongshu")
    if adapter_name == "douyin":
        return _extract_generic_platform_metadata(page_text, page_url, platform="douyin")
    return {"title": _extract_title(page_text), "has_public_video_signal": False}


def _extract_bilibili_metadata(page_text: str, page_url: str) -> dict[str, Any]:
    title = _extract_title(page_text)
    bvid = _first_match(page_text, r"\bBV[0-9A-Za-z]{10,}\b")
    aid = _json_number_or_string(page_text, "aid")
    cid = _json_number_or_string(page_text, "cid")
    if not bvid:
        bvid = _first_match(page_url, r"\bBV[0-9A-Za-z]{10,}\b")
    return {
        "title": title,
        "bvid": bvid,
        "aid": aid,
        "cid": cid,
        "has_public_video_signal": bool(title or bvid or aid or cid),
        "signals": [signal for signal, value in {"title": title, "bvid": bvid, "aid": aid, "cid": cid}.items() if value],
    }


def _extract_generic_platform_metadata(page_text: str, page_url: str, *, platform: str) -> dict[str, Any]:
    title = _extract_title(page_text) or _json_string(page_text, "title") or _json_string(page_text, "desc")
    video_id = _json_string(page_text, "noteId") or _json_string(page_text, "aweme_id") or _json_string(page_text, "group_id")
    page_type = _json_string(page_text, "type") or platform
    return {
        "title": title,
        "platform_page_type": page_type,
        "video_id": video_id,
        "resolved_url": redact_url(page_url),
        "has_public_video_signal": bool(title or video_id),
        "signals": [signal for signal, value in {"title": title, "video_id": video_id, "page_type": page_type}.items() if value],
    }


def _probe_result(*, url: str, site_adapter: dict[str, Any], probe_status: str, page_type: str, request_headers: dict[str, str], next_action: str, **extra: Any) -> dict[str, Any]:
    return {
        "service": "video-download-orchestrator",
        "status": "ok",
        "will_download": False,
        "url": redact_url(url),
        "site_adapter": site_adapter,
        "probe": {
            "probe_status": probe_status,
            "page_type": page_type,
            "request_headers": redact_headers(request_headers),
            "next_action": next_action,
            **_redact_probe_extra(extra),
        },
    }


def _requests_fetch_text(
    url: str,
    headers: dict[str, str],
    timeout_seconds: int,
    *,
    allowed_host_suffixes: tuple[str, ...],
    settings: dict[str, Any] | None = None,
    session: requests.Session | None = None,
) -> tuple[str, str | None, str, dict[str, Any]]:
    config = dict(settings or site_probe_config())
    proxy_url = str(config["proxy_url"])
    validate_loopback_proxy_url(proxy_url)
    max_redirects = int(config.get("max_redirects", 3))
    max_response_bytes = int(config.get("max_response_bytes", 2 * 1024 * 1024))
    configured_timeout = int(config.get("timeout_seconds", 10))
    effective_timeout = max(1, min(int(timeout_seconds), configured_timeout))
    safe_headers, dropped_headers = _safe_request_headers(headers)
    client = session or requests.Session()
    client.trust_env = False
    client.proxies = {"http": proxy_url, "https": proxy_url}
    current_url = url

    for redirect_count in range(max_redirects + 1):
        try:
            validate_adapter_probe_url(current_url, allowed_host_suffixes=allowed_host_suffixes)
        except UnsafeUrlError as exc:
            raise UnsafeUrlError(exc.reason, _security_metadata(safe_headers, dropped_headers, redirect_count)) from exc
        response = client.get(
            current_url,
            headers=safe_headers,
            timeout=(min(3, effective_timeout), effective_timeout),
            allow_redirects=False,
            stream=True,
        )
        try:
            if response.is_redirect:
                location = response.headers.get("location")
                if not location:
                    response.raise_for_status()
                if redirect_count >= max_redirects:
                    raise UnsafeUrlError("too_many_redirects", _security_metadata(safe_headers, dropped_headers, redirect_count))
                current_url = urljoin(current_url, str(location))
                continue
            response.raise_for_status()
            content_type = str(response.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
            if content_type and not any(content_type.startswith(prefix) for prefix in _ALLOWED_CONTENT_TYPES):
                raise UnsafeUrlError("unsupported_content_type", _security_metadata(safe_headers, dropped_headers, redirect_count))
            try:
                body = _read_limited_response(response, max_response_bytes)
            except UnsafeUrlError as exc:
                raise UnsafeUrlError(exc.reason, _security_metadata(safe_headers, dropped_headers, redirect_count)) from exc
            encoding = response.encoding or "utf-8"
            text = body.decode(encoding, errors="replace")
            return text, content_type or None, current_url, _security_metadata(safe_headers, dropped_headers, redirect_count)

        finally:
            response.close()
    raise UnsafeUrlError("too_many_redirects", _security_metadata(safe_headers, dropped_headers, max_redirects))


def _security_metadata(safe_headers: dict[str, str], dropped_headers: list[str], redirect_count: int) -> dict[str, Any]:
    return {
        "security_mode": _SECURITY_MODE,
        "proxy_used": True,
        "redirect_count": redirect_count,
        "sent_header_names": sorted(safe_headers),
        "dropped_header_names": dropped_headers,
    }

def _safe_request_headers(headers: dict[str, str]) -> tuple[dict[str, str], list[str]]:
    safe: dict[str, str] = {}
    dropped: list[str] = []
    for name, value in headers.items():
        lowered = str(name).lower()
        if lowered in _ALLOWED_REQUEST_HEADERS and "\r" not in str(value) and "\n" not in str(value):
            safe[str(name)] = str(value)
        else:
            dropped.append(str(name))
    return safe, sorted(dropped, key=str.lower)


def _read_limited_response(response: requests.Response, max_response_bytes: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    for chunk in response.iter_content(chunk_size=64 * 1024):
        if not chunk:
            continue
        total += len(chunk)
        if total > max_response_bytes:
            raise UnsafeUrlError("response_too_large")
        chunks.append(bytes(chunk))
    return b"".join(chunks)


def _base_security(headers: dict[str, str]) -> dict[str, Any]:
    safe_headers, dropped_headers = _safe_request_headers(headers)
    return {
        "security_mode": _SECURITY_MODE,
        "proxy_used": True,
        "redirect_count": 0,
        "sent_header_names": sorted(safe_headers),
        "dropped_header_names": dropped_headers,
    }


def _redact_probe_extra(extra: dict[str, Any]) -> dict[str, Any]:
    redacted: dict[str, Any] = {}
    for key, value in extra.items():
        if isinstance(value, str) and value.startswith(("http://", "https://")):
            redacted[key] = redact_url(value)
        else:
            redacted[key] = value
    return redacted


def _extract_title(page_text: str) -> str | None:
    match = re.search(r"<title[^>]*>(.*?)</title>", page_text, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return None
    title = re.sub(r"\s+", " ", html.unescape(match.group(1))).strip()
    return title or None


def _json_string(page_text: str, key: str) -> str | None:
    match = re.search(rf'"{re.escape(key)}"\s*:\s*"([^"]+)"', page_text)
    if not match:
        return None
    try:
        value = json.loads(f'"{match.group(1)}"')
    except json.JSONDecodeError:
        value = match.group(1)
    return str(value) if value else None


def _json_number_or_string(page_text: str, key: str) -> str | None:
    string_value = _json_string(page_text, key)
    if string_value:
        return string_value
    match = re.search(rf'"{re.escape(key)}"\s*:\s*(\d+)', page_text)
    return match.group(1) if match else None


def _first_match(value: str, pattern: str) -> str | None:
    match = re.search(pattern, value)
    return match.group(0) if match else None