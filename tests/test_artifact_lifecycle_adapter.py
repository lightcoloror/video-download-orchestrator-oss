from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from video_orchestrator.runtime.artifact_lifecycle import ArtifactLifecycleAdapterError
from video_orchestrator.runtime.artifact_lifecycle import RootDeclaration
from video_orchestrator.runtime.artifact_lifecycle import DEFAULT_CONFIG
from video_orchestrator.runtime.artifact_lifecycle import load_adapter_config
from video_orchestrator.runtime.artifact_lifecycle import load_core
from video_orchestrator.runtime.artifact_lifecycle import media_operation_preflight
from video_orchestrator.runtime.artifact_lifecycle import plan_gc
from video_orchestrator.runtime.artifact_lifecycle import restore_synthetic
from video_orchestrator.runtime.artifact_lifecycle import scan_media_root
from video_orchestrator.runtime.execution import execute_manifest


def _write(path: Path, content: bytes = b"fixture") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def _sidecar(path: Path, **overrides) -> None:
    payload = {
        "depends_on": [],
        "referenced_by": [],
        "reference_status": "verified_none",
        "minimum_keep_until": None,
        **overrides,
    }
    path.with_name(f"{path.name}.lifecycle.json").write_text(json.dumps(payload), encoding="utf-8")


def _scan(tmp_path: Path, files: dict[str, bytes], closure_run_id: str = "closure-fixture"):
    root = tmp_path / "artifacts"
    control = tmp_path / "control"
    root.mkdir()
    control.mkdir()
    paths = {relative: _write(root / relative, content) for relative, content in files.items()}
    declaration = RootDeclaration("synthetic-media", root, max_depth=6, max_items=100)
    output = control / "manifest.json"
    checkpoint = control / "checkpoint.sqlite3"
    return root, declaration, output, checkpoint, paths


def _by_path(manifest: dict) -> dict[str, dict]:
    return {item["path"]: item for item in manifest["artifacts"]}


def test_scan_classifies_only_declared_candidates_and_protects_authoritative_media(tmp_path):
    root, declaration, output, checkpoint, paths = _scan(
        tmp_path,
        {
            "failed/video.mp4.part": b"failed",
            "segments/chunk.m4s": b"fragment",
            "original.mp4": b"original",
            "final/final.mp4": b"final",
            ".vdo/manifests/task.json": b"{}",
            "browser-profile/cookie.json": b"{}",
            "active/tasks.sqlite": b"sqlite",
        },
    )
    _sidecar(paths["failed/video.mp4.part"])
    _sidecar(paths["segments/chunk.m4s"])
    before = {path: path.read_bytes() for path in paths.values()}

    manifest = scan_media_root(
        declaration,
        output=output,
        checkpoint=checkpoint,
        closure_run_id="closure-fixture",
    )

    items = _by_path(manifest)
    assert manifest["profile_id"] == "media"
    assert manifest["cleanup_mode"] == "plan_only"
    assert manifest["automatic_purge"] is False
    assert items["failed/video.mp4.part"]["adapter_evidence"]["category"] == "failed_download"
    assert items["segments/chunk.m4s"]["adapter_evidence"]["category"] == "temporary_fragment"
    assert items["original.mp4"]["artifact_role"] == "authoritative_input"
    assert items["original.mp4"]["pin"] is True
    assert items["final/final.mp4"]["artifact_role"] == "final_deliverable"
    assert items[".vdo/manifests/task.json"]["artifact_role"] == "evidence_receipt_manifest"
    assert items["browser-profile/cookie.json"]["artifact_role"] == "secret_or_account_state"
    assert items["active/tasks.sqlite"]["artifact_role"] == "runtime_state"
    for item in manifest["artifacts"]:
        for field in (
            "closure_run_id", "producer_tool_id", "owner_lane", "digest", "artifact_role",
            "depends_on", "referenced_by", "rebuild_proof", "minimum_keep_until", "sensitivity", "pin",
        ):
            assert field in item
    assert {path: path.read_bytes() for path in paths.values()} == before
    assert manifest["filesystem_mutation_performed"] is False


