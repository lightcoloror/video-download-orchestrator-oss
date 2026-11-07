from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any
from urllib.parse import urlparse


# This data-only table is shared by VDO runtime checks and the generated
# browser-sniffer asset so media protocol rules cannot drift between them.
MEDIA_PROTOCOLS: tuple[dict[str, Any], ...] = (
    {"ext": ("m3u8",), "content_types": ("application/x-mpegurl", "application/vnd.apple.mpegurl", "audio/vnd.apple.mpegurl"), "content_type_prefixes": (), "type": "HLS", "category": "stream", "media_kind": "adaptive"},
    {"ext": ("mpd",), "content_types": ("application/dash+xml", "video/vnd.mpeg.dash.mpd"), "content_type_prefixes": (), "type": "DASH", "category": "stream", "media_kind": "adaptive"},
    {"ext": ("f4m",), "content_types": ("application/f4m",), "content_type_prefixes": (), "type": "HDS", "category": "stream", "media_kind": "adaptive"},
    {"ext": ("ism/manifest", "ism"), "content_types": (), "content_type_prefixes": (), "type": "MSS", "category": "stream", "media_kind": "adaptive"},
    {"ext": ("vtt",), "content_types": ("text/vtt",), "content_type_prefixes": (), "type": "VTT", "category": "subtitles", "media_kind": "subtitle"},
    {"ext": ("srt",), "content_types": ("application/x-subrip",), "content_type_prefixes": (), "type": "SRT", "category": "subtitles", "media_kind": "subtitle"},
    {"ext": ("ttml", "ttml2"), "content_types": ("application/ttml+xml",), "content_type_prefixes": (), "type": "TTML", "category": "subtitles", "media_kind": "subtitle"},
    {"ext": ("mp4", "m4v", "m4s", "mov"), "content_types": ("video/x-m4v", "video/m4v", "video/mp4", "video/quicktime"), "content_type_prefixes": (), "type": "MP4", "category": "files", "media_kind": "video"},
    {"ext": ("ts", "m2t"), "content_types": ("video/mp2t",), "content_type_prefixes": (), "type": "TS", "category": "files", "media_kind": "video"},
    {"ext": ("aac", "m4a", "m4b", "m4p", "m4r"), "content_types": ("audio/aac", "audio/m4a", "audio/mp4"), "content_type_prefixes": (), "type": "AAC", "category": "files", "media_kind": "audio"},
    {"ext": ("mp3",), "content_types": ("audio/mpeg",), "content_type_prefixes": (), "type": "MP3", "category": "files", "media_kind": "audio"},
    {"ext": ("flac",), "content_types": ("audio/flac", "audio/x-flac"), "content_type_prefixes": (), "type": "FLAC", "category": "files", "media_kind": "audio"},
    {"ext": ("wav", "aif", "aiff", "wma"), "content_types": ("audio/wav", "audio/x-wav", "audio/aiff", "audio/x-ms-wma"), "content_type_prefixes": (), "type": "AUDIO", "category": "files", "media_kind": "audio"},
    {"ext": ("ogg", "oga", "opus"), "content_types": ("audio/ogg", "audio/opus"), "content_type_prefixes": (), "type": "OGG", "category": "files", "media_kind": "audio"},
    {"ext": ("ogv",), "content_types": ("video/ogg",), "content_type_prefixes": (), "type": "OGG", "category": "files", "media_kind": "video"},
    {"ext": ("weba",), "content_types": ("audio/webm",), "content_type_prefixes": (), "type": "WEBM", "category": "files", "media_kind": "audio"},
    {"ext": ("webm",), "content_types": ("video/webm",), "content_type_prefixes": (), "type": "WEBM", "category": "files", "media_kind": "mixed"},
    {"ext": (), "content_types": (), "content_type_prefixes": ("audio/",), "type": "AUDIO", "category": "files", "media_kind": "audio"},
)


def browser_protocols() -> list[dict[str, Any]]:
    return [
        {
            "ext": list(item["ext"]),
            "contentTypes": list(item["content_types"]),
            "contentTypePrefixes": list(item.get("content_type_prefixes", ())),
            "type": item["type"],
            "category": item["category"],
            "mediaKind": item["media_kind"],
        }
        for item in MEDIA_PROTOCOLS
    ]


def match_media_protocol(url: str, content_type: str | None = None, headers: Iterable[str] = ()) -> dict[str, Any] | None:
    path = urlparse(url).path.lower()
    values = " ".join([content_type or "", *headers]).lower()
    for item in MEDIA_PROTOCOLS:
        if (
            any(path.endswith(f".{extension}") or f".{extension}" in path for extension in item["ext"])
            or any(value in values for value in item["content_types"])
            or any(_has_content_type_prefix(values, prefix) for prefix in item.get("content_type_prefixes", ()))
        ):
            return item
    return None


def infer_media_kind(url: str, content_type: str | None = None, headers: Iterable[str] = ()) -> str:
    normalized_content_type = str(content_type or "").split(";", 1)[0].strip().lower()
    if normalized_content_type.startswith("audio/") and normalized_content_type != "audio/vnd.apple.mpegurl":
        return "audio"
    if normalized_content_type.startswith("video/"):
        return "video"
    item = match_media_protocol(url, content_type, headers)
    if item is None:
        return "unknown"
    media_kind = str(item.get("media_kind") or "unknown")
    if media_kind == "mixed":
        values = " ".join([content_type or "", *headers]).lower()
        if "audio/" in values:
            return "audio"
        if "video/" in values:
            return "video"
    if media_kind == "adaptive" and normalized_content_type == "audio/vnd.apple.mpegurl":
        return "audio"
    return media_kind


def _has_content_type_prefix(values: str, prefix: str) -> bool:
    return any(token.startswith(prefix) for token in re.split(r"[\s;,]+", values) if token)


def is_hls_protocol(media_type: str | None, category: str | None, url: str = "", content_type: str | None = None) -> bool:
    if str(media_type or "").upper() == "HLS" and category == "stream":
        return True
    item = match_media_protocol(url, content_type)
    return bool(item and item["type"] == "HLS")


def is_segment_capture(url: str, media_type: str | None, category: str | None) -> bool:
    path = urlparse(url).path.lower()
    return category == "files" and str(media_type or "").upper() in {"TS", "MP4"} and path.endswith((".ts", ".m4s"))
