"""Configured WeChat Channels profile providers.

Provider responses and signed media URLs stay in process memory. Persisted task
artifacts are produced by ``wechat_channels_resolve`` after redaction.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from typing import Any
from urllib.parse import urlsplit

import requests

from video_orchestrator.runtime.config import site_probe_config
from video_orchestrator.runtime.config import wechat_channels_config
from video_orchestrator.runtime.site_adapters import host_matches_suffixes
from video_orchestrator.runtime.url_safety import UnsafeUrlError
from video_orchestrator.runtime.url_safety import validate_loopback_proxy_url
from video_orchestrator.runtime.wechat_channels_media import parse_profile_payload


PROVIDER_NAME = "public-profile"
QUALITY_MODES = {"highest", "compatible", "h264", "h265", "original"}
TRANSPORT_MODES = {"auto", "direct", "proxy"}
SAFE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/128 Safari/537.36",
    "Accept": "application/json,text/plain,*/*",
    "Accept-Language": "zh-CN,zh;q=0.9",
}


def resolve_public_profile(
    share_url: str,
    *,
    transport: str = "auto",
    quality: str = "highest",
    request_get: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    config = _provider_config()
    if not config["enabled"]:
        return _failure("provider_disabled", "公共解析 provider 未启用。")
    if transport not in TRANSPORT_MODES:
        return _failure("invalid_transport", f"不支持的传输模式：{transport}")
    if quality not in QUALITY_MODES:
        return _failure("invalid_quality", f"不支持的画质偏好：{quality}")
    try:
        _validate_endpoint(config["endpoint"], config["allowed_host_suffixes"])
    except ValueError as exc:
        return _failure("unsafe_provider_config", str(exc))

    attempts: list[dict[str, Any]] = []
    for current_transport in _transport_order(transport, config["transport_order"]):
        try:
            response = _get_profile(
                config["endpoint"],
                share_url=share_url,
                timeout_seconds=config["timeout_seconds"],
                transport=current_transport,
                request_get=request_get,
            )
        except (requests.RequestException, OSError, ValueError) as exc:
            attempts.append({"transport": current_transport, "status": "failed", "reason": type(exc).__name__})
            continue
        if not 200 <= int(response.status_code) < 300:
            attempts.append({"transport": current_transport, "status": "failed", "http_status": int(response.status_code)})
            _close_response(response)
            return _provider_unavailable(attempts)
        try:
            body = _read_json_response(response, config["max_response_bytes"])
            parsed = _parse_profile(body, quality=quality)
        except (requests.RequestException, OSError) as exc:
            attempts.append({"transport": current_transport, "status": "failed", "reason": type(exc).__name__})
            _close_response(response)
            continue
        except (TypeError, ValueError) as exc:
            attempts.append({"transport": current_transport, "status": "failed", "reason": str(exc)[:120]})
            _close_response(response)
            return _provider_unavailable(attempts)
        _close_response(response)
        attempts.append({"transport": current_transport, "status": "resolved"})
        return {
            "status": "resolved",
            "provider": PROVIDER_NAME,
            "transport": current_transport,
            "title": parsed["title"],
            "description": parsed["description"],
            "content_id": parsed["content_id"],
            "media_type": parsed["media_type"],
            "cover_url": parsed["cover_url"],
            "created_at": parsed["created_at"],
            "duration_seconds": parsed["duration_seconds"],
            "author": parsed["author"],
            "background_music": parsed["background_music"],
            "source": "wechat-channels-public-profile",
            "quality": parsed["selected_variant"]["variant_key"],
            "quality_mode": quality,
            "media_url": parsed["media_url"],
            "selected_variant": parsed["selected_variant"],
            "media_candidates": parsed["media_candidates"],
            "candidate_count": len(parsed["media_candidates"]),
            "available_candidates": parsed["available_candidates"],
            "attempts": attempts,
        }
    return _provider_unavailable(attempts)


def _provider_config() -> dict[str, Any]:
    root = wechat_channels_config()
    raw = dict((root.get("providers") or {}).get(PROVIDER_NAME) or {})
    suffixes = raw.get("allowed_host_suffixes") or ()
    order = raw.get("transport_order") or ("direct", "proxy")
    if isinstance(suffixes, str):
        suffixes = [suffixes]
    if isinstance(order, str):
        order = [order]
    return {
        "enabled": bool(raw.get("enabled", False)),
        "endpoint": str(raw.get("endpoint") or "").strip(),
        "allowed_host_suffixes": tuple(str(item).lower().rstrip(".") for item in suffixes if str(item).strip()),
        "timeout_seconds": float(raw.get("timeout_seconds") or 10),
        "max_response_bytes": int(raw.get("max_response_bytes") or 2 * 1024 * 1024),
        "transport_order": tuple(str(item) for item in order if str(item) in {"direct", "proxy"}),
    }


def _validate_endpoint(endpoint: str, suffixes: Sequence[str]) -> None:
    try:
        parsed = urlsplit(endpoint)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("公共解析 provider 地址无效。") from exc
    hostname = str(parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme != "https" or not hostname or parsed.username or parsed.password:
        raise ValueError("公共解析 provider 必须使用无凭证的固定 HTTPS 地址。")
    if port not in {None, 443}:
        raise ValueError("公共解析 provider 不允许异常端口。")
    if parsed.query or parsed.fragment:
        raise ValueError("公共解析 provider 地址不允许查询参数或片段。")
    if not suffixes or not host_matches_suffixes(hostname, tuple(suffixes)):
        raise ValueError("公共解析 provider 地址未命中配置域名白名单。")


def _transport_order(requested: str, configured: Sequence[str]) -> tuple[str, ...]:
    if requested != "auto":
        return (requested,)
    unique: list[str] = []
    for item in configured or ("direct", "proxy"):
        if item in {"direct", "proxy"} and item not in unique:
            unique.append(item)
    return tuple(unique or ["direct", "proxy"])


def _get_profile(
    endpoint: str,
    *,
    share_url: str,
    timeout_seconds: float,
    transport: str,
    request_get: Callable[..., Any] | None,
) -> Any:
    kwargs: dict[str, Any] = {
        "params": {"url": share_url},
        "headers": dict(SAFE_HEADERS),
        "timeout": timeout_seconds,
        "allow_redirects": False,
        "stream": True,
    }
    if transport == "proxy":
        proxy_url = str(site_probe_config().get("proxy_url") or "")
        try:
            validate_loopback_proxy_url(proxy_url)
        except UnsafeUrlError as exc:
            raise ValueError(f"invalid_proxy:{exc.reason}") from exc
        kwargs["proxies"] = {"http": proxy_url, "https": proxy_url}
    if request_get is not None:
        return request_get(endpoint, **kwargs)
    with requests.Session() as session:
        session.trust_env = False
        return session.get(endpoint, **kwargs)


def _read_json_response(response: Any, max_bytes: int) -> Any:
    length = response.headers.get("Content-Length") if hasattr(response, "headers") else None
    try:
        parsed_length = int(length) if length else 0
    except (TypeError, ValueError):
        parsed_length = 0
    if parsed_length > max_bytes:
        raise ValueError("provider_response_too_large")
    iter_content = getattr(response, "iter_content", None)
    if callable(iter_content):
        chunks: list[bytes] = []
        total = 0
        for chunk in iter_content(chunk_size=64 * 1024):
            if not chunk:
                continue
            total += len(chunk)
            if total > max_bytes:
                raise ValueError("provider_response_too_large")
            chunks.append(bytes(chunk))
        content = b"".join(chunks)
    else:
        content = getattr(response, "content", b"")
        if not isinstance(content, bytes):
            raise ValueError("invalid_provider_response_body")
        if len(content) > max_bytes:
            raise ValueError("provider_response_too_large")
    return json.loads(content.decode("utf-8-sig"))


def _close_response(response: Any) -> None:
    close = getattr(response, "close", None)
    if callable(close):
        close()


def _parse_profile(body: Any, *, quality: str) -> dict[str, Any]:
    return parse_profile_payload(body, quality=quality)


def _failure(reason: str, next_action: str) -> dict[str, Any]:
    return {"status": "unavailable", "provider": PROVIDER_NAME, "reason": reason, "next_action": next_action}


def _provider_unavailable(attempts: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        **_failure("provider_unavailable", "公共解析 provider 当前不可用，请稍后重试或改用本地 helper。"),
        "attempts": attempts,
    }
