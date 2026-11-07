from __future__ import annotations

from pathlib import Path
from typing import Any

from video_orchestrator.backends.ytdlp_defaults import DEFAULT_SUBTITLE_LANGS


def build_ytdlp_api_command(url: str, output_dir: Path) -> list[str]:
    return [
        "python",
        "-m",
        "yt_dlp",
        "--newline",
        "--progress",
        "--no-color",
        "--no-playlist",
        "--write-info-json",
        "--write-description",
        "--write-subs",
        "--write-auto-subs",
        "--sub-langs",
        DEFAULT_SUBTITLE_LANGS,
        "-P",
        output_dir.as_posix(),
        url,
    ]


def build_ytdlp_api_metadata_preview() -> dict[str, Any]:
    try:
        import yt_dlp  # noqa: F401
    except ImportError:
        return {
            "available": False,
            "status": "module_not_installed",
            "detail": "Install yt-dlp to enable Python API metadata discovery.",
        }
    return {
        "available": True,
        "status": "not_queried",
        "detail": "Planning does not contact remote extractors by default.",
    }


def extract_ytdlp_metadata(url: str, *, flat: bool = True) -> dict[str, Any]:
    try:
        import yt_dlp
    except ImportError as exc:
        return {
            "available": False,
            "status": "module_not_installed",
            "error": str(exc),
        }

    options = {
        "quiet": True,
        "skip_download": True,
        "extract_flat": flat,
        "ignore_no_formats_error": True,
        "noplaylist": False,
    }
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as exc:  # yt-dlp raises several site-specific exception types.
        return {
            "available": True,
            "status": "error",
            "error": str(exc),
        }

    entries = info.get("entries") if isinstance(info, dict) else None
    formats = info.get("formats") if isinstance(info, dict) else None
    return {
        "available": True,
        "status": "ok",
        "id": info.get("id") if isinstance(info, dict) else None,
        "title": info.get("title") if isinstance(info, dict) else None,
        "webpage_url": info.get("webpage_url") if isinstance(info, dict) else url,
        "extractor": info.get("extractor") if isinstance(info, dict) else None,
        "playlist_count": len(entries) if isinstance(entries, list) else None,
        "format_summary": _summarize_formats(formats if isinstance(formats, list) else []),
    }


def _summarize_formats(formats: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summary = []
    for item in formats[:20]:
        summary.append(
            {
                "format_id": item.get("format_id"),
                "ext": item.get("ext"),
                "resolution": item.get("resolution"),
                "height": item.get("height"),
                "vcodec": item.get("vcodec"),
                "acodec": item.get("acodec"),
                "protocol": item.get("protocol"),
            }
        )
    return summary
