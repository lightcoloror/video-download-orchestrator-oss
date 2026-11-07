from __future__ import annotations

import os
from datetime import UTC, datetime
from collections.abc import Iterable
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl
from urllib.parse import urlencode
from urllib.parse import urlparse
from urllib.parse import urlunparse

import requests

from video_orchestrator.runtime.capture_lease import capture_lease_summary
from video_orchestrator.runtime.media_completeness import capture_selection_summary, sniffed_completeness_diagnostic

from video_orchestrator.router import classify_url
from video_orchestrator.runtime.config import mediago_config
from video_orchestrator.runtime.redaction import redact_ephemeral_url
from video_orchestrator.runtime.redaction import redact_url
from video_orchestrator.services import plan_download


def mediago_page_candidates(urls: Iterable[str], tasks: list[dict]) -> list[dict]:
    """Describe desktop-only recovery without starting a browser or a download."""
    by_url = {task["url"]: task for task in tasks}
    candidates = []
    for url in urls:
        route = classify_url(url)
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or route.kind not in {
            "general", "feishu-page", "tencent-meeting", "douyin-page", "xiaohongshu-page", "bilibili-page",
        }:
            continue
        # Established native platform tasks remain on the original queue path.
        if parsed.hostname and (parsed.hostname == "youtu.be" or parsed.hostname == "youtube.com" or parsed.hostname.endswith(".youtube.com")):
            continue
        if route.kind == "bilibili-page":
            continue
        native = by_url.get(url)
        candidates.append({"page_url": url, "native_task": native if native and native.get("type") == "youtube" else None, "route_kind": route.kind})
    return candidates


def build_mediago_capture_plan(capture: dict | None, *, page_url: str, started_at: datetime) -> dict:
    result = {"status": "waiting", "will_download": False, "payload": {"tasks": [], "startDownload": False},
              "next_action": "请在安装 Video Orchestrator Sniffer 扩展的浏览器中播放视频，正在等待同页捕获。"}
    if not capture or not capture_lease_summary(capture)["active"]:
        return result
    try:
        captured_at = datetime.fromisoformat(str(capture.get("captured_at", "")).replace("Z", "+00:00"))
        if captured_at.tzinfo is None or captured_at < started_at.astimezone(UTC):
            return result
        if urlparse(str(capture.get("page_url", "")))._replace(fragment="") != urlparse(page_url)._replace(fragment=""):
            return result
    except (ValueError, TypeError):
        return result
    selection = capture_selection_summary(capture)
    quality = sniffed_completeness_diagnostic(capture)
    result["quality_label"] = quality["label"]
    if not selection["eligible"] or quality["risk"] != "likely_complete":
        result["next_action"] = quality["next_action"]
        return result
    media_url = str(capture.get("url", ""))
    parsed = urlparse(media_url)
    headers = capture.get("request_headers") or {}
    # MediaGo persists task URLs/headers: do not silently export ephemeral credentials to its DB.
    if parsed.query or parsed.username or any(k.lower() not in {"user-agent", "referer"} for k in headers):
        return {**result, "status": "review_required", "next_action": "已捕获媒体，但地址含临时参数或授权头，不能直接写入 MediaGo 持久队列。请使用现有受控下载入口处理。"}
    if parsed.scheme not in {"http", "https"}:
        return result
    if capture.get("backend") == "n_m3u8dl_re" and not capture.get("manifest_probe", {}).get("has_endlist"):
        result["next_action"] = "已捕获播放清单，等待完整媒体清单探测或播放器后续请求。"
        return result
    if capture.get("manifest_probe", {}).get("has_encryption"):
        return {**result, "status": "review_required", "next_action": "播放清单存在加密标记，请使用现有受控下载入口，不自动交给队列。"}
    safe_headers = {}
    for key, value in headers.items():
        value = str(value)
        if "\r" in value or "\n" in value or (key.lower() == "referer" and (urlparse(value).query or urlparse(value).username)):
            return {**result, "status": "review_required", "next_action": "捕获头包含不能持久化的参数，请使用受控下载入口。"}
        safe_headers[key] = value
    task = {"type": "m3u8" if capture.get("backend") == "n_m3u8dl_re" else "direct", "url": media_url,
            "name": "", "headers": "\n".join(f"{key}: {value}" for key, value in safe_headers.items()), "folder": ""}
    return {**result, "status": "ready", "capture_id": capture.get("capture_id"),
            "payload": {"tasks": [task], "startDownload": False}, "next_action": "已捕获媒体，可以交回 MediaGo 队列。"}


