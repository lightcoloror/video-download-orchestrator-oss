from __future__ import annotations

from hashlib import sha256
import math
from typing import Any


VARIANT_ORDER = ("original", "h265", "h264", "default")


def parse_profile_payload(body: Any, *, quality: str = "highest") -> dict[str, Any]:
    """Normalize the small, unstable subset of the Channels profile response VDO uses."""

    if not isinstance(body, dict):
        raise ValueError("invalid_provider_payload")
    data = _unwrap_data(body)
    object_data = data.get("object") if isinstance(data.get("object"), dict) else {}
    object_desc = object_data.get("objectDesc") if isinstance(object_data.get("objectDesc"), dict) else {}
    feed = data.get("feedInfo") if isinstance(data.get("feedInfo"), dict) else object_desc or data
    if not isinstance(feed, dict):
        raise ValueError("missing_provider_data")

    media_items = feed.get("media") if isinstance(feed.get("media"), list) else []
    if isinstance(object_data.get("files"), list):
        media_items = [*media_items, *object_data["files"]]
    candidates = collect_media_candidates(feed, media_items=media_items)
    selected = select_media_candidate(candidates, quality=quality)
    if selected is None:
        raise ValueError("requested_quality_unavailable")

    author = data.get("authorInfo") if isinstance(data.get("authorInfo"), dict) else {}
    if not author and isinstance(object_data.get("contact"), dict):
        author = object_data["contact"]
    scene = data.get("sceneInfo") if isinstance(data.get("sceneInfo"), dict) else {}
    description = _text(feed.get("description") or feed.get("title"), limit=4000)
    title = description.splitlines()[0][:300] if description else None
    author_name = _text(author.get("nickname") or author.get("name"), limit=300)
    if not title:
        title = author_name

    duration_seconds = selected.get("duration_seconds") or _duration_seconds(feed)
    content_id = _first_text(
        feed,
        ("objectId", "objectID", "feedId", "feedID", "id", "nonceId", "objectNonceId"),
        limit=300,
    ) or _first_text(object_data, ("id", "objectId", "objectNonceId"), limit=300)
    content_id = content_id or _first_text(scene, ("dynamicExportId", "objectId", "feedId"), limit=300)
    media_type = _flexible_scalar(feed.get("mediaType") or feed.get("type"))
    first_media = next((item for item in media_items if isinstance(item, dict)), {})
    cover_url = _text(
        feed.get("coverUrl")
        or feed.get("coverURL")
        or first_media.get("coverUrl")
        or first_media.get("thumbUrl"),
        limit=2048,
    )
    created_at = _positive_number(
        feed.get("createtime")
        or feed.get("createTime")
        or feed.get("createdAt")
        or object_data.get("createtime")
        or object_data.get("createTime")
    )
    bgm = feed.get("bgmInfo") if isinstance(feed.get("bgmInfo"), dict) else {}
    follow_post = feed.get("followPostInfo") if isinstance(feed.get("followPostInfo"), dict) else {}
    if not bgm and isinstance(follow_post.get("musicInfo"), dict):
        bgm = follow_post["musicInfo"]

    return {
        "title": title,
        "description": description,
        "content_id": content_id,
        "media_type": media_type,
        "cover_url": cover_url,
        "created_at": int(created_at) if created_at is not None else None,
        "duration_seconds": duration_seconds,
        "author": {
            "id": _first_text(author, ("username", "finderUsername", "id"), limit=300),
            "name": author_name,
            "avatar_url": _text(
                author.get("headImgUrl")
                or author.get("headimgurl")
                or author.get("headUrl")
                or author.get("avatarUrl"),
                limit=2048,
            ),
        },
        "background_music": {
            "name": _text(bgm.get("name"), limit=300),
            "artist": _text(bgm.get("artist"), limit=300),
        },
        "media_url": str(selected["media_url"]),
        "selected_variant": public_candidate(selected),
        "media_candidates": [public_candidate(candidate) for candidate in candidates],
        "available_candidates": [str(candidate["variant_key"]) for candidate in candidates],
    }


