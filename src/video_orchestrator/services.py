from __future__ import annotations

import math
from collections.abc import Mapping
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from video_orchestrator.backends.base import get_backend_adapter
from video_orchestrator.backends.ytdlp_api import build_ytdlp_api_metadata_preview
from video_orchestrator.fallbacks import fallback_plan_for_url


def plan_download(
    url: str,
    output_dir: Path | str = Path("downloads"),
    backend: str | None = None,
    headers: Mapping[str, str] | None = None,
    ytdlp_options: Mapping[str, object] | None = None,
    expected_duration_seconds: float | None = None,
    estimated_output_bytes: int | None = None,
) -> dict[str, Any]:
    fallback_plan = fallback_plan_for_url(url, backend)
    output_path = Path(output_dir)
    selected_backend = fallback_plan["selected_backend"]

    manifest = {
        "url": url,
        "classification": fallback_plan["classification"],
        "route": fallback_plan["route"],
        "media_kind": fallback_plan["route"].get("media_kind", "unknown"),
        "selected_backend": selected_backend,
        "selection_source": fallback_plan["selection_source"],
        "fallback_backends": fallback_plan["fallback_backends"],
        "command": _build_command(selected_backend, url, output_path, headers=headers, ytdlp_options=ytdlp_options),
        "request_headers": dict(headers or {}),
        "yt_dlp_options": dict(ytdlp_options or {}),
        "output_dir": output_path.as_posix(),
        "status": "planned",
        "created_at": datetime.now(UTC).isoformat(),
    }
    if selected_backend == "yt-dlp-api":
        manifest["metadata_preview"] = build_ytdlp_api_metadata_preview()
        manifest["playlist_count"] = None
        manifest["format_summary"] = []
    if expected_duration_seconds is not None:
        duration = _validate_expected_duration_seconds(expected_duration_seconds)
        manifest["sniffed_media"] = {
            "evidence_source": "caller_expected_duration",
            "expected_duration_seconds": duration,
            "completeness": {
                "risk": "pending_download_validation",
                "label": "已记录预期时长，待下载后校验",
                "risk_reasons": [],
                "expected_duration_seconds": duration,
                "next_action": "下载后使用 ffprobe 时长与预期时长进行完整性校验。",
            },
        }
    if estimated_output_bytes is not None:
        manifest["estimated_output_bytes"] = _validate_estimated_output_bytes(estimated_output_bytes)
    return manifest


def _validate_expected_duration_seconds(value: float) -> float:
    duration = float(value)
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("expected_duration_seconds must be a finite number greater than zero")
    return duration


def _validate_estimated_output_bytes(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError("estimated_output_bytes must be a positive integer")
    return value


def _build_command(
    backend: str,
    url: str,
    output_dir: Path,
    headers: Mapping[str, str] | None = None,
    ytdlp_options: Mapping[str, object] | None = None,
) -> list[str]:
    command = get_backend_adapter(backend).build_command(url, output_dir, headers)
    if backend in {"yt-dlp", "yt-dlp-api"}:
        command = _append_ytdlp_options(command, ytdlp_options or {})
    return command


def _append_ytdlp_options(command: list[str], options: Mapping[str, object]) -> list[str]:
    additions: list[str] = []
    cookies_from_browser = options.get("cookies_from_browser")
    cookies_path = options.get("cookies")
    if cookies_from_browser:
        additions.extend(["--cookies-from-browser", str(cookies_from_browser)])
    if cookies_path:
        additions.extend(["--cookies", str(cookies_path)])
    js_runtimes = options.get("js_runtimes")
    if js_runtimes:
        additions.extend(["--js-runtimes", _join_ytdlp_option_values(js_runtimes)])
    for component in _iter_ytdlp_option_values(options.get("remote_components")):
        additions.extend(["--remote-components", component])
    if not additions:
        return command
    return [*command[:-1], *additions, command[-1]]


def _join_ytdlp_option_values(value: object) -> str:
    return ",".join(_iter_ytdlp_option_values(value))


def _iter_ytdlp_option_values(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        values = [value]
    elif isinstance(value, Sequence):
        values = [str(item) for item in value]
    else:
        values = [str(value)]
    return [item.strip() for item in values if item and item.strip()]
