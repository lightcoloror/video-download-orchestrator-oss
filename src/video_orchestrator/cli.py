from __future__ import annotations

import json
import shutil
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated
from uuid import uuid4

import typer

from video_orchestrator.backends.base import BackendSpec, get_backend_specs
from video_orchestrator.backends.base import get_backend_adapter
from video_orchestrator.backends.vsd import build_vsd_capture_command
from video_orchestrator.backends.ytdlp_api import extract_ytdlp_metadata
from video_orchestrator.external_uis import get_external_ui_status
from video_orchestrator.router import classify_url
from video_orchestrator.portability import build_portable_doctor
from video_orchestrator.runtime.backend_recovery import backend_fallback_decision
from video_orchestrator.runtime.config import api_base_url
from video_orchestrator.runtime.config import check_config
from video_orchestrator.runtime.config import hard_timeout_seconds as configured_hard_timeout_seconds
from video_orchestrator.runtime.config import idle_timeout_seconds as configured_idle_timeout_seconds
from video_orchestrator.runtime.entrypoint_status import build_entrypoint_status
from video_orchestrator.runtime.batch_cleaner import load_and_clean_batch_spec
from video_orchestrator.runtime.handoff import build_task_handoff
from video_orchestrator.runtime.handoff import build_url_handoff
from video_orchestrator.runtime.manifests import write_manifest
from video_orchestrator.runtime.logs import write_task_attempts_log
from video_orchestrator.runtime.mediago import mediago_task_status
from video_orchestrator.runtime.paths import report_dir
from video_orchestrator.runtime.quality import build_quality_review
from video_orchestrator.runtime.quality import classify_backend_error
from video_orchestrator.runtime.quality import discover_archive_files
from video_orchestrator.runtime.quality import discover_output_files
from video_orchestrator.runtime.quality import parse_backend_stdout_json
from video_orchestrator.runtime.quality import probe_media_files
from video_orchestrator.runtime.quality import write_quality_review
from video_orchestrator.runtime.operator import artifact_map
from video_orchestrator.runtime.operator import operator_review_summary
from video_orchestrator.runtime.openclaw import build_openclaw_link_plan
from video_orchestrator.runtime.openclaw import execute_openclaw_telegram_command
from video_orchestrator.runtime.page_link import capture_context_for_manifest as page_link_capture_context_for_manifest
from video_orchestrator.runtime.page_link import execute_page_capture
from video_orchestrator.runtime.page_link import page_link_site_adapter_diagnostic
from video_orchestrator.runtime.page_link import redact_capture_for_output
from video_orchestrator.runtime.page_link import sniff_timeout_message
from video_orchestrator.runtime.page_link import try_site_specific_page_download
from video_orchestrator.runtime.page_link import wait_for_sniffed_capture
from video_orchestrator.runtime.redaction import redact_attempts
from video_orchestrator.runtime.redaction import redact_command
from video_orchestrator.runtime.redaction import redact_manifest
from video_orchestrator.runtime.reports import write_task_report
from video_orchestrator.runtime.reports import render_review_package_summary
from video_orchestrator.runtime.sniff_store import cleanup_sniff_records
from video_orchestrator.runtime.sniff_store import read_sniff_diagnostics
from video_orchestrator.runtime.site_adapters import match_site_adapter
from video_orchestrator.runtime.subprocess_runner import command_exists
from video_orchestrator.runtime.subprocess_runner import run_command
from video_orchestrator.runtime.task_store import TaskStore
from video_orchestrator.runtime.vkp_handoff import build_vkp_handoff
from video_orchestrator.runtime.wechat_channels_helper import wechat_channels_helper_status
from video_orchestrator.runtime.wechat_channels_resolve import record_wechat_channels_resolution
from video_orchestrator.runtime.wechat_channels_resolve import resolve_and_import_wechat_channels
from video_orchestrator.runtime.wechat_channels_resolve import resolve_wechat_channels_share
from video_orchestrator.runtime.wechat_channels_resolve import wechat_channels_defaults
from video_orchestrator.services import plan_download

app = typer.Typer(help="Local video download orchestrator")
task_app = typer.Typer(help="Task metadata commands")
app.add_typer(task_app, name="task")

DEFAULT_DB = Path(".vdo/tasks.sqlite")


