from urllib.parse import urlparse

from pydantic import BaseModel

from video_orchestrator.runtime.media_protocols import infer_media_kind
from video_orchestrator.runtime.media_protocols import match_media_protocol
from video_orchestrator.runtime.site_adapters import match_site_adapter
from video_orchestrator.runtime.site_adapters import host_matches_suffixes


class UrlClassification(BaseModel):
    url: str
    kind: str
    preferred_backend: str
    reason: str
    media_kind: str = "unknown"


def classify_url(url: str) -> UrlClassification:
    parsed = urlparse(url)
    path = parsed.path.lower()
    host = (parsed.hostname or "").lower().rstrip(".")
    scheme = parsed.scheme.lower()

    site_adapter = match_site_adapter(url)
    if site_adapter is not None:
        return UrlClassification(
            url=url,
            kind=site_adapter.kind,
            preferred_backend=site_adapter.backend,
            reason=site_adapter.reason,
            media_kind=site_adapter.media_kind,
        )

    media_protocol = match_media_protocol(url)
    media_kind = infer_media_kind(url)
    if media_protocol is not None and media_protocol.get("category") == "stream":
        return UrlClassification(
            url=url,
            kind="manifest",
            preferred_backend="n_m3u8dl_re",
            reason="direct stream manifest URL",
            media_kind=media_kind,
        )

    if scheme in {"magnet", "ed2k", "thunder"} or path.endswith(".torrent"):
        return UrlClassification(
            url=url,
            kind="direct-transfer",
            preferred_backend="aria2",
            reason="direct transfer or peer-to-peer URL",
            media_kind="unknown",
        )

    if host == "internal-api-drive-stream.feishu.cn" and "/download/video/" in path:
        return UrlClassification(
            url=url,
            kind="direct-transfer",
            preferred_backend="aria2",
            reason="authenticated Feishu drive video stream URL",
            media_kind="video",
        )

    if media_protocol is not None and media_protocol.get("category") == "files":
        return UrlClassification(
            url=url,
            kind="direct-transfer",
            preferred_backend="aria2",
            reason="direct playable media URL",
            media_kind=media_kind,
        )

    if path.endswith((".mkv", ".avi", ".flv", ".zip", ".rar", ".7z", ".iso")):
        return UrlClassification(
            url=url,
            kind="direct-transfer",
            preferred_backend="aria2",
            reason="direct downloadable file URL",
            media_kind="video" if path.endswith((".mkv", ".avi", ".flv")) else "unknown",
        )

    if _host_matches(host, "spotify.com") or _host_matches(host, "spotify.link"):
        return UrlClassification(
            url=url,
            kind="spotify",
            preferred_backend="spotdl",
            reason="known Spotify URL",
            media_kind="audio",
        )

    if _host_matches(host, "twitch.tv"):
        return UrlClassification(
            url=url,
            kind="live",
            preferred_backend="streamlink",
            reason="known live-stream platform",
            media_kind="adaptive",
        )

    if any(_host_matches(host, site) for site in ("pixiv.net", "deviantart.com", "tumblr.com")):
        return UrlClassification(
            url=url,
            kind="gallery",
            preferred_backend="gallery-dl",
            reason="known gallery/archive-oriented platform",
        )

    return UrlClassification(
        url=url,
        kind="general",
        preferred_backend="yt-dlp-api",
        reason="default general-purpose downloader",
    )


def _host_matches(host: str, suffix: str) -> bool:
    return host_matches_suffixes(host, (suffix,))
