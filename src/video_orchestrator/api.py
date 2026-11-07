from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, PlainTextResponse
from pydantic import BaseModel, Field

from video_orchestrator.external_uis import get_external_ui_status
from video_orchestrator.runtime.entrypoint_status import build_entrypoint_status
from video_orchestrator.router import classify_url
from video_orchestrator.runtime.config import api_base_url
from video_orchestrator.runtime.config import browser_sniffer_config
from video_orchestrator.runtime.config import check_config
from video_orchestrator.runtime.config import config_path
from video_orchestrator.runtime.config import execution_config
from video_orchestrator.runtime.config import external_uis_config
from video_orchestrator.runtime.config import sniffer_plan_endpoint
from video_orchestrator.runtime.config import web_config
from video_orchestrator.runtime.capture_lease import capture_lease_summary
from video_orchestrator.runtime.capture_lease import expire_capture_secrets
from video_orchestrator.runtime.capture_lease import new_capture_lease
from video_orchestrator.runtime.execution import execute_manifest
from video_orchestrator.runtime.batch_cleaner import clean_batch_input
from video_orchestrator.runtime.handoff import build_task_handoff
from video_orchestrator.runtime.handoff import build_url_handoff
from video_orchestrator.runtime.manifests import write_manifest
from video_orchestrator.runtime.mediago import build_mediago_handoff_plan
from video_orchestrator.runtime.mediago import import_to_mediago
from video_orchestrator.runtime.mediago import mediago_status
from video_orchestrator.runtime.mediago import mediago_task_status
from video_orchestrator.runtime.mediago import mediago_page_candidates, build_mediago_capture_plan
from video_orchestrator.runtime.media_protocols import infer_media_kind
from video_orchestrator.runtime.media_protocols import match_media_protocol
from video_orchestrator.runtime.media_completeness import capture_selection_key
from video_orchestrator.runtime.media_completeness import capture_selection_summary
from video_orchestrator.runtime.media_completeness import content_range_diagnostic
from video_orchestrator.runtime.media_completeness import media_resource_role
from video_orchestrator.runtime.media_completeness import parse_hls_manifest_text
from video_orchestrator.runtime.media_completeness import sniffed_completeness_diagnostic
from video_orchestrator.runtime.operator import artifact_map
from video_orchestrator.runtime.operator import operator_review_summary
from video_orchestrator.runtime.openclaw import build_openclaw_link_plan
from video_orchestrator.runtime.openclaw import execute_openclaw_telegram_command
from video_orchestrator.runtime.page_link import PageLinkTimeoutError
from video_orchestrator.runtime.page_link import execute_page_capture
from video_orchestrator.runtime.page_link import page_link_site_adapter_diagnostic
from video_orchestrator.runtime.page_link import try_site_specific_page_download
from video_orchestrator.runtime.page_link import wait_for_sniffed_capture
from video_orchestrator.runtime.redaction import redact_manifest
from video_orchestrator.runtime.redaction import redact_ephemeral_url
from video_orchestrator.runtime.redaction import redact_url
from video_orchestrator.runtime.site_probe import probe_public_page
from video_orchestrator.runtime.sniff_store import append_sniff_record
from video_orchestrator.runtime.sniff_store import read_sniff_diagnostics
from video_orchestrator.runtime.task_store import TaskStore
from video_orchestrator.runtime.vkp_handoff import build_vkp_handoff
from video_orchestrator.runtime.wechat_channels_helper import wechat_channels_helper_status
from video_orchestrator.runtime.wechat_channels_resolve import record_wechat_channels_resolution
from video_orchestrator.runtime.wechat_channels_resolve import resolve_and_import_wechat_channels
from video_orchestrator.runtime.wechat_channels_resolve import resolve_wechat_channels_share
from video_orchestrator.runtime.wechat_channels_resolve import wechat_channels_defaults
from video_orchestrator.services import plan_download


class DownloadPlanRequest(BaseModel):
    url: str
    output_dir: str = "downloads"
    backend: str | None = None
    write_manifest: bool = True
    cookies_from_browser: str | None = None
    cookies: str | None = None
    js_runtimes: str | None = None
    remote_components: list[str] = Field(default_factory=list)


class DownloadExecuteRequest(BaseModel):
    url: str
    output_dir: str = "downloads"
    backend: str | None = None
    db_path: str = ".vdo/tasks.sqlite"
    hard_timeout: int | None = None
    idle_timeout: int | None = None
    cookies_from_browser: str | None = None
    cookies: str | None = None
    js_runtimes: str | None = None
    remote_components: list[str] = Field(default_factory=list)


class DownloadPageExecuteRequest(BaseModel):
    url: str
    output_dir: str = "downloads"
    db_path: str = ".vdo/tasks.sqlite"
    timeout_seconds: int = 120
    poll_interval: float = 1.0
    hard_timeout: int | None = None
    idle_timeout: int | None = None
    dry_run: bool = False


class SiteAdapterProbeRequest(BaseModel):
    url: str
    headers: dict[str, str] = Field(default_factory=dict)
    timeout_seconds: int = 10


class MediaGoPageCaptureRequest(BaseModel):
    page_url: str = Field(min_length=1, max_length=8192)
    started_at: datetime


class BatchItem(BaseModel):
    url: str
    backend: str | None = None


class BatchPlanRequest(BaseModel):
    items: list[BatchItem] = Field(min_length=1)
    output_dir: str = "downloads"
    write_manifests: bool = True
    cookies_from_browser: str | None = None
    cookies: str | None = None
    js_runtimes: str | None = None
    remote_components: list[str] = Field(default_factory=list)


class BatchCleanRequest(BaseModel):
    text: str | None = None
    items: list[BatchItem | str] | None = None


class BatchExecuteRequest(BaseModel):
    text: str | None = None
    items: list[BatchItem | str] | None = None
    output_dir: str = "downloads"
    db_path: str = ".vdo/tasks.sqlite"
    backend: str | None = None
    hard_timeout: int | None = None
    idle_timeout: int | None = None
    cookies_from_browser: str | None = None
    cookies: str | None = None
    js_runtimes: str | None = None
    remote_components: list[str] = Field(default_factory=list)


class OpenClawLinkPlanRequest(BaseModel):
    url: str | None = None
    text: str | None = None
    items: list[BatchItem | str] | None = None
    output_dir: str = "downloads/openclaw"
    backend: str | None = None
    write_manifests: bool = True
    include_manifests: bool = False


class OpenClawTelegramExecuteRequest(BaseModel):
    url: str | None = None
    text: str | None = None
    items: list[BatchItem | str] | None = None
    actor_id: str
    approval_channel: str = "telegram"
    approval_command: str = ""
    output_dir: str = "downloads/openclaw"
    db_path: str = ".vdo/tasks.sqlite"
    backend: str | None = None
    confirm: bool = False
    confirm_sensitive: bool = False
    request_id: str | None = None
    requested_at: str | None = None
    hard_timeout: int | None = None
    idle_timeout: int | None = None
    cookies_from_browser: str | None = None
    cookies: str | None = None
    js_runtimes: str | None = None
    remote_components: list[str] = Field(default_factory=list)


class MediaGoHandoffRequest(BaseModel):
    url: str | None = None
    text: str | None = None
    items: list[BatchItem | str] | None = None
    output_dir: str = "downloads"
    backend: str | None = None
    start_download: bool = False


class WechatChannelsResolveRequest(BaseModel):
    """视频号分享链接解析或显式 MediaGo 下载。

    调用 /platform/wechat-channels/import 本身就是显式用户动作；
    start_download 默认 True。其余字段是手动模式覆盖项。
    """

    url: str
    output_dir: str = "downloads"
    db_path: str = ".vdo/tasks.sqlite"
    start_download: bool | None = None
    provider: str | None = None
    transport: str | None = None
    quality: str | None = None
    force_reimport: bool = False


class AcceptTaskRequest(BaseModel):
    db_path: str = ".vdo/tasks.sqlite"


class RerunPlanRequest(BaseModel):
    output_dir: str = "downloads"
    db_path: str = ".vdo/tasks.sqlite"
    backend: str | None = None
    write_manifest: bool = True


class RerunExecuteRequest(BaseModel):
    output_dir: str = "downloads"
    db_path: str = ".vdo/tasks.sqlite"
    backend: str | None = None
    hard_timeout: int | None = None
    idle_timeout: int | None = None


class OperatorResumePlanRequest(BaseModel):
    output_dir: str = "downloads"
    db_path: str = ".vdo/tasks.sqlite"
    capture_session_id: str | None = None
    write_manifest: bool = True


class OperatorResumeExecuteRequest(BaseModel):
    output_dir: str = "downloads"
    db_path: str = ".vdo/tasks.sqlite"
    capture_session_id: str | None = None
    hard_timeout: int | None = None
    idle_timeout: int | None = None


