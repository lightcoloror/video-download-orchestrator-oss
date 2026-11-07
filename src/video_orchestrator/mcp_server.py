from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from video_orchestrator.backends.base import get_backend_specs
from video_orchestrator.runtime.handoff import build_task_handoff
from video_orchestrator.runtime.handoff import build_url_handoff
from video_orchestrator.runtime.config import check_config
from video_orchestrator.runtime.manifests import write_manifest
from video_orchestrator.runtime.operator import artifact_map
from video_orchestrator.runtime.operator import operator_review_summary
from video_orchestrator.runtime.redaction import redact_manifest
from video_orchestrator.runtime.task_store import TaskStore
from video_orchestrator.runtime.vkp_handoff import build_vkp_handoff
from video_orchestrator.services import plan_download

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "video-download-orchestrator"
SERVER_VERSION = "0.1.0"
DEFAULT_DB = Path(".vdo/tasks.sqlite")


def list_tools() -> list[dict[str, Any]]:
    return [
        _tool(
            "vdo_config_check",
            "Return runtime config check results. Does not download.",
            {},
        ),
        _tool(
            "vdo_backends",
            "List configured backend specs and fallback metadata. Does not run backend binaries.",
            {},
        ),
        _tool(
            "vdo_route",
            "Classify a URL and select the preferred backend. Does not download.",
            {"url": {"type": "string"}},
            required=["url"],
        ),
        _tool(
            "vdo_plan_download",
            "Create a single download plan and optionally write a redacted manifest. Does not download.",
            {
                "url": {"type": "string"},
                "output_dir": {"type": "string", "default": "downloads"},
                "backend": {"type": ["string", "null"], "default": None},
                "write_manifest": {"type": "boolean", "default": True},
            },
            required=["url"],
        ),
        _tool(
            "vdo_batch_plan",
            "Create planning manifests for multiple URLs. Does not download.",
            {
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "url": {"type": "string"},
                            "backend": {"type": ["string", "null"], "default": None},
                        },
                        "required": ["url"],
                    },
                },
                "output_dir": {"type": "string", "default": "downloads"},
                "write_manifests": {"type": "boolean", "default": True},
            },
            required=["items"],
        ),
        _tool(
            "vdo_list_tasks",
            "List task rows from the local SQLite task store.",
            {
                "db_path": {"type": "string", "default": ".vdo/tasks.sqlite"},
                "problem_only": {"type": "boolean", "default": False},
            },
        ),
        _tool(
            "vdo_get_task",
            "Read one task and its artifact paths from the local SQLite task store.",
            {
                "task_id": {"type": "integer"},
                "db_path": {"type": "string", "default": ".vdo/tasks.sqlite"},
            },
            required=["task_id"],
        ),
        _tool(
            "vdo_read_artifact",
            "Read a text artifact path under a .vdo directory.",
            {"path": {"type": "string"}},
            required=["path"],
        ),
        _tool(
            "vdo_operator_status",
            "Return planning-only operator status and retryable task count. Does not download.",
            {"db_path": {"type": "string", "default": ".vdo/tasks.sqlite"}},
        ),
        _tool(
            "vdo_task_retryable",
            "List retryable browser-sniffer/page-link tasks. Does not download.",
            {"db_path": {"type": "string", "default": ".vdo/tasks.sqlite"}},
        ),
        _tool(
            "vdo_task_review",
            "Return task review verdict and checklist summary. Does not download or accept the task.",
            {
                "task_id": {"type": "integer"},
                "db_path": {"type": "string", "default": ".vdo/tasks.sqlite"},
            },
            required=["task_id"],
        ),
        _tool(
            "vdo_task_resume_plan",
            "Plan retryable task recovery from a known media URL. Does not download.",
            {
                "task_id": {"type": "integer"},
                "db_path": {"type": "string", "default": ".vdo/tasks.sqlite"},
                "output_dir": {"type": "string", "default": "downloads"},
                "media_url": {"type": ["string", "null"], "default": None},
                "backend": {"type": ["string", "null"], "default": None},
                "write_manifest": {"type": "boolean", "default": True},
            },
            required=["task_id"],
        ),
        _tool(
            "vdo_handoff",
            "Return planning-only UI/CLI handoff guidance for a URL. Does not download.",
            {
                "url": {"type": "string"},
                "output_dir": {"type": "string", "default": "downloads"},
                "capture_session": {"type": ["string", "null"], "default": None},
            },
            required=["url"],
        ),
        _tool(
            "vdo_task_handoff",
            "Return planning-only UI/CLI handoff guidance for a task. Does not download.",
            {
                "task_id": {"type": "integer"},
                "db_path": {"type": "string", "default": ".vdo/tasks.sqlite"},
                "output_dir": {"type": "string", "default": "downloads"},
            },
            required=["task_id"],
        ),
        _tool(
            "vdo_task_vkp_handoff",
            "Return a planning-only VKP handoff manifest for a task. Does not download or call VKP.",
            {
                "task_id": {"type": "integer"},
                "db_path": {"type": "string", "default": ".vdo/tasks.sqlite"},
                "output_dir": {"type": "string", "default": "downloads/vkp-handoffs"},
                "require_accepted": {"type": "boolean", "default": False},
                "write_manifest": {"type": "boolean", "default": True},
            },
            required=["task_id"],
        ),
    ]


