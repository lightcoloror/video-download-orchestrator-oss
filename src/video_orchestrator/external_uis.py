from __future__ import annotations

import shutil
import os
from pathlib import Path

from pydantic import BaseModel, Field


class ExternalUiSpec(BaseModel):
    name: str
    purpose: str
    source_project: str
    local_path: str | None = None
    integration_mode: str
    routes: list[str]
    handoff_modes: list[str] = Field(default_factory=list)
    status: str = "reference"
    launch_command: list[str] | None = None
    start_hint: str | None = None
    installed_path: str | None = None


def get_external_uis() -> list[ExternalUiSpec]:
    return get_external_ui_status(check=False)


def get_external_ui_status(*, check: bool = False) -> list[ExternalUiSpec]:
    source_root = Path(os.environ.get("VDO_SOURCE_REVIEW_ROOT", ".source-reviews")).expanduser()
    candidates = [
        ExternalUiSpec(
            name="mediago",
            purpose="现成中文桌面 UI，可通过 HTTP API 或自定义协议接收下载任务",
            source_project="https://github.com/caorushizi/mediago",
            local_path=_path_if_exists(source_root / "video-router-candidates" / "mediago"),
            integration_mode="external_desktop_ui_http_handoff",
            routes=["yt-dlp-api", "yt-dlp", "n_m3u8dl_re", "aria2", "bilibili"],
            handoff_modes=["http_api_import", "custom_scheme_deeplink"],
            start_hint="启动 MediaGo Desktop 后，在本项目 WebUI 点击“导入 MediaGo”。",
        ),
        ExternalUiSpec(
            name="metube",
            purpose="Existing yt-dlp queue and web UI reference",
            source_project="https://github.com/alexta69/metube",
            local_path=_path_if_exists(source_root / "video-router-candidates" / "metube"),
            integration_mode="external_reference_or_separate_service",
            routes=["yt-dlp-api", "yt-dlp"],
            handoff_modes=["open_external_ui", "copy_ytdlp_url"],
            start_hint="Run MeTube separately if a full yt-dlp queue UI is desired.",
        ),
        ExternalUiSpec(
            name="hls-downloader",
            purpose="Browser UI and network sniffer for HLS manifests",
            source_project="https://github.com/puemos/hls-downloader",
            local_path=_path_if_exists(source_root / "video-router-candidates" / "hls-downloader"),
            integration_mode="browser_extension_posts_to_sniffed_media_api",
            routes=["n_m3u8dl_re"],
            handoff_modes=["browser_extension_capture"],
            start_hint="Use or adapt a browser extension/userscript to POST /sniffed-media/plan.",
        ),
        ExternalUiSpec(
            name="motrix-next",
            purpose="Existing aria2/direct transfer desktop UI reference",
            source_project="https://github.com/AnInsomniacy/motrix-next",
            local_path=_path_if_exists(source_root / "motrix-next"),
            integration_mode="external_aria2_ui_reference",
            routes=["aria2"],
            handoff_modes=["open_external_ui", "aria2_rpc_reference"],
            start_hint="Use Motrix Next separately for aria2 UI; orchestrator plans aria2c commands.",
        ),
        ExternalUiSpec(
            name="parabolic",
            purpose="Existing yt-dlp desktop UI and argument builder reference",
            source_project="https://github.com/NickvisionApps/Parabolic",
            local_path=_path_if_exists(source_root / "video-router-candidates" / "parabolic"),
            integration_mode="external_reference",
            routes=["yt-dlp-api", "yt-dlp"],
            handoff_modes=["open_external_ui", "copy_ytdlp_url"],
        ),
        ExternalUiSpec(
            name="gdownloader",
            purpose="Existing multi-backend desktop routing UI reference",
            source_project="https://github.com/hstr0100/GDownloader",
            local_path=_path_if_exists(source_root / "video-router-candidates" / "gdownloader"),
            integration_mode="external_reference",
            routes=["yt-dlp-api", "gallery-dl", "spotdl", "aria2"],
            handoff_modes=["open_external_ui", "multi_backend_reference"],
        ),
    ]
    return [_with_status(candidate, check=check) for candidate in candidates]


def _path_if_exists(path: Path) -> str | None:
    return str(path) if path.exists() else None


def _with_status(spec: ExternalUiSpec, *, check: bool) -> ExternalUiSpec:
    update: dict[str, object] = {}
    if spec.local_path:
        update["status"] = "source_available"
    if check and spec.name == "mediago":
        installed = discover_mediago_executable()
        update["installed_path"] = installed
        if installed:
            update["status"] = "installed_not_probed"
    if check and spec.launch_command:
        command = spec.launch_command[0]
        update["status"] = "launch_command_available" if shutil.which(command) else "launch_command_missing"
    return spec.model_copy(update=update)


def discover_mediago_executable() -> str | None:
    """Check only known installer destinations; discovery does not launch the client."""
    for variable, relative in (
        ("LOCALAPPDATA", "Programs/mediago/MediaGo.exe"),
        ("LOCALAPPDATA", "Programs/media-go/Media Go.exe"),
        ("ProgramFiles", "MediaGo/MediaGo.exe"),
        ("ProgramFiles", "Media Go/Media Go.exe"),
        ("ProgramFiles(x86)", "MediaGo/MediaGo.exe"),
    ):
        root = os.environ.get(variable)
        if root:
            candidate = Path(root) / relative
            if candidate.is_file():
                return str(candidate)
    return None