def test_scan_same_closure_is_incremental_and_idempotent(tmp_path, monkeypatch):
    root, declaration, output, checkpoint, _ = _scan(tmp_path, {"failed/video.mp4.part": b"failed"})
    _sidecar(root / "failed/video.mp4.part")
    first = scan_media_root(declaration, output=output, checkpoint=checkpoint, closure_run_id="same-run")
    core = load_core()

    def unexpected_rescan(*args, **kwargs):
        raise AssertionError("unchanged incremental scan should reuse the verified manifest")

    monkeypatch.setattr(core, "scan_root", unexpected_rescan)
    second = scan_media_root(declaration, output=output, checkpoint=checkpoint, closure_run_id="same-run")

    assert second == first
    assert second["manifest_sha256"] == first["manifest_sha256"]


def test_scan_fails_closed_on_depth_limit_and_path_drift(tmp_path):
    root, declaration, output, checkpoint, _ = _scan(tmp_path, {"a/b/c/video.part": b"x"})
    shallow = RootDeclaration("synthetic-media", root, max_depth=1, max_items=100)
    with pytest.raises(ArtifactLifecycleAdapterError, match="SCAN_DEPTH_LIMIT_REACHED"):
        scan_media_root(shallow, output=output, checkpoint=checkpoint, closure_run_id="depth")

    _write(root / "failed.part", b"x")
    _sidecar(root / "failed.part")
    manifest = scan_media_root(declaration, output=output, checkpoint=checkpoint, closure_run_id="drift")
    other = tmp_path / "other"
    other.mkdir()
    quarantine = tmp_path / "quarantine"
    quarantine.mkdir()
    with pytest.raises(ArtifactLifecycleAdapterError, match="ROOT_IDENTITY_DRIFT"):
        plan_gc(
            output,
            RootDeclaration("synthetic-media", other, 4, 100),
            quarantine_root=quarantine,
            output=tmp_path / "plan.json",
            owner_state="idle_confirmed",
            dispatch_state="none_active",
            process_probe={"state": "idle_confirmed", "active": [], "error": None},
        )
    assert manifest["root_identity"] != load_core().directory_identity(other)


def test_scan_delegates_reparse_and_cross_volume_checks_to_core(tmp_path, monkeypatch):
    root, declaration, output, checkpoint, paths = _scan(tmp_path, {"foreign.part": b"x"})
    _sidecar(paths["foreign.part"])
    core = load_core()
    original_volume_identity = core.volume_identity
    root_volume = original_volume_identity(root)

    monkeypatch.setattr(
        core,
        "volume_identity",
        lambda path: root_volume + 1 if Path(path).name == "foreign.part" else original_volume_identity(Path(path)),
    )
    with pytest.raises(ArtifactLifecycleAdapterError, match="CROSS_VOLUME_PATH_BLOCKED"):
        scan_media_root(declaration, output=output, checkpoint=checkpoint, closure_run_id="cross-volume")

    monkeypatch.setattr(core, "volume_identity", original_volume_identity)
    original_reparse_check = core.assert_no_reparse

    def reject_fixture(path, scan_root):
        if Path(path).name == "foreign.part":
            raise core.LifecycleError("REPARSE_POINT_BLOCKED", str(path))
        return original_reparse_check(Path(path), Path(scan_root))

    monkeypatch.setattr(core, "assert_no_reparse", reject_fixture)
    with pytest.raises(core.LifecycleError, match="REPARSE_POINT_BLOCKED"):
        scan_media_root(declaration, output=output, checkpoint=checkpoint, closure_run_id="reparse")


def test_plan_allows_failed_temp_candidate_but_protects_original_and_unknown_references(tmp_path):
    root, declaration, manifest_path, checkpoint, paths = _scan(
        tmp_path,
        {"failed/video.mp4.part": b"failed", "original.mp4": b"original", "cache/unknown.tmp": b"cache"},
    )
    _sidecar(paths["failed/video.mp4.part"])
    scan_media_root(declaration, output=manifest_path, checkpoint=checkpoint, closure_run_id="plan")
    quarantine = tmp_path / "quarantine"
    quarantine.mkdir()

    plan = plan_gc(
        manifest_path,
        declaration,
        quarantine_root=quarantine,
        output=tmp_path / "plan.json",
        owner_state="idle_confirmed",
        dispatch_state="none_active",
        process_probe={"state": "idle_confirmed", "active": [], "error": None},
    )

    entries = {Path(item["source_path"]).relative_to(root).as_posix(): item for item in plan["entries"]}
    assert entries["failed/video.mp4.part"]["eligible"] is True
    assert entries["original.mp4"]["eligible"] is False
    assert "adapter_not_gc_candidate" in entries["original.mp4"]["blocking_gates"]
    assert entries["cache/unknown.tmp"]["eligible"] is False
    assert "references_unknown" in entries["cache/unknown.tmp"]["blocking_gates"]
    assert plan["cleanup_mode"] == "plan_only"
    assert plan["filesystem_mutation_performed"] is False


