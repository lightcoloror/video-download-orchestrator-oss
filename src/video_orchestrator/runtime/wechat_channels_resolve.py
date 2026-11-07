"""微信视频号分享链接解析编排（显式执行与 MediaGo 自动下载）。

安全边界（与计划文档一致）：
- 本地 helper 只允许监听 127.0.0.1（loopback），VDO 通过稳定 HTTP 契约调用；
- helper 返回的媒体地址必须通过公网地址校验（防 SSRF）和腾讯媒体域名白名单；
- 完整签名媒体 URL、解密参数与 token 只存在于当前进程内存，绝不写入
  manifest、report、review-checklist、任务数据库或日志；
- 落盘内容只有非敏感解析摘要（标题、来源、质量、预计时长、可下载状态）；
- 解析本身不触发下载；只有显式调用 import 编排（CLI/API/MediaGo 的用户动作）
  才把解析到的媒体地址交给 MediaGo，默认 startDownload=true。
"""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Callable
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from urllib.parse import urlunsplit
from uuid import uuid4

import requests

from video_orchestrator.runtime.config import runtime_state_dir
from video_orchestrator.runtime.config import wechat_channels_config
from video_orchestrator.runtime.manifests import write_manifest
from video_orchestrator.runtime.mediago import import_to_mediago
from video_orchestrator.runtime.mediago import reconcile_mediago_tasks
from video_orchestrator.runtime.quality import write_quality_review
from video_orchestrator.runtime.redaction import redact_url
from video_orchestrator.runtime.reports import write_task_report
from video_orchestrator.runtime.task_store import TaskStore
from video_orchestrator.runtime.url_safety import Resolver
from video_orchestrator.runtime.url_safety import UnsafeUrlError
from video_orchestrator.runtime.url_safety import validate_loopback_proxy_url
from video_orchestrator.runtime.url_safety import validate_public_http_url
from video_orchestrator.runtime.wechat_channels_providers import resolve_public_profile
from video_orchestrator.runtime.wechat_channels_media import resolution_fingerprint
from video_orchestrator.router import classify_url


DEFAULT_RESOLVE_ENDPOINT = "/api/parse"
# helper 返回的媒体地址允许的宿主后缀（腾讯视频号媒体域；可被配置覆盖）
DEFAULT_MEDIA_HOST_SUFFIXES = (
    "channels.weixin.qq.com",
    "finder.video.qq.com",
    "qpic.cn",
    "wxapp.tc.qq.com",
)