def _emit(payload: dict, output_format: str) -> None:
    if output_format != "json":
        raise typer.BadParameter("Only json output is supported in the stable CLI v1.")
    typer.echo(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


@app.command()
def entrypoint_status(
    output_format: Annotated[str, typer.Option("--format", help="Output format. Stable v1 supports json.")] = "json",
) -> None:
    _emit(build_entrypoint_status(), output_format)


@app.command()
def version() -> None:
    typer.echo("video-download-orchestrator 0.1.0")


@app.command()
def doctor(
    output_format: Annotated[str, typer.Option("--format", help="Output format. Stable v1 supports json.")] = "json",
) -> None:
    backend_checks = [_backend_status(backend) for backend in get_backend_specs()]
    required_failures = [check for check in backend_checks if check["required"] and check["status"] != "ok"]
    warnings = [check for check in backend_checks if not check["required"] and check["status"] != "ok"]
    _emit(
        {
            "service": "video-download-orchestrator",
            "status": "degraded" if required_failures else "ok",
            "checks": [
                {"name": "python_package", "status": "ok", "detail": "video_orchestrator import ok"},
                {"name": "router", "status": "ok", "detail": "classify_url import ok"},
                {"name": "task_store", "status": "ok", "detail": str(DEFAULT_DB)},
            ],
            "backends": backend_checks,
            "warnings": warnings,
        },
        output_format,
    )


@app.command(name="portable-doctor")
def portable_doctor(
    output_format: Annotated[str, typer.Option("--format", help="Output format. Stable v1 supports json.")] = "json",
) -> None:
    payload = build_portable_doctor()
    _emit(payload, output_format)
    if payload["status"] == "failed":
        raise typer.Exit(1)


@app.command(name="backends")
def list_backends(
    output_format: Annotated[str, typer.Option("--format", help="Output format. Stable v1 supports json.")] = "json",
) -> None:
    _emit({"service": "video-download-orchestrator", "backends": [_backend_status(backend) for backend in get_backend_specs()]}, output_format)


@app.command(name="uis")
def list_external_uis(
    check: Annotated[bool, typer.Option("--check", help="Check local source/launcher availability without starting UI.")] = False,
    output_format: Annotated[str, typer.Option("--format", help="Output format. Stable v1 supports json.")] = "json",
) -> None:
    _emit(
        {
            "service": "video-download-orchestrator",
            "will_download": False,
            "uis": [ui.model_dump() for ui in get_external_ui_status(check=check)],
        },
        output_format,
    )


@app.command(name="page-link-doctor")
def page_link_doctor(
    output_format: Annotated[str, typer.Option("--format", help="Output format. Stable v1 supports json.")] = "json",
) -> None:
    checks = _page_link_readiness_checks()
    failed = [check for check in checks if check["status"] == "failed"]
    degraded = [check for check in checks if check["status"] in {"degraded", "missing_optional"}]
    _emit(
        {
            "service": "video-download-orchestrator",
            "status": "failed" if failed else ("degraded" if degraded else "ok"),
            "checks": checks,
        },
        output_format,
    )


@app.command(name="wechat-channels-status")
def wechat_channels_status(
    output_format: Annotated[str, typer.Option("--format", help="Output format. Stable v1 supports json.")] = "json",
) -> None:
    """检查微信视频号本机授权态解析 helper 可用性（只报告，不读取凭证）。"""
    _emit(wechat_channels_helper_status(), output_format)


@app.command(name="mediago-task-status")
def mediago_download_task_status(
    task_id: int,
    output_format: Annotated[str, typer.Option("--format", help="Output format. Stable v1 supports json.")] = "json",
) -> None:
    """只读检查 MediaGo 任务状态和输出文件回执。"""
    _emit(mediago_task_status(task_id), output_format)


@app.command(name="wechat-channels-resolve")
def wechat_channels_resolve(
    url: str,
    output_dir: Annotated[Path, typer.Option("--output-dir", help="Artifact output directory.")] = Path("downloads"),
    db_path: Annotated[Path, typer.Option("--db-path", help="Task SQLite path.")] = DEFAULT_DB,
    output_format: Annotated[str, typer.Option("--format", help="Output format. Stable v1 supports json.")] = "json",
) -> None:
    """解析微信视频号分享链接并记录非敏感结果；不导入 MediaGo、不触发下载。"""
    resolved = resolve_wechat_channels_share(url)
    store = TaskStore(db_path.resolve())
    record = record_wechat_channels_resolution(url, resolved, output_dir=output_dir, store=store)
    _emit(
        {
            **record,
            "explicit_user_action": True,
            "will_download": False,
            "imported": False,
            "next_action": str(
                resolved.get("next_action")
                or "解析完成。确认后可用 `vdo wechat-channels-import <url>` 显式导入 MediaGo（默认只入队）。"
            ),
        },
        output_format,
    )


@app.command(name="wechat-channels-import")
def wechat_channels_import(
    url: str,
    output_dir: Annotated[Path, typer.Option("--output-dir", help="Artifact output directory.")] = Path("downloads"),
    db_path: Annotated[Path, typer.Option("--db-path", help="Task SQLite path.")] = DEFAULT_DB,
    start_download: Annotated[
        bool | None,
        typer.Option("--start-download/--queue-only", help="Start in MediaGo immediately; use --queue-only for manual queue review."),
    ] = None,
    provider: Annotated[str | None, typer.Option("--provider", help="Manual mode: auto, local-helper, or public-profile.")] = None,
    transport: Annotated[str | None, typer.Option("--transport", help="Manual mode: auto, direct, or proxy.")] = None,
    quality: Annotated[str | None, typer.Option("--quality", help="Manual mode: highest, compatible, original, h264, or h265.")] = None,
    force_reimport: Annotated[
        bool,
        typer.Option("--force-reimport", help="Create another MediaGo task even when the content was imported before."),
    ] = False,
    output_format: Annotated[str, typer.Option("--format", help="Output format. Stable v1 supports json.")] = "json",
) -> None:
    """解析微信视频号分享链接并交给 MediaGo，默认立即开始下载。"""
    defaults = wechat_channels_defaults()
    _emit(
        resolve_and_import_wechat_channels(
            url,
            output_dir=output_dir,
            db_path=db_path,
            start_download=defaults["start_download"] if start_download is None else start_download,
            provider=provider or defaults["provider"],
            transport=transport or defaults["transport"],
            quality=quality or defaults["quality"],
            force_reimport=force_reimport,
        ),
        output_format,
    )


@app.command(name="operator-doctor")
def operator_doctor(
    db_path: Annotated[Path, typer.Option("--db-path", help="Task SQLite path.")] = DEFAULT_DB,
    output_format: Annotated[str, typer.Option("--format", help="Output format. Stable v1 supports json.")] = "json",
) -> None:
    readiness = _page_link_readiness_checks()
    store = TaskStore(db_path.resolve())
    retryable_tasks = store.list_retryable_tasks()
    latest_status = "unknown"
    latest_payload: dict = {}
    try:
        latest_payload = _api_get_json(api_base_url(), "/sniffed-media/latest", {})
        latest_status = str(latest_payload.get("status") or "unknown")
    except OSError as exc:
        latest_payload = {"error": str(exc)}
    failed = [check for check in readiness if check["status"] == "failed"]
    _emit(
        {
            "service": "video-download-orchestrator",
            "status": "failed" if failed else "ok",
            "checks": readiness,
            "retryable_task_count": len(retryable_tasks),
            "retryable_tasks": retryable_tasks,
            "latest_capture_status": latest_status,
            "latest_capture": latest_payload.get("capture"),
            "latest_rejection": latest_payload.get("latest_rejection"),
            "smoke_script": "scripts\\operator-smoke.ps1",
        },
        output_format,
    )


@app.command()
def route(
    url: str,
    output_format: Annotated[str, typer.Option("--format", help="Output format. Stable v1 supports json.")] = "json",
) -> None:
    classification = classify_url(url)
    _emit(
        {
            "service": "video-download-orchestrator",
            "route": classification.model_dump(),
            "site_adapter": page_link_site_adapter_diagnostic(url),
            "backend_guidance": _backend_guidance(classification.preferred_backend),
            "will_download": False,
        },
        output_format,
    )


@app.command(name="inspect")
def inspect_url(
    url: str,
    flat: Annotated[bool, typer.Option("--flat/--full", help="Use flat extraction for playlists when possible.")] = True,
    output_format: Annotated[str, typer.Option("--format", help="Output format. Stable v1 supports json.")] = "json",
) -> None:
    _emit(
        {
            "service": "video-download-orchestrator",
            "will_download": False,
            "metadata": extract_ytdlp_metadata(url, flat=flat),
        },
        output_format,
    )


@app.command(name="sniff")
def sniff_page(
    url: str,
    proxy: Annotated[str | None, typer.Option("--proxy", help="Proxy for the browser launched by vsd capture.")] = None,
    headless: Annotated[bool, typer.Option("--headless", help="Launch capture browser in headless mode.")] = False,
    save_cookies: Annotated[bool, typer.Option("--save-cookies", help="Ask vsd to save browser cookies as cookies.txt.")] = False,
    cookies_path: Annotated[Path | None, typer.Option("--cookies", help="Netscape cookie file passed to vsd capture.")] = None,
    output_format: Annotated[str, typer.Option("--format", help="Output format. Stable v1 supports json.")] = "json",
) -> None:
    command = build_vsd_capture_command(
        url,
        proxy=proxy,
        headless=headless,
        save_cookies=save_cookies,
        cookies_path=cookies_path,
    )
    _emit(
        {
            "service": "video-download-orchestrator",
            "will_download": False,
            "capture_backend": "vsd",
            "command": command,
            "next_action": "Run this command in a terminal, play the video in the launched browser, then copy the captured curl/manifest URL back into the orchestrator.",
        },
        output_format,
    )


@app.command(name="sniff-diagnostics")
def sniff_diagnostics(
    limit: Annotated[int, typer.Option("--limit", help="Maximum records per diagnostic file.")] = 100,
    base_dir: Annotated[Path, typer.Option("--base-dir", help="Sniff diagnostic base directory.")] = Path(".vdo"),
    output_format: Annotated[str, typer.Option("--format", help="Output format. Stable v1 supports json.")] = "json",
) -> None:
    diagnostics = read_sniff_diagnostics(limit=limit, base_dir=base_dir)
    _emit({"service": "video-download-orchestrator", "status": "ok", "will_download": False, **diagnostics}, output_format)


@app.command(name="handoff")
def handoff_url(
    url: str,
    output_dir: Annotated[Path, typer.Option("--output-dir", help="Planned output directory.")] = Path("downloads"),
    capture_session: Annotated[str | None, typer.Option("--capture-session", help="Browser sniffer capture session id.")] = None,
    output_format: Annotated[str, typer.Option("--format", help="Output format. Stable v1 supports json.")] = "json",
) -> None:
    _emit(build_url_handoff(url, output_dir=str(output_dir), capture_session=capture_session), output_format)


@app.command(name="openclaw-plan")
def openclaw_plan(
    text_or_path: Annotated[str, typer.Argument(help="Raw Telegram text, URL, or a path to a text/JSON file.")],
    output_dir: Annotated[Path, typer.Option("--output-dir", help="Planning artifact directory.")] = Path("downloads/openclaw"),
    backend: Annotated[str | None, typer.Option("--backend", help="Optional backend override for every retained URL.")] = None,
    write_manifests: Annotated[bool, typer.Option("--write-manifests/--no-write-manifests", help="Write local manifest artifacts for later human-approved execution.")] = True,
    include_manifests: Annotated[bool, typer.Option("--include-manifests", help="Include redacted manifests in stdout.")] = False,
    output_format: Annotated[str, typer.Option("--format", help="Output format. Stable v1 supports json.")] = "json",
) -> None:
    raw = _raw_text_or_file(text_or_path)
    _emit(
        build_openclaw_link_plan(
            raw,
            output_dir=output_dir,
            backend=backend,
            write_manifests=write_manifests,
            include_manifests=include_manifests,
        ),
        output_format,
    )


def _raw_text_or_file(text_or_path: str) -> object:
    path = Path(text_or_path)
    try:
        path_exists = path.exists()
    except OSError:
        path_exists = False
    if not path_exists:
        return text_or_path
    text = path.read_text(encoding="utf-8")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


@app.command(name="openclaw-execute")
def openclaw_execute(
    text_or_path: Annotated[str, typer.Argument(help="Raw Telegram text, URL, or a path to a text/JSON file.")],
    actor_id: Annotated[str, typer.Option("--actor-id", help="Telegram user id or approved actor id.")] = "",
    allowed_actor_ids: Annotated[list[str] | None, typer.Option("--allowed-actor-id", help="Allowed Telegram user id. Can be repeated. Env VDO_TELEGRAM_ALLOWED_USER_IDS is used when omitted.")] = None,
    approval_command: Annotated[str, typer.Option("--approval-command", help="Original Telegram command text for audit.")] = "",
    approval_channel: Annotated[str, typer.Option("--approval-channel", help="Approval channel label.")] = "telegram",
    confirm: Annotated[bool, typer.Option("--confirm", help="Execute after reviewing the returned summary. Without this flag only a confirmation summary is returned.")] = False,
    confirm_sensitive: Annotated[bool, typer.Option("--confirm-sensitive", help="Required when the plan may need browser sniffing, cookies, or login state.")] = False,
    request_id: Annotated[str | None, typer.Option("--request-id", help="Unique Telegram update/request id required for confirmed execution.")] = None,
    requested_at: Annotated[str | None, typer.Option("--requested-at", help="UTC ISO-8601 request timestamp required for confirmed execution.")] = None,
    output_dir: Annotated[Path, typer.Option("--output-dir", help="Execution artifact directory.")] = Path("downloads/openclaw"),
    db_path: Annotated[Path, typer.Option("--db-path", help="Task SQLite path.")] = DEFAULT_DB,
    backend: Annotated[str | None, typer.Option("--backend", help="Optional backend override for every retained URL.")] = None,
    cookies_from_browser: Annotated[str | None, typer.Option("--cookies-from-browser", help="Pass yt-dlp cookies from a local browser. Requires sensitive confirmation.")] = None,
    cookies_path: Annotated[Path | None, typer.Option("--cookies", help="Pass a Netscape cookies.txt file to yt-dlp. Requires sensitive confirmation.")] = None,
    js_runtimes: Annotated[str | None, typer.Option("--js-runtimes", help="Pass yt-dlp JavaScript runtimes, for example node.")] = None,
    remote_components: Annotated[list[str] | None, typer.Option("--remote-component", help="Pass an explicitly authorized yt-dlp remote component. Can be repeated.")] = None,
    hard_timeout: Annotated[int | None, typer.Option("--hard-timeout", help="Maximum seconds to allow each backend command.")] = None,
    idle_timeout: Annotated[int | None, typer.Option("--idle-timeout", help="Maximum seconds without backend output.")] = None,
    output_format: Annotated[str, typer.Option("--format", help="Output format. Stable v1 supports json.")] = "json",
) -> None:
    raw = _raw_text_or_file(text_or_path)
    _emit(
        execute_openclaw_telegram_command(
            raw,
            actor_id=actor_id,
            allowed_actor_ids=allowed_actor_ids,
            output_dir=output_dir,
            db_path=db_path,
            backend=backend,
            approval_channel=approval_channel,
            approval_command=approval_command,
            confirm=confirm,
            confirm_sensitive=confirm_sensitive,
            request_id=request_id,
            requested_at=requested_at,
            hard_timeout=hard_timeout,
            idle_timeout=idle_timeout,
            ytdlp_options=_yt_dlp_options(cookies_from_browser, cookies_path, js_runtimes, remote_components),
            execute_manifest_func=_execute_manifest,
        ),
        output_format,
    )


@app.command(name="sniff-cleanup")
def sniff_cleanup(
    older_than_days: Annotated[int, typer.Option("--older-than-days", help="Remove redacted sniff diagnostics older than this many days.")] = 7,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Report records that would be removed without writing files.")] = False,
    base_dir: Annotated[Path, typer.Option("--base-dir", help="Sniff diagnostic base directory.")] = Path(".vdo"),
    output_format: Annotated[str, typer.Option("--format", help="Output format. Stable v1 supports json.")] = "json",
) -> None:
    payload = cleanup_sniff_records(older_than_days=older_than_days, dry_run=dry_run, base_dir=base_dir)
    payload.update({"service": "video-download-orchestrator", "will_download": False})
    _emit(payload, output_format)


@app.command()
def download(
    url: str,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Write a manifest without running a backend.")] = False,
    output_dir: Annotated[Path, typer.Option("--output-dir", help="Download output directory.")] = Path("downloads"),
    backend: Annotated[str | None, typer.Option("--backend", help="Override selected backend.")] = None,
    headers: Annotated[list[str] | None, typer.Option("--header", "-H", help="Extra request header as 'Name: value'. Can be repeated.")] = None,
    cookie: Annotated[str | None, typer.Option("--cookie", help="Cookie header value. Redacted in artifacts.")] = None,
    cookies_from_browser: Annotated[str | None, typer.Option("--cookies-from-browser", help="Pass yt-dlp cookies from a local browser, for example chrome or edge.")] = None,
    cookies_path: Annotated[Path | None, typer.Option("--cookies", help="Pass a Netscape cookies.txt file to yt-dlp backends.")] = None,
    js_runtimes: Annotated[str | None, typer.Option("--js-runtimes", help="Pass yt-dlp JavaScript runtimes, for example node.")] = None,
    remote_components: Annotated[list[str] | None, typer.Option("--remote-component", help="Pass an explicitly authorized yt-dlp remote component, for example ejs:github. Can be repeated.")] = None,
    expected_duration_seconds: Annotated[float | None, typer.Option("--expected-duration-seconds", help="Expected media duration in seconds, used only for post-download completeness validation.")] = None,
    estimated_output_bytes: Annotated[int | None, typer.Option("--estimated-output-bytes", help="Positive estimated output size used by the shared disk safety gate.")] = None,
    db_path: Annotated[Path, typer.Option("--db-path", help="Task SQLite path.")] = DEFAULT_DB,
    hard_timeout: Annotated[int | None, typer.Option("--hard-timeout", help="Maximum seconds to allow each backend command. Defaults to config execution.hard_timeout_seconds.")] = None,
    idle_timeout: Annotated[int | None, typer.Option("--idle-timeout", help="Maximum seconds without backend output. Defaults to config execution.idle_timeout_seconds.")] = None,
    output_format: Annotated[str, typer.Option("--format", help="Output format. Stable v1 supports json.")] = "json",
) -> None:
    try:
        request_headers = _parse_cli_headers(headers or [], cookie)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    try:
        manifest = plan_download(
            url,
            output_dir=output_dir,
            backend=backend,
            headers=request_headers,
            ytdlp_options=_yt_dlp_options(cookies_from_browser, cookies_path, js_runtimes, remote_components),
            expected_duration_seconds=expected_duration_seconds,
            estimated_output_bytes=estimated_output_bytes,
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc), param_hint="--expected-duration-seconds") from exc
    manifest_path = write_manifest(manifest, output_dir)

    if dry_run:
        _emit(
            {
                "service": "video-download-orchestrator",
                "dry_run": True,
                "will_download": False,
                "manifest": redact_manifest(manifest),
                "manifest_path": str(manifest_path),
            },
            output_format,
        )
        return

    store = TaskStore(db_path.resolve())
    payload = _execute_manifest(
        store=store,
        manifest=manifest,
        artifact_base_dir=output_dir,
        manifest_path=manifest_path,
        hard_timeout=hard_timeout,
        idle_timeout=idle_timeout,
    )
    _emit(payload, output_format)


