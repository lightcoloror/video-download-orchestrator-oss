from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qsl, unquote, urlparse

from video_orchestrator.runtime.media_protocols import infer_media_kind
from video_orchestrator.runtime.media_protocols import is_segment_capture

try:
    import m3u8
except ImportError:  # pragma: no cover - fallback protects partially installed environments
    m3u8 = None


PREVIEW_TOKENS = (
    "preview",
    "trial",
    "sample",
    "teaser",
    "试看",
    "試看",
    "预览",
    "previewclip",
)

PRIMARY_MEDIA_HOST_TOKENS = (
    "bilivideo",
    "douyinvod",
    "googlevideo",
    "tiktokcdn",
)

AUXILIARY_MEDIA_TOKENS = (
    "byteeffect",
    "douyinstatic",
    "effectcdn",
    "/effect/",
    "/placeholder/",
)

SIGNED_QUERY_KEYS = {
    "auth_key",
    "deadline",
    "e",
    "expire",
    "expires",
    "signature",
    "sign",
    "token",
    "upsig",
}

CONTENT_RANGE_RE = re.compile(r"^\s*bytes\s+(\d+)-(\d+)/(\d+|\*)\s*$", re.IGNORECASE)


def sniffed_completeness_diagnostic(capture: dict[str, Any]) -> dict[str, Any]:
    url = str(capture.get("url") or "")
    filename = str(capture.get("filename") or "")
    content_range = str(capture.get("content_range") or "")
    response_headers = capture.get("response_headers") if isinstance(capture.get("response_headers"), dict) else {}
    header_range = _header_value(response_headers, "content-range")
    token_hits = _preview_token_hits(" ".join([url, filename]))
    range_diagnostic = content_range_diagnostic(content_range or header_range)
    is_range = bool(range_diagnostic["is_range_response"])
    content_length = _int_or_none(capture.get("content_length") or _header_value(response_headers, "content-length"))
    resource_role = media_resource_role(url)
    lifecycle = signed_url_lifecycle(url)
    media_kind = str(
        capture.get("media_kind")
        or infer_media_kind(url, str(capture.get("content_type") or ""), response_headers.values())
    )

    manifest_probe = capture.get("manifest_probe") if isinstance(capture.get("manifest_probe"), dict) else {}
    probe_risk = str(manifest_probe.get("risk") or "")
    risk_reasons: list[str] = list(manifest_probe.get("risk_reasons") or [])
    if token_hits:
        risk_reasons.append("URL 或文件名包含试看/预览关键词")
    if range_diagnostic["range_kind"] == "partial":
        risk_reasons.append("捕获到 Range 分片，可能只是试看或片段")
    if resource_role == "auxiliary":
        risk_reasons.append("媒体地址疑似页面特效、占位动画或其他辅助素材")
    if lifecycle["expired"]:
        risk_reasons.append("媒体签名地址已经过期")
    if content_length is not None and content_length < 2_000_000 and _looks_like_video_file(url):
        risk_reasons.append("媒体文件体积很小，可能不是完整视频")

    if lifecycle["expired"]:
        risk = "expired_signed_url"
    elif resource_role == "auxiliary":
        risk = "auxiliary_asset"
    elif token_hits or probe_risk == "suspected_preview":
        risk = "suspected_preview"
    elif range_diagnostic["range_kind"] == "partial":
        risk = "partial_capture"
    elif probe_risk == "live_or_unfinished":
        risk = "live_or_unfinished"
    elif _is_segment_url(url):
        risk = "unknown"
    elif content_length is not None and content_length < 2_000_000 and _looks_like_video_file(url):
        risk = "unknown"
    else:
        risk = "likely_complete"
    label = {
        "likely_complete": "未发现明显试看风险",
        "live_or_unfinished": "直播或未结束流，完整性待确认",
        "unknown": "完整性未知",
        "suspected_preview": "疑似试看/片段",
        "partial_capture": "仅捕获到部分字节范围",
        "auxiliary_asset": "疑似页面辅助素材",
        "expired_signed_url": "媒体签名地址已过期",
    }[risk]
    next_action = (
        "媒体签名已经过期。请回到原页面重新播放并捕获新的媒体请求。"
        if risk == "expired_signed_url"
        else "该候选疑似页面特效或占位素材，请选择同页更大的主视频候选。"
        if risk == "auxiliary_asset"
        else "只捕获到部分字节范围。请继续播放或使用该媒体地址发起不带 Range 的完整下载并在下载后验收。"
        if risk == "partial_capture"
        else
        "疑似只捕获到试看或分片。请在已登录/已授权页面播放完整视频后重新嗅探。"
        if risk == "suspected_preview"
        else "该 HLS playlist 没有结束标记，可能是直播或尚未结束。请按直播任务确认。"
        if risk == "live_or_unfinished"
        else "如下载后时长过短，请重新播放完整视频并嗅探。"
        if risk == "unknown"
        else "可以继续下载，但完成后仍需检查时长和可播放性。"
    )
    return {
        "risk": risk,
        "label": label,
        "risk_reasons": risk_reasons,
        "preview_tokens": token_hits,
        "is_range_response": is_range,
        **range_diagnostic,
        "content_length": content_length,
        "expected_size_bytes": range_diagnostic.get("range_total"),
        "resource_role": resource_role,
        "media_kind": media_kind,
        "signed_url": lifecycle,
        "next_action": next_action,
    }


