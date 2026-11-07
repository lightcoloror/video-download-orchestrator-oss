from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from video_orchestrator.runtime.execution import execute_manifest
from video_orchestrator.runtime.manifests import write_manifest
from video_orchestrator.runtime.redaction import redact_manifest
from video_orchestrator.runtime.redaction import redact_url
from video_orchestrator.runtime.site_adapters import match_site_adapter
from video_orchestrator.runtime.site_adapters import site_adapter_diagnostic
from video_orchestrator.runtime.task_store import TaskStore
from video_orchestrator.services import plan_download


class PageLinkTimeoutError(TimeoutError):
    def __init__(self, message: str, last_payload: dict[str, Any] | None = None):
        super().__init__(message)
        self.last_payload = last_payload


LatestCaptureFetcher = Callable[[str, str | None], dict[str, Any]]
ManifestExecutor = Callable[..., dict[str, Any]]


def wait_for_sniffed_capture(
    *,
    fetch_latest: LatestCaptureFetcher,
    page_url: str,
    capture_session_id: str | None = None,
    timeout_seconds: int,
    poll_interval: float,
) -> tuple[dict[str, Any], dict[str, Any]]:
    deadline = time.monotonic() + timeout_seconds
    last_error: OSError | None = None
    last_payload: dict[str, Any] | None = None
    while time.monotonic() <= deadline:
        try:
            payload = fetch_latest(page_url, capture_session_id)
        except OSError as exc:
            last_error = exc
            time.sleep(poll_interval)
            continue
        last_payload = payload
        if payload.get("status") == "ok" and payload.get("capture"):
            return payload["capture"], payload
        time.sleep(poll_interval)
    if last_error is not None:
        raise last_error
    raise PageLinkTimeoutError(sniff_timeout_message(timeout_seconds, last_payload), last_payload)


def execute_page_capture(
    *,
    page_url: str,
    capture: dict[str, Any],
    capture_payload: dict[str, Any] | None,
    output_dir: Path,
    db_path: Path,
    dry_run: bool,
    hard_timeout: int | None,
    idle_timeout: int | None,
    execute_manifest_func: ManifestExecutor = execute_manifest,
) -> dict[str, Any]:
    media_url = str(capture["url"])
    backend = str(capture.get("backend") or "curl")
    request_headers = capture.get("request_headers") if isinstance(capture.get("request_headers"), dict) else {}
    manifest = plan_download(media_url, output_dir=output_dir, backend=backend, headers=request_headers)
    manifest["source_page_url"] = page_url
    manifest["source_capture_id"] = capture.get("capture_id")
    manifest["sniffed_media"] = capture_context_for_manifest(capture)
    manifest_path = write_manifest(manifest, output_dir)
    extra_payload = _page_capture_extra_payload(page_url, capture, capture_payload)

    if dry_run:
        return {
            "service": "video-download-orchestrator",
            "status": "planned",
            "dry_run": True,
            "will_download": False,
            "manifest": redact_manifest(manifest),
            "manifest_path": str(manifest_path),
            **extra_payload,
        }

    store = TaskStore(db_path.resolve())
    payload = execute_manifest_func(
        store=store,
        manifest=manifest,
        artifact_base_dir=output_dir,
        manifest_path=manifest_path,
        extra_payload=extra_payload,
        hard_timeout=hard_timeout,
        idle_timeout=idle_timeout,
    )
    payload["will_download"] = True
    return payload


def try_site_specific_page_download(
    *,
    page_url: str,
    output_dir: Path,
    db_path: Path,
    dry_run: bool,
    hard_timeout: int | None,
    idle_timeout: int | None,
    execute_manifest_func: ManifestExecutor = execute_manifest,
) -> dict[str, Any] | None:
    site_adapter = match_site_adapter(page_url)
    if site_adapter is None or "site_fallback" not in site_adapter.capabilities:
        return None
    manifest = plan_download(page_url, output_dir=output_dir, backend=site_adapter.backend)
    manifest["source_page_url"] = page_url
    manifest["sniff_fallback_reason"] = "browser sniffing timed out"
    manifest["site_adapter"] = site_adapter.diagnostic(matched=True)
    manifest_path = write_manifest(manifest, output_dir)
    extra_payload = {"site_adapter": site_adapter.diagnostic(matched=True)}
    if dry_run:
        return {
            "service": "video-download-orchestrator",
            "status": "planned",
            "dry_run": True,
            "will_download": False,
            "manifest": redact_manifest(manifest),
            "manifest_path": str(manifest_path),
            **extra_payload,
        }
    store = TaskStore(db_path.resolve())
    payload = execute_manifest_func(
        store=store,
        manifest=manifest,
        artifact_base_dir=output_dir,
        manifest_path=manifest_path,
        extra_payload=extra_payload,
        hard_timeout=hard_timeout,
        idle_timeout=idle_timeout,
    )
    payload.setdefault("site_adapter", site_adapter.diagnostic(matched=True))
    return payload


def page_link_site_adapter_diagnostic(page_url: str) -> dict[str, Any]:
    return site_adapter_diagnostic(page_url)


def sniff_timeout_message(timeout_seconds: int, last_payload: dict[str, Any] | None) -> str:
    base = f"no accepted sniffed media capture for page within {timeout_seconds}s"
    if not last_payload:
        return base
    rejection = last_payload.get("latest_rejection")
    if not isinstance(rejection, dict):
        diagnostic = last_payload.get("diagnostic")
        return f"{base}; {diagnostic}" if diagnostic else base

    details = []
    reason = rejection.get("reason")
    if reason:
        details.append(str(reason))
    url = rejection.get("url")
    if isinstance(url, str) and url:
        details.append(f"url={redact_url(url)}")
    content_type = rejection.get("content_type")
    if content_type:
        details.append(f"content_type={content_type}")
    resource_type = rejection.get("resource_type")
    if resource_type:
        details.append(f"resource_type={resource_type}")
    status_code = rejection.get("status_code")
    if status_code is not None:
        details.append(f"status_code={status_code}")
    if not details:
        return base
    return f"{base}; latest rejected submission: {', '.join(details)}"


def redact_capture_for_output(capture: dict[str, Any]) -> dict[str, Any]:
    redacted = dict(capture)
    if isinstance(redacted.get("manifest"), dict):
        redacted["manifest"] = redact_manifest(redacted["manifest"])
    if isinstance(redacted.get("request_headers"), dict):
        redacted["request_headers"] = redact_manifest({"request_headers": redacted["request_headers"]})["request_headers"]
    return redacted


def capture_context_for_manifest(capture: dict[str, Any]) -> dict[str, Any]:
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
        "manifest_path",
    }
    return {key: capture.get(key) for key in keys if key in capture}


def _page_capture_extra_payload(page_url: str, capture: dict[str, Any], capture_payload: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "page_url": page_url,
        "capture_session_id": capture.get("capture_session_id") or (capture_payload or {}).get("capture_session_id"),
        "capture": redact_capture_for_output(capture),
        "capture_page_match": (capture_payload or {}).get("capture_page_match"),
        "sniff_status": "captured",
    }
