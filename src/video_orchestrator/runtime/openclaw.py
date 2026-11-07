from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from video_orchestrator.runtime.batch_cleaner import clean_batch_input
from video_orchestrator.runtime.manifests import write_manifest
from video_orchestrator.runtime.openclaw_security import OpenClawReplayGuard
from video_orchestrator.runtime.page_link import page_link_site_adapter_diagnostic
from video_orchestrator.runtime.redaction import redact_manifest
from video_orchestrator.runtime.redaction import redact_url
from video_orchestrator.runtime.task_store import TaskStore
from video_orchestrator.services import plan_download

DEFAULT_REPLAY_GUARD = OpenClawReplayGuard()


def build_openclaw_link_plan(
    raw: object,
    *,
    output_dir: str | Path = "downloads/openclaw",
    backend: str | None = None,
    write_manifests: bool = True,
    include_manifests: bool = False,
) -> dict[str, Any]:
    """Plan Telegram/OpenClaw pasted links without running downloaders."""

    clean_payload = clean_batch_input(raw)
    plan_id = "openclaw-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    batch_dir = Path(output_dir) / plan_id
    items = []

    for index, item in enumerate(clean_payload["items"], start=1):
        item_dir = batch_dir / f"item-{index:03d}"
        item_backend = item.get("backend") or backend
        manifest = plan_download(str(item["url"]), output_dir=item_dir, backend=item_backend)
        manifest_path = write_manifest(redact_manifest(manifest), item_dir) if write_manifests else None
        route = dict(manifest["route"])
        route["url"] = redact_url(str(route.get("url") or item["url"]))
        site_adapter = page_link_site_adapter_diagnostic(str(item["url"]))
        plan_item: dict[str, Any] = {
            "index": index,
            "source_index": item.get("source_index"),
            "url": redact_url(str(item["url"])),
            "route": route,
            "site_adapter": site_adapter,
            "selected_backend": manifest["selected_backend"],
            "fallback_backends": list(manifest.get("fallback_backends") or []),
            "extraction_method": _extraction_method(route, site_adapter),
            "needs_browser_sniffing_if_direct_fails": _needs_browser_sniffing(route, site_adapter),
            "will_download": False,
            "manifest_path": str(manifest_path) if manifest_path else None,
            "next_action": _next_action(route, site_adapter),
        }
        if include_manifests:
            plan_item["manifest"] = redact_manifest(manifest)
        items.append(plan_item)

    return {
        "service": "video-download-orchestrator",
        "contract": "openclaw_telegram_link_plan_v1",
        "status": "planned" if items else "empty",
        "will_download": False,
        "execute": False,
        "explicit_user_action_required_for_download": True,
        "plan_id": plan_id,
        "batch_dir": str(batch_dir) if write_manifests else None,
        "cleaning": _redacted_cleaning(clean_payload),
        "item_count": len(items),
        "items": items,
        "execution_boundary": {
            "default": "planning_only",
            "allowed_execution": "只有经过人工确认的本地显式动作，才允许调用 execute endpoint 或 CLI 真实下载命令。",
            "forbidden": "不要自动下载任意 Telegram 链接。",
        },
        "docker_access": {
            "preferred_api_base": "http://host.docker.internal:8921",
            "host_fallback_api_base": "http://127.0.0.1:8921",
            "health_path": "/health",
        },
    }