def test_plan_blocks_active_process_and_unclosed_dispatch(tmp_path):
    root, declaration, manifest_path, checkpoint, paths = _scan(tmp_path, {"failed/video.part": b"failed"})
    _sidecar(paths["failed/video.part"])
    scan_media_root(declaration, output=manifest_path, checkpoint=checkpoint, closure_run_id="process")
    quarantine = tmp_path / "quarantine"
    quarantine.mkdir()

    plan = plan_gc(
        manifest_path,
        declaration,
        quarantine_root=quarantine,
        output=tmp_path / "plan.json",
        owner_state="unknown",
        dispatch_state="active",
        process_probe={"state": "active", "active": [{"pid": 42, "name": "ffmpeg"}], "error": None},
    )

    blockers = plan["entries"][0]["blocking_gates"]
    assert "active_process_not_cleared" in blockers
    assert "dispatch_not_cleared" in blockers
    assert "owner_not_idle_confirmed" in blockers
    assert plan["entries"][0]["eligible"] is False


def test_plan_requires_rebuild_proof_and_complete_hardlink_coverage(tmp_path):
    root, declaration, manifest_path, checkpoint, paths = _scan(tmp_path, {"remux/output.mp4": b"derived"})
    _sidecar(paths["remux/output.mp4"], reference_status="verified_none")
    outside_link = tmp_path / "outside-hardlink.mp4"
    try:
        os.link(paths["remux/output.mp4"], outside_link)
    except OSError:
        pytest.skip("hardlinks are unavailable in this test environment")
    scan_media_root(declaration, output=manifest_path, checkpoint=checkpoint, closure_run_id="hardlink")
    quarantine = tmp_path / "quarantine"
    quarantine.mkdir()

    plan = plan_gc(
        manifest_path,
        declaration,
        quarantine_root=quarantine,
        output=tmp_path / "plan.json",
        owner_state="idle_confirmed",
        dispatch_state="none_active",
        process_probe={"state": "idle_confirmed", "active": [], "error": None},
    )

    blockers = next(item for item in plan["entries"] if item["source_path"].endswith("output.mp4"))["blocking_gates"]
    assert "rebuild_proof_incomplete" in blockers
    assert "hardlink_coverage_incomplete" in blockers


def test_output_aware_disk_policy_allows_small_declared_output_and_blocks_large_or_unknown(monkeypatch, tmp_path):
    core = load_core()

    def two_gib_status(
        path,
        minimum_ratio=None,
        *,
        minimum_free_bytes,
        warning_free_bytes,
        estimated_output_bytes,
        output_headroom_multiplier,
    ):
        free_bytes = 2 * 1024**3
        required_free_bytes = minimum_free_bytes + int(estimated_output_bytes * output_headroom_multiplier)
        blocked = free_bytes < required_free_bytes
        warned = not blocked and free_bytes < warning_free_bytes
        return {
            "status": "blocked_low_disk" if blocked else ("low_disk_allowed" if warned else "ok"),
            "policy_mode": "absolute_reserve_plus_estimated_output",
            "free_ratio": 0.02,
            "minimum_free_ratio": minimum_ratio,
            "total_bytes": 100 * 1024**3,
            "free_bytes": free_bytes,
            "minimum_free_bytes": minimum_free_bytes,
            "warning_free_bytes": warning_free_bytes,
            "estimated_output_bytes": estimated_output_bytes,
            "output_headroom_multiplier": output_headroom_multiplier,
            "output_reserve_bytes": int(estimated_output_bytes * output_headroom_multiplier),
            "required_free_bytes": required_free_bytes,
            "free_after_estimated_output_bytes": free_bytes - estimated_output_bytes,
            "warning_only": warned,
        }

    monkeypatch.setattr(
        core,
        "free_space_status",
        two_gib_status,
    )

    small = media_operation_preflight(
        tmp_path,
        operation="download",
        expected_duration_seconds=30,
        estimated_output_bytes=1024**3,
    )
    assert small["status"] == "warning_only"
    assert small["allowed"] is True
    assert small["disk"]["required_free_bytes"] == 1792 * 1024**2

    large = media_operation_preflight(
        tmp_path,
        operation="download",
        estimated_output_bytes=2 * 1024**3,
    )
    assert large["status"] == "blocked_low_disk"
    assert large["allowed"] is False
    assert large["disk"]["required_free_bytes"] == 3 * 1024**3

    missing = media_operation_preflight(tmp_path, operation="download")
    assert missing["status"] == "blocked_output_estimate_required"
    assert missing["estimate_state"] == "required_missing"
    assert media_operation_preflight(tmp_path, operation="transcode")["status"] == "blocked_output_estimate_required"