class ManifestProbeRequest(BaseModel):
    probe_status: str = "ok"
    playlist_text: str | None = None
    error_code: str | None = None
    probe_depth: int = Field(default=0, ge=0, le=1)


class SniffedMediaPlanRequest(BaseModel):
    url: str
    capture_session_id: str | None = None
    request_id: str | None = None
    tab_id: int | None = None
    frame_id: int | None = None
    resource_type: str | None = None
    method: str | None = None
    status_code: int | None = None
    redirect_count: int = Field(default=0, ge=0)
    media_type: str | None = None
    media_kind: str | None = None
    category: str | None = None
    filename: str | None = None
    page_url: str | None = None
    page_title: str | None = None
    initiator: str | None = None
    document_url: str | None = None
    origin_url: str | None = None
    content_type: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    request_headers: dict[str, str] = Field(default_factory=dict)
    response_headers: dict[str, str] = Field(default_factory=dict)
    content_length: int | None = None
    content_range: str | None = None
    dedupe_key: str | None = None
    detected_at: str | None = None
    output_dir: str = "downloads"
    write_manifest: bool = True


app = FastAPI(title="video-download-orchestrator", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:39719",
        "http://localhost:39719",
        "http://127.0.0.1:8921",
        "http://localhost:8921",
    ],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)
SNIFFED_MEDIA_CAPTURES: list[dict] = []
SNIFFED_MEDIA_REJECTIONS: list[dict] = []
MAX_SNIFFED_MEDIA_CAPTURES = 100
MAX_SNIFFED_MEDIA_REJECTIONS = 100


@app.get("/", response_class=HTMLResponse)
def web_ui() -> HTMLResponse:
    index_path = Path(__file__).parent / "web" / "index.html"
    return HTMLResponse(index_path.read_text(encoding="utf-8"))


@app.get("/health")
def health() -> dict:
    return {
        "service": "video-download-orchestrator", "status": "ok",
        "health_scope": "liveness", "service_online": True,
        "backend_readiness": "not_checked", "output_validity": "not_checked",
        "will_download": False,
    }


@app.get("/entrypoints/status")
def entrypoints_status() -> dict:
    return build_entrypoint_status(listener_probe=lambda host, port: True)


@app.get("/config")
def runtime_config() -> dict:
    return {
        "service": "video-download-orchestrator",
        "config_path": str(config_path()),
        "web": web_config(),
        "browser_sniffer": browser_sniffer_config(),
        "execution": execution_config(),
        "external_uis": external_uis_config(),
        "api_base_url": api_base_url(),
        "sniffer_plan_endpoint": sniffer_plan_endpoint(),
        "check": check_config(),
    }


@app.get("/external-uis")
def external_uis(check: bool = False) -> dict:
    return {
        "service": "video-download-orchestrator",
        "status": "ok",
        "will_download": False,
        "uis": [ui.model_dump() for ui in get_external_ui_status(check=check)],
    }


@app.get("/external-uis/mediago/status")
def external_ui_mediago_status() -> dict:
    return mediago_status()


@app.get("/external-uis/mediago/tasks/{task_id}/status")
def external_ui_mediago_task_status(task_id: int) -> dict:
    """Read one MediaGo task receipt without starting or mutating downloads."""
    return mediago_task_status(task_id)


@app.get("/platform/wechat-channels/helper-status")
def wechat_channels_helper_status_endpoint() -> dict:
    """只报告微信视频号本机解析 helper 可用性，不读取或返回任何凭证。"""
    return wechat_channels_helper_status()


@app.post("/platform/wechat-channels/resolve")
def wechat_channels_resolve_endpoint(request: WechatChannelsResolveRequest) -> dict:
    """解析视频号分享链接并记录非敏感结果；不导入 MediaGo、不触发下载。"""
    _require_wechat_channels_share(request.url)
    output_dir = Path(request.output_dir)
    resolved = resolve_wechat_channels_share(request.url)
    store = TaskStore(Path(request.db_path).resolve())
    record = record_wechat_channels_resolution(request.url, resolved, output_dir=output_dir, store=store)
    return {
        **record,
        "service": "video-orchestrator",
        "platform": "wechat-channels",
        "explicit_user_action": True,
        "will_download": False,
        "imported": False,
        "next_action": str(resolved.get("next_action") or "解析完成。确认后可通过 /platform/wechat-channels/import 显式导入 MediaGo。"),
    }


@app.post("/platform/wechat-channels/import")
def wechat_channels_import_endpoint(request: WechatChannelsResolveRequest) -> dict:
    """解析视频号分享链接并显式交给 MediaGo，默认立即开始下载。"""
    _require_wechat_channels_share(request.url)
    defaults = wechat_channels_defaults()
    result = resolve_and_import_wechat_channels(
        request.url,
        output_dir=request.output_dir,
        db_path=request.db_path,
        start_download=defaults["start_download"] if request.start_download is None else request.start_download,
        provider=request.provider or defaults["provider"],
        transport=request.transport or defaults["transport"],
        quality=request.quality or defaults["quality"],
        force_reimport=request.force_reimport,
    )
    return result


def _require_wechat_channels_share(url: str) -> None:
    if classify_url(url).kind != "wechat-channels-page":
        raise HTTPException(
            status_code=422,
            detail="wechat_channels_share_url_required: 只接受微信视频号分享链接（https://weixin.qq.com/sph/... 或 channels.weixin.qq.com 页面）。",
        )


@app.post("/external-uis/mediago/plan")
def external_ui_mediago_plan(request: MediaGoHandoffRequest) -> dict:
    clean_payload = _clean_mediago_request(request)
    plan = build_mediago_handoff_plan(
        [str(item["url"]) for item in clean_payload["items"]],
        output_dir=request.output_dir,
        backend=request.backend,
        start_download=request.start_download,
    )
    summary = _mediago_plan_summary(plan, clean_payload)
    return {
        **plan,
        "page_candidates": mediago_page_candidates([str(item["url"]) for item in clean_payload["items"]], plan["payload"]["tasks"]),
        "cleaning": clean_payload,
        "summary": summary,
        "type_counts": summary["type_counts"],
        "input_count": clean_payload["input_count"],
        "item_count": clean_payload["item_count"],
        "skipped_count": clean_payload["skipped_count"],
    }


@app.post("/external-uis/mediago/page/plan")
def external_ui_mediago_page_plan(request: MediaGoPageCaptureRequest) -> dict:
    if request.started_at.tzinfo is None or urlparse(request.page_url).scheme not in {"http", "https"}:
        raise HTTPException(status_code=422, detail="需要 HTTP(S) 页面链接和带时区的开始时间。")
    capture, _ = _latest_capture_for_page(request.page_url)
    return build_mediago_capture_plan(capture, page_url=request.page_url, started_at=request.started_at)


@app.post("/external-uis/mediago/import")
def external_ui_mediago_import(request: MediaGoHandoffRequest) -> dict:
    clean_payload = _clean_mediago_request(request)
    result = import_to_mediago(
        [str(item["url"]) for item in clean_payload["items"]],
        output_dir=request.output_dir,
        backend=request.backend,
        start_download=request.start_download,
    )
    summary = _mediago_plan_summary(result, clean_payload)
    return {
        **result,
        "cleaning": clean_payload,
        "summary": summary,
        "type_counts": summary["type_counts"],
        "input_count": clean_payload["input_count"],
        "item_count": clean_payload["item_count"],
        "skipped_count": clean_payload["skipped_count"],
    }


@app.get("/route")
def route(url: str) -> dict:
    return {
        "service": "video-download-orchestrator",
        "route": classify_url(url).model_dump(),
        "site_adapter": page_link_site_adapter_diagnostic(url),
        "will_download": False,
    }


@app.post("/download/plan")
def download_plan(request: DownloadPlanRequest) -> dict:
    output_dir = Path(request.output_dir)
    manifest = plan_download(request.url, output_dir=output_dir, backend=request.backend, ytdlp_options=_request_ytdlp_options(request))
    manifest_path = write_manifest(manifest, output_dir) if request.write_manifest else None
    return {
        "service": "video-download-orchestrator",
        "status": "planned",
        "will_download": False,
        "manifest": manifest,
        "manifest_path": str(manifest_path) if manifest_path else None,
    }


@app.post("/download/execute")
def download_execute(request: DownloadExecuteRequest) -> dict:
    output_dir = Path(request.output_dir)
    manifest = plan_download(request.url, output_dir=output_dir, backend=request.backend, ytdlp_options=_request_ytdlp_options(request))
    manifest_path = write_manifest(manifest, output_dir)
    store = TaskStore(Path(request.db_path).resolve())
    payload = execute_manifest(
        store=store,
        manifest=manifest,
        artifact_base_dir=output_dir,
        manifest_path=manifest_path,
        hard_timeout=request.hard_timeout,
        idle_timeout=request.idle_timeout,
    )
    payload["explicit_user_action"] = True
    payload["will_download"] = True
    return payload