@app.command(name="download-tencent-meeting")
def download_tencent_meeting(
    url: str,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Write a manifest without running the Tencent Meeting backend.")] = False,
    output_dir: Annotated[Path, typer.Option("--output-dir", help="Download output directory.")] = Path("downloads"),
    expected_duration_seconds: Annotated[float | None, typer.Option("--expected-duration-seconds", help="Expected media duration in seconds, used only for post-download completeness validation.")] = None,
    estimated_output_bytes: Annotated[int | None, typer.Option("--estimated-output-bytes", help="Positive estimated output size used by the shared disk safety gate.")] = None,
    db_path: Annotated[Path, typer.Option("--db-path", help="Task SQLite path.")] = DEFAULT_DB,
    hard_timeout: Annotated[int | None, typer.Option("--hard-timeout", help="Maximum seconds to allow each backend command. Defaults to config execution.hard_timeout_seconds.")] = None,
    idle_timeout: Annotated[int | None, typer.Option("--idle-timeout", help="Maximum seconds without backend output. Defaults to config execution.idle_timeout_seconds.")] = None,
    output_format: Annotated[str, typer.Option("--format", help="Output format. Stable v1 supports json.")] = "json",
) -> None:
    try:
        manifest = plan_download(
            url,
            output_dir=output_dir,
            backend="tencent-meeting",
            expected_duration_seconds=expected_duration_seconds,
            estimated_output_bytes=estimated_output_bytes,
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc), param_hint="--expected-duration-seconds") from exc
    manifest_path = write_manifest(manifest, output_dir)
    if dry_run:
        _emit(
            {
                "service": "video-download-orchestrator",
                "dry_run": True,
                "will_download": False,
                "manifest": redact_manifest(manifest),
                "manifest_path": str(manifest_path),
            },
            output_format,
        )
        return
    store = TaskStore(db_path.resolve())
    payload = _execute_manifest(
        store=store,
        manifest=manifest,
        artifact_base_dir=output_dir,
        manifest_path=manifest_path,
        hard_timeout=hard_timeout,
        idle_timeout=idle_timeout,
    )
    _emit(payload, output_format)