def collect_media_candidates(
    feed: dict[str, Any],
    *,
    media_items: list[Any] | None = None,
) -> list[dict[str, Any]]:
    sources: list[tuple[str, str | None, Any, Any]] = [
        ("original", None, feed.get("originVideoInfo"), feed.get("originVideoUrl")),
        ("h265", "h265", feed.get("h265VideoInfo"), None),
        ("h264", "h264", feed.get("h264VideoInfo"), None),
        ("default", None, feed.get("videoInfo"), feed.get("videoUrl")),
    ]
    candidates: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for variant_key, default_codec, raw_info, fallback_url in sources:
        info = raw_info if isinstance(raw_info, dict) else {}
        media_url = _text(
            info.get("videoUrl") or info.get("url") or fallback_url,
            limit=8192,
        )
        if not media_url or media_url in seen_urls:
            continue
        seen_urls.add(media_url)
        spec = _best_spec(info.get("spec") or feed.get("spec"))
        candidate = {
            "variant_key": variant_key,
            "codec": _text(
                info.get("codec")
                or info.get("codingFormat")
                or spec.get("codingFormat")
                or default_codec,
                limit=80,
            ),
            "media_url": media_url,
            "width": _positive_int(info.get("width") or spec.get("width")),
            "height": _positive_int(info.get("height") or spec.get("height")),
            "bitrate": _positive_int(
                info.get("bitrate")
                or info.get("bitRate")
                or info.get("videoBitrate")
                or spec.get("bitRate")
                or spec.get("videoBitrate")
            ),
            "frame_rate": _positive_number(
                info.get("frameRate") or info.get("fps") or info.get("vfps") or spec.get("vfps")
            ),
            "file_size": _positive_int(info.get("fileSize") or info.get("size")),
            "quality_score": _positive_number(info.get("qualityScore") or spec.get("qualityScore")),
            "duration_seconds": _duration_seconds(info) or _duration_seconds(spec),
        }
        candidates.append(candidate)
    for index, raw_item in enumerate(media_items or []):
        if not isinstance(raw_item, dict):
            continue
        media_url = _text(raw_item.get("url") or raw_item.get("videoUrl"), limit=8192)
        if not media_url or media_url in seen_urls:
            continue
        seen_urls.add(media_url)
        spec = _best_spec(raw_item.get("spec"))
        codec = _text(
            raw_item.get("codec") or raw_item.get("codingFormat") or spec.get("codingFormat"),
            limit=80,
        )
        candidates.append(
            {
                "variant_key": _variant_key(codec, raw_item, spec, index),
                "codec": codec,
                "media_url": media_url,
                "width": _positive_int(raw_item.get("width") or spec.get("width")),
                "height": _positive_int(raw_item.get("height") or spec.get("height")),
                "bitrate": _positive_int(
                    raw_item.get("bitrate")
                    or raw_item.get("bitRate")
                    or spec.get("videoBitrate")
                    or spec.get("bitRate")
                ),
                "frame_rate": _positive_number(
                    raw_item.get("frameRate") or raw_item.get("fps") or spec.get("vfps")
                ),
                "file_size": _positive_int(raw_item.get("fileSize") or raw_item.get("size")),
                "quality_score": _positive_number(raw_item.get("qualityScore") or spec.get("qualityScore")),
                "duration_seconds": _duration_seconds(raw_item) or _duration_seconds(spec),
            }
        )
    return candidates


def select_media_candidate(candidates: list[dict[str, Any]], *, quality: str) -> dict[str, Any] | None:
    if not candidates:
        return None
    if quality in {"compatible", "h264", "h265", "original"}:
        order = {
            "compatible": ("h264", "default", "original", "h265"),
            "h264": ("h264",),
            "h265": ("h265",),
            "original": ("original",),
        }[quality]
        for variant_key in order:
            matching = [candidate for candidate in candidates if str(candidate.get("variant_key")) == variant_key]
            if matching:
                return max(matching, key=_quality_sort_key)
        return None

    if quality != "highest":
        raise ValueError("invalid_quality")
    has_measured_quality = any(_has_measured_quality(candidate) for candidate in candidates)
    if not has_measured_quality:
        for variant_key in VARIANT_ORDER:
            matching = [candidate for candidate in candidates if str(candidate.get("variant_key")) == variant_key]
            if matching:
                return matching[0]
        return candidates[0]
    return max(candidates, key=_quality_sort_key)