def test_custom_config_cannot_restore_ratio_or_change_shared_disk_policy(tmp_path):
    config = json.loads(DEFAULT_CONFIG.read_text(encoding="utf-8"))
    legacy_ratio = tmp_path / "legacy-ratio.json"
    config["minimum_free_ratio"] = 0.15
    legacy_ratio.write_text(json.dumps(config), encoding="utf-8")
    with pytest.raises(ArtifactLifecycleAdapterError, match="LEGACY_RATIO_POLICY_REJECTED"):
        load_adapter_config(legacy_ratio)

    for field, value in (
        ("minimum_free_bytes", 1),
        ("warning_free_bytes", 2 * 1024**3),
        ("output_headroom_multiplier", 1.0),
    ):
        config = json.loads(DEFAULT_CONFIG.read_text(encoding="utf-8"))
        config[field] = value
        unsafe_disk = tmp_path / f"unsafe-{field}.json"
        unsafe_disk.write_text(json.dumps(config), encoding="utf-8")
        with pytest.raises(ArtifactLifecycleAdapterError, match="DISK_POLICY_NOT_SHARED"):
            load_adapter_config(unsafe_disk)

    config = json.loads(DEFAULT_CONFIG.read_text(encoding="utf-8"))
    unsafe_scan = tmp_path / "unsafe-scan.json"
    config["artifact_roots"][0]["max_items"] = 10_001
    unsafe_scan.write_text(json.dumps(config), encoding="utf-8")
    with pytest.raises(ArtifactLifecycleAdapterError, match="ARTIFACT_ROOT_LIMITS_UNSAFE"):
        load_adapter_config(unsafe_scan)


def test_execution_preflight_uses_manifest_or_sniffed_output_estimate(monkeypatch, tmp_path):
    import video_orchestrator.runtime.execution as execution

    calls = []

    def capture(output_dir, **kwargs):
        calls.append(kwargs)
        return {"status": "ok", "filesystem_mutation_performed": False}

    monkeypatch.setattr(execution, "media_operation_preflight", capture)
    direct = execution._download_space_preflight(
        {"estimated_output_bytes": 1234, "expected_duration_seconds": 900},
        tmp_path,
    )
    sniffed = execution._download_space_preflight(
        {
            "sniffed_media": {
                "completeness": {"duration_seconds": 700, "expected_size_bytes": 5678},
            }
        },
        tmp_path,
    )

    assert calls[0]["estimated_output_bytes"] == 1234
    assert direct["estimated_output_source"] == "manifest.estimated_output_bytes"
    assert calls[1]["estimated_output_bytes"] == 5678
    assert calls[1]["expected_duration_seconds"] == 700
    assert sniffed["estimated_output_source"] == "sniffed_media.completeness.expected_size_bytes"


