from __future__ import annotations

from typing import Any

from video_orchestrator.runtime.site_adapters import match_site_adapter


SESSION_BLOCKING_ERROR_TYPES = {
    "browser_session_required",
    "browser_cookie_decryption_unavailable",
    "chrome_cookie_database_locked",
}


def backend_fallback_decision(
    *,
    url: str,
    error_info: dict[str, Any] | None,
    remaining_backends: list[str],
) -> dict[str, Any]:
    """Decide whether another backend attempt can add useful information.

    Cookie/session failures are environmental, not extractor-selection failures.
    Repeating the same browser state through another yt-dlp wrapper only adds
    noise and can trigger another DPAPI/database error.
    """

    error_info = error_info or {}
    error_type = str(error_info.get("error_type") or "")
    adapter = match_site_adapter(url)
    if error_type == "http_precondition_failed" and adapter is not None and adapter.kind == "bilibili-page":
        return {
            "continue": False,
            "status": "retry_later",
            "reason": "bilibili_412_same_family_circuit_breaker",
            "error_type": error_type,
            "skipped_backends": list(remaining_backends),
            "site_adapter": adapter.name,
            "next_action": "B站返回 412，已停止本次同站重试。稍后重试或通过已授权浏览器播放捕获。",
        }
    if error_type in SESSION_BLOCKING_ERROR_TYPES:
        return {
            "continue": False,
            "status": "needs_browser_sniffing",
            "reason": "browser_session_state_unavailable",
            "error_type": error_type,
            "skipped_backends": list(remaining_backends),
            "site_adapter": adapter.name if adapter is not None else None,
            "next_action": error_info.get("next_action")
            or "请在当前浏览器会话中播放视频，并通过 browser-sniffer 捕获新的媒体请求。",
        }
    return {
        "continue": bool(remaining_backends),
        "status": "fallback_available" if remaining_backends else "exhausted",
        "reason": "backend_failure_can_try_fallback" if remaining_backends else "no_fallback_remaining",
        "error_type": error_type or None,
        "skipped_backends": [],
        "site_adapter": adapter.name if adapter is not None else None,
        "next_action": None,
    }