def call_tool(name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    args = arguments or {}
    if name == "vdo_config_check":
        return check_config()
    if name == "vdo_backends":
        return {"backends": [spec.model_dump() for spec in get_backend_specs()]}
    if name == "vdo_route":
        return _route(args)
    if name == "vdo_plan_download":
        return _plan_download_tool(args)
    if name == "vdo_batch_plan":
        return _batch_plan_tool(args)
    if name == "vdo_list_tasks":
        return _list_tasks_tool(args)
    if name == "vdo_get_task":
        return _get_task_tool(args)
    if name == "vdo_read_artifact":
        return _read_artifact_tool(args)
    if name == "vdo_operator_status":
        return _operator_status_tool(args)
    if name == "vdo_task_retryable":
        return _task_retryable_tool(args)
    if name == "vdo_task_review":
        return _task_review_tool(args)
    if name == "vdo_task_resume_plan":
        return _task_resume_plan_tool(args)
    if name == "vdo_handoff":
        return build_url_handoff(
            _required_str(args, "url"),
            output_dir=str(args.get("output_dir") or "downloads"),
            capture_session=args.get("capture_session"),
        )
    if name == "vdo_task_handoff":
        return build_task_handoff(
            int(args["task_id"]),
            db_path=Path(str(args.get("db_path") or DEFAULT_DB)),
            output_dir=str(args.get("output_dir") or "downloads"),
        )
    if name == "vdo_task_vkp_handoff":
        return build_vkp_handoff(
            int(args["task_id"]),
            db_path=Path(str(args.get("db_path") or DEFAULT_DB)),
            output_dir=str(args.get("output_dir") or "downloads/vkp-handoffs"),
            require_accepted=bool(args.get("require_accepted", False)),
            write_manifest=bool(args.get("write_manifest", True)),
        )
    raise ValueError(f"unknown tool: {name}")


def handle_request(request: dict[str, Any]) -> dict[str, Any] | None:
    request_id = request.get("id")
    method = request.get("method")
    try:
        if method == "initialize":
            return _result(
                request_id,
                {
                    "protocolVersion": PROTOCOL_VERSION,
                    "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                    "capabilities": {"tools": {}},
                },
            )
        if method == "notifications/initialized":
            return None
        if method == "tools/list":
            return _result(request_id, {"tools": list_tools()})
        if method == "tools/call":
            params = request.get("params") or {}
            payload = call_tool(str(params.get("name")), params.get("arguments") or {})
            return _result(request_id, _tool_result(payload))
        return _error(request_id, -32601, f"method not found: {method}")
    except Exception as exc:
        return _error(request_id, -32000, str(exc))


def serve_stdio() -> None:
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError as exc:
            response = _error(None, -32700, str(exc))
        else:
            response = handle_request(request)
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n")
            sys.stdout.flush()


def _tool(name: str, description: str, properties: dict[str, Any], *, required: list[str] | None = None) -> dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "inputSchema": {
            "type": "object",
            "properties": properties,
            "required": required or [],
            "additionalProperties": False,
        },
    }


def _route(args: dict[str, Any]) -> dict[str, Any]:
    manifest = plan_download(_required_str(args, "url"), output_dir="downloads", backend=args.get("backend"))
    return {
        "status": "planned",
        "will_download": False,
        "route": manifest["route"],
        "selected_backend": manifest["selected_backend"],
        "fallback_backends": manifest["fallback_backends"],
    }


def _plan_download_tool(args: dict[str, Any]) -> dict[str, Any]:
    output_dir = Path(str(args.get("output_dir") or "downloads"))
    manifest = plan_download(
        _required_str(args, "url"),
        output_dir=output_dir,
        backend=args.get("backend"),
    )
    manifest_path = write_manifest(manifest, output_dir) if bool(args.get("write_manifest", True)) else None
    return {
        "status": "planned",
        "will_download": False,
        "manifest": redact_manifest(manifest),
        "manifest_path": str(manifest_path) if manifest_path else None,
    }