def mediago_status(*, timeout_seconds: float = 1.5, request_get=requests.get) -> dict[str, Any]:
    config = _resolved_config()
    try:
        response = request_get(_join_url(config["desktop_http_base"], "/healthy"), headers=_auth_headers(config), timeout=timeout_seconds)
        ok = 200 <= int(response.status_code) < 300
        readiness = {}
        if ok:
            for name, endpoint in (("config_api", "/api/config"), ("queue_api", "/api/downloads")):
                try:
                    params = {"current": 1, "pageSize": 1} if name == "queue_api" else {}
                    checked = request_get(_join_url(config["desktop_http_base"], endpoint),
                                          headers=_auth_headers(config), timeout=timeout_seconds, params=params)
                    data = _response_data(_safe_json(checked))
                    shape_ok = isinstance(data, dict) and (
                        "local" in data if name == "config_api" else isinstance(data.get("list"), list)
                    )
                    readiness[name] = "ready" if 200 <= int(checked.status_code) < 300 and shape_ok else "unavailable"
                except (requests.RequestException, OSError, ValueError):
                    readiness[name] = "unavailable"
        api_ready = ok and len(readiness) == 2 and all(value == "ready" for value in readiness.values())
        return {
            "service": "video-download-orchestrator",
            "ui": "mediago",
            "status": "available" if api_ready else "degraded" if ok else "unavailable",
            "service_online": ok,
            "api_ready": api_ready,
            "readiness": readiness,
            "download_verified": False,
            "output_validity": "not_checked",
            "will_download": False,
            "http_base": config["desktop_http_base"],
            "custom_scheme": config["custom_scheme"],
            "http_status": int(response.status_code),
            "next_action": "MediaGo 已可用，可以把任务导入现成桌面 UI。"
            if api_ready
            else "MediaGo 配置或队列 API 尚不可用，请检查桌面客户端和 API 配置。" if ok
            else "MediaGo HTTP 服务未返回成功状态。请启动 MediaGo Desktop 后重试。",
        }
    except (requests.RequestException, OSError) as exc:
        return {
            "service": "video-download-orchestrator",
            "ui": "mediago",
            "status": "unavailable",
            "service_online": False,
            "api_ready": False,
            "download_verified": False,
            "will_download": False,
            "http_base": config["desktop_http_base"],
            "custom_scheme": config["custom_scheme"],
            "error": type(exc).__name__,
            "next_action": "未连接到 MediaGo Desktop。请先启动 MediaGo，或检查本项目配置里的 MediaGo HTTP 地址。",
        }


def mediago_task_status(
    task_id: int,
    *,
    timeout_seconds: float = 3,
    request_get=requests.get,
) -> dict[str, Any]:
    """Read one MediaGo task and return a non-sensitive status/output receipt."""

    config = _resolved_config()
    endpoint = _join_url(config["desktop_http_base"], f"/api/downloads/{int(task_id)}")
    try:
        response = request_get(endpoint, headers=_auth_headers(config), timeout=timeout_seconds)
    except (requests.RequestException, OSError) as exc:
        return {
            "service": "video-download-orchestrator",
            "ui": "mediago",
            "status": "unavailable",
            "will_download": False,
            "task_id": int(task_id),
            "error": type(exc).__name__,
            "next_action": "无法读取 MediaGo 任务状态。请先启动 MediaGo，再重新检查。",
        }
    if not 200 <= int(response.status_code) < 300:
        return {
            "service": "video-download-orchestrator",
            "ui": "mediago",
            "status": "not_found" if int(response.status_code) == 404 else "unavailable",
            "will_download": False,
            "task_id": int(task_id),
            "http_status": int(response.status_code),
            "next_action": "MediaGo 中没有该任务。" if int(response.status_code) == 404 else "MediaGo 状态接口暂不可用。",
        }
    body = _safe_json(response)
    task = _response_data(body)
    if not isinstance(task, dict):
        return {
            "service": "video-download-orchestrator",
            "ui": "mediago",
            "status": "invalid_response",
            "will_download": False,
            "task_id": int(task_id),
            "next_action": "MediaGo 返回了无法识别的任务状态。请查看 MediaGo 本地日志。",
        }
    task = _augment_mediago_output(
        task,
        task_id=int(task_id),
        config=config,
        timeout_seconds=timeout_seconds,
        request_get=request_get,
    )
    safe_task = _safe_mediago_task(task)
    return {
        "service": "video-download-orchestrator",
        "ui": "mediago",
        "status": "ok",
        "will_download": False,
        "task_id": int(task_id),
        "task": safe_task,
        "download_status": safe_task.get("status"),
        "output_path": safe_task.get("file"),
        "media_exists": bool(safe_task.get("exists")),
        "next_action": _mediago_status_next_action(str(safe_task.get("status") or ""), bool(safe_task.get("exists"))),
    }