def resolve_wechat_channels_share(
    share_url: str,
    *,
    quality: str | None = None,
    timeout_seconds: float | None = None,
    request_post: Callable[..., Any] | None = None,
    resolver: Resolver | None = None,
) -> dict[str, Any]:
    """调用本机授权态 helper 解析视频号分享链接。

    返回 status 取值：
    - not_configured: 配置里没有 helper HTTP 地址，只做识别与计划
    - unavailable:    helper 已配置但不可达或未通过健康检查
    - review_required: helper 返回了需要人工处理的解析结果（授权缺失/过期/不可下载）
    - resolved:        解析成功，media_url 只存在于返回 dict 内存中
    - rejected:        安全校验拒绝（http_base 非 loopback、媒体地址不通过 SSRF/白名单）

    落盘与日志只允许出现 redacted_media_url，绝不允许出现完整 media_url。
    """
    config = _resolved_config()
    http_base = config["http_base"]
    if not http_base:
        return _payload(
            status="not_configured",
            share_url=share_url,
            http_base=None,
            next_action="尚未配置微信视频号本机解析 helper。要解析媒体地址，需先接入只监听 127.0.0.1 的本地授权态 helper。",
        )
    try:
        validate_loopback_proxy_url(http_base)
    except UnsafeUrlError as exc:
        return _payload(
            status="rejected",
            share_url=share_url,
            http_base=http_base,
            reason=exc.reason,
            next_action="本地解析 helper 地址必须只监听 127.0.0.1（loopback）。已拒绝非本机 helper 地址，避免任意地址转发。",
        )

    endpoint = http_base.rstrip("/") + "/" + config["resolve_endpoint"].lstrip("/")
    effective_timeout = timeout_seconds if timeout_seconds is not None else config["timeout_seconds"]
    headers = _auth_headers(config)
    try:
        transport = request_post or _loopback_post
        request_body = {"url": share_url}
        if quality is not None:
            request_body["quality"] = quality
        response = transport(endpoint, json=request_body, headers=headers, timeout=effective_timeout)
    except (requests.RequestException, OSError) as exc:
        return _payload(
            status="unavailable",
            share_url=share_url,
            http_base=http_base,
            reason="helper_unreachable",
            error=str(exc),
            next_action="已配置本地解析 helper，但解析请求失败。请确认 helper 已启动且只监听 127.0.0.1；解析不可用时不误报成功。",
        )
    if not 200 <= int(response.status_code) < 300:
        try:
            error_body = response.json()
        except ValueError:
            error_body = None
        if isinstance(error_body, dict) and error_body.get("status") in {"review_required", "unavailable"}:
            return _normalize_helper_payload(
                share_url,
                error_body,
                http_base=http_base,
                config=config,
                resolver=resolver,
            )
        return _payload(
            status="unavailable",
            share_url=share_url,
            http_base=http_base,
            reason="helper_http_error",
            http_status=int(response.status_code),
            error=f"helper returned HTTP {int(response.status_code)}",
            next_action="本地解析 helper 未返回成功状态。请检查 helper 日志；解析不可用时不误报成功。",
        )
    try:
        body = response.json()
    except ValueError as exc:
        return _payload(
            status="unavailable",
            share_url=share_url,
            http_base=http_base,
            reason="invalid_helper_payload",
            error=str(exc),
            next_action="本地解析 helper 返回了无法解析的响应。请检查 helper 契约（POST /api/parse 返回 JSON）。",
        )
    return _normalize_helper_payload(share_url, body, http_base=http_base, config=config, resolver=resolver)


def resolve_wechat_channels_share_auto(
    share_url: str,
    *,
    provider: str = "auto",
    transport: str = "auto",
    quality: str = "highest",
    request_post: Callable[..., Any] | None = None,
    profile_request_get: Callable[..., Any] | None = None,
    resolver: Resolver | None = None,
) -> dict[str, Any]:
    """Resolve with the local helper first, then the approved public provider."""

    if provider not in {"auto", "local-helper", "public-profile"}:
        return _payload(
            status="rejected",
            share_url=share_url,
            http_base=None,
            reason="invalid_provider",
            next_action=f"不支持的解析来源：{provider}",
        )

    provider_attempts: list[dict[str, Any]] = []
    if provider in {"auto", "local-helper"}:
        local = resolve_wechat_channels_share(
            share_url,
            quality=quality,
            request_post=request_post,
            resolver=resolver,
        )
        local["provider"] = "local-helper"
        provider_attempts.append(
            {
                "provider": "local-helper",
                "status": local["status"],
                "reason": local.get("reason"),
            }
        )
        if local["status"] == "resolved" or provider == "local-helper" or local["status"] == "rejected":
            local["provider_attempts"] = provider_attempts
            return local

    public = resolve_public_profile(
        _redact_share_url(share_url),
        transport=transport,
        quality=quality,
        request_get=profile_request_get,
    )
    provider_attempts.append(
        {
            "provider": "public-profile",
            "status": public["status"],
            "reason": public.get("reason"),
            "transport": public.get("transport"),
        }
    )
    if public["status"] != "resolved":
        return _payload(
            status="unavailable",
            share_url=share_url,
            http_base=None,
            provider="public-profile",
            provider_attempts=provider_attempts,
            reason=str(public.get("reason") or "provider_unavailable")[:100],
            next_action=str(public.get("next_action") or "公共解析 provider 暂不可用，请稍后重试。")[:500],
        )

    normalized = _normalize_helper_payload(
        share_url,
        public,
        http_base=None,
        config=_resolved_config(),
        resolver=resolver,
    )
    normalized["provider"] = "public-profile"
    normalized["provider_attempts"] = provider_attempts
    return normalized