def capture_selection_key(capture: dict[str, Any]) -> tuple[int, int, int, int, str]:
    diagnostic = sniffed_completeness_diagnostic(capture)
    probe = capture.get("manifest_probe") if isinstance(capture.get("manifest_probe"), dict) else {}
    risk = str(probe.get("risk") or diagnostic.get("risk") or "unknown")
    risk_bonus = {
        "likely_complete": 30,
        "live_or_unfinished": 5,
        "unknown": 0,
        "partial_capture": -35,
        "suspected_preview": -50,
        "auxiliary_asset": -100,
        "expired_signed_url": -200,
    }.get(risk, 0)
    playlist_kind = str(probe.get("playlist_kind") or "")
    protocol_bonus = 0
    if playlist_kind == "media" and probe.get("has_endlist"):
        protocol_bonus = 45
    elif playlist_kind == "master" and int(probe.get("variant_count") or 0) > 0:
        protocol_bonus = 25
    elif playlist_kind == "media":
        protocol_bonus = 10
    if is_segment_capture(str(capture.get("url") or ""), capture.get("media_type"), capture.get("category")):
        protocol_bonus -= 100
    duration = int(probe.get("duration_seconds") or _duration_from_capture(capture))
    content_length = int(diagnostic.get("content_length") or capture.get("content_length") or 0)
    range_kind = str(diagnostic.get("range_kind") or "none")
    range_bonus = 25 if range_kind == "complete" else -20 if range_kind == "partial" else 0
    resource_role = str(diagnostic.get("resource_role") or media_resource_role(str(capture.get("url") or "")))
    resource_bonus = 20 if resource_role == "primary" else -80 if resource_role == "auxiliary" else 0
    score = int(capture.get("quality_score") or 0) + risk_bonus + protocol_bonus + range_bonus + resource_bonus
    return score, duration, content_length, int(bool(probe.get("has_endlist"))), str(capture.get("captured_at") or "")