@app.post("/download/page/execute")
def download_page_execute(request: DownloadPageExecuteRequest) -> dict:
    output_dir = Path(request.output_dir)
    try:
        capture, capture_payload = wait_for_sniffed_capture(
            fetch_latest=lambda page_url, capture_session_id: _latest_sniffed_media_payload(
                page_url=page_url,
                capture_session_id=capture_session_id,
                include_sensitive=True,
            ),
            page_url=request.url,
            capture_session_id=None,
            timeout_seconds=request.timeout_seconds,
            poll_interval=request.poll_interval,
        )
    except PageLinkTimeoutError as exc:
        site_specific = try_site_specific_page_download(
            page_url=request.url,
            output_dir=output_dir,
            db_path=Path(request.db_path),
            dry_run=request.dry_run,
            hard_timeout=request.hard_timeout,
            idle_timeout=request.idle_timeout,
            execute_manifest_func=execute_manifest,
        )
        if site_specific is not None:
            site_specific.update(
                {
                    "page_url": request.url,
                    "capture_session_id": None,
                    "sniff_status": "timeout",
                    "sniff_error": str(exc),
                    "site_specific_fallback": True,
                    "explicit_user_action": True,
                    "will_download": not request.dry_run,
                }
            )
            return site_specific
        return {
            "service": "video-download-orchestrator",
            "status": "timeout",
            "will_download": False,
            "explicit_user_action": True,
            "page_url": request.url,
            "capture_session_id": None,
            "sniff_status": "timeout",
            "error": str(exc),
            "latest_rejection": (exc.last_payload or {}).get("latest_rejection"),
            "site_adapter": page_link_site_adapter_diagnostic(request.url),
            "next_action": "已打开页面但还没有捕获到可下载视频。请在新页面点击播放视频，确认浏览器扩展已启用，然后再次点击一键下载。",
        }
    except OSError as exc:
        return {
            "service": "video-download-orchestrator",
            "status": "api_unavailable",
            "will_download": False,
            "explicit_user_action": True,
            "page_url": request.url,
            "sniff_status": "api_unavailable",
            "error": str(exc),
            "next_action": "本地 API 暂不可用。请重新启动视频下载调度器 WebUI 后再试。",
        }

    payload = execute_page_capture(
        page_url=request.url,
        capture=capture,
        capture_payload=capture_payload,
        output_dir=output_dir,
        db_path=Path(request.db_path),
        dry_run=request.dry_run,
        hard_timeout=request.hard_timeout,
        idle_timeout=request.idle_timeout,
        execute_manifest_func=execute_manifest,
    )
    payload["explicit_user_action"] = True
    if not request.dry_run:
        payload["will_download"] = True
    return payload


@app.post("/site-adapter/probe")
def site_adapter_probe(request: SiteAdapterProbeRequest) -> dict:
    return probe_public_page(request.url, headers=request.headers, timeout_seconds=request.timeout_seconds)


@app.post("/batch/clean")
def batch_clean(request: BatchCleanRequest) -> dict:
    raw: object
    if request.text is not None:
        raw = request.text
    else:
        raw = {"items": [item.model_dump() if isinstance(item, BatchItem) else item for item in request.items or []]}
    return clean_batch_input(raw)


@app.post("/integrations/openclaw/link/plan")
def openclaw_link_plan(request: OpenClawLinkPlanRequest) -> dict:
    raw = _openclaw_raw_input(request)
    return build_openclaw_link_plan(
        raw,
        output_dir=request.output_dir,
        backend=request.backend,
        write_manifests=request.write_manifests,
        include_manifests=request.include_manifests,
    )


@app.post("/integrations/openclaw/telegram/execute")
def openclaw_telegram_execute(request: OpenClawTelegramExecuteRequest) -> dict:
    raw = _openclaw_raw_input(request)
    return execute_openclaw_telegram_command(
        raw,
        actor_id=request.actor_id,
        output_dir=request.output_dir,
        db_path=request.db_path,
        backend=request.backend,
        approval_channel=request.approval_channel,
        approval_command=request.approval_command,
        confirm=request.confirm,
        confirm_sensitive=request.confirm_sensitive,
        request_id=request.request_id,
        requested_at=request.requested_at,
        hard_timeout=request.hard_timeout,
        idle_timeout=request.idle_timeout,
        ytdlp_options=_request_ytdlp_options(request),
        execute_manifest_func=execute_manifest,
    )


@app.post("/batch/plan")
def batch_plan(request: BatchPlanRequest) -> dict:
    clean_payload = clean_batch_input({"items": [item.model_dump() for item in request.items]})
    batch_dir = Path(request.output_dir)
    results = []
    for index, item in enumerate(clean_payload["items"], start=1):
        item_dir = batch_dir / f"item-{index:03d}"
        manifest = plan_download(str(item["url"]), output_dir=item_dir, backend=item.get("backend"), ytdlp_options=_request_ytdlp_options(request))
        manifest["batch_item_index"] = index
        manifest_path = write_manifest(manifest, item_dir) if request.write_manifests else None
        results.append(
            {
                "index": index,
                "url": item["url"],
                "backend": manifest["selected_backend"],
                "will_download": False,
                "manifest": manifest,
                "manifest_path": str(manifest_path) if manifest_path else None,
            }
        )
    return {
        "service": "video-download-orchestrator",
        "contract": "agent_batch_v1",
        "status": "planned",
        "execute": False,
        "item_count": len(results),
        "cleaning": {
            "contract": clean_payload["contract"],
            "input_count": clean_payload["input_count"],
            "item_count": clean_payload["item_count"],
            "skipped_count": clean_payload["skipped_count"],
            "skipped": clean_payload["skipped"],
            "defaults": clean_payload["defaults"],
        },
        "items": results,
    }


@app.post("/batch/execute")
def batch_execute(request: BatchExecuteRequest) -> dict:
    raw: object
    if request.text is not None:
        raw = request.text
    else:
        raw = {"items": [item.model_dump() if isinstance(item, BatchItem) else item for item in request.items or []]}
    clean_payload = clean_batch_input(raw)
    output_dir = Path(request.output_dir)
    batch_dir = _versioned_batch_dir(output_dir)
    store = TaskStore(Path(request.db_path).resolve())
    ytdlp_options = _request_ytdlp_options(request)
    results = []
    for index, item in enumerate(clean_payload["items"], start=1):
        item_dir = batch_dir / f"item-{index:03d}"
        manifest = plan_download(
            str(item["url"]),
            output_dir=item_dir,
            backend=item.get("backend") or request.backend,
            ytdlp_options=ytdlp_options,
        )
        manifest["batch_id"] = batch_dir.name
        manifest["batch_item_index"] = index
        manifest_path = write_manifest(manifest, item_dir)
        item_result = {
            "index": index,
            "url": item["url"],
            "backend": manifest["selected_backend"],
            "manifest_path": str(manifest_path),
            "will_download": True,
        }
        item_result.update(
            execute_manifest(
                store=store,
                manifest=manifest,
                artifact_base_dir=item_dir,
                manifest_path=manifest_path,
                hard_timeout=request.hard_timeout,
                idle_timeout=request.idle_timeout,
            )
        )
        results.append(item_result)
    return {
        "service": "video-download-orchestrator",
        "contract": "agent_batch_v1",
        "status": _batch_status(results),
        "execute": True,
        "explicit_user_action": True,
        "will_download": bool(results),
        "batch_dir": str(batch_dir),
        "item_count": len(results),
        "cleaning": {
            "contract": clean_payload["contract"],
            "input_count": clean_payload["input_count"],
            "item_count": clean_payload["item_count"],
            "skipped_count": clean_payload["skipped_count"],
            "skipped": clean_payload["skipped"],
            "defaults": clean_payload["defaults"],
        },
        "items": results,
    }