def _normalize_helper_payload(
    share_url: str,
    body: Any,
    *,
    http_base: str | None,
    config: dict[str, Any],
    resolver: Resolver | None = None,
) -> dict[str, Any]:
    if not isinstance(body, dict):
        return _payload(
            status="unavailable",
            share_url=share_url,
            http_base=http_base,
            reason="invalid_helper_payload",
            next_action="本地解析 helper 返回了非 JSON 对象。请检查 helper 契约。",
        )
    status = str(body.get("status") or "")
    if status == "unavailable":
        return _payload(
            status="unavailable",
            share_url=share_url,
            http_base=http_base,
            reason=str(body.get("reason") or "helper_unavailable")[:100],
            next_action=str(body.get("next_action") or "本地解析 helper 暂不可用，请检查服务状态后重试。")[:500],
        )
    if status == "review_required":
        reason = str(body.get("reason") or "review_required")
        return _payload(
            status="review_required",
            share_url=share_url,
            http_base=http_base,
            reason=reason,
            message=str(body.get("message") or "")[:500],
            next_action=str(
                body.get("next_action")
                or "解析需要人工处理（授权缺失、过期或暂不可下载）。请在本机微信完成登录/授权后重试，或改用微信桌面端播放捕获。"
            ),
        )
    if status != "resolved":
        return _payload(
            status="unavailable",
            share_url=share_url,
            http_base=http_base,
            reason="invalid_helper_status",
            next_action="本地解析 helper 返回了未知状态。请检查 POST /api/parse 契约。",
        )

    media_url = body.get("media_url")
    if not isinstance(media_url, str) or not media_url:
        return _payload(
            status="review_required",
            share_url=share_url,
            http_base=http_base,
            reason="missing_media_url",
            next_action="helper 标记 resolved 但未返回媒体地址。请检查 helper 契约；缺少媒体地址不生成下载任务。",
        )
    # 先做宿主白名单校验（非腾讯媒体域直接拒绝，无需 DNS），再做公网地址校验（防 DNS 重绑定到内网）
    hostname = _hostname(media_url)
    if not hostname or not _host_allowed(hostname, config["media_host_suffixes"]):
        return _payload(
            status="rejected",
            share_url=share_url,
            http_base=http_base,
            reason="media_host_not_allowed",
            next_action="helper 返回的媒体地址不在腾讯媒体域名白名单内。已拒绝非白名单媒体地址。",
        )
    try:
        validate_public_http_url(media_url, resolver=resolver)
    except UnsafeUrlError as exc:
        return _payload(
            status="rejected",
            share_url=share_url,
            http_base=http_base,
            reason=f"unsafe_media_url:{exc.reason}",
            next_action="helper 返回的媒体地址未通过公网地址校验（防 SSRF）。已拒绝该媒体地址。",
        )

    duration = body.get("duration_seconds")
    duration_seconds = _optional_float(duration)
    selected_variant = _safe_variant(body.get("selected_variant"))
    media_candidates = [
        candidate
        for candidate in (_safe_variant(item) for item in body.get("media_candidates", []))
        if candidate
    ] if isinstance(body.get("media_candidates"), list) else []
    return _payload(
        status="resolved",
        share_url=share_url,
        http_base=http_base,
        title=str(body.get("title") or "").strip()[:300] or None,
        source=str(body.get("source") or "wechat-channels")[:100],
        quality=str(body.get("quality") or "").strip()[:50] or None,
        quality_mode=str(body.get("quality_mode") or "").strip()[:50] or None,
        provider=str(body.get("provider") or "local-helper")[:100],
        transport=str(body.get("transport") or "").strip()[:50] or None,
        candidate_count=max(1, _optional_int(body.get("candidate_count")) or 1),
        available_candidates=[str(item)[:50] for item in body.get("available_candidates", []) if isinstance(item, str)],
        duration_seconds=duration_seconds,
        duration_hint=_format_duration(duration_seconds),
        description=_bounded_text(body.get("description"), 4000),
        content_id=_bounded_text(body.get("content_id"), 300),
        media_type=_safe_scalar(body.get("media_type")),
        cover_url=_redact_optional_url(body.get("cover_url")),
        created_at=_optional_int(body.get("created_at")),
        author=_safe_author(body.get("author")),
        background_music=_safe_background_music(body.get("background_music")),
        selected_variant=selected_variant or None,
        media_candidates=media_candidates,
        media_url=media_url,
        redacted_media_url=_redact_media_url(media_url),
        next_action="解析成功。点击下载后将自动交给 MediaGo 开始下载。",
    )