def capture_selection_summary(capture: dict[str, Any]) -> dict[str, Any]:
    probe = capture.get("manifest_probe") if isinstance(capture.get("manifest_probe"), dict) else {}
    diagnostic = sniffed_completeness_diagnostic(capture)
    is_segment = is_segment_capture(str(capture.get("url") or ""), capture.get("media_type"), capture.get("category"))
    risk = str(probe.get("risk") or diagnostic.get("risk") or "unknown")
    if is_segment:
        return {"eligible": False, "reason": "segment_only", "label": "分片仅作诊断，不作为下载候选"}
    if risk == "expired_signed_url":
        return {"eligible": False, "reason": "expired_signed_url", "label": "签名地址已过期，等待重新捕获"}
    if risk == "auxiliary_asset":
        return {"eligible": True, "reason": "auxiliary_asset", "label": "疑似页面辅助素材，优先级已降低"}
    if risk == "partial_capture":
        return {"eligible": True, "reason": "partial_range", "label": "部分 Range 候选，下载后必须验收"}
    if risk == "suspected_preview":
        return {"eligible": True, "reason": "suspected_preview", "label": "疑似试看/片段，优先级已降低"}
    if probe.get("playlist_kind") == "media" and probe.get("has_endlist"):
        return {"eligible": True, "reason": "complete_hls_media", "label": "完整 HLS media playlist"}
    if probe.get("playlist_kind") == "master":
        return {"eligible": True, "reason": "hls_master", "label": "可信 HLS master playlist"}
    if probe.get("playlist_kind") == "media":
        return {"eligible": True, "reason": "live_or_unfinished", "label": "直播或尚未结束的 HLS playlist"}
    if diagnostic.get("range_kind") == "complete":
        return {"eligible": True, "reason": "complete_direct_media", "label": "完整字节范围的直接媒体"}
    return {"eligible": True, "reason": "direct_or_unprobed", "label": "普通媒体候选"}
