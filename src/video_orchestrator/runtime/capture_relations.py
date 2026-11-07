from __future__ import annotations

from typing import Any


RELATION_KIND = "master_variant_natural_request"


def validate_master_variant_relation(parent: dict[str, Any], child: dict[str, Any]) -> dict[str, Any]:
    parent_id = str(parent.get("capture_id") or "")
    child_id = str(child.get("capture_id") or "")
    base = {
        "status": "rejected",
        "kind": RELATION_KIND,
        "parent_capture_id": parent_id or None,
        "child_capture_id": child_id or None,
    }
    if not parent_id or not child_id:
        return {**base, "reason": "missing_capture_id", "label": "捕获关系缺少编号"}
    if parent_id == child_id:
        return {**base, "reason": "same_capture", "label": "master 与 variant 不能是同一捕获"}
    if not _same_capture_group(parent, child):
        return {**base, "reason": "capture_group_mismatch", "label": "捕获不属于同一页面会话或标签页"}

    master_probe = _master_probe(_probe(parent))
    if master_probe.get("playlist_kind") != "master":
        return {**base, "reason": "parent_not_master", "label": "父捕获不是 HLS master playlist"}
    selected_variant = master_probe.get("selected_variant")
    if not isinstance(selected_variant, dict) or selected_variant.get("index") is None:
        return {**base, "reason": "parent_variant_not_selected", "label": "master 尚未选出视频 variant"}

    child_probe = _probe(child)
    if child_probe.get("playlist_kind") != "media" or int(child_probe.get("segment_count") or 0) <= 0:
        return {**base, "reason": "child_not_media_playlist", "label": "自然捕获结果不是有效 HLS media playlist"}

    return {
        **base,
        "status": "verified",
        "reason": "verified",
        "label": "已关联播放器自然请求的最高质量 variant",
        "selection_reason": "已关联播放器自然请求的最高质量 variant",
        "selected_variant": _safe_variant(selected_variant),
    }


def merge_master_variant_completeness(parent: dict[str, Any], child: dict[str, Any]) -> dict[str, Any]:
    relation = validate_master_variant_relation(parent, child)
    child_probe = dict(_probe(child))
    if relation["status"] != "verified":
        return child_probe
    master_probe = _master_probe(_probe(parent))
    return {
        **child_probe,
        "probe_depth": 1,
        "master_playlist": _safe_master(master_probe),
        "selected_variant": relation.get("selected_variant"),
        "follow_up_status": "completed_natural_request",
        "capture_relation": relation,
    }


def relation_diagnostic(parent: dict[str, Any] | None, child: dict[str, Any] | None) -> dict[str, Any]:
    if parent is None:
        return {
            "status": "rejected",
            "kind": RELATION_KIND,
            "reason": "parent_not_found",
            "label": "未找到对应的 master 捕获，当前媒体仍作为独立候选保留",
        }
    if child is None:
        return {
            "status": "pending",
            "kind": RELATION_KIND,
            "parent_capture_id": parent.get("capture_id"),
            "reason": "child_not_captured",
            "label": "等待播放器自然请求跨域 variant",
        }
    return validate_master_variant_relation(parent, child)


def _same_capture_group(parent: dict[str, Any], child: dict[str, Any]) -> bool:
    parent_group = str(parent.get("capture_group_id") or "")
    child_group = str(child.get("capture_group_id") or "")
    return bool(parent_group and parent_group == child_group)


def _probe(capture: dict[str, Any]) -> dict[str, Any]:
    probe = capture.get("manifest_probe")
    return probe if isinstance(probe, dict) else {}


def _master_probe(probe: dict[str, Any]) -> dict[str, Any]:
    master = probe.get("master_playlist")
    return master if isinstance(master, dict) else probe


def _safe_variant(variant: dict[str, Any]) -> dict[str, Any]:
    keys = {"index", "width", "height", "bandwidth", "frame_rate", "codecs", "video_group", "audio_only"}
    return {key: variant.get(key) for key in keys if key in variant}


def _safe_master(master: dict[str, Any]) -> dict[str, Any]:
    keys = {
        "parser",
        "playlist_kind",
        "variant_count",
        "max_bandwidth",
        "max_width",
        "max_height",
        "selected_variant_index",
        "selected_variant",
        "risk",
        "risk_reasons",
        "quality_label",
    }
    result = {key: master.get(key) for key in keys if key in master}
    if isinstance(result.get("selected_variant"), dict):
        result["selected_variant"] = _safe_variant(result["selected_variant"])
    return result