def record_wechat_channels_resolution(
    share_url: str,
    resolved: dict[str, Any],
    *,
    output_dir: Path | str,
    store: TaskStore,
) -> dict[str, Any]:
    """通过现有 manifest / TaskStore / report / review-checklist 记录非敏感解析结果。"""
    output_path = Path(output_dir)
    safe_share_url = _redact_share_url(share_url)
    dedupe_key = (
        resolution_fingerprint(safe_share_url, resolved)
        if resolved.get("status") == "resolved"
        else None
    )
    if dedupe_key:
        task_id, created = store.get_or_create_task(
            safe_share_url,
            _selected_backend(resolved),
            dedupe_key,
        )
    else:
        task_id = store.create_task(safe_share_url, _selected_backend(resolved))
        created = True

    existing_artifacts = _artifact_paths(store, task_id) if not created else {}
    if not created and existing_artifacts:
        task = store.get_task(task_id)
        return {
            "service": "video-orchestrator",
            "platform": "wechat-channels",
            "task_id": task_id,
            "status": str(task.get("status") or "finished"),
            "duplicate": True,
            "dedupe_key": dedupe_key,
            "resolution": _non_sensitive_summary(resolved),
            "artifacts": existing_artifacts,
        }

    manifest = build_resolve_manifest(share_url, resolved, output_dir=output_path)
    manifest_path = write_manifest(manifest, output_path)

    status = resolved["status"]
    if status == "resolved":
        store.mark_finished(task_id, 0)
        record_status = "finished"
    elif status == "review_required":
        store.mark_retryable(task_id)
        record_status = "retryable"
    else:
        store.mark_failed(task_id, 1)
        record_status = "failed"

    summary = _record_summary(
        task_id=task_id,
        share_url=safe_share_url,
        resolved=resolved,
        status=record_status,
        manifest_path=manifest_path,
    )
    review = _build_resolution_review(resolved)
    review_path = write_quality_review(report_dir(output_path, task_id), review)
    report_path = write_task_report(output_path, {**summary, "review_checklist_path": str(review_path)})
    store.add_artifact(task_id, "manifest", manifest_path)
    store.add_artifact(task_id, "report", report_path)
    store.add_artifact(task_id, "review_checklist", review_path)

    return {
        "service": "video-orchestrator",
        "platform": "wechat-channels",
        "task_id": task_id,
        "status": record_status,
        "duplicate": not created,
        "dedupe_key": dedupe_key,
        "resolution": _non_sensitive_summary(resolved),
        "artifacts": {
            "manifest_path": str(manifest_path),
            "report_path": str(report_path),
            "review_checklist_path": str(review_path),
        },
    }


def build_resolve_manifest(
    share_url: str,
    resolved: dict[str, Any],
    *,
    output_dir: Path,
) -> dict[str, Any]:
    """生成解析计划 manifest（脱敏后落盘，绝不含完整媒体地址）。"""
    classification = classify_url(share_url)
    safe_share_url = _redact_share_url(share_url)
    route = classification.model_dump()
    if isinstance(route.get("url"), str):
        route["url"] = safe_share_url
    return {
        "url": safe_share_url,
        "classification": classification.kind,
        "route": route,
        "selected_backend": _selected_backend(resolved),
        "fallback_backends": [],
        "command": [
            "python",
            "-m",
            "video_orchestrator.cli",
            "wechat-channels-resolve",
            safe_share_url,
            "--output-dir",
            output_dir.as_posix(),
        ],
        "request_headers": {},
        "yt_dlp_options": {},
        "output_dir": output_dir.as_posix(),
        "status": resolved["status"],
        "resolution": _non_sensitive_summary(resolved),
        "will_download": False,
        "created_at": datetime.now(UTC).isoformat(),
    }