def downloaded_completeness_check(
    *,
    sniffed_media: dict[str, Any] | None,
    ffprobe_results: list[dict[str, Any]],
) -> dict[str, Any]:
    sniffed_media = sniffed_media or {}
    sniff_diagnostic = (
        sniffed_media.get("completeness")
        if isinstance(sniffed_media.get("completeness"), dict)
        else sniffed_completeness_diagnostic(sniffed_media)
        if sniffed_media
        else {}
    )
    durations = [_duration_from_ffprobe(item) for item in ffprobe_results if item.get("returncode") == 0]
    durations = [duration for duration in durations if duration is not None]
    shortest = min(durations) if durations else None
    risk_reasons = list(sniff_diagnostic.get("risk_reasons") or [])
    status = "ok"
    message = "未发现明显试看或完整性风险"
    expected_duration = _expected_duration(sniffed_media)
    expected_size = _int_or_none(sniff_diagnostic.get("expected_size_bytes"))
    actual_sizes = [_ffprobe_size(item) for item in ffprobe_results if item.get("returncode") == 0]
    actual_sizes = [size for size in actual_sizes if size is not None]
    actual_size = max(actual_sizes) if actual_sizes else None
    valid_probe_count = sum(1 for item in ffprobe_results if item.get("returncode") == 0 and isinstance(item.get("metadata"), dict))
    has_video = any(_ffprobe_has_stream(item, "video") for item in ffprobe_results)
    has_audio = any(_ffprobe_has_stream(item, "audio") for item in ffprobe_results)
    stream_metadata_present = any(_ffprobe_has_stream_metadata(item) for item in ffprobe_results)
    media_type = str(sniffed_media.get("media_type") or "").lower()
    media_kind = str(
        sniffed_media.get("media_kind")
        or infer_media_kind(
            str(sniffed_media.get("url") or ""),
            str(sniffed_media.get("content_type") or ""),
            (sniffed_media.get("response_headers") or {}).values()
            if isinstance(sniffed_media.get("response_headers"), dict)
            else (),
        )
    )
    manifest_probe = sniffed_media.get("manifest_probe") if isinstance(sniffed_media.get("manifest_probe"), dict) else {}
    probed_media_kind = str(manifest_probe.get("playlist_media_kind") or "unknown")
    if media_kind in {"adaptive", "unknown"} and probed_media_kind in {"audio", "mixed", "video"}:
        media_kind = probed_media_kind
    if media_kind in {"adaptive", "unknown"} and stream_metadata_present:
        if has_audio and has_video:
            media_kind = "mixed"
        elif has_audio:
            media_kind = "audio"
        elif has_video:
            media_kind = "video"
    known_audio_types = {"audio", "aac", "flac", "m4a", "m4b", "mp3", "ogg", "opus", "wav", "weba", "wma"}
    expects_audio = media_kind == "audio" or media_type in known_audio_types
    expects_video = not expects_audio and media_kind != "subtitle"

    if ffprobe_results and valid_probe_count == 0:
        status = "failed"
        message = "下载文件无法通过 ffprobe 可播放性检查"
        risk_reasons.append("ffprobe 未能解析任何下载文件")
    elif expects_video and stream_metadata_present and not has_video:
        status = "failed"
        message = "下载文件没有可识别的视频轨道"
        risk_reasons.append("ffprobe 未检测到视频轨道")
    elif expects_audio and stream_metadata_present and not has_audio:
        status = "failed"
        message = "下载文件没有可识别的音频轨道"
        risk_reasons.append("ffprobe 未检测到音频轨道")
    elif expected_size and actual_size is not None and not _sizes_match(actual_size, expected_size):
        status = "failed"
        message = "下载文件大小与媒体总字节数不一致"
        risk_reasons.append(f"下载文件 {actual_size} 字节，预期 {expected_size} 字节")
    elif expected_duration and shortest is not None and shortest < expected_duration * 0.9:
        status = "failed"
        message = "下载后媒体时长明显短于预期时长"
        risk_reasons.append(f"ffprobe 时长 {shortest:.2f} 秒，预期约 {expected_duration:.2f} 秒")
    elif sniff_diagnostic.get("risk") in {"suspected_preview", "auxiliary_asset", "expired_signed_url"}:
        status = "warning"
        message = "嗅探候选疑似试看或片段"
    elif sniff_diagnostic.get("risk") == "partial_capture" and not (expected_size and actual_size == expected_size):
        status = "warning"
        message = "捕获来自部分 Range，下载后尚未证明覆盖完整文件"

    if status == "failed":
        next_action = f"完整性检查失败，请重新捕获主{'音频' if expects_audio else '视频'}后下载。"
    elif status == "warning":
        next_action = f"候选仍有不确定性，请人工播放确认；不完整时请保持登录、播放完整{'音频' if expects_audio else '视频'}后重新嗅探。"
    else:
        next_action = f"人工确认{'音频' if expects_audio else '视频'}时长和内容完整后再标记通过。"

    return {
        "name": "media_completeness",
        "status": status,
        "message": message,
        "quality_label": "完整性检查失败"
        if status == "failed"
        else str(sniff_diagnostic.get("label") or "需要人工确认")
        if status == "warning"
        else "完整性初检通过",
        "risk": "incomplete_download" if status == "failed" else str(sniff_diagnostic.get("risk") or "likely_complete"),
        "risk_reasons": _dedupe(risk_reasons),
        "sniffed_completeness": sniff_diagnostic,
        "ffprobe_duration_seconds": shortest,
        "expected_duration_seconds": expected_duration,
        "expected_size_bytes": expected_size,
        "actual_size_bytes": actual_size,
        "has_video_stream": has_video,
        "has_audio_stream": has_audio,
        "media_kind": media_kind,
        "next_action": next_action,
    }


def content_range_diagnostic(value: str | None) -> dict[str, Any]:
    raw = str(value or "").strip()
    if not raw:
        return {
            "is_range_response": False,
            "range_kind": "none",
            "range_start": None,
            "range_end": None,
            "range_total": None,
            "range_coverage_ratio": None,
        }
    match = CONTENT_RANGE_RE.match(raw)
    if match is None:
        return {
            "is_range_response": True,
            "range_kind": "unknown",
            "range_start": None,
            "range_end": None,
            "range_total": None,
            "range_coverage_ratio": None,
        }
    start = int(match.group(1))
    end = int(match.group(2))
    total = None if match.group(3) == "*" else int(match.group(3))
    span = max(end - start + 1, 0)
    complete = total is not None and total > 0 and start == 0 and end == total - 1
    return {
        "is_range_response": True,
        "range_kind": "complete" if complete else "partial",
        "range_start": start,
        "range_end": end,
        "range_total": total,
        "range_coverage_ratio": span / total if total else None,
    }