def execute_openclaw_telegram_command(
    raw: object,
    *,
    actor_id: str,
    output_dir: str | Path = "downloads/openclaw",
    db_path: str | Path = ".vdo/tasks.sqlite",
    backend: str | None = None,
    approval_channel: str = "telegram",
    approval_command: str = "",
    confirm: bool = False,
    confirm_sensitive: bool = False,
    request_id: str | None = None,
    requested_at: str | None = None,
    allowed_actor_ids: list[str] | None = None,
    hard_timeout: int | None = None,
    idle_timeout: int | None = None,
    ytdlp_options: dict[str, object] | None = None,
    execute_manifest_func=None,
    replay_guard: OpenClawReplayGuard | None = None,
) -> dict[str, Any]:
    """Execute a Telegram command only after allowlist and confirmation checks."""

    allowed = allowed_actor_ids if allowed_actor_ids is not None else allowed_telegram_actor_ids()
    actor_check = _actor_check(actor_id, allowed)
    actor_payload = {
        "id": actor_id,
        "authorized": actor_check["authorized"],
        "reason": actor_check["reason"],
    }
    if not actor_check["authorized"]:
        return {
            "service": "video-download-orchestrator",
            "contract": "openclaw_telegram_execute_v1",
            "approval_channel": approval_channel,
            "approval_command": approval_command,
            "actor": actor_payload,
            "status": "blocked",
            "will_download": False,
            "execute": False,
            "explicit_user_action": False,
            "next_action": "Telegram 用户不在允许列表中，已拒绝执行。请配置 VDO_TELEGRAM_ALLOWED_USER_IDS 或传入允许列表。",
        }

    plan = build_openclaw_link_plan(
        raw,
        output_dir=output_dir,
        backend=backend,
        write_manifests=False,
        include_manifests=False,
    )
    summary = _execution_summary(plan, ytdlp_options or {})

    base_payload = {
        "service": "video-download-orchestrator",
        "contract": "openclaw_telegram_execute_v1",
        "approval_channel": approval_channel,
        "approval_command": approval_command,
        "actor": actor_payload,
        "summary": summary,
        "plan": plan,
        "explicit_user_action": bool(confirm),
        "request_id_present": bool(request_id),
    }
    if not confirm:
        return {
            **base_payload,
            "status": "confirmation_required",
            "will_download": False,
            "next_action": _confirmation_next_action(summary),
        }
    if summary["requires_sensitive_confirmation"] and not confirm_sensitive:
        return {
            **base_payload,
            "status": "sensitive_confirmation_required",
            "will_download": False,
            "next_action": "该任务涉及页面嗅探、登录态/Cookie 或不确定网页提取。请追加敏感确认后再执行。",
        }
    if not plan["items"]:
        return {
            **base_payload,
            "status": "empty",
            "will_download": False,
            "next_action": "没有识别到可执行的视频链接。",
        }
    replay_check = (replay_guard or DEFAULT_REPLAY_GUARD).check_and_claim(
        actor_id=actor_id,
        request_id=request_id,
        requested_at=requested_at,
    )
    if replay_check["status"] != "accepted":
        return {
            **base_payload,
            "status": str(replay_check["reason"]),
            "will_download": False,
            "execute": False,
            "replay_guard": replay_check,
            "next_action": str(replay_check["next_action"]),
        }
    if execute_manifest_func is None:
        raise ValueError("execute_manifest_func is required for real execution")

    clean_payload = clean_batch_input(raw)
    plan_id = "openclaw-exec-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    batch_dir = Path(output_dir) / plan_id
    store = TaskStore(Path(db_path).resolve())
    results = []
    for index, item in enumerate(clean_payload["items"], start=1):
        item_dir = batch_dir / f"item-{index:03d}"
        manifest = plan_download(
            str(item["url"]),
            output_dir=item_dir,
            backend=item.get("backend") or backend,
            ytdlp_options=ytdlp_options or {},
        )
        manifest["batch_id"] = plan_id
        manifest["batch_item_index"] = index
        manifest["source"] = "openclaw_telegram_execute"
        manifest_path = write_manifest(manifest, item_dir)
        item_result = {
            "index": index,
            "url": redact_url(str(item["url"])),
            "backend": manifest["selected_backend"],
            "manifest_path": str(manifest_path),
            "will_download": True,
        }
        item_result.update(
            execute_manifest_func(
                store=store,
                manifest=manifest,
                artifact_base_dir=item_dir,
                manifest_path=manifest_path,
                extra_payload={
                    "openclaw_approval": {
                        "actor_id": actor_id,
                        "approval_channel": approval_channel,
                        "approval_command": approval_command,
                    }
                },
                hard_timeout=hard_timeout,
                idle_timeout=idle_timeout,
            )
        )
        results.append(item_result)

    return {
        **base_payload,
        "status": _execution_status(results),
        "will_download": True,
        "execute": True,
        "replay_guard": replay_check,
        "batch_dir": str(batch_dir),
        "item_count": len(results),
        "items": results,
        "next_action": "下载已由 Telegram 显式命令触发。完成后请检查 report.md 和 review-checklist.json。",
    }


def allowed_telegram_actor_ids(env: dict[str, str] | None = None) -> list[str]:
    source = (env or os.environ).get("VDO_TELEGRAM_ALLOWED_USER_IDS", "")
    return [item.strip() for item in source.replace(";", ",").split(",") if item.strip()]


def _redacted_cleaning(clean_payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "contract": clean_payload["contract"],
        "input_count": clean_payload["input_count"],
        "item_count": clean_payload["item_count"],
        "skipped_count": clean_payload["skipped_count"],
        "items": [{**item, "url": redact_url(str(item.get("url") or "")), "source_url": redact_url(str(item.get("source_url") or ""))} for item in clean_payload["items"]],
        "skipped": [{**item, "url": redact_url(str(item.get("url") or ""))} for item in clean_payload["skipped"]],
        "defaults": clean_payload["defaults"],
    }