def resolve_and_import_wechat_channels(
    share_url: str,
    *,
    output_dir: Path | str = "downloads",
    store: TaskStore | None = None,
    db_path: Path | str = ".vdo/tasks.sqlite",
    start_download: bool | None = None,
    provider: str | None = None,
    transport: str | None = None,
    quality: str | None = None,
    force_reimport: bool = False,
    timeout_seconds: float = 10,
    request_post: Callable[..., Any] | None = None,
    profile_request_get: Callable[..., Any] | None = None,
    import_post: Callable[..., Any] = requests.post,
    mediago_request_get: Callable[..., Any] = requests.get,
    resolver: Resolver | None = None,
) -> dict[str, Any]:
    """解析视频号分享链接，记录非敏感结果，并把媒体地址显式导入 MediaGo。

    调用本函数本身就是显式用户动作（CLI/API/MediaGo 的下载入口）；默认
    ``start_download=true``。provider、transport 和 quality 是手动模式覆盖项。
    """
    defaults = wechat_channels_defaults()
    resolved_start_download = defaults["start_download"] if start_download is None else start_download
    resolved_output_dir, storage = _select_writable_output_dir(output_dir)
    resolved = resolve_wechat_channels_share_auto(
        share_url,
        provider=provider or defaults["provider"],
        transport=transport or defaults["transport"],
        quality=quality or defaults["quality"],
        request_post=request_post,
        profile_request_get=profile_request_get,
        resolver=resolver,
    )
    task_store = store if store is not None else _open_task_store(db_path, fallback_output_dir=resolved_output_dir)
    record = record_wechat_channels_resolution(share_url, resolved, output_dir=resolved_output_dir, store=task_store)

    if resolved["status"] != "resolved":
        return {
            **record,
            "service": "video-orchestrator",
            "platform": "wechat-channels",
            "explicit_user_action": True,
            "will_download": False,
            "imported": False,
            "storage": storage,
        }

    existing_receipt = _load_mediago_receipt(record.get("artifacts", {}).get("mediago_receipt_path"))
    existing_task_ids = _receipt_task_ids(existing_receipt)
    if record.get("duplicate") and existing_task_ids and not force_reimport:
        return {
            **record,
            "service": "video-orchestrator",
            "platform": "wechat-channels",
            "explicit_user_action": True,
            "will_download": False,
            "imported": False,
            "duplicate": True,
            "storage": storage,
            "media_import": {
                "status": "already_imported",
                "will_download": False,
                "task_receipt": existing_receipt,
                "reconciliation": reconcile_mediago_tasks(
                    existing_task_ids,
                    timeout_seconds=timeout_seconds,
                    request_get=mediago_request_get,
                ),
                "next_action": "该内容已导入 MediaGo，未重复创建任务；请查看现有任务状态。需要重新导入时使用强制重导选项。",
            },
        }

    media_url = str(resolved["media_url"])
    title = resolved.get("title")
    task_names = {media_url: title} if title else None
    imported = import_to_mediago(
        [media_url],
        output_dir=resolved_output_dir,
        start_download=resolved_start_download,
        timeout_seconds=timeout_seconds,
        request_post=import_post,
        task_names=task_names,
    )
    if imported["status"] == "imported":
        receipt = imported.get("task_receipt")
        if isinstance(receipt, dict) and _receipt_task_ids(receipt):
            receipt_path = _write_mediago_receipt(resolved_output_dir, record["task_id"], receipt)
            task_store.add_artifact(record["task_id"], "mediago_receipt", receipt_path)
            record["artifacts"]["mediago_receipt_path"] = str(receipt_path)
    return {
        **record,
        "service": "video-orchestrator",
        "platform": "wechat-channels",
        "explicit_user_action": True,
        "will_download": bool(resolved_start_download),
        "imported": imported["status"] == "imported",
        "storage": storage,
        "media_import": _non_sensitive_mediago_result(imported),
    }


# ---- 内部工具 ----

def _payload(status: str, *, share_url: str, http_base: str | None, **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "service": "video-orchestrator",
        "platform": "wechat-channels",
        "status": status,
        "share_url": _redact_share_url(str(share_url)),
        "http_base": http_base,
        "will_download": False,
        "credentials_accessed": False,
        **extra,
    }
    return payload