def reconcile_mediago_tasks(
    task_ids: Iterable[int],
    *,
    timeout_seconds: float = 3,
    request_get=requests.get,
) -> dict[str, Any]:
    statuses = [
        mediago_task_status(task_id, timeout_seconds=timeout_seconds, request_get=request_get)
        for task_id in dict.fromkeys(int(task_id) for task_id in task_ids)
    ]
    return {
        "service": "video-download-orchestrator",
        "ui": "mediago",
        "status": "ok" if statuses and all(item["status"] == "ok" for item in statuses) else "degraded",
        "will_download": False,
        "task_count": len(statuses),
        "tasks": statuses,
    }


def build_mediago_handoff_plan(
    urls: Iterable[str],
    *,
    output_dir: str | Path = "downloads",
    backend: str | None = None,
    start_download: bool = False,
    task_names: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    config = _resolved_config()
    tasks: list[dict[str, str]] = []
    task_summaries: list[dict[str, Any]] = []
    wechat_channels_urls: list[str] = []
    blocked_items: list[dict[str, Any]] = []
    for url in urls:
        url = str(url).strip()
        if not url:
            continue
        route = classify_url(url)
        if route.kind == "wechat-channels-page":
            # P1 planning-only：视频号链接不生成下载任务，只做识别与计划
            wechat_channels_urls.append(url)
            continue
        capability = mediago_task_capability(url, route_kind=route.kind)
        if not capability["supported"]:
            blocked_items.append(
                {
                    "url": redact_url(url),
                    "route_kind": route.kind,
                    "media_kind": route.media_kind,
                    "reason": capability["reason"],
                    "recommended_path": capability["recommended_path"],
                    "will_download": False,
                }
            )
            continue
        tasks.append(_task_for_url(url, output_dir=output_dir, backend=backend, name_override=_task_name(task_names, url)))
        task_summaries.append(
            {
                "url": _summary_url(url),
                "route_kind": route.kind,
                "media_kind": route.media_kind,
                "media_label": _media_kind_label(route.media_kind),
                "auto_detected": True,
            }
        )
    payload = {"tasks": tasks, "startDownload": bool(start_download)}
    deeplink = _build_deeplink(tasks[0], start_download=start_download, custom_scheme=config["custom_scheme"]) if len(tasks) == 1 else None
    plan: dict[str, Any] = {
        "service": "video-download-orchestrator",
        "ui": "mediago",
        "status": "planned",
        "will_download": False,
        "task_count": len(tasks),
        "blocked_count": len(blocked_items),
        "blocked_items": blocked_items,
        "task_summaries": task_summaries,
        "media_summary": _media_summary(task_summaries),
        "start_download": bool(start_download),
        "http_base": config["desktop_http_base"],
        "import_endpoint": _join_url(config["desktop_http_base"], "/api/downloads"),
        "payload": payload,
        "deeplink": deeplink,
        "next_action": "点击“导入 MediaGo”会把任务交给现成桌面 UI；默认只导入队列，不自动开始下载。",
    }
    if wechat_channels_urls:
        plan["wechat_channels"] = _wechat_channels_handoff_summary(wechat_channels_urls)
    if blocked_items:
        plan["next_action"] = (
            "可支持的任务仍可导入 MediaGo；被阻止的平台页面应先由 VDO/browser-sniffer 解析成真实 MP4 或 HLS，再显式导入。"
        )
    return plan


def _wechat_channels_handoff_summary(urls: list[str]) -> dict[str, Any]:
    return {
        "recognized": len(urls),
        "urls": [redact_url(url) for url in urls],
        "status": "review_required",
        "will_download": False,
        "credentials_accessed": False,
        "next_action": "微信视频号链接需要本机授权态解析或微信桌面端播放捕获，已从 MediaGo 下载任务中排除。"
        "请在完成解析并获得可下载媒体地址后，再显式导入 MediaGo。",
    }


def import_to_mediago(
    urls: Iterable[str],
    *,
    output_dir: str | Path = "downloads",
    backend: str | None = None,
    start_download: bool = False,
    timeout_seconds: float = 10,
    request_post=requests.post,
    task_names: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    plan = build_mediago_handoff_plan(
        urls,
        output_dir=output_dir,
        backend=backend,
        start_download=start_download,
        task_names=task_names,
    )
    config = _resolved_config()
    if not plan["payload"]["tasks"]:
        if plan.get("blocked_items"):
            return {
                **plan,
                "status": "review_required",
                "explicit_user_action": True,
                "will_download": False,
                "next_action": "MediaGo 没有该平台页面类型，已阻止误映射。请先用 browser-sniffer 捕获真实 MP4/HLS，再显式导入。",
            }
        if plan.get("wechat_channels"):
            return {
                **plan,
                "status": "empty",
                "explicit_user_action": True,
                "will_download": False,
                "next_action": "只识别到微信视频号链接，未生成 MediaGo 下载任务。"
                "请先完成本机授权态解析或微信桌面端播放捕获，获得可下载媒体地址后再导入。",
            }
        return {
            **plan,
            "status": "empty",
            "explicit_user_action": True,
            "will_download": False,
            "next_action": "没有可导入 MediaGo 的链接。请先输入或清洗链接。",
        }
    try:
        response = request_post(
            plan["import_endpoint"],
            json=plan["payload"],
            headers=_auth_headers(config, content_type=True),
            timeout=timeout_seconds,
        )
    except requests.RequestException as exc:
        return {
            **plan,
            "status": "unavailable",
            "explicit_user_action": True,
            "will_download": False,
            "error": str(exc),
            "next_action": "未能连接 MediaGo。请启动 MediaGo Desktop 后重试；也可以复制单任务 deeplink 手动打开。",
        }
    if not 200 <= int(response.status_code) < 300:
        return {
            **plan,
            "status": "failed",
            "explicit_user_action": True,
            "will_download": False,
            "http_status": int(response.status_code),
            "error": _response_error(response),
            "next_action": "MediaGo 拒绝导入任务。请确认 MediaGo 已启动、API Key 配置正确、任务类型被支持。",
        }
    result = {
        **plan,
        "status": "imported",
        "explicit_user_action": True,
        "will_download": bool(start_download),
        "http_status": int(response.status_code),
        "response": _safe_json(response),
        "next_action": "任务已导入 MediaGo。请切到 MediaGo 桌面 UI 查看队列和开始下载。"
        if not start_download
        else "任务已导入 MediaGo 并请求开始下载。请切到 MediaGo 桌面 UI 查看进度。",
    }
    result["task_receipt"] = _mediago_import_receipt(result["response"])
    return result


def _task_for_url(
    url: str,
    *,
    output_dir: str | Path,
    backend: str | None,
    name_override: str | None = None,
) -> dict[str, str]:
    manifest = plan_download(url, output_dir=output_dir, backend=backend)
    route = classify_url(url)
    task_type = _mediago_type(manifest["selected_backend"], route.kind, url)
    return {
        "type": task_type,
        "name": name_override or _display_name(route.kind, url),
        "url": url,
        "headers": _headers_for_mediago(manifest),
        "folder": "",
    }


def mediago_task_capability(url: str, *, route_kind: str | None = None) -> dict[str, Any]:
    """Describe whether a page can be represented by MediaGo's real type enum."""

    route = classify_url(url)
    kind = route_kind or route.kind
    if kind == "douyin-page":
        return {
            "supported": False,
            "mediago_type": None,
            "media_kind": route.media_kind,
            "reason": "当前 MediaGo 类型表没有 Douyin；不能把抖音页面伪装成 youtube 任务。",
            "recommended_path": "browser_sniffer_then_direct_or_m3u8",
        }
    if _is_torrent_source(url):
        return {
            "supported": True,
            "mediago_type": "torrent",
            "media_kind": route.media_kind,
            "reason": "磁力链接或 BT 种子由 MediaGo 的 aria2 队列处理。",
            "recommended_path": "mediago_torrent_import",
        }
    return {
        "supported": True,
        "mediago_type": None,
        "media_kind": route.media_kind,
        "reason": "该链接可映射到现有 MediaGo 类型。",
        "recommended_path": "mediago_import",
    }


def _task_name(task_names: Mapping[str, str] | None, url: str) -> str | None:
    if not task_names:
        return None
    name = task_names.get(url)
    if not isinstance(name, str):
        return None
    name = name.strip()
    return name if name else None


def _mediago_type(backend: str, kind: str, url: str) -> str:
    lowered = url.lower()
    if _is_torrent_source(url):
        return "torrent"
    if kind == "manifest" or backend == "n_m3u8dl_re" or ".m3u8" in lowered:
        return "m3u8"
    if kind == "direct-transfer" or backend == "aria2":
        return "direct"
    if "bilibili.com" in lowered or kind == "bilibili-page":
        return "bilibili"
    if kind == "douyin-page":
        raise ValueError("MediaGo does not expose a Douyin task type; resolve the page to direct media first")
    if backend in {"yt-dlp-api", "yt-dlp"}:
        return "youtube"
    return "direct"


def _display_name(kind: str, url: str) -> str:
    if _is_torrent_source(url):
        parsed = urlparse(url)
        if parsed.scheme.lower() == "magnet":
            query = [(key.casefold(), value) for key, value in parse_qsl(parsed.query, keep_blank_values=True)]
            display_name = next((value.strip() for key, value in query if key == "dn" and value.strip()), "")
            if display_name:
                return display_name
            topic = next((value for key, value in query if key == "xt" and value), "")
            digest = topic.rsplit(":", 1)[-1][:12]
            return f"magnet-{digest}" if digest else "magnet-download"
        return Path(parsed.path).stem or "torrent-download"
    redacted = redact_url(url)
    return f"{kind or 'video'} - {redacted}"


def _is_torrent_source(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme.lower() == "magnet" or parsed.path.lower().endswith(".torrent")


def _mediago_import_receipt(response: Any) -> dict[str, Any]:
    data = _response_data(response)
    tasks = data if isinstance(data, list) else []
    accepted = [_safe_mediago_task(task) for task in tasks if isinstance(task, dict)]
    task_ids = [int(task["id"]) for task in accepted if str(task.get("id") or "").isdigit()]
    return {
        "contract": "vdo_mediago_import_receipt_v1",
        "accepted_count": len(accepted),
        "task_ids": task_ids,
        "tasks": accepted,
        "contains_media_url": False,
    }


def _response_data(body: Any) -> Any:
    if isinstance(body, dict) and "data" in body:
        return body.get("data")
    return body


def _safe_mediago_task(task: dict[str, Any]) -> dict[str, Any]:
    return {
        key: task.get(key)
        for key in (
            "id",
            "name",
            "type",
            "status",
            "createdDate",
            "updatedDate",
            "exists",
            "file",
        )
        if task.get(key) is not None
    }


def _augment_mediago_output(
    task: dict[str, Any],
    *,
    task_id: int,
    config: dict[str, Any],
    timeout_seconds: float,
    request_get: Any,
) -> dict[str, Any]:
    if str(task.get("status") or "") != "success":
        return task
    try:
        config_response = request_get(
            _join_url(config["desktop_http_base"], "/api/config"),
            headers=_auth_headers(config),
            timeout=timeout_seconds,
        )
        config_data = _response_data(_safe_json(config_response)) if 200 <= int(config_response.status_code) < 300 else {}
        local_path = str(config_data.get("local") or "") if isinstance(config_data, dict) else ""
        if not local_path:
            return task
        list_response = request_get(
            _join_url(config["desktop_http_base"], "/api/downloads"),
            params={"current": 1, "pageSize": 1000, "localPath": local_path},
            headers=_auth_headers(config),
            timeout=timeout_seconds,
        )
        if not 200 <= int(list_response.status_code) < 300:
            return task
        page = _response_data(_safe_json(list_response))
        items = page.get("list") if isinstance(page, dict) and isinstance(page.get("list"), list) else []
        matched = next((item for item in items if isinstance(item, dict) and int(item.get("id") or 0) == task_id), None)
        return {**task, **matched} if matched else task
    except (requests.RequestException, OSError, TypeError, ValueError):
        return task


def _mediago_status_next_action(status: str, exists: bool) -> str:
    if status == "success" and exists:
        return "MediaGo 已完成下载并确认输出文件存在，可以继续做 VDO 质量检查。"
    if status == "success":
        return "MediaGo 标记下载成功，但状态响应没有确认输出文件；请在桌面 UI 检查下载目录。"
    if status == "failed":
        return "MediaGo 下载失败。请查看该任务日志，再决定重新执行或重新解析媒体地址。"
    if status in {"pending", "downloading"}:
        return "MediaGo 正在处理该任务，请稍后刷新状态。"
    if status == "stopped":
        return "MediaGo 任务已停止；需要时请在桌面 UI 中重新开始。"
    return "已读取 MediaGo 任务，请在桌面 UI 中查看详细状态。"


def _summary_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme.lower() != "magnet":
        return redact_ephemeral_url(url)
    safe_query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key.casefold() in {"xt", "dn"}
    ]
    return urlunparse(parsed._replace(query=urlencode(safe_query, doseq=True), fragment=""))


def _media_summary(items: list[dict[str, Any]]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for item in items:
        media_kind = str(item.get("media_kind") or "unknown")
        counts[media_kind] = counts.get(media_kind, 0) + 1
    labels = [f"{_media_kind_label(kind)} {count} 项" for kind, count in sorted(counts.items())]
    return {
        "auto_detected": True,
        "counts": counts,
        "label": "，".join(labels) if labels else "没有可导入的媒体任务",
    }


def _media_kind_label(media_kind: str) -> str:
    return {
        "adaptive": "自适应流",
        "audio": "音频",
        "mixed": "音视频容器",
        "subtitle": "字幕",
        "video": "视频",
    }.get(media_kind, "待识别媒体")


def _headers_for_mediago(manifest: dict[str, Any]) -> str:
    headers = manifest.get("request_headers") if isinstance(manifest.get("request_headers"), dict) else {}
    if not headers:
        return ""
    parts = [f"{key}: {value}" for key, value in headers.items() if key and value]
    return "\n".join(parts)


def _resolved_config() -> dict[str, str]:
    config = mediago_config()
    return {
        "desktop_http_base": str(config.get("desktop_http_base") or "http://127.0.0.1:39719").rstrip("/"),
        "custom_scheme": str(config.get("custom_scheme") or "mediago-community"),
        "api_key_env": str(config.get("api_key_env") or "MEDIAGO_API_KEY"),
    }


def _auth_headers(config: dict[str, str], *, content_type: bool = False) -> dict[str, str]:
    headers = {"Accept": "application/json"}
    if content_type:
        headers["Content-Type"] = "application/json"
    api_key = os.environ.get(config["api_key_env"])
    if api_key:
        headers["X-API-Key"] = api_key
    return headers


def _join_url(base: str, path: str) -> str:
    return base.rstrip("/") + "/" + path.lstrip("/")


def _build_deeplink(task: dict[str, str], *, start_download: bool, custom_scheme: str) -> str:
    params = {
        "n": "1",
        "silent": "1",
        "url": task["url"],
        "name": task["name"],
        "type": task["type"],
    }
    if start_download:
        params["downloadNow"] = "1"
    if task.get("headers"):
        params["headers"] = task["headers"]
    return f"{custom_scheme}://index.html/?{urlencode(params)}"


def _response_error(response: requests.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text[:500]
    if isinstance(payload, dict):
        return str(payload.get("message") or payload.get("error") or payload)
    return str(payload)


def _safe_json(response: requests.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return {"text": response.text[:500]}