@app.command(name="download-page")
def download_page(
    page_url: str,
    api_base: Annotated[str | None, typer.Option("--api", help="Local orchestrator API base URL. Defaults to config/video_orchestrator.json.")] = None,
    capture_session: Annotated[str | None, typer.Option("--capture-session", help="Only use browser-sniffed captures from this session ID.")] = None,
    timeout_seconds: Annotated[int, typer.Option("--timeout", help="Seconds to wait for browser sniffer capture.")] = 120,
    poll_interval: Annotated[float, typer.Option("--poll-interval", help="Polling interval in seconds.")] = 1.0,
    open_browser: Annotated[bool, typer.Option("--open-browser", help="Open page URL in the default browser before waiting.")] = False,
    output_dir: Annotated[Path, typer.Option("--output-dir", help="Download output directory.")] = Path("downloads"),
    db_path: Annotated[Path, typer.Option("--db-path", help="Task SQLite path.")] = DEFAULT_DB,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Only plan the download after a capture is found.")] = False,
    hard_timeout: Annotated[int | None, typer.Option("--hard-timeout", help="Maximum seconds to allow each backend command. Defaults to config execution.hard_timeout_seconds.")] = None,
    idle_timeout: Annotated[int | None, typer.Option("--idle-timeout", help="Maximum seconds without backend output. Defaults to config execution.idle_timeout_seconds.")] = None,
    output_format: Annotated[str, typer.Option("--format", help="Output format. Stable v1 supports json.")] = "json",
) -> None:
    local_capture_session_id = capture_session or f"cli-{uuid4().hex[:8]}"
    if open_browser:
        webbrowser.open(page_url)

    try:
        resolved_api_base_url = api_base or api_base_url()
        capture = _wait_for_sniffed_capture(
            api_base_url=resolved_api_base_url,
            page_url=page_url,
            capture_session_id=capture_session,
            timeout_seconds=timeout_seconds,
            poll_interval=poll_interval,
        )
    except TimeoutError as exc:
        site_specific = _try_site_specific_page_download(
            page_url=page_url,
            output_dir=output_dir,
            db_path=db_path,
            dry_run=dry_run,
            hard_timeout=hard_timeout,
            idle_timeout=idle_timeout,
        )
        if site_specific is not None:
            site_specific.update(
                {
                    "page_url": page_url,
                    "capture_session_id": capture_session or local_capture_session_id,
                    "sniff_status": "timeout",
                    "sniff_error": str(exc),
                    "site_specific_fallback": True,
                }
            )
            _emit(site_specific, output_format)
            return
        _emit(
            {
                "service": "video-download-orchestrator",
                "status": "timeout",
                "will_download": False,
                "page_url": page_url,
                "capture_session_id": capture_session or local_capture_session_id,
                "error": str(exc),
                "site_adapter": page_link_site_adapter_diagnostic(page_url),
                "next_action": "Open the page in a browser with the browser-sniffer extension installed, play the video, then rerun download-page.",
            },
            output_format,
        )
        raise typer.Exit(1) from exc
    except OSError as exc:
        _emit(
            {
                "service": "video-download-orchestrator",
                "status": "api_unavailable",
                "will_download": False,
                "page_url": page_url,
                "capture_session_id": capture_session or local_capture_session_id,
                "api_base_url": resolved_api_base_url,
                "error": str(exc),
            },
            output_format,
        )
        raise typer.Exit(1) from exc

    payload = execute_page_capture(
        page_url=page_url,
        capture=capture,
        capture_payload={"capture_session_id": capture_session or local_capture_session_id},
        output_dir=output_dir,
        db_path=db_path,
        dry_run=dry_run,
        hard_timeout=hard_timeout,
        idle_timeout=idle_timeout,
        execute_manifest_func=_execute_manifest,
    )
    _emit(payload, output_format)


