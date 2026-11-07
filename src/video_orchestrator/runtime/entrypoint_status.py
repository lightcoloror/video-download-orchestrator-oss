from __future__ import annotations

from datetime import UTC, datetime
import os
import socket
from pathlib import Path
from typing import Any, Callable

from video_orchestrator.runtime.config import runtime_state_dir
from video_orchestrator.runtime.config import web_config
from video_orchestrator.runtime.service_lifecycle import build_service_lifecycle_status


ListenerProbe = Callable[[str, int], bool]


def build_entrypoint_status(
    *,
    listener_probe: ListenerProbe | None = None,
    planning_cli_ready: bool = True,
    execute_context: dict[str, Any] | None = None,
    checked_at: datetime | None = None,
) -> dict[str, Any]:
    """Report independent planning/listener/execute states without executing anything."""

    timestamp = (checked_at or datetime.now(UTC)).isoformat()
    web = web_config()
    host = str(web["host"])
    port = int(web["port"])
    probe = listener_probe or _tcp_listener_probe
    listener_ready = bool(probe(host, port))
    project_root = Path(__file__).resolve().parents[3]
    lifecycle = build_service_lifecycle_status(
        metadata_path=runtime_state_dir() / "ui-server.json",
        expected_host=host,
        expected_port=port,
        expected_project_root=project_root,
        listener_ready=listener_ready,
    )

    planning_cli = {
        "status": "ready" if planning_cli_ready else "unavailable",
        "reason": "cli_process_running" if planning_cli_ready else "cli_import_or_config_failed",
        "checked_at": timestamp,
        "available_interfaces": [
            "python -m video_orchestrator.cli route",
            "python -m video_orchestrator.cli openclaw-plan",
            "python -m video_orchestrator.cli entrypoint-status",
        ] if planning_cli_ready else [],
        "will_download": False,
        "execute": False,
    }
    http_listener = {
        "status": "healthy" if listener_ready else "stopped",
        "reason": "configured_port_accepting_connections" if listener_ready else "configured_port_not_listening",
        "checked_at": timestamp,
        "base_url": f"{web['scheme']}://{host}:{port}",
        "available_interfaces": ["GET /health", "GET /entrypoints/status", "POST /integrations/openclaw/link/plan"] if listener_ready else [],
        "will_download": False,
        "execute": False,
    }
    execute_gate = evaluate_execute_gate(execute_context, checked_at=timestamp)

    if planning_cli_ready:
        overall = "ready_for_planning" if listener_ready else "cli_planning_ready"
    elif listener_ready:
        overall = "http_planning_ready"
    else:
        overall = "unavailable"

    return {
        "service": "video-download-orchestrator",
        "contract": "vdo_entrypoint_status_v1",
        "status": overall,
        "checked_at": timestamp,
        "planning_cli": planning_cli,
        "http_listener": http_listener,
        "service_lifecycle": lifecycle,
        "execute_gate": execute_gate,
        "will_download": False,
        "execute": False,
        "next_action": _next_action(overall),
    }


def evaluate_execute_gate(context: dict[str, Any] | None, *, checked_at: str | None = None) -> dict[str, Any]:
    timestamp = checked_at or datetime.now(UTC).isoformat()
    context = context or {}
    if not context:
        reason = "no_execution_request"
    elif context.get("stale_request"):
        reason = "stale_request"
    elif not context.get("allowlist_configured", _allowlist_configured()):
        reason = "allowlist_not_configured"
    elif not context.get("actor_authorized"):
        reason = "actor_not_allowed"
    elif not context.get("confirm"):
        reason = "confirmation_required"
    elif context.get("sensitive_confirmation_required") and not context.get("confirm_sensitive"):
        reason = "sensitive_confirmation_required"
    else:
        reason = "independent_execute_call_required"
    return {
        "status": "blocked" if reason != "independent_execute_call_required" else "eligible_for_explicit_recheck",
        "reason": reason,
        "checked_at": timestamp,
        "available_interfaces": ["CLI openclaw-execute", "POST /integrations/openclaw/telegram/execute"],
        "requirements": ["actor allowlist", "confirm", "confirm-sensitive when required", "fresh unique request_id"],
        "will_download": False,
        "execute": False,
    }


def _allowlist_configured() -> bool:
    return bool(os.environ.get("VDO_TELEGRAM_ALLOWED_USER_IDS", "").strip())


def _tcp_listener_probe(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.2):
            return True
    except OSError:
        return False


def _next_action(status: str) -> str:
    if status == "cli_planning_ready":
        return "本地 planning CLI 可用；HTTP listener 已停止。需要 HTTP 时必须另行显式启动服务。"
    if status == "ready_for_planning":
        return "CLI 与 HTTP planning 入口可用；真实执行仍需独立通过 allowlist 和确认门禁。"
    if status == "http_planning_ready":
        return "HTTP planning 入口可用，但当前 CLI 检查失败；不要据此授权执行。"
    return "CLI 与 HTTP planning 入口均不可用；不要尝试执行下载。"