def media_resource_role(url: str) -> str:
    parsed = urlparse(url)
    target = f"{parsed.hostname or ''}{parsed.path}".lower()
    if any(token in target for token in AUXILIARY_MEDIA_TOKENS):
        return "auxiliary"
    if any(token in target for token in PRIMARY_MEDIA_HOST_TOKENS):
        return "primary"
    return "unknown"


def signed_url_lifecycle(url: str, *, now: datetime | None = None) -> dict[str, Any]:
    query = {key.lower(): value for key, value in parse_qsl(urlparse(url).query, keep_blank_values=True)}
    signed_keys = sorted(key for key in query if key in SIGNED_QUERY_KEYS or "token" in key or "sign" in key)
    expires_at = None
    for key in ("deadline", "expires", "expire", "e"):
        value = query.get(key)
        if not value or not value.isdigit():
            continue
        timestamp = int(value)
        if timestamp < 1_000_000_000:
            continue
        try:
            expires_at = datetime.fromtimestamp(timestamp, tz=UTC)
        except (OverflowError, OSError, ValueError):
            continue
        break
    current = now or datetime.now(UTC)
    return {
        "signed_url_likely": bool(signed_keys),
        "expires_at": expires_at.isoformat() if expires_at else None,
        "expired": bool(expires_at and expires_at <= current),
    }


def parse_hls_manifest_text(text: str) -> dict[str, Any]:
    if m3u8 is None:
        return _parse_hls_manifest_fallback(text, parser="fallback_missing_dependency")
    try:
        playlist = m3u8.loads(text)
    except Exception:
        return _parse_hls_manifest_fallback(text, parser="fallback_parse_error")

    segments = list(getattr(playlist, "segments", []) or [])
    variants = list(getattr(playlist, "playlists", []) or [])
    duration = sum(float(getattr(segment, "duration", 0) or 0) for segment in segments)
    variant_summaries = [_variant_summary(index, variant) for index, variant in enumerate(variants)]
    selected_variant = _select_preferred_variant(variant_summaries)
    widths = [int(item["width"]) for item in variant_summaries if item.get("width") is not None]
    heights = [int(item["height"]) for item in variant_summaries if item.get("height") is not None]
    bandwidths = [int(item["bandwidth"]) for item in variant_summaries if item.get("bandwidth") is not None]
    has_encryption = any(
        bool(getattr(getattr(segment, "key", None), "method", None))
        and str(getattr(getattr(segment, "key", None), "method", "")).upper() != "NONE"
        for segment in segments
    )
    has_byterange = any(bool(getattr(segment, "byterange", None)) for segment in segments)
    discontinuity_count = sum(1 for segment in segments if bool(getattr(segment, "discontinuity", False)))
    playlist_kind = "master" if variants else "media" if segments else "unknown"
    has_endlist = bool(getattr(playlist, "is_endlist", False))
    risk, risk_reasons = _hls_risk(playlist_kind, has_endlist, duration, len(segments))
    return {
        "parser": "m3u8",
        "playlist_kind": playlist_kind,
        "has_endlist": has_endlist,
        "is_master_playlist": playlist_kind == "master",
        "segment_count": len(segments),
        "duration_seconds": duration if segments else None,
        "variant_count": len(variants),
        "playlist_media_kind": _variant_media_kind(variant_summaries),
        "max_bandwidth": max(bandwidths) if bandwidths else None,
        "max_width": max(widths) if widths else None,
        "max_height": max(heights) if heights else None,
        "selected_variant_index": selected_variant.get("index") if selected_variant else None,
        "selected_variant": selected_variant,
        "selected_variant_reason": _selected_variant_reason(selected_variant),
        "has_encryption": has_encryption,
        "has_byterange": has_byterange,
        "discontinuity_count": discontinuity_count,
        "risk": risk,
        "risk_reasons": risk_reasons,
        "quality_label": _hls_quality_label(risk),
    }