@app.command(name="batch-clean")
def batch_clean(
    spec_path: Annotated[Path, typer.Argument(help="JSON batch spec or raw text file with URLs.")],
    output_path: Annotated[Path | None, typer.Option("--output", help="Optional cleaned batch spec output path.")]=None,
    output_format: Annotated[str, typer.Option("--format", help="Output format. Stable v1 supports json.")] = "json",
) -> None:
    try:
        payload = load_and_clean_batch_spec(spec_path)
    except OSError as exc:
        _emit({"service": "video-download-orchestrator", "status": "failed", "error": str(exc), "spec_path": str(spec_path)}, output_format)
        raise typer.Exit(1) from exc
    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps({"items": _items_for_batch(payload["items"])}, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        payload["output_path"] = str(output_path)
    _emit(payload, output_format)


@app.command()
def batch(
    spec_path: Annotated[Path, typer.Argument(help="JSON batch spec path. Accepts an array or an object with an items array.")],
    execute: Annotated[bool, typer.Option("--execute", help="Run backend commands. Without this flag the batch only writes manifests.")] = False,
    page_link_mode: Annotated[str, typer.Option("--page-link-mode", help="Page URL handling for batch execution: off, sniff, or site-fallback.")] = "off",
    api_base: Annotated[str | None, typer.Option("--api", help="Local orchestrator API base URL for page-link batch sniffing.")] = None,
    sniff_timeout: Annotated[int, typer.Option("--sniff-timeout", help="Seconds to wait for each page-link capture in batch sniff mode.")] = 120,
    sniff_poll_interval: Annotated[float, typer.Option("--sniff-poll-interval", help="Polling interval for page-link batch sniffing.")] = 1.0,
    cookies_from_browser: Annotated[str | None, typer.Option("--cookies-from-browser", help="Pass yt-dlp cookies from a local browser for every yt-dlp batch item.")] = None,
    cookies_path: Annotated[Path | None, typer.Option("--cookies", help="Pass a Netscape cookies.txt file to yt-dlp batch items.")] = None,
    js_runtimes: Annotated[str | None, typer.Option("--js-runtimes", help="Pass yt-dlp JavaScript runtimes for every yt-dlp batch item, for example node.")] = None,
    remote_components: Annotated[list[str] | None, typer.Option("--remote-component", help="Pass an explicitly authorized yt-dlp remote component for every yt-dlp batch item. Can be repeated.")] = None,
    output_dir: Annotated[Path, typer.Option("--output-dir", help="Root output directory.")] = Path("downloads"),
    db_path: Annotated[Path, typer.Option("--db-path", help="Task SQLite path.")] = DEFAULT_DB,
    hard_timeout: Annotated[int | None, typer.Option("--hard-timeout", help="Maximum seconds to allow each backend command. Defaults to config execution.hard_timeout_seconds.")] = None,
    idle_timeout: Annotated[int | None, typer.Option("--idle-timeout", help="Maximum seconds without backend output. Defaults to config execution.idle_timeout_seconds.")] = None,
    output_format: Annotated[str, typer.Option("--format", help="Output format. Stable v1 supports json.")] = "json",
) -> None:
    if page_link_mode not in {"off", "sniff", "site-fallback"}:
        raise typer.BadParameter("page-link-mode must be one of: off, sniff, site-fallback")
    try:
        clean_payload = load_and_clean_batch_spec(spec_path)
        items = _items_for_batch(clean_payload["items"])
        if not items:
            raise ValueError("batch spec did not contain any downloadable URLs after cleaning")
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        _emit({"service": "video-download-orchestrator", "status": "failed", "error": str(exc), "spec_path": str(spec_path)}, output_format)
        raise typer.Exit(1) from exc

    store = TaskStore(db_path.resolve()) if execute else None
    batch_dir = _versioned_batch_dir(output_dir)
    ytdlp_options = _yt_dlp_options(cookies_from_browser, cookies_path, js_runtimes, remote_components)
    results = []
    for index, item in enumerate(items, start=1):
        item_dir = batch_dir / f"item-{index:03d}"
        if execute and page_link_mode != "off" and _should_use_page_link_mode(str(item["url"]), item.get("backend")):
            item_result = _execute_batch_page_link_item(
                item=item,
                item_dir=item_dir,
                db_path=db_path,
                page_link_mode=page_link_mode,
                api_base_url=api_base or api_base_url(),
                sniff_timeout=sniff_timeout,
                sniff_poll_interval=sniff_poll_interval,
                hard_timeout=hard_timeout,
                idle_timeout=idle_timeout,
            )
            item_result["index"] = index
            results.append(item_result)
            continue
        manifest = plan_download(str(item["url"]), output_dir=item_dir, backend=item.get("backend"), ytdlp_options=ytdlp_options)
        manifest["batch_id"] = batch_dir.name
        manifest["batch_item_index"] = index
        manifest_path = write_manifest(manifest, item_dir)
        item_result = {
            "index": index,
            "url": item["url"],
            "backend": manifest["selected_backend"],
            "will_download": execute,
            "manifest_path": str(manifest_path),
        }
        if execute and store is not None:
            item_result.update(
                _execute_manifest(
                    store=store,
                    manifest=manifest,
                    artifact_base_dir=item_dir,
                    manifest_path=manifest_path,
                    hard_timeout=hard_timeout,
                    idle_timeout=idle_timeout,
                )
            )
        results.append(item_result)

    _emit(
        {
            "service": "video-download-orchestrator",
            "contract": "agent_batch_v1",
            "status": _batch_status(results, execute),
            "execute": execute,
            "page_link_mode": page_link_mode,
            "yt_dlp_options": _redacted_yt_dlp_options(ytdlp_options),
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
        },
        output_format,
    )


def _should_use_page_link_mode(url: str, backend: str | None) -> bool:
    if backend:
        return False
    return classify_url(url).kind in {"general", "feishu-page", "tencent-meeting"}


def _execute_batch_page_link_item(
    *,
    item: dict,
    item_dir: Path,
    db_path: Path,
    page_link_mode: str,
    api_base_url: str,
    sniff_timeout: int,
    sniff_poll_interval: float,
    hard_timeout: int | None,
    idle_timeout: int | None,
) -> dict:
    page_url = str(item["url"])
    if page_link_mode == "site-fallback":
        site_specific = _try_site_specific_page_download(
            page_url=page_url,
            output_dir=item_dir,
            db_path=db_path,
            dry_run=False,
            hard_timeout=hard_timeout,
            idle_timeout=idle_timeout,
        )
        if site_specific is not None:
            site_specific.update({"url": page_url, "page_link_mode": page_link_mode, "site_specific_fallback": True})
            return site_specific

    try:
        capture = _wait_for_sniffed_capture(
            api_base_url=api_base_url,
            page_url=page_url,
            capture_session_id=None,
            timeout_seconds=sniff_timeout,
            poll_interval=sniff_poll_interval,
        )
    except TimeoutError as exc:
        store = TaskStore(db_path.resolve())
        task_id = store.create_task(page_url, "browser-sniffer")
        store.mark_retryable(task_id)
        return {
            "url": page_url,
            "backend": "browser-sniffer",
            "selected_backend": "browser-sniffer",
            "page_link_mode": page_link_mode,
            "status": "retryable",
            "will_download": False,
            "sniff_error": str(exc),
            "task": store.get_task(task_id),
        }

    media_url = str(capture["url"])
    backend = str(capture.get("backend") or "curl")
    request_headers = capture.get("request_headers") if isinstance(capture.get("request_headers"), dict) else {}
    manifest = plan_download(media_url, output_dir=item_dir, backend=backend, headers=request_headers)
    manifest["source_page_url"] = page_url
    manifest["source_capture_id"] = capture.get("capture_id")
    manifest["sniffed_media"] = _capture_context_for_manifest(capture)
    manifest_path = write_manifest(manifest, item_dir)
    store = TaskStore(db_path.resolve())
    result = _execute_manifest(
        store=store,
        manifest=manifest,
        artifact_base_dir=item_dir,
        manifest_path=manifest_path,
        hard_timeout=hard_timeout,
        idle_timeout=idle_timeout,
    )
    result.update(
        {
            "url": media_url,
            "source_page_url": page_url,
            "selected_backend": result.get("selected_backend", backend),
            "page_link_mode": page_link_mode,
            "capture": _redact_capture_for_cli(capture),
        }
    )
    return result


def _batch_status(results: list[dict], execute: bool) -> str:
    if not execute:
        return "planned"
    if any(item.get("status") == "failed" for item in results):
        return "finished_with_failures"
    if any(item.get("status") == "retryable" for item in results):
        return "finished_with_retryable"
    return "finished"


@app.command()
def rerun(
    task_id: int,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Plan a versioned rerun without running a backend.")] = False,
    output_dir: Annotated[Path, typer.Option("--output-dir", help="Root output directory.")] = Path("downloads"),
    backend: Annotated[str | None, typer.Option("--backend", help="Override backend for this rerun.")] = None,
    db_path: Annotated[Path, typer.Option("--db-path", help="Task SQLite path.")] = DEFAULT_DB,
    hard_timeout: Annotated[int | None, typer.Option("--hard-timeout", help="Maximum seconds to allow each backend command. Defaults to config execution.hard_timeout_seconds.")] = None,
    idle_timeout: Annotated[int | None, typer.Option("--idle-timeout", help="Maximum seconds without backend output. Defaults to config execution.idle_timeout_seconds.")] = None,
    output_format: Annotated[str, typer.Option("--format", help="Output format. Stable v1 supports json.")] = "json",
) -> None:
    try:
        store = TaskStore(db_path.resolve())
        original_task = store.get_task(task_id)
    except sqlite3.Error as exc:
        _emit({"service": "video-download-orchestrator", "status": "failed", "error": str(exc), "db_path": str(db_path)}, output_format)
        raise typer.Exit(1) from exc
    except KeyError as exc:
        _emit({"service": "video-download-orchestrator", "status": "not_found", "error": str(exc), "task_id": task_id}, output_format)
        raise typer.Exit(1) from exc

    selected_backend = backend or str(original_task["backend"])
    rerun_dir = _versioned_rerun_dir(output_dir, task_id, selected_backend)
    manifest = plan_download(str(original_task["url"]), output_dir=rerun_dir, backend=selected_backend)
    manifest["rerun_of_task_id"] = task_id
    manifest["artifact_scope"] = "versioned_rerun"
    manifest_path = write_manifest(manifest, rerun_dir)

    if dry_run:
        _emit(
            {
                "service": "video-download-orchestrator",
                "dry_run": True,
                "will_download": False,
                "original_task": original_task,
                "rerun_dir": str(rerun_dir),
                "manifest": manifest,
                "manifest_path": str(manifest_path),
            },
            output_format,
        )
        return

    payload = _execute_manifest(
        store=store,
        manifest=manifest,
        artifact_base_dir=rerun_dir,
        manifest_path=manifest_path,
        extra_payload={"original_task": original_task, "rerun_dir": str(rerun_dir)},
        hard_timeout=hard_timeout,
        idle_timeout=idle_timeout,
    )
    _emit(payload, output_format)


@task_app.command("create")
def task_create(
    url: str,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Preview task creation without writing SQLite.")] = False,
    db_path: Annotated[Path, typer.Option("--db-path", help="Task SQLite path.")] = DEFAULT_DB,
    output_format: Annotated[str, typer.Option("--format", help="Output format. Stable v1 supports json.")] = "json",
) -> None:
    classification = classify_url(url)
    payload = {
        "url": url,
        "backend": classification.preferred_backend,
        "status": "pending",
        "route": classification.model_dump(),
        "will_download": False,
    }
    if dry_run:
        _emit({"service": "video-download-orchestrator", "dry_run": True, "task": payload}, output_format)
        return
    try:
        store = TaskStore(db_path.resolve())
        task_id = store.create_task(url, classification.preferred_backend)
        _emit({"service": "video-download-orchestrator", "dry_run": False, "task": store.get_task(task_id)}, output_format)
    except sqlite3.Error as exc:
        _emit({"service": "video-download-orchestrator", "status": "failed", "error": str(exc), "db_path": str(db_path)}, output_format)
        raise typer.Exit(1) from exc


@task_app.command("status")
def task_status(
    task_id: int,
    db_path: Annotated[Path, typer.Option("--db-path", help="Task SQLite path.")] = DEFAULT_DB,
    output_format: Annotated[str, typer.Option("--format", help="Output format. Stable v1 supports json.")] = "json",
) -> None:
    try:
        store = TaskStore(db_path.resolve())
        task = store.get_task(task_id)
    except sqlite3.Error as exc:
        _emit({"service": "video-download-orchestrator", "status": "failed", "error": str(exc), "db_path": str(db_path)}, output_format)
        raise typer.Exit(1) from exc
    except KeyError as exc:
        _emit({"service": "video-download-orchestrator", "status": "not_found", "error": str(exc), "task_id": task_id}, output_format)
        raise typer.Exit(1) from exc
    _emit({"service": "video-download-orchestrator", "status": "ok", "task": task}, output_format)


@task_app.command("retryable")
def task_retryable(
    db_path: Annotated[Path, typer.Option("--db-path", help="Task SQLite path.")] = DEFAULT_DB,
    output_format: Annotated[str, typer.Option("--format", help="Output format. Stable v1 supports json.")] = "json",
) -> None:
    store = TaskStore(db_path.resolve())
    _emit({"service": "video-download-orchestrator", "status": "ok", "tasks": store.list_retryable_tasks()}, output_format)


@task_app.command("resume")
def task_resume(
    task_id: int,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Plan resume without running a backend.")] = False,
    api_base: Annotated[str | None, typer.Option("--api", help="Local orchestrator API base URL.")] = None,
    capture_session: Annotated[str | None, typer.Option("--capture-session", help="Browser sniffer capture session filter.")] = None,
    timeout_seconds: Annotated[int, typer.Option("--timeout", "--sniff-timeout", help="Seconds to wait for a browser sniffer capture.")] = 1,
    poll_interval: Annotated[float, typer.Option("--poll-interval", "--sniff-poll-interval", help="Polling interval in seconds.")] = 0.1,
    output_dir: Annotated[Path, typer.Option("--output-dir", help="Root output directory.")] = Path("downloads"),
    db_path: Annotated[Path, typer.Option("--db-path", help="Task SQLite path.")] = DEFAULT_DB,
    hard_timeout: Annotated[int | None, typer.Option("--hard-timeout", help="Maximum seconds to allow each backend command.")] = None,
    idle_timeout: Annotated[int | None, typer.Option("--idle-timeout", help="Maximum seconds without backend output.")] = None,
    output_format: Annotated[str, typer.Option("--format", help="Output format. Stable v1 supports json.")] = "json",
) -> None:
    store = TaskStore(db_path.resolve())
    try:
        task = store.get_task(task_id)
    except KeyError as exc:
        _emit({"service": "video-download-orchestrator", "status": "not_found", "error": str(exc), "task_id": task_id}, output_format)
        raise typer.Exit(1) from exc
    if task["status"] != "retryable":
        _emit({"service": "video-download-orchestrator", "status": "not_retryable", "task": task}, output_format)
        raise typer.Exit(1)

    resume_dir = output_dir / ".vdo" / "resumes" / f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-task-{task_id}"
    try:
        capture = _wait_for_sniffed_capture(
            api_base_url=api_base or api_base_url(),
            page_url=str(task["url"]),
            capture_session_id=capture_session,
            timeout_seconds=timeout_seconds,
            poll_interval=poll_interval,
        )
    except (TimeoutError, OSError) as exc:
        site_specific = _try_site_specific_page_download(
            page_url=str(task["url"]),
            output_dir=resume_dir,
            db_path=db_path,
            dry_run=dry_run,
            hard_timeout=hard_timeout,
            idle_timeout=idle_timeout,
        )
        if site_specific is None:
            _emit({"service": "video-download-orchestrator", "status": "retryable", "task": task, "error": str(exc)}, output_format)
            return
        site_specific.update({"resumed_task": task, "site_specific_fallback": True})
        _emit(site_specific, output_format)
        return

    media_url = str(capture["url"])
    backend = str(capture.get("backend") or "curl")
    request_headers = capture.get("request_headers") if isinstance(capture.get("request_headers"), dict) else {}
    manifest = plan_download(media_url, output_dir=resume_dir, backend=backend, headers=request_headers)
    manifest["resumed_task_id"] = task_id
    manifest["source_page_url"] = task["url"]
    manifest["source_capture_id"] = capture.get("capture_id")
    manifest["sniffed_media"] = _capture_context_for_manifest(capture)
    manifest_path = write_manifest(manifest, resume_dir)
    if dry_run:
        _emit(
            {
                "service": "video-download-orchestrator",
                "status": "planned",
                "dry_run": True,
                "resumed_task": task,
                "capture": _redact_capture_for_cli(capture),
                "manifest": redact_manifest(manifest),
                "manifest_path": str(manifest_path),
            },
            output_format,
        )
        return
    payload = _execute_manifest(
        store=store,
        manifest=manifest,
        artifact_base_dir=resume_dir,
        manifest_path=manifest_path,
        extra_payload={"resumed_task": task, "capture": _redact_capture_for_cli(capture)},
        hard_timeout=hard_timeout,
        idle_timeout=idle_timeout,
    )
    _emit(payload, output_format)


@task_app.command("review")
def task_review(
    task_id: int,
    db_path: Annotated[Path, typer.Option("--db-path", help="Task SQLite path.")] = DEFAULT_DB,
    output_format: Annotated[str, typer.Option("--format", help="Output format. Stable v1 supports json.")] = "json",
) -> None:
    store = TaskStore(db_path.resolve())
    try:
        task = store.get_task(task_id)
    except KeyError as exc:
        _emit({"service": "video-download-orchestrator", "status": "not_found", "error": str(exc), "task_id": task_id}, output_format)
        raise typer.Exit(1) from exc
    artifacts = artifact_map(store.list_artifacts(task_id))
    review_summary = operator_review_summary(task, artifacts)
    _emit(
        {
            "service": "video-download-orchestrator",
            "status": "ok",
            "verdict": review_summary["verdict"],
            "task": task,
            "artifacts": artifacts,
            "review": review_summary["review"],
            "summary": review_summary["summary"],
            "next_action": review_summary["next_action"],
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
        },
        output_format,
    )


@task_app.command("handoff")
def task_handoff(
    task_id: int,
    db_path: Annotated[Path, typer.Option("--db-path", help="Task SQLite path.")] = DEFAULT_DB,
    output_dir: Annotated[Path, typer.Option("--output-dir", help="Planned output directory.")] = Path("downloads"),
    output_format: Annotated[str, typer.Option("--format", help="Output format. Stable v1 supports json.")] = "json",
) -> None:
    _emit(build_task_handoff(task_id, db_path=db_path, output_dir=str(output_dir)), output_format)


@task_app.command("vkp-handoff")
def task_vkp_handoff(
    task_id: int,
    db_path: Annotated[Path, typer.Option("--db-path", help="Task SQLite path.")] = DEFAULT_DB,
    output_dir: Annotated[Path, typer.Option("--output-dir", help="Planning handoff manifest directory.")] = Path("downloads/vkp-handoffs"),
    require_accepted: Annotated[bool, typer.Option("--require-accepted", help="Return review_required unless the task is marked accepted.")] = False,
    write_manifest: Annotated[bool, typer.Option("--write-manifest/--no-write-manifest", help="Write a local VKP handoff JSON manifest.")] = True,
    output_format: Annotated[str, typer.Option("--format", help="Output format. Stable v1 supports json.")] = "json",
) -> None:
    _emit(
        build_vkp_handoff(
            task_id,
            db_path=db_path,
            output_dir=output_dir,
            require_accepted=require_accepted,
            write_manifest=write_manifest,
        ),
        output_format,
    )


@task_app.command("export-review")
def task_export_review(
    task_id: int,
    output_dir: Annotated[Path, typer.Option("--output-dir", help="Review package output directory.")] = Path("downloads/review-packages"),
    db_path: Annotated[Path, typer.Option("--db-path", help="Task SQLite path.")] = DEFAULT_DB,
    output_format: Annotated[str, typer.Option("--format", help="Output format. Stable v1 supports json.")] = "json",
) -> None:
    store = TaskStore(db_path.resolve())
    try:
        task = store.get_task(task_id)
    except KeyError as exc:
        _emit({"service": "video-download-orchestrator", "status": "not_found", "error": str(exc), "task_id": task_id}, output_format)
        raise typer.Exit(1) from exc
    artifacts = artifact_map(store.list_artifacts(task_id))
    review_summary = operator_review_summary(task, artifacts)
    handoff = build_task_handoff(task_id, db_path=db_path, output_dir=str(output_dir))
    package_dir = output_dir / f"task-{task_id}"
    package_dir.mkdir(parents=True, exist_ok=True)

    copied_artifacts = {}
    for kind, artifact in artifacts.items():
        source = Path(artifact)
        if not source.exists() or not source.is_file():
            continue
        target = package_dir / source.name
        shutil.copy2(source, target)
        copied_artifacts[kind] = str(target)

    task_payload = {
        "task": task,
        "artifacts": artifacts,
        "copied_artifacts": copied_artifacts,
        "verdict": review_summary["verdict"],
        "review": review_summary["review"],
        "summary": review_summary["summary"],
        "next_action": review_summary["next_action"],
        "handoff": handoff,
    }
    task_json_path = package_dir / "task.json"
    task_json_path.write_text(json.dumps(redact_manifest(task_payload), ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    review_summary_path = package_dir / "review-summary.md"
    review_summary_path.write_text(
        render_review_package_summary(
            task=task,
            verdict=review_summary["verdict"],
            artifacts=copied_artifacts or artifacts,
            next_action=review_summary["next_action"],
            handoff=handoff,
        ),
        encoding="utf-8",
    )
    _emit(
        {
            "service": "video-download-orchestrator",
            "status": "ok",
            "will_download": False,
            "task_id": task_id,
            "verdict": review_summary["verdict"],
            "package_dir": str(package_dir),
            "review_summary_path": str(review_summary_path),
            "task_json_path": str(task_json_path),
            "copied_artifacts": copied_artifacts,
            "handoff": handoff,
        },
        output_format,
    )


def _backend_status(backend: BackendSpec) -> dict:
    available = command_exists(backend.command)
    status = "ok" if available else ("missing_required" if backend.required else "missing_optional")
    version_command = [backend.command, *backend.version_args]
    version = None
    version_error = None
    if available:
        version_result = run_command(version_command, hard_timeout_seconds=5, idle_timeout_seconds=3)
        if version_result.returncode == 0:
            version = version_result.stdout.strip().splitlines()[0] if version_result.stdout.strip() else ""
        else:
            version_error = version_result.stderr.strip()
            status = "probe_timeout" if version_result.timed_out else "probe_failed"
    return {
        "name": backend.name,
        "command": backend.command,
        "required": backend.required,
        "purpose": backend.purpose,
        "status": status,
        "available": available,
        "discovered": available,
        "executable_verified": available and status == "ok",
        "download_verified": False,
        "version_command": version_command,
        "version": version,
        "version_error": version_error,
        "supported_kinds": backend.supported_kinds,
        "fallbacks": backend.fallbacks,
        "install_hint": backend.install_hint,
        "reference_url": backend.reference_url,
        "recommended_for": backend.recommended_for,
    }


def _backend_guidance(selected_backend: str) -> dict:
    adapter = get_backend_adapter(selected_backend)
    selected_available = command_exists(adapter.spec.command)
    fallback_statuses = []
    next_available_backend = None
    for fallback in adapter.spec.fallbacks:
        fallback_adapter = get_backend_adapter(fallback)
        available = command_exists(fallback_adapter.spec.command)
        if available and next_available_backend is None:
            next_available_backend = fallback
        fallback_statuses.append(
            {
                "backend": fallback,
                "command": fallback_adapter.spec.command,
                "available": available,
                "install_hint": fallback_adapter.spec.install_hint,
            }
        )
    return {
        "selected_backend": selected_backend,
        "selected_command": adapter.spec.command,
        "selected_available": selected_available,
        "fallback_backends": list(adapter.spec.fallbacks),
        "fallback_statuses": fallback_statuses,
        "next_available_backend": selected_backend if selected_available else next_available_backend,
        "install_hint": adapter.spec.install_hint,
        "reference_url": adapter.spec.reference_url,
        "recommended_for": adapter.spec.recommended_for,
    }


def _page_link_readiness_checks() -> list[dict]:
    checks: list[dict] = []
    config_check = check_config()
    checks.append(
        {
            "name": "runtime_config",
            "status": config_check["status"],
            "config_path": config_check.get("config_path"),
            "api_base_url": config_check.get("api_base_url"),
        }
    )
    browser_check = next((check for check in config_check.get("checks", []) if check.get("name") == "browser_sniffer_generated_config"), None)
    manifest_check = next((check for check in config_check.get("checks", []) if check.get("name") == "browser_sniffer_manifest"), None)
    browser_status = "ok" if browser_check and manifest_check and browser_check["status"] == "ok" and manifest_check["status"] == "ok" else "failed"
    checks.append(
        {
            "name": "browser_sniffer_config",
            "status": browser_status,
            "generated_config": browser_check,
            "manifest": manifest_check,
        }
    )

    resolved_api_base_url = api_base_url()
    try:
        health = _api_get_json(resolved_api_base_url, "/health", {})
        checks.append({"name": "api", "status": "ok" if health.get("status") == "ok" else "degraded", "api_base_url": resolved_api_base_url})
    except OSError as exc:
        checks.append({"name": "api", "status": "degraded", "api_base_url": resolved_api_base_url, "message": str(exc)})

    try:
        latest = _api_get_json(resolved_api_base_url, "/sniffed-media/latest", {})
        extension_status = "ok" if latest.get("capture") else "unknown"
        checks.append(
            {
                "name": "extension_recent_submit",
                "status": extension_status,
                "capture_count": latest.get("capture_count", 0),
                "rejection_count": latest.get("rejection_count", 0),
                "diagnostic": latest.get("diagnostic"),
            }
        )
    except OSError as exc:
        checks.append({"name": "extension_recent_submit", "status": "unknown", "message": str(exc)})

    required_backend_available = command_exists("yt-dlp")
    checks.append(
        {
            "name": "required_backend",
            "status": "ok" if required_backend_available else "failed",
            "backend": "yt-dlp",
            "available": required_backend_available,
        }
    )

    fallback_commands = {"curl": "curl.exe", "python-http": "python"}
    fallback_available = {name: command_exists(command) for name, command in fallback_commands.items()}
    checks.append(
        {
            "name": "direct_transfer_fallback",
            "status": "ok" if any(fallback_available.values()) else "degraded",
            "available": fallback_available,
        }
    )
    return checks


def _execute_manifest(
    *,
    store: TaskStore,
    manifest: dict,
    artifact_base_dir: Path,
    manifest_path: Path,
    extra_payload: dict | None = None,
    hard_timeout: int | None = None,
    idle_timeout: int | None = None,
) -> dict:
    task_id = store.create_task(str(manifest["url"]), str(manifest["selected_backend"]))
    attempts = []
    attempt_logs = []
    result = None
    selected_backend = str(manifest["selected_backend"])
    fallback_used = False
    route_backends = [selected_backend, *manifest.get("fallback_backends", [])]
    resolved_hard_timeout = hard_timeout if hard_timeout is not None else configured_hard_timeout_seconds()
    resolved_idle_timeout = idle_timeout if idle_timeout is not None else configured_idle_timeout_seconds()

    for index, backend in enumerate(route_backends):
        headers = manifest.get("request_headers") if isinstance(manifest.get("request_headers"), dict) else {}
        ytdlp_options = manifest.get("yt_dlp_options") if isinstance(manifest.get("yt_dlp_options"), dict) else {}
        command = (
            list(manifest["command"])
            if index == 0
            else list(
                plan_download(
                    str(manifest["url"]),
                    output_dir=artifact_base_dir,
                    backend=backend,
                    headers=headers,
                    ytdlp_options=ytdlp_options,
                )["command"]
            )
        )
        fallback_reason = None if index == 0 else f"previous backend failed: {attempts[-1]['backend']}"
        result = run_command(
            command,
            hard_timeout_seconds=resolved_hard_timeout,
            idle_timeout_seconds=resolved_idle_timeout,
        )
        error_info = classify_backend_error(result.stderr) or {}
        fallback_decision = backend_fallback_decision(
            url=str(manifest["url"]),
            error_info=error_info,
            remaining_backends=route_backends[index + 1 :],
        )
        attempt_logs.append(
            {
                "backend": backend,
                "command": command,
                "fallback_reason": fallback_reason,
                "result": result,
            }
        )
        attempts.append(
            {
                "backend": backend,
                "command": redact_command(command),
                "returncode": result.returncode,
                **error_info,
                "fallback_reason": fallback_reason,
                "timeout": result.timeout_kind,
                "timed_out": result.timed_out,
                "timeout_kind": result.timeout_kind,
                "duration_seconds": result.duration_seconds,
                "hard_timeout_seconds": resolved_hard_timeout,
                "idle_timeout_seconds": resolved_idle_timeout,
                "fallback_decision": fallback_decision,
            }
        )
        if result.returncode == 0:
            selected_backend = backend
            fallback_used = index > 0
            break
        if not fallback_decision["continue"]:
            break

    assert result is not None
    if result.returncode == 0:
        store.mark_finished(task_id, result.returncode)
        status = "finished"
    else:
        store.mark_failed(task_id, result.returncode)
        status = "failed"

    task_log_path = write_task_attempts_log(
        artifact_base_dir,
        task_id=task_id,
        attempts=attempt_logs,
        idle_timeout=resolved_idle_timeout,
        hard_timeout=resolved_hard_timeout,
    )

    backend_result = parse_backend_stdout_json(result.stdout)
    output_files = discover_output_files(artifact_base_dir, backend_result)
    archive_files = discover_archive_files(artifact_base_dir)
    ffprobe_results = probe_media_files(output_files)
    task_report_dir = report_dir(artifact_base_dir, task_id)
    review = build_quality_review(
        status="ok" if result.returncode == 0 else "failed",
        output_files=output_files,
        stderr=result.stderr,
        fallback_used=fallback_used,
        backend_result=backend_result,
        ffprobe_results=ffprobe_results,
        attempts=redact_attempts(attempts),
        sniffed_media=manifest.get("sniffed_media") if isinstance(manifest.get("sniffed_media"), dict) else None,
        archive_files=archive_files,
        archive_expected=selected_backend in {"yt-dlp", "yt-dlp-api"},
    )
    review_path = write_quality_review(task_report_dir, review)
    report_path = write_task_report(
        artifact_base_dir,
        {
            "task_id": task_id,
            "url": manifest["url"],
            "selected_backend": selected_backend,
            "status": status,
            "command": redact_command(list(result.args)),
            "attempts": redact_attempts(attempts),
            "output_dir": manifest["output_dir"],
            "log_path": str(task_log_path),
            "review_checklist_path": str(review_path),
            "backend_result": backend_result,
            "ffprobe": ffprobe_results,
            "archive_files": [path.as_posix() for path in archive_files],
            "sniffed_media": manifest.get("sniffed_media"),
            "quality_status": review["quality_status"],
            "quality_label": review["quality_label"],
            "transport_status": review["transport_status"],
            "transport_label": review["transport_label"],
            "transport_next_action": review["transport_next_action"],
            "media_integrity_status": review["media_integrity_status"],
            "media_integrity_label": review["media_integrity_label"],
            "media_integrity_next_action": review["media_integrity_next_action"],
            "timeout": result.timeout_kind,
            "next_action": review["quality_next_action"],
        },
    )
    store.add_artifact(task_id, "manifest", manifest_path)
    store.add_artifact(task_id, "log", task_log_path)
    store.add_artifact(task_id, "report", report_path)
    store.add_artifact(task_id, "review_checklist", review_path)
    payload = {
        "service": "video-download-orchestrator",
        "status": status,
        "selected_backend": selected_backend,
        "quality_status": review["quality_status"],
        "quality_label": review["quality_label"],
        "transport_status": review["transport_status"],
        "transport_label": review["transport_label"],
        "transport_next_action": review["transport_next_action"],
        "media_integrity_status": review["media_integrity_status"],
        "media_integrity_label": review["media_integrity_label"],
        "media_integrity_next_action": review["media_integrity_next_action"],
        "next_action": review["quality_next_action"],
        "attempts": redact_attempts(attempts),
        "task": store.get_task(task_id),
        "artifacts": {
            "manifest_path": str(manifest_path),
            "log_path": str(task_log_path),
            "report_path": str(report_path),
            "review_checklist_path": str(review_path),
        },
    }
    adapter = match_site_adapter(str(manifest["url"]))
    if status == "failed" and adapter is not None and adapter.fallback_mode == "browser_sniffing":
        stopped_attempt = next(
            (
                attempt
                for attempt in reversed(attempts)
                if isinstance(attempt.get("fallback_decision"), dict)
                and attempt["fallback_decision"].get("status") == "needs_browser_sniffing"
            ),
            None,
        )
        payload["recovery"] = {
            "status": "needs_browser_sniffing",
            "mode": "browser_sniffing",
            "site_adapter": adapter.name,
            "requires_cookie_export": False,
            "fallback_stopped": stopped_attempt is not None,
            "skipped_backends": (stopped_attempt or {}).get("fallback_decision", {}).get("skipped_backends", []),
            "next_action": (stopped_attempt or {}).get("fallback_decision", {}).get("next_action")
            or "请在当前登录浏览器打开原页面并播放几秒，系统将捕获新的主视频请求后继续。",
        }
    if extra_payload:
        payload.update(extra_payload)
    return payload


def _try_site_specific_page_download(
    *,
    page_url: str,
    output_dir: Path,
    db_path: Path,
    dry_run: bool,
    hard_timeout: int | None,
    idle_timeout: int | None,
) -> dict | None:
    return try_site_specific_page_download(
        page_url=page_url,
        output_dir=output_dir,
        db_path=db_path,
        dry_run=dry_run,
        hard_timeout=hard_timeout,
        idle_timeout=idle_timeout,
        execute_manifest_func=_execute_manifest,
    )


def _parse_cli_headers(headers: list[str], cookie: str | None) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for header in headers:
        name, sep, value = header.partition(":")
        if not sep or not name.strip():
            raise ValueError(f"header must use 'Name: value' format: {header}")
        parsed[name.strip()] = value.strip()
    if cookie:
        parsed["Cookie"] = cookie
    return parsed


def _yt_dlp_options(
    cookies_from_browser: str | None,
    cookies_path: Path | None,
    js_runtimes: str | None = None,
    remote_components: list[str] | None = None,
) -> dict[str, object]:
    options: dict[str, object] = {}
    if cookies_from_browser:
        options["cookies_from_browser"] = cookies_from_browser
    if cookies_path:
        options["cookies"] = str(cookies_path)
    if js_runtimes:
        options["js_runtimes"] = js_runtimes
    if remote_components:
        options["remote_components"] = [component for component in remote_components if component]
    return options


def _redacted_yt_dlp_options(options: dict[str, object]) -> dict[str, str | list[str] | bool]:
    redacted: dict[str, str | list[str] | bool] = {}
    if options.get("cookies_from_browser"):
        redacted["cookies_from_browser"] = str(options["cookies_from_browser"])
    if options.get("cookies"):
        redacted["cookies"] = "<cookies-file>"
    if options.get("js_runtimes"):
        redacted["js_runtimes"] = str(options["js_runtimes"])
    if options.get("remote_components"):
        redacted["remote_components"] = [str(component) for component in options["remote_components"]] if isinstance(options["remote_components"], list) else str(options["remote_components"])
    return redacted


def _wait_for_sniffed_capture(
    *,
    api_base_url: str,
    page_url: str,
    capture_session_id: str | None = None,
    timeout_seconds: int,
    poll_interval: float,
) -> dict:
    capture, _payload = wait_for_sniffed_capture(
        fetch_latest=lambda latest_page_url, latest_capture_session_id: _api_get_json(
            api_base_url,
            "/sniffed-media/latest",
            _latest_sniffed_media_params(latest_page_url, latest_capture_session_id),
        ),
        page_url=page_url,
        capture_session_id=capture_session_id,
        timeout_seconds=timeout_seconds,
        poll_interval=poll_interval,
    )
    return capture


def _latest_sniffed_media_params(page_url: str, capture_session_id: str | None) -> dict[str, str]:
    params = {"page_url": page_url, "include_sensitive": "true"}
    if capture_session_id:
        params["capture_session_id"] = capture_session_id
    return params


def _sniff_timeout_message(timeout_seconds: int, last_payload: dict | None) -> str:
    return sniff_timeout_message(timeout_seconds, last_payload)


def _api_get_json(api_base_url: str, path: str, params: dict[str, str]) -> dict:
    query = urllib.parse.urlencode(params)
    url = api_base_url.rstrip("/") + path + ("?" + query if query else "")
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        raise OSError(str(exc)) from exc


def _redact_capture_for_cli(capture: dict) -> dict:
    return redact_capture_for_output(capture)


def _capture_context_for_manifest(capture: dict) -> dict:
    return page_link_capture_context_for_manifest(capture)


def _versioned_rerun_dir(output_dir: Path, task_id: int, backend: str) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    safe_backend = backend.replace("/", "_").replace("\\", "_")
    return output_dir / ".vdo" / "reruns" / f"{stamp}-task-{task_id}-{safe_backend}"


def _versioned_batch_dir(output_dir: Path) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return output_dir / ".vdo" / "batches" / f"{stamp}-agent-batch"


def _items_for_batch(items: list[dict]) -> list[dict]:
    normalized = []
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"batch item {index} must be a URL string or object")
        url = item.get("url")
        if not isinstance(url, str) or not url.strip():
            raise ValueError(f"batch item {index} must include a non-empty url")
        normalized.append({"url": url, "backend": item.get("backend")})
    return normalized


if __name__ == "__main__":
    app()