def public_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        key: candidate.get(key)
        for key in (
            "variant_key",
            "codec",
            "width",
            "height",
            "bitrate",
            "frame_rate",
            "file_size",
            "quality_score",
            "duration_seconds",
        )
        if candidate.get(key) is not None
    }


def resolution_fingerprint(source_url: str, resolved: dict[str, Any]) -> str:
    content_id = _text(resolved.get("content_id"), limit=300) or source_url
    selected = resolved.get("selected_variant") if isinstance(resolved.get("selected_variant"), dict) else {}
    variant_key = _text(selected.get("variant_key") or resolved.get("quality"), limit=80) or "default"
    identity = f"wechat-channels\n{content_id}\n{variant_key}"
    return "wechat-channels:" + sha256(identity.encode("utf-8")).hexdigest()


def _unwrap_data(body: dict[str, Any]) -> dict[str, Any]:
    current = body
    for _ in range(3):
        nested = current.get("data")
        if not isinstance(nested, dict):
            break
        if any(key in current for key in ("feedInfo", "authorInfo", "sceneInfo")):
            break
        current = nested
    return current


def _best_spec(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not isinstance(value, list):
        return {}
    specs = [item for item in value if isinstance(item, dict)]
    if not specs:
        return {}
    return max(
        specs,
        key=lambda item: (
            (_positive_int(item.get("width")) or 0) * (_positive_int(item.get("height")) or 0),
            _positive_int(item.get("videoBitrate") or item.get("bitRate")) or 0,
            _positive_number(item.get("qualityScore")) or 0,
        ),
    )


def _quality_sort_key(candidate: dict[str, Any]) -> tuple[float, ...]:
    width = float(candidate.get("width") or 0)
    height = float(candidate.get("height") or 0)
    fallback = float(
        {"original": 4, "h265": 3, "h264": 2, "default": 1}.get(
            str(candidate.get("variant_key")),
            0,
        )
    )
    return (
        1.0 if _has_measured_quality(candidate) else 0.0,
        width * height,
        height,
        float(candidate.get("quality_score") or 0),
        float(candidate.get("bitrate") or 0),
        float(candidate.get("frame_rate") or 0),
        float(candidate.get("file_size") or 0),
        fallback,
    )


def _has_measured_quality(candidate: dict[str, Any]) -> bool:
    return any(candidate.get(key) is not None for key in ("width", "height", "bitrate", "quality_score", "file_size"))


def _variant_key(codec: str | None, item: dict[str, Any], spec: dict[str, Any], index: int) -> str:
    normalized_codec = str(codec or "").casefold().replace(".", "")
    if "265" in normalized_codec or "hevc" in normalized_codec:
        return "h265"
    if "264" in normalized_codec or "avc" in normalized_codec:
        return "h264"
    height = _positive_int(item.get("height") or spec.get("height"))
    return f"{height}p" if height else f"media-{index}"


def _duration_seconds(value: dict[str, Any]) -> float | None:
    for key in (
        "duration_seconds",
        "duration",
        "mediaDuration",
        "videoDuration",
        "playDuration",
        "videoPlayLen",
    ):
        number = _positive_number(value.get(key))
        if number is not None:
            return number / 1000 if number > 10_000 else number
    for key in ("duration_ms", "durationMs", "media_duration", "video_duration", "play_duration"):
        number = _positive_number(value.get(key))
        if number is not None:
            return number / 1000
    return None


def _first_text(value: dict[str, Any], keys: tuple[str, ...], *, limit: int) -> str | None:
    for key in keys:
        text = _text(value.get(key), limit=limit)
        if text:
            return text
    return None


def _text(value: Any, *, limit: int) -> str | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (str, int, float)):
        text = str(value).strip()
        return text[:limit] if text else None
    return None


def _positive_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def _positive_int(value: Any) -> int | None:
    number = _positive_number(value)
    return int(number) if number is not None else None


def _flexible_scalar(value: Any) -> str | int | float | None:
    if isinstance(value, bool) or value is None:
        return None
    return value if isinstance(value, (str, int, float)) else None