@app.post("/sniffed-media/plan")
def sniffed_media_plan(request: SniffedMediaPlanRequest) -> dict:
    if not _looks_like_sniffed_media(request):
        detail = "sniffed media URL must be a playable media URL or manifest"
        _store_sniffed_rejection(request, detail)
        raise HTTPException(status_code=400, detail=detail)

    output_dir = Path(request.output_dir)
    backend = _sniffed_backend(request)
    request_headers = request.request_headers or _download_headers_from_combined(request.headers)
    quality_score, candidate_reason = _sniffed_candidate_quality(request, backend)
    dedupe_key = request.dedupe_key or _sniffed_dedupe_key(request)
    media_kind = _sniffed_media_kind(request)
    completeness = sniffed_completeness_diagnostic(
        {
            "url": request.url,
            "filename": request.filename,
            "media_kind": media_kind,
            "content_type": request.content_type,
            "content_range": request.content_range,
            "content_length": request.content_length,
            "response_headers": request.response_headers,
        }
    )
    manifest = plan_download(request.url, output_dir=output_dir, backend=backend, headers=request_headers)
    manifest["source"] = "browser_sniffer"
    manifest["media_kind"] = media_kind
    manifest["sniffed_media"] = {
        "capture_session_id": request.capture_session_id,
        "capture_group_id": _capture_group_id(request.capture_session_id, request.tab_id, request.page_url),
        "request_id": request.request_id,
        "tab_id": request.tab_id,
        "frame_id": request.frame_id,
        "resource_type": request.resource_type,
        "method": request.method,
        "status_code": request.status_code,
        "redirect_count": request.redirect_count,
        "media_type": request.media_type,
        "media_kind": media_kind,
        "category": request.category,
        "filename": request.filename,
        "page_url": request.page_url,
        "page_title": request.page_title,
        "initiator": request.initiator,
        "document_url": request.document_url,
        "origin_url": request.origin_url,
        "content_type": request.content_type,
        "headers": request.headers,
        "request_headers": request_headers,
        "response_headers": request.response_headers,
        "content_length": request.content_length,
        "content_range": request.content_range,
        "dedupe_key": dedupe_key,
        "quality_score": quality_score,
        "download_candidate_reason": candidate_reason,
        "completeness": completeness,
        "detected_at": request.detected_at,
    }
    manifest_path = write_manifest(manifest, output_dir) if request.write_manifest else None
    capture = _store_sniffed_capture(
        request,
        manifest,
        manifest_path,
        dedupe_key=dedupe_key,
        quality_score=quality_score,
        candidate_reason=candidate_reason,
    )
    append_sniff_record("captures", _serialize_capture(capture, include_sensitive=False))
    return {
        "service": "video-download-orchestrator",
        "status": "planned",
        "will_download": False,
        "capture_id": capture["capture_id"],
        "dedupe_key": capture["dedupe_key"],
        "quality_score": capture["quality_score"],
        "download_candidate_reason": capture["download_candidate_reason"],
        "completeness": capture["completeness"],
        "capture_lease": capture_lease_summary(capture),
        "manifest": redact_manifest(manifest),
        "manifest_path": str(manifest_path) if manifest_path else None,
    }


@app.post("/sniffed-media/{capture_id}/manifest-probe")
def sniffed_media_manifest_probe(capture_id: str, request: ManifestProbeRequest) -> dict:
    _expire_sniffed_capture_secrets()
    capture = next((item for item in SNIFFED_MEDIA_CAPTURES if item.get("capture_id") == capture_id), None)
    if capture is None:
        raise HTTPException(status_code=404, detail="sniffed capture not found")
    if not capture_lease_summary(capture)["active"]:
        raise HTTPException(status_code=410, detail="嗅探捕获已过期，请回到原页面重新播放并捕获。")

    playlist_size = len((request.playlist_text or "").encode("utf-8"))
    budget = capture.setdefault("manifest_probe_budget", {"request_count": 0, "total_bytes": 0})
    if int(budget.get("request_count") or 0) >= 2 or int(budget.get("total_bytes") or 0) + playlist_size > 1024 * 1024:
        raise HTTPException(status_code=400, detail="manifest probe budget exceeded")
    if playlist_size > 512 * 1024:
        raise HTTPException(status_code=400, detail="playlist exceeds per-request limit")

    existing_probe = capture.get("manifest_probe") if isinstance(capture.get("manifest_probe"), dict) else {}
    master_probe = existing_probe.get("master_playlist") if isinstance(existing_probe.get("master_playlist"), dict) else None
    if request.probe_depth == 1 and master_probe is None:
        raise HTTPException(status_code=400, detail="variant probe requires a master playlist probe")

    budget["request_count"] = int(budget.get("request_count") or 0) + 1
    budget["total_bytes"] = int(budget.get("total_bytes") or 0) + playlist_size
    follow_up = None

    if request.probe_status != "ok":
        if request.probe_depth == 1 and master_probe is not None:
            probe = {
                **master_probe,
                "probe_status": "ok",
                "probe_depth": 0,
                "master_playlist": master_probe,
                "selected_variant": master_probe.get("selected_variant"),
                "follow_up_status": request.probe_status,
                "follow_up_error_code": request.error_code or "variant_probe_failed",
            }
        else:
            probe = {
                "probe_status": request.probe_status,
                "probe_depth": request.probe_depth,
                "error_code": request.error_code or "probe_failed",
                "quality_label": "HLS 完整性未获取",
                "risk": "unknown",
                "risk_reasons": ["浏览器未能读取 HLS playlist"],
            }
    elif not request.playlist_text:
        probe = {
            "probe_status": "parse_failed",
            "probe_depth": request.probe_depth,
            "error_code": "missing_playlist_text",
            "quality_label": "HLS 完整性未获取",
            "risk": "unknown",
            "risk_reasons": ["没有收到 HLS playlist 内容"],
        }
    else:
        parsed_probe = {"probe_status": "ok", **parse_hls_manifest_text(request.playlist_text)}
        if parsed_probe.get("playlist_media_kind") in {None, "unknown"}:
            parsed_probe["playlist_media_kind"] = (
                master_probe.get("playlist_media_kind")
                if request.probe_depth == 1 and isinstance(master_probe, dict)
                else capture.get("media_kind") or "unknown"
            )
        if request.probe_depth == 0 and parsed_probe.get("playlist_kind") == "master":
            master_probe = dict(parsed_probe)
            selected_variant = master_probe.get("selected_variant") if isinstance(master_probe.get("selected_variant"), dict) else None
            probe = {
                **master_probe,
                "probe_depth": 0,
                "master_playlist": master_probe,
                "selected_variant": selected_variant,
                "follow_up_status": "pending" if selected_variant else "not_available",
            }
            if selected_variant is not None:
                follow_up = {
                    "required": True,
                    "kind": "variant_playlist",
                    "variant_index": selected_variant.get("index"),
                    "same_origin_only": True,
                    "max_depth": 1,
                    "reason": selected_variant.get("selection_reason")
                    or master_probe.get("selected_variant_reason")
                    or "已选择最高质量 variant。",
                }
        elif request.probe_depth == 1:
            selected_variant = master_probe.get("selected_variant") if isinstance(master_probe, dict) else None
            if parsed_probe.get("playlist_kind") != "media":
                parsed_probe["risk"] = "unknown"
                parsed_probe["quality_label"] = "variant playlist 未解析为 media playlist"
                parsed_probe["risk_reasons"] = ["跟进结果没有有效 HLS segment"]
            probe = {
                **parsed_probe,
                "probe_depth": 1,
                "master_playlist": master_probe,
                "selected_variant": selected_variant,
                "follow_up_status": "completed" if parsed_probe.get("playlist_kind") == "media" else "invalid_variant_playlist",
            }
        else:
            probe = {**parsed_probe, "probe_depth": 0, "follow_up_status": "not_required"}

    probe["probe_request_count"] = budget["request_count"]
    probe["probe_total_bytes"] = budget["total_bytes"]
    capture["manifest_probe"] = probe
    probed_media_kind = str(probe.get("playlist_media_kind") or "unknown")
    if capture.get("media_kind") in {None, "adaptive", "unknown"} and probed_media_kind in {"audio", "mixed", "video"}:
        capture["media_kind"] = probed_media_kind
    capture["completeness"] = sniffed_completeness_diagnostic(
        {
            "url": capture.get("url"),
            "filename": capture.get("filename"),
            "content_range": capture.get("content_range"),
            "content_length": capture.get("content_length"),
            "response_headers": capture.get("response_headers"),
            "manifest_probe": probe,
        }
    )
    manifest = capture.get("manifest")
    if isinstance(manifest, dict) and isinstance(manifest.get("sniffed_media"), dict):
        manifest["media_kind"] = capture.get("media_kind") or manifest.get("media_kind")
        manifest["sniffed_media"]["media_kind"] = capture.get("media_kind")
        manifest["sniffed_media"]["manifest_probe"] = probe
        manifest["sniffed_media"]["completeness"] = capture["completeness"]
    append_sniff_record("captures", _serialize_capture(capture, include_sensitive=False))
    next_action = (
        "已识别 HLS master，请浏览器扩展按 variant_index 读取一次同源 media playlist。"
        if follow_up
        else "跨源 variant 不主动读取，等待播放器自然请求后由浏览器扩展捕获。"
        if probe.get("follow_up_status") == "cross_origin_waiting"
        else "HLS 完整性摘要已更新，可继续人工确认或选择下载。"
    )
    return {
        "service": "video-download-orchestrator",
        "status": "planned",
        "will_download": False,
        "capture_id": capture_id,
        "manifest_probe": probe,
        "master_playlist": probe.get("master_playlist"),
        "selected_variant": probe.get("selected_variant"),
        "follow_up": follow_up,
        "completeness": capture["completeness"],
        "next_action": next_action,
    }