def _extraction_method(route: dict[str, Any], site_adapter: dict[str, Any]) -> str:
    kind = str(route.get("kind") or "")
    if site_adapter.get("matched"):
        if site_adapter.get("fallback_mode") == "browser_sniffing":
            return "平台适配：优先普通下载，失败后建议页面嗅探"
        return "站点适配"
    if kind in {"manifest", "direct-transfer", "live", "gallery", "spotify"}:
        return "普通下载"
    return "普通下载尝试；失败后页面嗅探"


def _needs_browser_sniffing(route: dict[str, Any], site_adapter: dict[str, Any]) -> bool:
    kind = str(route.get("kind") or "")
    return kind == "general" or site_adapter.get("fallback_mode") == "browser_sniffing"


def _next_action(route: dict[str, Any], site_adapter: dict[str, Any]) -> str:
    kind = str(route.get("kind") or "")
    if kind in {"manifest", "direct-transfer"}:
        return "已生成普通下载计划；人工确认后才可执行真实下载。"
    if site_adapter.get("supports_site_fallback"):
        return "已命中站点适配；可先人工审核计划，必要时由用户显式触发页面链接下载。"
    if site_adapter.get("fallback_mode") == "browser_sniffing" or kind == "general":
        return "先保留规划结果；如果普通下载失败，再由用户打开页面播放视频并触发页面嗅探。"
    return "已生成规划结果；真实下载需要二次确认。"


def _actor_check(actor_id: str, allowed_actor_ids: list[str]) -> dict[str, Any]:
    if not actor_id:
        return {"authorized": False, "reason": "missing_actor_id"}
    if not allowed_actor_ids:
        return {"authorized": False, "reason": "allowlist_not_configured"}
    if actor_id not in allowed_actor_ids:
        return {"authorized": False, "reason": "actor_not_allowed"}
    return {"authorized": True, "reason": "actor_allowed"}


def _execution_summary(plan: dict[str, Any], ytdlp_options: dict[str, object]) -> dict[str, Any]:
    backend_counts: dict[str, int] = {}
    route_counts: dict[str, int] = {}
    sensitive_reasons = []
    for item in plan.get("items", []):
        backend = str(item.get("selected_backend") or "unknown")
        backend_counts[backend] = backend_counts.get(backend, 0) + 1
        kind = str((item.get("route") or {}).get("kind") or "unknown")
        route_counts[kind] = route_counts.get(kind, 0) + 1
        if item.get("needs_browser_sniffing_if_direct_fails"):
            sensitive_reasons.append("可能需要页面嗅探")
        site_adapter = item.get("site_adapter") or {}
        if site_adapter.get("fallback_mode") == "browser_sniffing":
            sensitive_reasons.append("平台页面失败后建议浏览器嗅探")
    if ytdlp_options.get("cookies_from_browser") or ytdlp_options.get("cookies"):
        sensitive_reasons.append("使用浏览器 Cookie 或 cookies.txt")
    sensitive_reasons = sorted(set(sensitive_reasons))
    labels = [
        f"链接 {plan.get('item_count', 0)} 个",
        "后端：" + "、".join(f"{name} {count}" for name, count in sorted(backend_counts.items())) if backend_counts else "后端：无",
        "路由：" + "、".join(f"{name} {count}" for name, count in sorted(route_counts.items())) if route_counts else "路由：无",
    ]
    if sensitive_reasons:
        labels.append("敏感确认：" + "、".join(sensitive_reasons))
    return {
        "label": "；".join(labels),
        "item_count": plan.get("item_count", 0),
        "backend_counts": backend_counts,
        "route_counts": route_counts,
        "requires_sensitive_confirmation": bool(sensitive_reasons),
        "sensitive_reasons": sensitive_reasons,
        "output_batch_dir": plan.get("batch_dir"),
    }


def _confirmation_next_action(summary: dict[str, Any]) -> str:
    if summary.get("requires_sensitive_confirmation"):
        return "请先核对摘要；若确认执行，发送带 confirm 和 sensitive 确认的 Telegram 命令。"
    return "请先核对摘要；若确认执行，发送带 confirm 的 Telegram 命令。"


def _execution_status(results: list[dict[str, Any]]) -> str:
    if not results:
        return "empty"
    statuses = {str(item.get("status") or "") for item in results}
    if statuses == {"finished"}:
        return "finished"
    if "finished" in statuses:
        return "partial"
    return "failed"
