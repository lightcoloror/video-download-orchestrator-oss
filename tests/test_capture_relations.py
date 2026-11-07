from video_orchestrator.runtime.capture_relations import merge_master_variant_completeness
from video_orchestrator.runtime.capture_relations import relation_diagnostic
from video_orchestrator.runtime.capture_relations import validate_master_variant_relation


def _capture(capture_id: str, kind: str, *, group: str = "group-a", risk: str = "likely_complete") -> dict:
    probe = {
        "playlist_kind": kind,
        "risk": risk,
        "risk_reasons": [],
        "segment_count": 3 if kind == "media" else 0,
        "duration_seconds": 90 if kind == "media" else None,
        "has_endlist": kind == "media",
    }
    if kind == "master":
        probe.update(
            {
                "variant_count": 2,
                "selected_variant_index": 1,
                "selected_variant": {
                    "index": 1,
                    "width": 1920,
                    "height": 1080,
                    "bandwidth": 3_000_000,
                    "frame_rate": 30,
                    "codecs": "avc1.640028,mp4a.40.2",
                },
                "follow_up_status": "cross_origin_waiting",
            }
        )
    return {"capture_id": capture_id, "capture_group_id": group, "manifest_probe": probe}


def test_relation_requires_same_capture_group_and_hls_shapes():
    parent = _capture("parent", "master")
    child = _capture("child", "media")
    assert validate_master_variant_relation(parent, child)["status"] == "verified"
    assert validate_master_variant_relation(parent, _capture("other", "media", group="group-b"))["reason"] == "capture_group_mismatch"
    assert validate_master_variant_relation(_capture("not-master", "media"), child)["reason"] == "parent_not_master"
    assert validate_master_variant_relation(parent, _capture("not-media", "master"))["reason"] == "child_not_media_playlist"


def test_merge_uses_media_completeness_and_safe_master_quality():
    merged = merge_master_variant_completeness(_capture("parent", "master"), _capture("child", "media"))
    assert merged["playlist_kind"] == "media"
    assert merged["duration_seconds"] == 90
    assert merged["has_endlist"] is True
    assert merged["selected_variant"]["width"] == 1920
    assert merged["capture_relation"]["status"] == "verified"
    assert "url" not in str(merged).lower()


def test_missing_parent_returns_nonfatal_chinese_diagnostic():
    diagnostic = relation_diagnostic(None, _capture("child", "media"))
    assert diagnostic["status"] == "rejected"
    assert diagnostic["reason"] == "parent_not_found"
    assert "独立候选" in diagnostic["label"]
