from __future__ import annotations

from collections.abc import Callable
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel


class BackendSpec(BaseModel):
    name: str
    command: str
    required: bool = False
    purpose: str = ""
    version_args: list[str] = []
    supported_kinds: list[str] = []
    fallbacks: list[str] = []
    install_hint: str | None = None
    reference_url: str | None = None
    recommended_for: list[str] = []


@dataclass(frozen=True)
class BackendAdapter:
    spec: BackendSpec
    build_command: Callable[[str, Path, Mapping[str, str] | None], list[str]]


def get_backend_registry() -> dict[str, BackendAdapter]:
    from video_orchestrator.backends.aria2 import build_aria2_command
    from video_orchestrator.backends.curl import build_curl_command
    from video_orchestrator.backends.feishu import build_feishu_command
    from video_orchestrator.backends.gallery_dl import build_gallery_dl_command
    from video_orchestrator.backends.lux import build_lux_command
    from video_orchestrator.backends.n_m3u8dl_re import build_n_m3u8dl_re_command
    from video_orchestrator.backends.python_http import build_python_http_command
    from video_orchestrator.backends.spotdl import build_spotdl_command
    from video_orchestrator.backends.streamlink import build_streamlink_command
    from video_orchestrator.backends.tencent_meeting import build_tencent_meeting_command
    from video_orchestrator.backends.vsd import build_vsd_command
    from video_orchestrator.backends.ytdlp import build_ytdlp_command
    from video_orchestrator.backends.ytdlp_api import build_ytdlp_api_command

    def streamlink_adapter(url: str, output_dir: Path, headers: Mapping[str, str] | None = None) -> list[str]:
        return build_streamlink_command(url, output_dir / "stream.ts")

    def no_header_adapter(builder: Callable[[str, Path], list[str]]) -> Callable[[str, Path, Mapping[str, str] | None], list[str]]:
        def wrapped(url: str, output_dir: Path, headers: Mapping[str, str] | None = None) -> list[str]:
            return builder(url, output_dir)

        return wrapped

    adapters = [
        BackendAdapter(
            BackendSpec(
                name="yt-dlp-api",
                command="python",
                required=False,
                purpose="Python yt-dlp module planning backend",
                version_args=["-m", "yt_dlp", "--version"],
                supported_kinds=["general"],
                fallbacks=["yt-dlp", "lux"],
                install_hint="Install yt-dlp as a Python package so `python -m yt_dlp` is available.",
                reference_url="https://github.com/yt-dlp/yt-dlp",
                recommended_for=["default general extraction", "metadata inspection"],
            ),
            no_header_adapter(build_ytdlp_api_command),
        ),
        BackendAdapter(
            BackendSpec(
                name="yt-dlp",
                command="yt-dlp",
                required=True,
                purpose="general video downloader",
                version_args=["--version"],
                supported_kinds=["general"],
                fallbacks=["lux"],
                install_hint="Install yt-dlp and ensure `yt-dlp` is on PATH.",
                reference_url="https://github.com/yt-dlp/yt-dlp",
                recommended_for=["required general download fallback"],
            ),
            no_header_adapter(build_ytdlp_command),
        ),
        BackendAdapter(
            BackendSpec(
                name="gallery-dl",
                command="gallery-dl",
                required=False,
                purpose="gallery/archive downloader",
                version_args=["--version"],
                supported_kinds=["gallery"],
                fallbacks=["yt-dlp"],
                install_hint="Install gallery-dl and ensure `gallery-dl` is on PATH.",
                reference_url="https://github.com/mikf123/gallery-dl",
                recommended_for=["gallery and archive-oriented sites"],
            ),
            no_header_adapter(build_gallery_dl_command),
        ),
        BackendAdapter(
            BackendSpec(
                name="streamlink",
                command="streamlink",
                required=False,
                purpose="live stream downloader",
                version_args=["--version"],
                supported_kinds=["live"],
                fallbacks=["yt-dlp"],
                install_hint="Install streamlink and ensure `streamlink` is on PATH.",
                reference_url="https://github.com/streamlink/streamlink",
                recommended_for=["live streams"],
            ),
            streamlink_adapter,
        ),
        BackendAdapter(
            BackendSpec(
                name="lux",
                command="lux",
                required=False,
                purpose="alternate video downloader",
                version_args=["--version"],
                supported_kinds=["general"],
                fallbacks=[],
                install_hint="Install lux and ensure `lux` is on PATH if you want this optional fallback.",
                reference_url="https://github.com/iawia002/lux",
                recommended_for=["alternate general downloader fallback"],
            ),
            no_header_adapter(build_lux_command),
        ),
        BackendAdapter(
            BackendSpec(
                name="n_m3u8dl_re",
                command="N_m3u8DL-RE",
                required=False,
                purpose="direct HLS/DASH manifest downloader",
                version_args=["--version"],
                supported_kinds=["manifest"],
                fallbacks=["vsd", "yt-dlp-api", "yt-dlp"],
                install_hint="Install N_m3u8DL-RE and ensure `N_m3u8DL-RE` is on PATH for HLS/DASH manifests.",
                reference_url="https://github.com/nilaoda/N_m3u8DL-RE",
                recommended_for=["HLS/DASH manifest downloads"],
            ),
            no_header_adapter(build_n_m3u8dl_re_command),
        ),
        BackendAdapter(
            BackendSpec(
                name="vsd",
                command="vsd",
                required=False,
                purpose="HLS/DASH downloader with browser capture, headers, cookies, proxy, retries and threads",
                version_args=["--version"],
                supported_kinds=["manifest"],
                fallbacks=["yt-dlp-api", "yt-dlp"],
                install_hint="Install vsd and ensure `vsd` is on PATH for header-bound manifests and browser capture.",
                reference_url="https://github.com/clitic/vsd",
                recommended_for=["authenticated HLS/DASH with headers", "browser capture"],
            ),
            build_vsd_command,
        ),
        BackendAdapter(
            BackendSpec(
                name="aria2",
                command="aria2c",
                required=False,
                purpose="direct transfer, BitTorrent, magnet, ED2K and related URLs",
                version_args=["--version"],
                supported_kinds=["direct-transfer"],
                fallbacks=["curl", "yt-dlp"],
                install_hint="Install aria2 and ensure `aria2c` is on PATH for multi-connection direct transfers.",
                reference_url="https://github.com/aria2/aria2",
                recommended_for=["large direct files", "magnet/torrent/ed2k"],
            ),
            build_aria2_command,
        ),
        BackendAdapter(
            BackendSpec(
                name="curl",
                command="curl.exe",
                required=False,
                purpose="authenticated direct HTTP(S) transfer fallback",
                version_args=["--version"],
                supported_kinds=["direct-transfer"],
                fallbacks=["python-http", "yt-dlp"],
                install_hint="curl.exe is included on modern Windows; ensure it is available on PATH.",
                reference_url="https://curl.se/",
                recommended_for=["authenticated direct HTTP(S) fallback"],
            ),
            build_curl_command,
        ),
        BackendAdapter(
            BackendSpec(
                name="python-http",
                command="python",
                required=False,
                purpose="direct HTTP(S) transfer fallback using Python requests when curl/Schannel fails",
                version_args=["--version"],
                supported_kinds=["direct-transfer"],
                fallbacks=["yt-dlp"],
                install_hint="Python requests backend ships with this project environment; install project dependencies if unavailable.",
                reference_url=None,
                recommended_for=["direct HTTP(S) fallback when curl fails"],
            ),
            build_python_http_command,
        ),
        BackendAdapter(
            BackendSpec(
                name="tencent-meeting",
                command="python",
                required=False,
                purpose="Tencent Meeting recording share downloader using the page sign API and Python streaming fallback",
                version_args=["--version"],
                supported_kinds=["tencent-meeting"],
                fallbacks=[],
                install_hint="This adapter runs through the project Python package.",
                reference_url=None,
                recommended_for=["Tencent Meeting recording share pages"],
            ),
            no_header_adapter(build_tencent_meeting_command),
        ),
        BackendAdapter(
            BackendSpec(
                name="feishu",
                command="python",
                required=False,
                purpose="Feishu page video adapter that extracts drive stream URLs and reuses python-http streaming",
                version_args=["--version"],
                supported_kinds=["feishu-page"],
                fallbacks=[],
                install_hint="This adapter runs through the project Python package.",
                reference_url=None,
                recommended_for=["Feishu page video links"],
            ),
            build_feishu_command,
        ),
        BackendAdapter(
            BackendSpec(
                name="spotdl",
                command="spotdl",
                required=False,
                purpose="Spotify audio downloader",
                version_args=["--version"],
                supported_kinds=["spotify"],
                fallbacks=["yt-dlp-api"],
                install_hint="Install spotDL and ensure `spotdl` is on PATH for Spotify audio.",
                reference_url="https://github.com/spotDL/spotify-downloader",
                recommended_for=["Spotify URLs"],
            ),
            no_header_adapter(build_spotdl_command),
        ),
        BackendAdapter(
            BackendSpec(
                name="wechat-channels-helper",
                command="wechat-channels-helper",
                required=False,
                purpose="微信视频号本机授权态解析 helper（P1 planning-only 占位，不执行真实下载）",
                version_args=["--version"],
                supported_kinds=["wechat-channels-page"],
                fallbacks=[],
                install_hint="该 backend 当前为规划占位：识别视频号链接并生成计划，显式执行会安全失败，等待本机授权态解析 helper 接入。",
                reference_url=None,
                recommended_for=["wechat channels share pages (planning only)"],
            ),
            no_header_adapter(build_wechat_channels_helper_command),
        ),
    ]
    return {adapter.spec.name: adapter for adapter in adapters}


def get_backend_specs() -> list[BackendSpec]:
    return [adapter.spec for adapter in get_backend_registry().values()]


def get_backend_adapter(name: str) -> BackendAdapter:
    registry = get_backend_registry()
    try:
        return registry[name]
    except KeyError as exc:
        raise ValueError(f"unsupported backend: {name}") from exc


def build_wechat_channels_helper_command(url: str, output_dir: Path, headers: Mapping[str, str] | None = None) -> list[str]:
    """P1 planning-only 占位：视频号链接不允许自动下载，显式执行必须安全失败。"""
    raise ValueError(
        "微信视频号解析 helper 未启用：当前为 planning-only，不自动下载。"
        "请先完成本机授权态解析（或微信桌面端播放捕获），确认后再显式导入 MediaGo。"
    )