def _non_sensitive_summary(resolved: dict[str, Any]) -> dict[str, Any]:
    """只保留可以落盘/日志输出的非敏感字段。"""
    summary = {
        "status": resolved["status"],
        "reason": resolved.get("reason"),
        "title": resolved.get("title"),
        "source": resolved.get("source"),
        "quality": resolved.get("quality"),
        "quality_mode": resolved.get("quality_mode"),
        "provider": resolved.get("provider"),
        "transport": resolved.get("transport"),
        "candidate_count": resolved.get("candidate_count"),
        "available_candidates": resolved.get("available_candidates"),
        "provider_attempts": resolved.get("provider_attempts"),
        "duration_seconds": resolved.get("duration_seconds"),
        "duration_hint": resolved.get("duration_hint"),
        "description": resolved.get("description"),
        "content_id": resolved.get("content_id"),
        "media_type": resolved.get("media_type"),
        "cover_url": resolved.get("cover_url"),
        "created_at": resolved.get("created_at"),
        "author": resolved.get("author"),
        "background_music": resolved.get("background_music"),
        "selected_variant": resolved.get("selected_variant"),
        "media_candidates": resolved.get("media_candidates"),
        "http_base": resolved.get("http_base"),
    }
    if resolved.get("redacted_media_url"):
        summary["redacted_media_url"] = resolved["redacted_media_url"]
    return {key: value for key, value in summary.items() if value is not None}


def _record_summary(
    *,
    task_id: int,
    share_url: str,
    resolved: dict[str, Any],
    status: str,
    manifest_path: Path,
) -> dict[str, Any]:
    now = datetime.now(UTC).isoformat()
    return {
        "task_id": task_id,
        "url": share_url,
        "selected_backend": _selected_backend(resolved),
        "status": status,
        "command": [
            "python",
            "-m",
            "video_orchestrator.runtime.wechat_channels_resolve",
            share_url,
        ],
        "attempts": [
            {
                "backend": _selected_backend(resolved),
                "command": ["python", "-m", "video_orchestrator.runtime.wechat_channels_resolve", share_url],
                "returncode": 0 if status == "finished" else 1,
                "status": status,
                "resolution": _non_sensitive_summary(resolved),
            }
        ],
        "output_dir": str(manifest_path.parents[2]),
        "log_path": None,
        "backend_result": _non_sensitive_summary(resolved),
        "started_at": now,
        "finished_at": now,
        "next_action": str(resolved.get("next_action") or "请查看解析摘要后决定是否显式导入 MediaGo。"),
    }


def _build_resolution_review(resolved: dict[str, Any]) -> dict[str, Any]:
    status = resolved["status"]
    if status == "resolved":
        outcome = "ok"
        label = "解析成功，媒体可下载"
        next_action = "解析已完成；点击下载后由 MediaGo 自动开始下载。"
    elif status == "review_required":
        outcome = "warning"
        label = "解析需要人工处理"
        next_action = str(
            resolved.get("next_action")
            or "授权缺失、过期或暂不可下载。请完成本机授权后重试，或改用微信桌面端播放捕获。"
        )
    else:
        outcome = "failed"
        label = "解析失败"
        next_action = str(resolved.get("next_action") or "解析失败。请检查 helper 状态后重试。")
    return {
        "status": outcome,
        "needs_review": status != "resolved",
        "manual_review_required": True,
        "quality_label": label,
        "next_action": next_action,
        "checks": [
            {
                "name": "helper_status",
                "status": "ok" if status != "unavailable" else "failed",
                "message": f"resolution status is {status}",
            },
            {
                "name": "media_available",
                "status": "ok" if status == "resolved" else "failed",
                "message": "resolved media address available in memory only"
                if status == "resolved"
                else "no downloadable media address",
            },
            {
                "name": "credentials",
                "status": "ok",
                "message": "no credentials or decryption parameters were recorded",
            },
        ],
        "resolution": _non_sensitive_summary(resolved),
        "created_at": datetime.now(UTC).isoformat(),
    }


