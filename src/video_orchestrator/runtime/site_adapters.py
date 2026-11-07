from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlparse


@dataclass(frozen=True)
class SiteAdapter:
    name: str
    kind: str
    backend: str
    reason: str
    capabilities: tuple[str, ...]
    fallback_mode: str
    match: Callable[[str], bool]
    media_kind: str = "video"
    probe_host_suffixes: tuple[str, ...] = ()

    def diagnostic(self, *, matched: bool) -> dict:
        return {
            "matched": matched,
            "name": self.name,
            "kind": self.kind,
            "backend": self.backend,
            "reason": self.reason,
            "capabilities": list(self.capabilities),
            "fallback_mode": self.fallback_mode,
            "media_kind": self.media_kind,
            "supports_site_fallback": "site_fallback" in self.capabilities,
            "probe_host_suffixes": list(self.probe_host_suffixes),
        }


def host_matches_suffixes(hostname: str, suffixes: tuple[str, ...] | list[str]) -> bool:
    normalized = hostname.lower().rstrip(".")
    return any(normalized == suffix or normalized.endswith(f".{suffix}") for suffix in suffixes)


def _host_path(url: str) -> tuple[str, str]:
    parsed = urlparse(url)
    return (parsed.hostname or "").lower(), parsed.path.lower()


def _matches_tencent_meeting(url: str) -> bool:
    host, path = _host_path(url)
    return host == "meeting.tencent.com" and path.startswith("/meeting-record/shares")


def _matches_feishu_page(url: str) -> bool:
    host, path = _host_path(url)
    return host_matches_suffixes(host, ("feishu.cn",)) and path.startswith(("/wiki/", "/docx/", "/docs/", "/file/"))


def _matches_bilibili_page(url: str) -> bool:
    host, path = _host_path(url)
    if host == "b23.tv":
        return True
    if host_matches_suffixes(host, ("bilibili.com",)) and path.startswith("/video/"):
        return True
    return host == "space.bilibili.com" and any(token in path for token in ("/video", "/channel", "/list"))


def _matches_xiaohongshu_page(url: str) -> bool:
    host, path = _host_path(url)
    if host == "xhslink.com":
        return True
    return host_matches_suffixes(host, ("xiaohongshu.com",)) and path.startswith(("/explore/", "/discovery/item/"))


def _matches_douyin_page(url: str) -> bool:
    host, path = _host_path(url)
    if host_matches_suffixes(host, ("douyin.com", "iesdouyin.com")):
        return True
    if host_matches_suffixes(host, ("ixigua.com",)) and (path.startswith(("/video/", "/home/")) or path.strip("/").isdigit()):
        return True
    return host_matches_suffixes(host, ("toutiao.com",)) and path.startswith("/video/")


def _matches_wechat_channels_page(url: str) -> bool:
    host, path = _host_path(url)
    # 视频号分享短链：https://weixin.qq.com/sph/<id>（外部浏览器只显示二维码）
    if host == "weixin.qq.com" and path.startswith("/sph/"):
        return True
    # 视频号页面：https://channels.weixin.qq.com/...
    if host == "channels.weixin.qq.com":
        return True
    return False


def _matches_elevenlabs_music_page(url: str) -> bool:
    host, path = _host_path(url)
    return host_matches_suffixes(host, ("elevenlabs.io",)) and path.startswith("/music/songs/")


def get_site_adapters() -> list[SiteAdapter]:
    return [
        SiteAdapter(
            name="tencent-meeting",
            kind="tencent-meeting",
            backend="tencent-meeting",
            reason="Tencent Meeting recording share page with signed media API",
            capabilities=("route", "site_fallback"),
            fallback_mode="site_adapter",
            match=_matches_tencent_meeting,
        ),
        SiteAdapter(
            name="feishu",
            kind="feishu-page",
            backend="feishu",
            reason="Feishu page that may contain an authenticated drive video stream URL",
            capabilities=("route", "site_fallback"),
            fallback_mode="site_adapter",
            match=_matches_feishu_page,
        ),
        SiteAdapter(
            name="bilibili",
            kind="bilibili-page",
            backend="yt-dlp-api",
            reason="Bilibili video page; try yt-dlp first and use browser sniffing if extraction fails",
            capabilities=("route", "yt_dlp_first", "browser_sniffing", "public_page_probe"),
            fallback_mode="browser_sniffing",
            match=_matches_bilibili_page,
            probe_host_suffixes=("bilibili.com", "b23.tv"),
        ),
        SiteAdapter(
            name="xiaohongshu",
            kind="xiaohongshu-page",
            backend="yt-dlp-api",
            reason="Xiaohongshu note page; try public page probe and browser sniffing when direct extraction fails",
            capabilities=("route", "yt_dlp_first", "browser_sniffing", "public_page_probe"),
            fallback_mode="browser_sniffing",
            match=_matches_xiaohongshu_page,
            probe_host_suffixes=("xiaohongshu.com", "xhslink.com"),
        ),
        SiteAdapter(
            name="douyin",
            kind="douyin-page",
            backend="yt-dlp-api",
            reason="Douyin/Xigua/Toutiao video page; try yt-dlp first and use browser sniffing if extraction fails",
            capabilities=("route", "yt_dlp_first", "browser_sniffing", "public_page_probe"),
            fallback_mode="browser_sniffing",
            match=_matches_douyin_page,
            probe_host_suffixes=("douyin.com", "iesdouyin.com", "ixigua.com", "toutiao.com"),
        ),
        SiteAdapter(
            name="elevenlabs-music",
            kind="elevenlabs-music-page",
            backend="yt-dlp-api",
            reason="ElevenLabs Music song page; use yt-dlp generic extraction and validate the resulting audio stream",
            capabilities=("route", "yt_dlp_first", "browser_sniffing"),
            fallback_mode="browser_sniffing",
            match=_matches_elevenlabs_music_page,
            media_kind="audio",
        ),
        SiteAdapter(
            name="wechat-channels",
            kind="wechat-channels-page",
            backend="wechat-channels-helper",
            reason="微信视频号分享页面；外部浏览器只显示二维码，需要本机授权态解析或微信桌面端播放捕获，当前仅识别与计划，不自动下载",
            capabilities=("route", "planning_only"),
            fallback_mode="manual_review",
            match=_matches_wechat_channels_page,
        ),
    ]


def match_site_adapter(url: str) -> SiteAdapter | None:
    for adapter in get_site_adapters():
        if adapter.match(url):
            return adapter
    return None


def site_adapter_diagnostic(url: str) -> dict:
    adapter = match_site_adapter(url)
    if adapter is not None:
        return adapter.diagnostic(matched=True)
    return {
        "matched": False,
        "name": None,
        "kind": None,
        "backend": None,
        "reason": "No registered site adapter matched this URL",
        "capabilities": [],
        "fallback_mode": "none",
        "supports_site_fallback": False,
    }