@app.get("/sniffed-media/latest")
def latest_sniffed_media(
    page_url: str | None = None,
    capture_session_id: str | None = None,
    include_sensitive: bool = False,
) -> dict:
    return _latest_sniffed_media_payload(page_url=page_url, capture_session_id=capture_session_id, include_sensitive=include_sensitive)


def _latest_sniffed_media_payload(
    *,
    page_url: str | None = None,
    capture_session_id: str | None = None,
    include_sensitive: bool = False,
) -> dict:
    _expire_sniffed_capture_secrets()
    capture, capture_page_match = _latest_capture_for_page(page_url, capture_session_id)
    all_candidates, _candidate_match_kind = _records_matching_page(SNIFFED_MEDIA_CAPTURES, page_url, capture_session_id)
    candidates = _latest_capture_group(all_candidates)
    rejection, rejection_page_match = _latest_rejection_for_page(page_url, capture_session_id)
    capture_count = len(all_candidates)
    rejection_count = _rejection_count_for_page(page_url, capture_session_id)
    candidate_summaries = [_serialize_capture_summary(item, include_sensitive=include_sensitive) for item in sorted(candidates, key=capture_selection_key, reverse=True)]
    if capture is None:
        return {
            "service": "video-download-orchestrator",
            "status": "not_found",
            "capture": None,
            "candidates": candidate_summaries,
            "capture_count": capture_count,
            "group_capture_count": len(candidates),
            "rejection_count": rejection_count,
            "latest_rejection": _serialize_rejection(rejection, include_sensitive=include_sensitive) if rejection else None,
            "capture_page_match": capture_page_match,
            "rejection_page_match": rejection_page_match,
            "capture_session_id": capture_session_id,
            "diagnostic": "捕获已过期，请在原页面重新播放视频。"
            if candidates and all(not capture_lease_summary(item)["active"] for item in candidates)
            else "当前页面只捕获到分片，尚无可下载媒体候选"
            if candidates
            else "no browser-sniffer submissions for page",
        }
    return {
        "service": "video-download-orchestrator",
        "status": "ok",
        "capture_count": capture_count,
        "group_capture_count": len(candidates),
        "rejection_count": rejection_count,
        "capture": _serialize_capture(capture, include_sensitive=include_sensitive),
        "candidates": candidate_summaries,
        "latest_rejection": _serialize_rejection(rejection, include_sensitive=include_sensitive) if rejection else None,
        "capture_page_match": capture_page_match,
        "rejection_page_match": rejection_page_match,
        "capture_session_id": capture_session_id,
    }

@app.get("/sniffed-media/diagnostics")
def sniffed_media_diagnostics(limit: int = 100) -> dict:
    diagnostics = read_sniff_diagnostics(limit=limit)
    return {
        "service": "video-download-orchestrator",
        "status": "ok",
        "will_download": False,
        **diagnostics,
    }


@app.get("/tasks")
def list_tasks(
    db_path: str = ".vdo/tasks.sqlite",
    problem_only: bool = False,
) -> dict:
    store = TaskStore(Path(db_path).resolve())
    tasks = []
    for task in store.list_tasks(problem_only=problem_only):
        task["artifacts"] = artifact_map(store.list_artifacts(task["id"]))
        tasks.append(task)
    return {
        "service": "video-download-orchestrator",
        "status": "ok",
        "problem_only": problem_only,
        "tasks": tasks,
    }