def report_dir(output_dir: Path, task_id: int) -> Path:
    root = output_dir if output_dir.name == ".vdo" else output_dir / ".vdo"
    return root / "reports" / str(task_id)


def _selected_backend(resolved: dict[str, Any]) -> str:
    if resolved.get("provider") == "public-profile":
        return "wechat-channels-public-profile"
    return "wechat-channels-helper"


def wechat_channels_defaults() -> dict[str, Any]:
    """Return the single configured source for automatic import defaults."""

    defaults = dict(wechat_channels_config().get("defaults") or {})
    return {
        "provider": str(defaults.get("provider") or "auto"),
        "transport": str(defaults.get("transport") or "auto"),
        "quality": str(defaults.get("quality") or "highest"),
        "start_download": bool(defaults.get("start_download", True)),
    }


def _select_writable_output_dir(output_dir: Path | str) -> tuple[Path, dict[str, Any]]:
    requested = Path(output_dir).expanduser().resolve()
    if _directory_writable(requested):
        return requested, {
            "requested_output_dir": str(requested),
            "effective_output_dir": str(requested),
            "fallback_used": False,
        }
    fallback = runtime_state_dir() / "downloads" / "wechat-channels"
    if not _directory_writable(fallback):
        raise OSError("视频号运行目录不可写，且统一 fallback 目录也不可用。")
    return fallback, {
        "requested_output_dir": str(requested),
        "effective_output_dir": str(fallback),
        "fallback_used": True,
        "reason": "requested_output_dir_not_writable",
    }


def _directory_writable(path: Path) -> bool:
    probe = path / f".vdo-write-check-{uuid4().hex}"
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe.write_text("ok", encoding="ascii")
        probe.unlink()
    except OSError:
        return False
    return True


def _open_task_store(db_path: Path | str, *, fallback_output_dir: Path) -> TaskStore:
    requested = Path(db_path).expanduser().resolve()
    try:
        return TaskStore(requested)
    except (OSError, sqlite3.Error):
        return TaskStore(fallback_output_dir / ".vdo" / "tasks.sqlite")


def _resolved_config() -> dict[str, Any]:
    config = dict(wechat_channels_config().get("helper") or {})
    media_host_suffixes = config.get("media_host_suffixes") or DEFAULT_MEDIA_HOST_SUFFIXES
    if isinstance(media_host_suffixes, str):
        media_host_suffixes = [suffix.strip() for suffix in media_host_suffixes.split(",") if suffix.strip()]
    return {
        "http_base": str(config.get("http_base") or "").strip().rstrip("/"),
        "resolve_endpoint": str(config.get("resolve_endpoint") or DEFAULT_RESOLVE_ENDPOINT).lstrip("/"),
        "timeout_seconds": float(config.get("timeout_seconds") or 2),
        "api_key_env": str(config.get("api_key_env") or ""),
        "media_host_suffixes": tuple(media_host_suffixes),
    }


def _auth_headers(config: dict[str, Any]) -> dict[str, str]:
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    api_key_env = config.get("api_key_env") or ""
    if api_key_env:
        api_key = os.environ.get(api_key_env)
        if api_key:
            headers["X-API-Key"] = api_key
    return headers


def _loopback_post(url: str, **kwargs: Any) -> requests.Response:
    """Call the local helper without inheriting process proxy settings."""

    with requests.Session() as session:
        session.trust_env = False
        return session.post(url, **kwargs)


def _host_allowed(hostname: str, suffixes: tuple[str, ...]) -> bool:
    normalized = hostname.lower().rstrip(".")
    return any(normalized == suffix or normalized.endswith(f".{suffix}") for suffix in suffixes)


def _hostname(url: str) -> str:
    try:
        from urllib.parse import urlsplit

        return str(urlsplit(url).hostname or "").lower().rstrip(".")
    except ValueError:
        return ""