def _batch_plan_tool(args: dict[str, Any]) -> dict[str, Any]:
    items = args.get("items")
    if not isinstance(items, list) or not items:
        raise ValueError("items must be a non-empty array")
    output_dir = Path(str(args.get("output_dir") or "downloads"))
    write_manifests = bool(args.get("write_manifests", True))
    results = []
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"item {index} must be an object")
        item_dir = output_dir / f"item-{index:03d}"
        manifest = plan_download(_required_str(item, "url"), output_dir=item_dir, backend=item.get("backend"))
        manifest["batch_item_index"] = index
        manifest_path = write_manifest(manifest, item_dir) if write_manifests else None
        results.append(
            {
                "index": index,
                "backend": manifest["selected_backend"],
                "will_download": False,
                "manifest": redact_manifest(manifest),
                "manifest_path": str(manifest_path) if manifest_path else None,
            }
        )
    return {
        "contract": "agent_batch_v1",
        "status": "planned",
        "execute": False,
        "item_count": len(results),
        "items": results,
    }


def _list_tasks_tool(args: dict[str, Any]) -> dict[str, Any]:
    store = TaskStore(Path(str(args.get("db_path") or DEFAULT_DB)).resolve())
    return {"status": "ok", "tasks": store.list_tasks(problem_only=bool(args.get("problem_only", False)))}


def _get_task_tool(args: dict[str, Any]) -> dict[str, Any]:
    store = TaskStore(Path(str(args.get("db_path") or DEFAULT_DB)).resolve())
    task_id = int(args.get("task_id"))
    return {
        "status": "ok",
        "task": store.get_task(task_id),
        "artifacts": {artifact["kind"]: artifact["path"] for artifact in store.list_artifacts(task_id)},
    }


def _read_artifact_tool(args: dict[str, Any]) -> dict[str, Any]:
    path = Path(_required_str(args, "path")).resolve()
    if ".vdo" not in path.parts:
        raise ValueError("artifact path must be inside a .vdo directory")
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(f"artifact not found: {path}")
    return {"status": "ok", "path": str(path), "text": path.read_text(encoding="utf-8")}


def _operator_status_tool(args: dict[str, Any]) -> dict[str, Any]:
    store = _task_store(args)
    retryable_tasks = store.list_retryable_tasks()
    return {
        "status": "ok",
        "will_download": False,
        "retryable_task_count": len(retryable_tasks),
        "retryable_tasks": retryable_tasks,
    }


def _task_retryable_tool(args: dict[str, Any]) -> dict[str, Any]:
    return {"status": "ok", "will_download": False, "tasks": _task_store(args).list_retryable_tasks()}


def _task_review_tool(args: dict[str, Any]) -> dict[str, Any]:
    store = _task_store(args)
    task_id = int(args.get("task_id"))
    task = store.get_task(task_id)
    artifacts = artifact_map(store.list_artifacts(task_id))
    review_summary = operator_review_summary(task, artifacts)
    return {
        "status": "ok",
        "will_download": False,
        "verdict": review_summary["verdict"],
        "task": task,
        "artifacts": artifacts,
        "review": review_summary["review"],
        "summary": review_summary["summary"],
        "next_action": review_summary["next_action"],
    }


def _task_resume_plan_tool(args: dict[str, Any]) -> dict[str, Any]:
    store = _task_store(args)
    task_id = int(args.get("task_id"))
    task = store.get_task(task_id)
    if task["status"] != "retryable":
        raise ValueError("task is not retryable")
    media_url = args.get("media_url")
    if not isinstance(media_url, str) or not media_url.strip():
        return {
            "status": "retryable",
            "will_download": False,
            "task": task,
            "diagnostic": "media_url is required to plan recovery through MCP; use browser/API latest capture or CLI task resume for live capture matching",
        }
    output_dir = Path(str(args.get("output_dir") or "downloads"))
    backend = args.get("backend")
    manifest = plan_download(media_url, output_dir=_versioned_resume_dir(output_dir, task_id), backend=backend)
    manifest["resumed_task_id"] = task_id
    manifest["source_page_url"] = task["url"]
    manifest["source"] = "mcp_operator_resume_plan"
    manifest_path = write_manifest(manifest, Path(manifest["output_dir"])) if bool(args.get("write_manifest", True)) else None
    return {
        "status": "planned",
        "will_download": False,
        "resumed_task": task,
        "manifest": redact_manifest(manifest),
        "manifest_path": str(manifest_path) if manifest_path else None,
    }


def _task_store(args: dict[str, Any]) -> TaskStore:
    return TaskStore(Path(str(args.get("db_path") or DEFAULT_DB)).resolve())


def _versioned_resume_dir(output_dir: Path, task_id: int) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return output_dir / ".vdo" / "resumes" / f"{stamp}-task-{task_id}"


def _required_str(args: dict[str, Any], name: str) -> str:
    value = args.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} is required")
    return value


def _tool_result(payload: dict[str, Any]) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)}]}


def _result(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


if __name__ == "__main__":
    serve_stdio()
