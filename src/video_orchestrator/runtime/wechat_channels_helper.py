"""微信视频号本机授权态 helper 状态检查（P1 planning-only）。

只报告 helper 的可用性（available / unavailable / not_configured），
绝不读取、保存或返回任何登录凭证、Cookie、generalToken、解密参数
或签名媒体地址。所有真实解析/下载必须来自 MediaGo、CLI 或本地
操作入口的显式用户动作。
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

import requests

from video_orchestrator.runtime.config import wechat_channels_config
from video_orchestrator.runtime.url_safety import UnsafeUrlError
from video_orchestrator.runtime.url_safety import validate_loopback_proxy_url


def wechat_channels_helper_status(
    *,
    timeout_seconds: float | None = None,
    request_get: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """检查本地视频号解析 helper 是否可用。

    返回的 status 取值：
    - not_configured: 配置里没有 helper HTTP 地址，VDO 只做识别与计划
    - available:      helper 健康检查通过（本机授权态解析可用）
    - unavailable:    helper 已配置但不可达或未通过健康检查
    """
    config = _resolved_config()
    http_base = config["http_base"]
    if not http_base:
        return {
            "service": "video-download-orchestrator",
            "platform": "wechat-channels",
            "helper_status": "not_configured",
            "will_download": False,
            "credentials_accessed": False,
            "http_base": None,
            "next_action": "尚未配置微信视频号本机解析 helper。当前只识别链接并生成计划；"
            "要解析媒体地址，需先接入只监听 127.0.0.1 的本地授权态 helper。",
        }
    try:
        validate_loopback_proxy_url(http_base)
    except UnsafeUrlError as exc:
        return {
            "service": "video-orchestrator",
            "platform": "wechat-channels",
            "helper_status": "rejected",
            "will_download": False,
            "credentials_accessed": False,
            "http_base": None,
            "reason": exc.reason,
            "next_action": "helper 地址必须是 loopback HTTP(S) 地址；已拒绝向远程地址发送健康检查或 API Key。",
        }
    endpoint = http_base.rstrip("/") + "/" + config["status_endpoint"].lstrip("/")
    effective_timeout = timeout_seconds if timeout_seconds is not None else config["timeout_seconds"]
    headers = _auth_headers(config)
    try:
        transport = request_get or _loopback_get
        response = transport(endpoint, headers=headers, timeout=effective_timeout)
    except (requests.RequestException, OSError) as exc:
        return {
            "service": "video-download-orchestrator",
            "platform": "wechat-channels",
            "helper_status": "unavailable",
            "will_download": False,
            "credentials_accessed": False,
            "http_base": http_base,
            "error": str(exc),
            "next_action": "已配置本地解析 helper，但连接失败。请确认 helper 已启动且只监听 127.0.0.1；"
            "解析不可用时不误报成功。",
        }
    ok = 200 <= int(response.status_code) < 300
    return {
        "service": "video-download-orchestrator",
        "platform": "wechat-channels",
        "helper_status": "available" if ok else "unavailable",
        "will_download": False,
        "credentials_accessed": False,
        "http_base": http_base,
        "http_status": int(response.status_code),
        "next_action": "本机授权态解析 helper 可用，可继续解析视频号链接（仍需用户显式确认后执行）。"
        if ok
        else "本地解析 helper 未返回成功状态。请检查 helper 日志；解析不可用时不误报成功。",
    }


def _resolved_config() -> dict[str, Any]:
    config = dict(wechat_channels_config().get("helper") or {})
    return {
        "http_base": str(config.get("http_base") or "").strip().rstrip("/"),
        "status_endpoint": str(config.get("status_endpoint") or "/healthy").lstrip("/"),
        "timeout_seconds": float(config.get("timeout_seconds") or 2),
        "api_key_env": str(config.get("api_key_env") or ""),
    }


def _auth_headers(config: dict[str, Any]) -> dict[str, str]:
    headers = {"Accept": "application/json"}
    api_key_env = config.get("api_key_env") or ""
    if api_key_env:
        api_key = os.environ.get(api_key_env)
        if api_key:
            headers["X-API-Key"] = api_key
    return headers


def _loopback_get(url: str, **kwargs: Any) -> requests.Response:
    """Call the local helper without inheriting process proxy settings."""

    with requests.Session() as session:
        session.trust_env = False
        return session.get(url, **kwargs)