def _variant_summary(index: int, variant: Any) -> dict[str, Any]:
    info = getattr(variant, "stream_info", None)
    resolution = getattr(info, "resolution", None)
    width = int(resolution[0]) if isinstance(resolution, tuple) and len(resolution) == 2 else None
    height = int(resolution[1]) if isinstance(resolution, tuple) and len(resolution) == 2 else None
    bandwidth = getattr(info, "bandwidth", None) or getattr(info, "average_bandwidth", None)
    frame_rate = _float_or_none(getattr(info, "frame_rate", None))
    codecs = str(getattr(info, "codecs", None) or "") or None
    video_group = str(getattr(info, "video", None) or "") or None
    audio_only = _is_audio_only_variant(width=width, height=height, codecs=codecs, video_group=video_group)
    return {
        "index": index,
        "width": width,
        "height": height,
        "bandwidth": int(bandwidth) if bandwidth is not None else None,
        "frame_rate": frame_rate,
        "codecs": codecs,
        "video_group": video_group,
        "audio_only": audio_only,
        "media_kind": "audio" if audio_only else "video",
    }


def _select_preferred_variant(variants: list[dict[str, Any]]) -> dict[str, Any] | None:
    video_candidates = [item for item in variants if not item.get("audio_only")]
    candidates = video_candidates or [item for item in variants if item.get("audio_only")]
    if not candidates:
        return None
    selected = max(
        candidates,
        key=lambda item: (
            int(item.get("width") or 0) * int(item.get("height") or 0),
            int(item.get("bandwidth") or 0),
            float(item.get("frame_rate") or 0),
            -int(item.get("index") or 0),
        ),
    )
    return dict(selected)


def _variant_media_kind(variants: list[dict[str, Any]]) -> str:
    kinds = {str(item.get("media_kind") or "unknown") for item in variants}
    if kinds == {"audio"}:
        return "audio"
    if "audio" in kinds and "video" in kinds:
        return "mixed"
    if "video" in kinds:
        return "video"
    return "unknown"


def _selected_variant_reason(selected: dict[str, Any] | None) -> str | None:
    if selected is None:
        return None
    if selected.get("audio_only"):
        return "没有视频 variant，已选择码率最高的纯音频 variant。"
    return "已按分辨率、带宽和帧率选择最高质量视频 variant。"


def _is_audio_only_variant(*, width: int | None, height: int | None, codecs: str | None, video_group: str | None) -> bool:
    if width or height or video_group:
        return False
    lowered = str(codecs or "").lower()
    if not lowered:
        return False
    video_tokens = ("avc", "hvc", "hev", "vp8", "vp9", "vp09", "av01", "theora")
    audio_tokens = ("mp4a", "aac", "ac-3", "ec-3", "opus", "vorbis")
    return any(token in lowered for token in audio_tokens) and not any(token in lowered for token in video_tokens)

def _parse_hls_manifest_fallback(text: str, *, parser: str) -> dict[str, Any]:
    durations = []
    has_endlist = "#EXT-X-ENDLIST" in text
    has_stream_inf = "#EXT-X-STREAM-INF" in text
    for match in re.finditer(r"#EXTINF:([0-9.]+)", text):
        try:
            durations.append(float(match.group(1)))
        except ValueError:
            continue
    total = sum(durations)
    playlist_kind = "master" if has_stream_inf else "media" if durations else "unknown"
    risk, risk_reasons = _hls_risk(playlist_kind, has_endlist, total, len(durations))
    return {
        "parser": parser,
        "playlist_kind": playlist_kind,
        "has_endlist": has_endlist,
        "is_master_playlist": has_stream_inf,
        "segment_count": len(durations),
        "duration_seconds": total if durations else None,
        "variant_count": 0,
        "playlist_media_kind": "unknown",
        "max_bandwidth": None,
        "selected_variant_index": None,
        "selected_variant": None,
        "selected_variant_reason": None,
        "max_width": None,
        "max_height": None,
        "has_encryption": "#EXT-X-KEY" in text,
        "has_byterange": "#EXT-X-BYTERANGE" in text,
        "discontinuity_count": text.count("#EXT-X-DISCONTINUITY"),
        "risk": risk,
        "risk_reasons": risk_reasons,
        "quality_label": _hls_quality_label(risk),
    }