@app.get("/tasks/{task_id}")
def task_detail(task_id: int, db_path: str = ".vdo/tasks.sqlite") -> dict:
    store = TaskStore(Path(db_path).resolve())
    try:
        task = store.get_task(task_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    artifacts = artifact_map(store.list_artifacts(task_id))
    return {
        "service": "video-download-orchestrator",
        "status": "ok",
        "task": task,
        "artifacts": artifacts,
    }


@app.post("/tasks/{task_id}/accept")
def accept_task(task_id: int, request: AcceptTaskRequest) -> dict:
    store = TaskStore(Path(request.db_path).resolve())
    try:
        store.get_task(task_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    store.mark_accepted(task_id)
    return {
        "service": "video-download-orchestrator",
        "status": "accepted",
        "task": store.get_task(task_id),
        "artifacts": artifact_map(store.list_artifacts(task_id)),
    }


@app.post("/tasks/{task_id}/rerun/plan")
def rerun_plan(task_id: int, request: RerunPlanRequest) -> dict:
    store = TaskStore(Path(request.db_path).resolve())
    try:
        task = store.get_task(task_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    backend = request.backend or str(task["backend"])
    rerun_dir = _versioned_rerun_dir(Path(request.output_dir), task_id, backend)
    manifest = plan_download(str(task["url"]), output_dir=rerun_dir, backend=backend)
    manifest["rerun_of_task_id"] = task_id
    manifest["artifact_scope"] = "versioned_rerun"
    manifest_path = write_manifest(manifest, rerun_dir) if request.write_manifest else None
    return {
        "service": "video-download-orchestrator",
        "status": "planned",
        "will_download": False,
        "original_task": task,
        "rerun_dir": str(rerun_dir),
        "manifest": manifest,
        "manifest_path": str(manifest_path) if manifest_path else None,
    }


@app.post("/tasks/{task_id}/rerun/execute")
def rerun_execute(task_id: int, request: RerunExecuteRequest) -> dict:
    store = TaskStore(Path(request.db_path).resolve())
    try:
        task = store.get_task(task_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    backend = request.backend or str(task["backend"])
    rerun_dir = _versioned_rerun_dir(Path(request.output_dir), task_id, backend)
    manifest = plan_download(str(task["url"]), output_dir=rerun_dir, backend=backend)
    manifest["rerun_of_task_id"] = task_id
    manifest["artifact_scope"] = "versioned_rerun"
    manifest_path = write_manifest(manifest, rerun_dir)
    payload = execute_manifest(
        store=store,
        manifest=manifest,
        artifact_base_dir=rerun_dir,
        manifest_path=manifest_path,
        hard_timeout=request.hard_timeout,
        idle_timeout=request.idle_timeout,
    )
    payload.update(
        {
            "explicit_user_action": True,
            "will_download": True,
            "original_task": task,
            "rerun_dir": str(rerun_dir),
        }
    )
    return payload


@app.get("/operator/status")
def operator_status(db_path: str = ".vdo/tasks.sqlite") -> dict:
    store = TaskStore(Path(db_path).resolve())
    retryable_tasks = store.list_retryable_tasks()
    capture, _capture_match = _latest_capture_for_page(None)
    rejection, _rejection_match = _latest_rejection_for_page(None)
    return {
        "service": "video-download-orchestrator",
        "status": "ok",
        "will_download": False,
        "retryable_task_count": len(retryable_tasks),
        "retryable_tasks": retryable_tasks,
        "latest_capture_status": "ok" if capture else "not_found",
        "latest_capture": _serialize_capture(capture, include_sensitive=False) if capture else None,
        "latest_rejection": _serialize_rejection(rejection, include_sensitive=False) if rejection else None,
    }


@app.get("/operator/tasks/retryable")
def operator_retryable_tasks(db_path: str = ".vdo/tasks.sqlite") -> dict:
    store = TaskStore(Path(db_path).resolve())
    return {
        "service": "video-download-orchestrator",
        "status": "ok",
        "will_download": False,
        "tasks": store.list_retryable_tasks(),
    }


@app.get("/operator/tasks/{task_id}/review")
def operator_task_review(task_id: int, db_path: str = ".vdo/tasks.sqlite") -> dict:
    store = TaskStore(Path(db_path).resolve())
    try:
        task = store.get_task(task_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    artifacts = artifact_map(store.list_artifacts(task_id))
    review_summary = operator_review_summary(task, artifacts)
    return {
        "service": "video-download-orchestrator",
        "status": "ok",
        "will_download": False,
        "verdict": review_summary["verdict"],
        "quality_status": review_summary["quality_status"],
        "quality_label": review_summary["quality_label"],
        "transport_status": review_summary["transport_status"],
        "transport_label": review_summary["transport_label"],
        "media_integrity_status": review_summary["media_integrity_status"],
        "media_integrity_label": review_summary["media_integrity_label"],
        "reason_labels": review_summary["reason_labels"],
        "recommended_actions": review_summary["recommended_actions"],
        "can_accept": review_summary["can_accept"],
        "can_rerun": review_summary["can_rerun"],
        "can_resume": review_summary["can_resume"],
        "task": task,
        "artifacts": artifacts,
        "review": review_summary["review"],
        "summary": review_summary["summary"],
        "next_action": review_summary["next_action"],
    }


@app.get("/operator/handoff")
def operator_handoff(url: str, output_dir: str = "downloads", capture_session: str | None = None) -> dict:
    return build_url_handoff(url, output_dir=output_dir, capture_session=capture_session)


@app.get("/operator/tasks/{task_id}/handoff")
def operator_task_handoff(task_id: int, db_path: str = ".vdo/tasks.sqlite", output_dir: str = "downloads") -> dict:
    return build_task_handoff(task_id, db_path=Path(db_path), output_dir=output_dir)


@app.get("/operator/tasks/{task_id}/vkp-handoff")
def operator_task_vkp_handoff(
    task_id: int,
    db_path: str = ".vdo/tasks.sqlite",
    output_dir: str = "downloads/vkp-handoffs",
    require_accepted: bool = False,
    write_manifest: bool = True,
) -> dict:
    payload = build_vkp_handoff(
        task_id,
        db_path=Path(db_path),
        output_dir=output_dir,
        require_accepted=require_accepted,
        write_manifest=write_manifest,
    )
    if payload.get("status") == "not_found":
        raise HTTPException(status_code=404, detail=payload.get("next_action") or "task not found")
    return payload


@app.post("/operator/tasks/{task_id}/resume/plan")
def operator_task_resume_plan(task_id: int, request: OperatorResumePlanRequest) -> dict:
    store = TaskStore(Path(request.db_path).resolve())
    try:
        task = store.get_task(task_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if task["status"] != "retryable":
        raise HTTPException(status_code=400, detail="task is not retryable")

    capture, capture_page_match = _latest_capture_for_page(str(task["url"]), request.capture_session_id)
    if capture is None:
        return {
            "service": "video-download-orchestrator",
            "status": "retryable",
            "will_download": False,
            "task": task,
            "capture_session_id": request.capture_session_id,
            "diagnostic": "no accepted browser-sniffer capture for retryable task",
        }

    media_url = str(capture["url"])
    backend = str(capture.get("backend") or "curl")
    request_headers = capture.get("request_headers") if isinstance(capture.get("request_headers"), dict) else {}
    resume_dir = _versioned_resume_dir(Path(request.output_dir), task_id)
    manifest = plan_download(media_url, output_dir=resume_dir, backend=backend, headers=request_headers)
    manifest["resumed_task_id"] = task_id
    manifest["source_page_url"] = task["url"]
    manifest["source_capture_id"] = capture.get("capture_id")
    manifest["sniffed_media"] = _capture_context_for_manifest(capture)
    manifest_path = write_manifest(manifest, resume_dir) if request.write_manifest else None
    return {
        "service": "video-download-orchestrator",
        "status": "planned",
        "will_download": False,
        "resumed_task": task,
        "capture_page_match": capture_page_match,
        "capture": _serialize_capture(capture, include_sensitive=False),
        "resume_dir": str(resume_dir),
        "manifest": redact_manifest(manifest),
        "manifest_path": str(manifest_path) if manifest_path else None,
    }


@app.post("/operator/tasks/{task_id}/resume/execute")
def operator_task_resume_execute(task_id: int, request: OperatorResumeExecuteRequest) -> dict:
    store = TaskStore(Path(request.db_path).resolve())
    try:
        task = store.get_task(task_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if task["status"] != "retryable":
        raise HTTPException(status_code=400, detail="task is not retryable")

    capture, capture_page_match = _latest_capture_for_page(str(task["url"]), request.capture_session_id)
    if capture is None:
        return {
            "service": "video-download-orchestrator",
            "status": "retryable",
            "will_download": False,
            "explicit_user_action": True,
            "task": task,
            "capture_session_id": request.capture_session_id,
            "diagnostic": "no accepted browser-sniffer capture for retryable task",
            "next_action": "请在打开的原页面播放视频，等待浏览器扩展捕获后再次点击页面嗅探恢复。",
        }

    media_url = str(capture["url"])
    backend = str(capture.get("backend") or "curl")
    request_headers = capture.get("request_headers") if isinstance(capture.get("request_headers"), dict) else {}
    resume_dir = _versioned_resume_dir(Path(request.output_dir), task_id)
    manifest = plan_download(media_url, output_dir=resume_dir, backend=backend, headers=request_headers)
    manifest["resumed_task_id"] = task_id
    manifest["source_page_url"] = task["url"]
    manifest["source_capture_id"] = capture.get("capture_id")
    manifest["sniffed_media"] = _capture_context_for_manifest(capture)
    manifest_path = write_manifest(manifest, resume_dir)
    payload = execute_manifest(
        store=store,
        manifest=manifest,
        artifact_base_dir=resume_dir,
        manifest_path=manifest_path,
        hard_timeout=request.hard_timeout,
        idle_timeout=request.idle_timeout,
    )
    payload.update(
        {
            "explicit_user_action": True,
            "will_download": True,
            "resumed_task": task,
            "capture_page_match": capture_page_match,
            "capture": _serialize_capture(capture, include_sensitive=False),
            "resume_dir": str(resume_dir),
        }
    )
    return payload


@app.get("/artifacts", response_class=PlainTextResponse)
def read_artifact(path: str = Query(min_length=1)) -> PlainTextResponse:
    artifact_path = _safe_artifact_path(Path(path))
    if not artifact_path.exists() or not artifact_path.is_file():
        raise HTTPException(status_code=404, detail=f"artifact not found: {path}")
    return PlainTextResponse(artifact_path.read_text(encoding="utf-8"))


def _safe_artifact_path(path: Path) -> Path:
    resolved = path.resolve()
    if ".vdo" not in resolved.parts:
        raise HTTPException(status_code=400, detail="artifact path must be inside a .vdo directory")
    return resolved


def _versioned_batch_dir(output_dir: Path) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return output_dir / ".vdo" / "batches" / f"{stamp}-web-batch"


def _batch_status(results: list[dict]) -> str:
    if any(item.get("status") == "failed" for item in results):
        return "finished_with_failures"
    if any(item.get("status") == "retryable" for item in results):
        return "finished_with_retryable"
    return "finished"


def _request_ytdlp_options(request: object) -> dict[str, object]:
    options: dict[str, object] = {}
    cookies_from_browser = getattr(request, "cookies_from_browser", None)
    cookies = getattr(request, "cookies", None)
    js_runtimes = getattr(request, "js_runtimes", None)
    remote_components = getattr(request, "remote_components", None)
    if cookies_from_browser:
        options["cookies_from_browser"] = str(cookies_from_browser)
    if cookies:
        options["cookies"] = str(cookies)
    if js_runtimes:
        options["js_runtimes"] = str(js_runtimes)
    if remote_components:
        options["remote_components"] = [str(component) for component in remote_components if component]
    return options


def _clean_mediago_request(request: MediaGoHandoffRequest) -> dict:
    if request.text is not None:
        return clean_batch_input(request.text)
    if request.items is not None:
        raw_items = [
            item.model_dump() if isinstance(item, BatchItem) else item
            for item in request.items
        ]
        return clean_batch_input({"items": raw_items})
    if request.url:
        return clean_batch_input(request.url)
    return clean_batch_input("")


def _openclaw_raw_input(request: OpenClawLinkPlanRequest | OpenClawTelegramExecuteRequest) -> object:
    if request.text is not None:
        return request.text
    if request.url is not None:
        return request.url
    return {"items": [item.model_dump() if isinstance(item, BatchItem) else item for item in request.items or []]}


def _mediago_plan_summary(plan: dict, clean_payload: dict) -> dict:
    tasks = plan.get("payload", {}).get("tasks", [])
    blocked_items = plan.get("blocked_items", []) if isinstance(plan.get("blocked_items"), list) else []
    type_counts: dict[str, int] = {}
    for task in tasks:
        task_type = str(task.get("type") or "unknown")
        type_counts[task_type] = type_counts.get(task_type, 0) + 1

    skipped_reasons: dict[str, int] = {}
    for skipped in clean_payload.get("skipped", []):
        reason = str(skipped.get("reason") or "unknown")
        skipped_reasons[reason] = skipped_reasons.get(reason, 0) + 1

    strategy_counts: dict[str, int] = {}
    for task in tasks:
        task_url = str(task.get("url") or "")
        route = classify_url(task_url)
        adapter = page_link_site_adapter_diagnostic(task_url)
        strategy = (
            "peer_to_peer"
            if str(task.get("type") or "") == "torrent"
            else "browser_sniffing_fallback"
            if adapter.get("matched") and adapter.get("fallback_mode") == "browser_sniffing"
            else "site_adapter"
            if adapter.get("matched")
            else "direct_download"
            if route.kind in {"direct-transfer", "manifest"}
            else "general_download"
        )
        strategy_counts[strategy] = strategy_counts.get(strategy, 0) + 1

    lines = (
        [
            f"识别 {clean_payload.get('item_count', 0)} 个候选链接",
            f"可导入 MediaGo {len(tasks)} 个",
            f"过滤 {clean_payload.get('skipped_count', 0)} 项",
        ]
        if blocked_items
        else [
            f"识别 {clean_payload.get('item_count', 0)} 个可导入链接",
            f"过滤 {clean_payload.get('skipped_count', 0)} 项",
        ]
    )
    if blocked_items:
        lines.append(f"MediaGo 能力准入阻止 {len(blocked_items)} 项")
    if type_counts:
        type_text = "、".join(
            f"{_mediago_type_label(task_type)} {count} 个"
            for task_type, count in sorted(type_counts.items())
        )
        lines.append(f"类型：{type_text}")
    if skipped_reasons:
        reason_text = "、".join(
            f"{_skip_reason_label(reason)} {count} 项"
            for reason, count in sorted(skipped_reasons.items())
        )
        lines.append(f"过滤原因：{reason_text}")
    if strategy_counts.get("browser_sniffing_fallback"):
        lines.append(f"{strategy_counts['browser_sniffing_fallback']} 个平台页面失败后需要浏览器嗅探")

    strategy_labels = {
        "browser_sniffing_fallback": "普通下载，失败后页面嗅探",
        "direct_download": "直接媒体下载",
        "general_download": "通用下载器",
        "peer_to_peer": "磁力 / BT 下载",
        "site_adapter": "站点适配",
    }

    return {
        "label": "；".join(lines),
        "type_counts": type_counts,
        "type_labels": {
            task_type: _mediago_type_label(task_type)
            for task_type in sorted(type_counts)
        },
        "skipped_reasons": skipped_reasons,
        "skipped_reason_labels": {
            reason: _skip_reason_label(reason)
            for reason in sorted(skipped_reasons)
        },
        "strategy_counts": strategy_counts,
        "strategy_labels": {strategy: strategy_labels[strategy] for strategy in sorted(strategy_counts)},
        "quality_gate": "下载完成后检查文件大小、可播放性、时长和音视频轨道；存在异常时不要标记成功。",
        "blocked_count": len(blocked_items),
        "blocked_items": blocked_items,
        "recommended_action": "确认清洗和能力准入结果后，可导入 MediaGo 队列；被阻止的平台页先通过 VDO 嗅探解析。"
        if blocked_items
        else "确认清洗结果后，可导入 MediaGo 队列或直接开始下载。",
    }


def _mediago_type_label(task_type: str) -> str:
    return {
        "bilibili": "B站",
        "direct": "直链",
        "torrent": "磁力链接 / BT 种子",
        "m3u8": "m3u8",
        "mediago": "MediaGo",
        "youtube": "YouTube",
    }.get(task_type, task_type)


def _skip_reason_label(reason: str) -> str:
    return {
        "duplicate": "重复",
        "empty_url": "空链接",
        "missing_youtube_video_id": "缺少 YouTube 视频 ID",
        "invalid_magnet_uri": "无效磁力链接",
        "not_http_url": "非 HTTP 链接",
        "youtube_redirect_external": "YouTube 外部跳转",
    }.get(reason, reason)


def _versioned_rerun_dir(output_dir: Path, task_id: int, backend: str) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    safe_backend = backend.replace("/", "_").replace("\\", "_")
    return output_dir / ".vdo" / "reruns" / f"{stamp}-task-{task_id}-{safe_backend}"


def _versioned_resume_dir(output_dir: Path, task_id: int) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return output_dir / ".vdo" / "resumes" / f"{stamp}-task-{task_id}"


def _capture_context_for_manifest(capture: dict) -> dict:
    keys = {
        "capture_id",
        "capture_session_id",
        "captured_at",
        "page_url",
        "url",
        "tab_id",
        "frame_id",
        "resource_type",
        "method",
        "status_code",
        "redirect_count",
        "media_type",
        "media_kind",
        "category",
        "filename",
        "backend",
        "request_headers",
        "response_headers",
        "content_length",
        "content_range",
        "dedupe_key",
        "quality_score",
        "download_candidate_reason",
        "completeness",
        "manifest_probe",
        "manifest_probe_budget",
        "capture_group_id",
        "manifest_path",
    }
    return {key: capture.get(key) for key in keys if key in capture}


def _media_protocol(url: str, content_type: str | None, headers: dict[str, str]) -> dict | None:
    return match_media_protocol(url, content_type, headers.values())


def _sniffed_media_kind(request: SniffedMediaPlanRequest) -> str:
    requested = str(request.media_kind or "").lower()
    if requested in {"adaptive", "audio", "mixed", "subtitle", "unknown", "video"}:
        return requested
    return infer_media_kind(request.url, request.content_type, request.response_headers.values())


def _looks_like_stream_manifest(url: str, content_type: str | None, headers: dict[str, str]) -> bool:
    protocol = _media_protocol(url, content_type, headers)
    return bool(protocol and protocol.get("category") == "stream")


def _looks_like_direct_media(url: str, content_type: str | None, headers: dict[str, str]) -> bool:
    protocol = _media_protocol(url, content_type, headers)
    return bool(protocol and protocol.get("category") == "files")


def _looks_like_sniffed_media(request: SniffedMediaPlanRequest) -> bool:
    headers = {**request.headers, **request.response_headers}
    return request.category in {"stream", "files", "subtitles"} or _media_protocol(request.url, request.content_type, headers) is not None


def _sniffed_backend(request: SniffedMediaPlanRequest) -> str:
    headers = request.request_headers or _download_headers_from_combined(request.headers)
    protocol = _media_protocol(request.url, request.content_type, request.response_headers)
    if request.category == "files" or (protocol and protocol.get("category") == "files"):
        return "curl"
    if headers:
        return "vsd"
    return "n_m3u8dl_re"
def _download_headers_from_combined(headers: dict[str, str]) -> dict[str, str]:
    wanted = {"user-agent", "referer", "cookie", "authorization"}
    return {name: value for name, value in headers.items() if name.lower() in wanted}

def _store_sniffed_capture(
    request: SniffedMediaPlanRequest,
    manifest: dict,
    manifest_path: Path | None,
    *,
    dedupe_key: str,
    quality_score: int,
    candidate_reason: str,
) -> dict:
    captured_at = datetime.now(UTC)
    lease = new_capture_lease(captured_at=captured_at)
    capture = {
        "capture_id": str(uuid4()),
        "captured_at": captured_at.isoformat(),
        "expires_at": lease["expires_at"],
        "lease_status": "active",
        "sensitive_data_available": True,
        "capture_session_id": request.capture_session_id,
        "capture_group_id": _capture_group_id(request.capture_session_id, request.tab_id, request.page_url),
        "page_url": request.page_url,
        "url": request.url,
        "tab_id": request.tab_id,
        "frame_id": request.frame_id,
        "resource_type": request.resource_type,
        "method": request.method,
        "status_code": request.status_code,
        "redirect_count": request.redirect_count,
        "media_type": request.media_type,
        "media_kind": _sniffed_media_kind(request),
        "category": request.category,
        "filename": request.filename,
        "backend": manifest["selected_backend"],
        "request_headers": request.request_headers or _download_headers_from_combined(request.headers),
        "response_headers": request.response_headers,
        "content_length": request.content_length,
        "content_range": request.content_range,
        "dedupe_key": dedupe_key,
        "quality_score": quality_score,
        "download_candidate_reason": candidate_reason,
        "completeness": manifest.get("sniffed_media", {}).get("completeness") if isinstance(manifest.get("sniffed_media"), dict) else {},
        "manifest_probe": {},
        "manifest_probe_budget": {"request_count": 0, "total_bytes": 0},
        "manifest": manifest,
        "manifest_path": str(manifest_path) if manifest_path else None,
    }
    for index, existing in enumerate(SNIFFED_MEDIA_CAPTURES):
        if existing.get("dedupe_key") == dedupe_key and existing.get("capture_group_id") == capture["capture_group_id"]:
            if int(existing.get("quality_score") or 0) <= quality_score:
                capture["capture_id"] = existing["capture_id"]
                SNIFFED_MEDIA_CAPTURES[index] = capture
            return SNIFFED_MEDIA_CAPTURES[index]
    SNIFFED_MEDIA_CAPTURES.append(capture)
    del SNIFFED_MEDIA_CAPTURES[:-MAX_SNIFFED_MEDIA_CAPTURES]
    return capture


def _store_sniffed_rejection(request: SniffedMediaPlanRequest, reason: str) -> dict:
    rejection = {
        "rejection_id": str(uuid4()),
        "rejected_at": datetime.now(UTC).isoformat(),
        "capture_session_id": request.capture_session_id,
        "capture_group_id": _capture_group_id(request.capture_session_id, request.tab_id, request.page_url),
        "page_url": request.page_url,
        "url": request.url,
        "tab_id": request.tab_id,
        "frame_id": request.frame_id,
        "resource_type": request.resource_type,
        "method": request.method,
        "status_code": request.status_code,
        "content_type": request.content_type,
        "media_type": request.media_type,
        "media_kind": _sniffed_media_kind(request),
        "category": request.category,
        "filename": request.filename,
        "request_id": request.request_id,
        "reason": reason,
        "headers": request.headers,
        "request_headers": request.request_headers,
        "response_headers": request.response_headers,
    }
    SNIFFED_MEDIA_REJECTIONS.append(rejection)
    del SNIFFED_MEDIA_REJECTIONS[:-MAX_SNIFFED_MEDIA_REJECTIONS]
    append_sniff_record("rejections", _serialize_rejection(rejection, include_sensitive=False))
    return rejection


def _latest_capture_for_page(page_url: str | None, capture_session_id: str | None = None) -> tuple[dict | None, str | None]:
    _expire_sniffed_capture_secrets()
    matches, match_kind = _records_matching_page(SNIFFED_MEDIA_CAPTURES, page_url, capture_session_id)
    group = _latest_capture_group(matches)
    eligible = [
        item
        for item in group
        if capture_lease_summary(item)["active"] and capture_selection_summary(item)["eligible"]
    ]
    if not eligible:
        return None, match_kind
    return max(eligible, key=capture_selection_key), match_kind


def _latest_capture_group(records: list[dict]) -> list[dict]:
    if not records:
        return []
    latest = max(enumerate(records), key=lambda pair: (str(pair[1].get("captured_at") or ""), pair[0]))[1]
    group_id = latest.get("capture_group_id")
    return [item for item in records if item.get("capture_group_id") == group_id]


def _capture_group_id(capture_session_id: str | None, tab_id: int | None, page_url: str | None) -> str:
    normalized_page = _normalize_page_url_for_match(page_url or "")
    raw = "|".join([capture_session_id or "", str(tab_id) if tab_id is not None else "", normalized_page])
    return sha256(raw.encode("utf-8")).hexdigest()[:16]
def _latest_rejection_for_page(page_url: str | None, capture_session_id: str | None = None) -> tuple[dict | None, str | None]:
    matches, match_kind = _records_matching_page(SNIFFED_MEDIA_REJECTIONS, page_url, capture_session_id)
    if not matches:
        return None, match_kind
    return max(matches, key=lambda rejection: str(rejection.get("rejected_at") or "")), match_kind


def _capture_count_for_page(page_url: str | None, capture_session_id: str | None = None) -> int:
    matches, _match_kind = _records_matching_page(SNIFFED_MEDIA_CAPTURES, page_url, capture_session_id)
    return len(matches)


def _rejection_count_for_page(page_url: str | None, capture_session_id: str | None = None) -> int:
    matches, _match_kind = _records_matching_page(SNIFFED_MEDIA_REJECTIONS, page_url, capture_session_id)
    return len(matches)


def _records_matching_page(records: list[dict], page_url: str | None, capture_session_id: str | None = None) -> tuple[list[dict], str | None]:
    if capture_session_id:
        records = [record for record in records if record.get("capture_session_id") == capture_session_id]
    if not page_url:
        return list(records), "unfiltered" if records else None

    exact_matches = [record for record in records if record.get("page_url") == page_url]
    if exact_matches:
        return exact_matches, "exact"

    normalized_page_url = _normalize_page_url_for_match(page_url)
    if not normalized_page_url:
        return [], None
    normalized_matches = [
        record
        for record in records
        if isinstance(record.get("page_url"), str) and _normalize_page_url_for_match(str(record["page_url"])) == normalized_page_url
    ]
    if normalized_matches:
        return normalized_matches, "normalized"
    return [], None


def _normalize_page_url_for_match(value: str) -> str:
    try:
        parsed = urlparse(value)
    except ValueError:
        return ""
    if not parsed.scheme or not parsed.netloc:
        return ""
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}{parsed.path}"


def _serialize_capture(capture: dict, *, include_sensitive: bool) -> dict:
    expire_capture_secrets(capture)
    serialized = dict(capture)
    serialized["completeness"] = sniffed_completeness_diagnostic(capture)
    serialized["capture_lease"] = capture_lease_summary(capture)
    if not include_sensitive and isinstance(serialized.get("url"), str):
        serialized["url"] = redact_ephemeral_url(serialized["url"])
    if not include_sensitive and isinstance(serialized.get("page_url"), str):
        serialized["page_url"] = redact_url(serialized["page_url"])
    serialized["manifest"] = redact_manifest(capture["manifest"])
    if not include_sensitive:
        serialized["request_headers"] = redact_manifest({"request_headers": capture["request_headers"]})["request_headers"]
        serialized["response_headers"] = redact_manifest({"sniffed_media": {"response_headers": capture.get("response_headers", {})}})["sniffed_media"][
            "response_headers"
        ]
    return serialized


def _serialize_capture_summary(capture: dict, *, include_sensitive: bool) -> dict:
    expire_capture_secrets(capture)
    keys = {
        "capture_id",
        "captured_at",
        "capture_session_id",
        "capture_group_id",
        "page_url",
        "url",
        "media_type",
        "media_kind",
        "category",
        "filename",
        "backend",
        "content_length",
        "content_range",
        "quality_score",
        "download_candidate_reason",
        "completeness",
        "manifest_probe",
        "manifest_probe_budget",
        "capture_group_id",
        "completeness",
        "manifest_probe",
    }
    summary = {key: capture.get(key) for key in keys if key in capture}
    summary["completeness"] = sniffed_completeness_diagnostic(capture)
    summary["selection"] = capture_selection_summary(capture)
    summary["capture_lease"] = capture_lease_summary(capture)
    if not include_sensitive and isinstance(summary.get("url"), str):
        summary["url"] = redact_ephemeral_url(str(summary["url"]))
    if not include_sensitive and isinstance(summary.get("page_url"), str):
        summary["page_url"] = redact_url(str(summary["page_url"]))
    return summary


def _expire_sniffed_capture_secrets() -> None:
    for capture in SNIFFED_MEDIA_CAPTURES:
        expire_capture_secrets(capture)


def _serialize_rejection(rejection: dict, *, include_sensitive: bool) -> dict:
    serialized = dict(rejection)
    if not include_sensitive and isinstance(serialized.get("url"), str):
        serialized["url"] = redact_url(serialized["url"])
    if not include_sensitive and isinstance(serialized.get("page_url"), str):
        serialized["page_url"] = redact_url(serialized["page_url"])
    if not include_sensitive:
        serialized["headers"] = redact_manifest({"request_headers": serialized.get("headers", {})})["request_headers"]
        serialized["request_headers"] = redact_manifest({"request_headers": serialized.get("request_headers", {})})["request_headers"]
        serialized["response_headers"] = redact_manifest({"sniffed_media": {"response_headers": serialized.get("response_headers", {})}})["sniffed_media"][
            "response_headers"
        ]
    return serialized


def _sniffed_candidate_quality(request: SniffedMediaPlanRequest, backend: str) -> tuple[int, str]:
    headers = {**request.headers, **request.response_headers}
    is_manifest = _looks_like_stream_manifest(request.url, request.content_type, headers)
    is_direct = _looks_like_direct_media(request.url, request.content_type, headers)
    has_headers = bool(request.request_headers or _download_headers_from_combined(request.headers))
    range_diagnostic = content_range_diagnostic(request.content_range or _header_value(request.response_headers, "content-range"))
    resource_role = media_resource_role(request.url)
    score = 20
    reasons = []
    if is_manifest:
        score += 80
        reasons.append("stream manifest")
    elif is_direct:
        score += 60
        reasons.append("direct media file")
    if has_headers:
        score += 10
        reasons.append("request headers captured")
    if backend == "vsd":
        score += 5
        reasons.append("header-bound manifest uses vsd")
    if request.content_length and request.content_length > 5_000_000:
        score += 10
        reasons.append("substantial content length")
    elif request.content_length and request.content_length > 1_000_000:
        score += 5
        reasons.append("large content length")
    if range_diagnostic["range_kind"] == "complete":
        score += 15
        reasons.append("range covers the complete file")
    elif range_diagnostic["range_kind"] == "partial":
        score -= 30
        reasons.append("range response is less suitable for full download")
    if resource_role == "primary":
        score += 15
        reasons.append("known primary video CDN")
    elif resource_role == "auxiliary":
        score -= 50
        reasons.append("likely page effect or auxiliary media")
    if _sniffed_media_kind(request) == "audio":
        score += 10
        reasons.append("audio media identified")
    return max(score, 0), "; ".join(reasons) if reasons else "sniffed playable media"


def _sniffed_dedupe_key(request: SniffedMediaPlanRequest) -> str:
    parsed = urlparse(request.url)
    normalized_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
    return "|".join(
        [
            request.page_url or "",
            normalized_url,
            request.media_type or "",
            _sniffed_media_kind(request),
            request.category or "",
        ]
    )


def _header_value(headers: dict[str, str], name: str) -> str:
    lowered = name.lower()
    for key, value in headers.items():
        if key.lower() == lowered:
            return value
    return ""