def _redact_media_url(url: str) -> str:
    """媒体地址的脱敏形式：保留 scheme/host/path，整体丢弃 query 与 fragment。

    签名 CDN URL 的 query 携带签名参数（如 encfilekey），值级脱敏无法覆盖
    腾讯自定义参数名；因此媒体地址一律不保留任何查询参数，保证完整签名
    URL 不进入 manifest、report、日志或数据库。
    """
    try:
        parsed = urlsplit(url)
    except ValueError:
        return redact_url(url)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def _redact_share_url(url: str) -> str:
    try:
        parsed = urlsplit(url)
    except ValueError:
        return redact_url(url)
    host = str(parsed.hostname or "").lower().rstrip(".")
    if host in {"weixin.qq.com", "channels.weixin.qq.com"}:
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
    return redact_url(url)


def _non_sensitive_mediago_result(result: dict[str, Any]) -> dict[str, Any]:
    """Return an audit result without replayable media URLs or header material."""

    safe = deepcopy(result)
    payload = safe.get("payload")
    if isinstance(payload, dict) and isinstance(payload.get("tasks"), list):
        for task in payload["tasks"]:
            if not isinstance(task, dict):
                continue
            if isinstance(task.get("url"), str):
                task["url"] = _redact_media_url(task["url"])
            if task.get("headers"):
                task["headers"] = "<redacted>"
    safe["deeplink"] = None
    if "response" in safe:
        safe["response"] = {"received": safe["response"] is not None}
    if safe.get("error"):
        safe["error"] = "MediaGo import failed; inspect the local MediaGo log for details."
    return safe


def _safe_variant(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    allowed = (
        "variant_key",
        "codec",
        "width",
        "height",
        "bitrate",
        "frame_rate",
        "file_size",
        "quality_score",
        "duration_seconds",
    )
    return {key: value.get(key) for key in allowed if _safe_scalar(value.get(key)) is not None}


def _safe_author(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    result = {
        "id": _bounded_text(value.get("id"), 300),
        "name": _bounded_text(value.get("name"), 300),
    }
    safe = {key: item for key, item in result.items() if item is not None}
    return safe or None


def _safe_background_music(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    result = {
        "name": _bounded_text(value.get("name"), 300),
        "artist": _bounded_text(value.get("artist"), 300),
    }
    safe = {key: item for key, item in result.items() if item is not None}
    return safe or None


def _safe_scalar(value: Any) -> str | int | float | None:
    if value is None or isinstance(value, bool):
        return None
    return value if isinstance(value, (str, int, float)) else None


def _bounded_text(value: Any, limit: int) -> str | None:
    scalar = _safe_scalar(value)
    if scalar is None:
        return None
    text = str(scalar).strip()
    return text[:limit] if text else None


def _redact_optional_url(value: Any) -> str | None:
    text = _bounded_text(value, 2048)
    return _redact_media_url(text) if text else None


def _artifact_paths(store: TaskStore, task_id: int) -> dict[str, str]:
    key_by_kind = {
        "manifest": "manifest_path",
        "report": "report_path",
        "review_checklist": "review_checklist_path",
        "mediago_receipt": "mediago_receipt_path",
    }
    return {
        key_by_kind[item["kind"]]: str(item["path"])
        for item in store.list_artifacts(task_id)
        if item.get("kind") in key_by_kind
    }


def _write_mediago_receipt(output_dir: Path, task_id: int, receipt: dict[str, Any]) -> Path:
    path = report_dir(output_dir, task_id) / "mediago-receipt.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _load_mediago_receipt(path: Any) -> dict[str, Any] | None:
    if not isinstance(path, str) or not path:
        return None
    try:
        body = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return body if isinstance(body, dict) and body.get("contract") == "vdo_mediago_import_receipt_v1" else None


def _receipt_task_ids(receipt: Any) -> list[int]:
    if not isinstance(receipt, dict) or not isinstance(receipt.get("task_ids"), list):
        return []
    return [int(item) for item in receipt["task_ids"] if str(item).isdigit()]


def _optional_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _optional_int(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def _format_duration(duration_seconds: float | None) -> str | None:
    if duration_seconds is None:
        return None
    total = int(duration_seconds)
    minutes, seconds = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}小时{minutes}分{seconds}秒"
    if minutes:
        return f"{minutes}分{seconds}秒"
    return f"{seconds}秒"