def _hls_risk(playlist_kind: str, has_endlist: bool, duration: float, segment_count: int) -> tuple[str, list[str]]:
    if playlist_kind == "media" and segment_count and duration < 60:
        return "suspected_preview", ["HLS 总时长少于 60 秒"]
    if playlist_kind == "media" and not has_endlist:
        return "live_or_unfinished", ["playlist 没有 EXT-X-ENDLIST，可能是直播或尚未结束"]
    if playlist_kind in {"media", "master"}:
        return "likely_complete", []
    return "unknown", ["未识别出有效 HLS segment 或 variant"]


def _hls_quality_label(risk: str) -> str:
    return {
        "likely_complete": "HLS 完整性初检通过",
        "live_or_unfinished": "直播或未结束流，完整性待确认",
        "suspected_preview": "疑似试看/片段",
        "unknown": "HLS 完整性未知",
    }.get(risk, "HLS 完整性未知")
def _duration_from_capture(capture: dict[str, Any]) -> int:
    for key in ("duration_seconds", "expected_duration_seconds"):
        value = _float_or_none(capture.get(key))
        if value is not None:
            return int(value)
    manifest_probe = capture.get("manifest_probe")
    if isinstance(manifest_probe, dict):
        value = _float_or_none(manifest_probe.get("duration_seconds"))
        if value is not None:
            return int(value)
    return 0


def _duration_from_ffprobe(item: dict[str, Any]) -> float | None:
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    fmt = metadata.get("format") if isinstance(metadata.get("format"), dict) else {}
    return _float_or_none(fmt.get("duration"))


def _ffprobe_size(item: dict[str, Any]) -> int | None:
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    fmt = metadata.get("format") if isinstance(metadata.get("format"), dict) else {}
    return _int_or_none(fmt.get("size"))


def _ffprobe_has_stream(item: dict[str, Any], codec_type: str) -> bool:
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    streams = metadata.get("streams") if isinstance(metadata.get("streams"), list) else []
    return any(isinstance(stream, dict) and str(stream.get("codec_type") or "") == codec_type for stream in streams)


def _ffprobe_has_stream_metadata(item: dict[str, Any]) -> bool:
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    return isinstance(metadata.get("streams"), list)


def _expected_duration(sniffed_media: dict[str, Any]) -> float | None:
    for key in ("expected_duration_seconds", "duration_seconds"):
        value = _float_or_none(sniffed_media.get(key))
        if value is not None and value > 0:
            return value
    probe = sniffed_media.get("manifest_probe") if isinstance(sniffed_media.get("manifest_probe"), dict) else {}
    value = _float_or_none(probe.get("duration_seconds"))
    return value if value is not None and value > 0 else None


def _sizes_match(actual: int, expected: int) -> bool:
    tolerance = max(4096, int(expected * 0.01))
    return abs(actual - expected) <= tolerance


def _preview_token_hits(text: str) -> list[str]:
    lowered = unquote(text).lower()
    return [token for token in PREVIEW_TOKENS if token.lower() in lowered]


def _looks_like_video_file(url: str) -> bool:
    return urlparse(url).path.lower().endswith((".mp4", ".m4v", ".webm", ".mov", ".ts"))


def _is_segment_url(url: str) -> bool:
    path = urlparse(url).path.lower()
    return path.endswith((".m4s", ".ts")) and not path.endswith(".m3u8")


def _header_value(headers: dict[str, Any], name: str) -> str:
    lowered = name.lower()
    for key, value in headers.items():
        if str(key).lower() == lowered:
            return str(value)
    return ""


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _dedupe(values: list[str]) -> list[str]:
    result = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return result