@pytest.mark.parametrize("blocked_status", ["blocked_low_disk", "blocked_output_estimate_required"])
def test_execute_manifest_blocks_before_task_creation_or_backend_run(monkeypatch, tmp_path, blocked_status):
    import video_orchestrator.runtime.execution as execution

    monkeypatch.setattr(
        execution,
        "_download_space_preflight",
        lambda manifest, artifact_base_dir: {
            "status": blocked_status,
            "allowed": False,
            "next_action": "satisfy preflight",
            "filesystem_mutation_performed": False,
        },
    )
    monkeypatch.setattr(execution, "run_command", lambda *args, **kwargs: pytest.fail("backend must not run"))

    result = execute_manifest(
        store=object(),
        manifest={"url": "https://example.invalid/video", "selected_backend": "yt-dlp-api"},
        artifact_base_dir=tmp_path / "downloads",
        manifest_path=tmp_path / "manifest.json",
    )

    assert result["status"] == blocked_status
    assert result["will_download"] is False
    assert result["task"] is None


def test_restore_is_limited_to_marked_synthetic_fixture(tmp_path):
    core = load_core()
    root = tmp_path / "synthetic-root"
    quarantine = tmp_path / "synthetic-quarantine"
    root.mkdir()
    quarantine.mkdir()
    (root / ".vdo-artifact-lifecycle-synthetic").write_text("fixture", encoding="ascii")
    original = root / "failed.part"
    quarantined = quarantine / "manifest" / "artifact" / "failed.part"
    _write(quarantined, b"restore-fixture")
    digest = core.sha256_file(quarantined)
    plan = core.attach_hash(
        {
            "schema_version": "artifact-lifecycle-operation-plan.v1",
            "cleanup_mode": "plan_only",
            "automatic_purge": False,
            "authorization_state": "not_authorized",
            "entries": [],
        },
        "plan_sha256",
    )
    receipt = core.attach_hash(
        {
            "schema_version": "artifact-lifecycle-operation-receipt.v1",
            "operation": "quarantine",
            "status": "completed",
            "plan_sha256": plan["plan_sha256"],
            "items": [
                {
                    "artifact_id": "artifact",
                    "from": str(original),
                    "to": str(quarantined),
                    "digest": {"algorithm": "sha256", "value": digest},
                }
            ],
        },
        "receipt_sha256",
    )
    plan_path = tmp_path / "plan.json"
    receipt_path = tmp_path / "receipt.json"
    core.atomic_write_json(plan_path, plan)
    core.atomic_write_json(receipt_path, receipt)

    result = restore_synthetic(
        synthetic_root=root,
        quarantine_root=quarantine,
        plan_path=plan_path,
        receipt_path=receipt_path,
        output=tmp_path / "restore-receipt.json",
    )

    assert result["synthetic_only"] is True
    assert original.read_bytes() == b"restore-fixture"
    assert not quarantined.exists()


def test_restore_without_synthetic_marker_fails_closed(tmp_path):
    root = tmp_path / "root"
    quarantine = tmp_path / "quarantine"
    root.mkdir()
    quarantine.mkdir()
    with pytest.raises(ArtifactLifecycleAdapterError, match="SYNTHETIC_RESTORE_MARKER_REQUIRED"):
        restore_synthetic(
            synthetic_root=root,
            quarantine_root=quarantine,
            plan_path=tmp_path / "missing-plan.json",
            receipt_path=tmp_path / "missing-receipt.json",
            output=tmp_path / "restore.json",
        )


def test_restore_cannot_escape_declared_synthetic_fixture_boundary(monkeypatch, tmp_path):
    fixture_boundary = tmp_path / "allowed-fixtures"
    root = tmp_path / "outside-root"
    quarantine = tmp_path / "outside-quarantine"
    fixture_boundary.mkdir()
    root.mkdir()
    quarantine.mkdir()
    (root / ".vdo-artifact-lifecycle-synthetic").write_text("fixture", encoding="ascii")
    plan_path = tmp_path / "plan.json"
    receipt_path = tmp_path / "receipt.json"
    plan_path.write_text("{}", encoding="ascii")
    receipt_path.write_text("{}", encoding="ascii")
    monkeypatch.setenv("TEST_TMP_ROOT", str(fixture_boundary))

    with pytest.raises(ArtifactLifecycleAdapterError, match="SYNTHETIC_RESTORE_OUTSIDE_FIXTURE_BOUNDARY"):
        restore_synthetic(
            synthetic_root=root,
            quarantine_root=quarantine,
            plan_path=plan_path,
            receipt_path=receipt_path,
            output=tmp_path / "restore.json",
        )
