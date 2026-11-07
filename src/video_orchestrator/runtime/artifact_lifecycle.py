from __future__ import annotations

import importlib.util
import importlib
import json
import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import Any

from video_orchestrator.runtime.config import project_root
from video_orchestrator.runtime.config import project_asset
from video_orchestrator.runtime.config import runtime_state_dir


ADAPTER_CONTRACT = "vdo-artifact-lifecycle-adapter.v1"
CORE_ENV_VAR = "VDO_ARTIFACT_LIFECYCLE_CORE"
DEFAULT_CONFIG = project_asset("config/artifact_lifecycle_media.json")
EXPECTED_PRODUCER_TOOL_ID = "video-download-orchestrator"
EXPECTED_OWNER_LANE = "video-download-orchestrator"
EXPECTED_SYNTHETIC_RESTORE_MARKER = ".vdo-artifact-lifecycle-synthetic"
MAX_LONG_MEDIA_THRESHOLD_SECONDS = 600.0
MAX_SCAN_DEPTH = 16
MAX_SCAN_ITEMS = 10_000
REQUIRED_AUTHORITATIVE_PATHS = {
    "**/.vdo/manifests/**",
    "**/.vdo/reports/**",
    "**/review-checklist.json",
    "**/summary.json",
    "**/*.info.json",
    "**/*.description",
    "**/receipts/**",
    "**/approvals/**",
    "**/final/**",
    "**/deliverables/**",
    "**/archive/**",
    "**/成品/**",
}
REQUIRED_CANDIDATE_CATEGORIES = {
    "failed_download",
    "temporary_fragment",
    "rebuildable_remux",
    "proxy_media",
    "duplicate_conversion",
    "cache",
}
REQUIRED_PROTECTED_CATEGORIES = {
    "original_download",
    "successful_archive",
    "source_platform_evidence",
    "human_retained_clip",
    "final_deliverable",
    "account_or_authorization_state",
    "approval_or_operation_receipt",
    "active_run_or_checkpoint",
}
MEDIA_SUFFIXES = {
    ".3gp", ".aac", ".flac", ".flv", ".m4a", ".m4v", ".mkv", ".mov",
    ".mp3", ".mp4", ".mpeg", ".mpg", ".ogg", ".opus", ".ts", ".wav", ".webm",
}
EVIDENCE_SUFFIXES = {".json", ".jsonl", ".md", ".txt", ".log", ".srt", ".vtt", ".ass", ".description"}
FAILED_SUFFIXES = {".part", ".ytdl", ".aria2", ".download", ".crdownload"}
FRAGMENT_SUFFIXES = {".frag", ".m4s", ".segment", ".tmp"}
PROCESS_NAMES = {"aria2c", "ffmpeg", "ffprobe", "lux", "mediago", "n_m3u8dl-re", "streamlink", "yt-dlp", "youtube-dl"}


class ArtifactLifecycleAdapterError(RuntimeError):
    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}" if detail else code)


@dataclass(frozen=True)
class RootDeclaration:
    root_id: str
    path: Path
    max_depth: int
    max_items: int


def _core_path() -> Path:
    override = os.environ.get(CORE_ENV_VAR)
    candidate = Path(override).expanduser() if override else project_root().parent / "artifact-lifecycle-core" / "artifact_lifecycle_core.py"
    return candidate.resolve(strict=True)


@lru_cache(maxsize=2)
def load_core(path: str | None = None):
    if path is None and not os.environ.get(CORE_ENV_VAR):
        try:
            return importlib.import_module("artifact_lifecycle_core")
        except ModuleNotFoundError as exc:
            if exc.name != "artifact_lifecycle_core":
                raise
    core_path = Path(path).resolve(strict=True) if path else _core_path()
    spec = importlib.util.spec_from_file_location("vdo_artifact_lifecycle_core", core_path)
    if spec is None or spec.loader is None:
        raise ArtifactLifecycleAdapterError("CORE_IMPORT_FAILED", str(core_path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_adapter_config(path: Path | None = None) -> dict[str, Any]:
    config_path = (path or DEFAULT_CONFIG).resolve(strict=True)
    try:
        value = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ArtifactLifecycleAdapterError("ADAPTER_CONFIG_INVALID", str(exc)) from exc
    if value.get("contract") != ADAPTER_CONTRACT:
        raise ArtifactLifecycleAdapterError("ADAPTER_CONTRACT_INVALID", str(value.get("contract")))
    if value.get("lifecycle_profile") != "media" or value.get("cleanup_mode") != "plan_only" or value.get("automatic_purge") is not False:
        raise ArtifactLifecycleAdapterError("UNSAFE_LIFECYCLE_POLICY")
    if value.get("producer_tool_id") != EXPECTED_PRODUCER_TOOL_ID or value.get("owner_lane") != EXPECTED_OWNER_LANE:
        raise ArtifactLifecycleAdapterError("ADAPTER_OWNERSHIP_INVALID")
    if value.get("synthetic_restore_marker") != EXPECTED_SYNTHETIC_RESTORE_MARKER:
        raise ArtifactLifecycleAdapterError("SYNTHETIC_RESTORE_MARKER_INVALID")
    if "minimum_free_ratio" in value:
        raise ArtifactLifecycleAdapterError("LEGACY_RATIO_POLICY_REJECTED")
    try:
        minimum_free_bytes = int(value["minimum_free_bytes"])
        warning_free_bytes = int(value["warning_free_bytes"])
        output_headroom_multiplier = float(value["output_headroom_multiplier"])
        long_media_threshold_seconds = float(value["long_media_threshold_seconds"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ArtifactLifecycleAdapterError("MEDIA_SAFETY_GATE_INVALID") from exc
    if any(
        isinstance(value.get(field), bool)
        for field in ("minimum_free_bytes", "warning_free_bytes", "output_headroom_multiplier")
    ):
        raise ArtifactLifecycleAdapterError("MEDIA_SAFETY_GATE_INVALID")
    core = load_core()
    if (
        minimum_free_bytes != int(core.MIN_FREE_BYTES)
        or warning_free_bytes != int(core.WARN_FREE_BYTES)
        or output_headroom_multiplier != float(core.OUTPUT_HEADROOM_MULTIPLIER)
    ):
        raise ArtifactLifecycleAdapterError("DISK_POLICY_NOT_SHARED")
    if not 0.0 < long_media_threshold_seconds <= MAX_LONG_MEDIA_THRESHOLD_SECONDS:
        raise ArtifactLifecycleAdapterError("LONG_MEDIA_THRESHOLD_UNSAFE")
    authoritative_paths = value.get("authoritative_paths")
    if not isinstance(authoritative_paths, list) or any(not isinstance(item, str) or not item for item in authoritative_paths):
        raise ArtifactLifecycleAdapterError("AUTHORITATIVE_PATHS_INVALID")
    if not REQUIRED_AUTHORITATIVE_PATHS.issubset(set(authoritative_paths)):
        raise ArtifactLifecycleAdapterError("AUTHORITATIVE_PATHS_INCOMPLETE")
    candidate_categories = value.get("candidate_categories")
    if (
        not isinstance(candidate_categories, list)
        or any(not isinstance(item, str) for item in candidate_categories)
        or set(candidate_categories) != REQUIRED_CANDIDATE_CATEGORIES
    ):
        raise ArtifactLifecycleAdapterError("CANDIDATE_CATEGORIES_UNSAFE")
    protected_categories = value.get("protected_categories")
    if (
        not isinstance(protected_categories, list)
        or any(not isinstance(item, str) for item in protected_categories)
        or not REQUIRED_PROTECTED_CATEGORIES.issubset(set(protected_categories))
    ):
        raise ArtifactLifecycleAdapterError("PROTECTED_CATEGORIES_INCOMPLETE")
    roots = value.get("artifact_roots")
    if not isinstance(roots, list) or not roots:
        raise ArtifactLifecycleAdapterError("ARTIFACT_ROOTS_REQUIRED")
    root_ids = [str(item.get("root_id") or "") for item in roots if isinstance(item, dict)]
    if not all(root_ids) or len(root_ids) != len(set(root_ids)) or set(root_ids) != {"runtime-state", "project-downloads"}:
        raise ArtifactLifecycleAdapterError("ARTIFACT_ROOT_IDS_INVALID")
    for item in roots:
        if not isinstance(item, dict):
            raise ArtifactLifecycleAdapterError("ARTIFACT_ROOT_INVALID")
        try:
            max_depth = int(item["max_depth"])
            max_items = int(item["max_items"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ArtifactLifecycleAdapterError("ARTIFACT_ROOT_LIMITS_INVALID", str(item.get("root_id") or "")) from exc
        if not 1 <= max_depth <= MAX_SCAN_DEPTH or not 1 <= max_items <= MAX_SCAN_ITEMS:
            raise ArtifactLifecycleAdapterError("ARTIFACT_ROOT_LIMITS_UNSAFE", str(item.get("root_id") or ""))
        root_id = str(item["root_id"])
        if root_id == "runtime-state" and item.get("path_source") != "runtime_state_dir":
            raise ArtifactLifecycleAdapterError("ARTIFACT_ROOT_SOURCE_INVALID", root_id)
        if root_id == "project-downloads" and (
            item.get("path_source") != "project_relative" or Path(str(item.get("path") or "")).as_posix() != "downloads"
        ):
            raise ArtifactLifecycleAdapterError("ARTIFACT_ROOT_PATH_INVALID", root_id)
    return value


def adapter_contract(config_path: Path | None = None) -> dict[str, Any]:
    config = load_adapter_config(config_path)
    core = load_core()
    return {
        "contract": ADAPTER_CONTRACT,
        "producer_tool_id": config["producer_tool_id"],
        "owner_lane": config["owner_lane"],
        "lifecycle_profile": "media",
        "cleanup_mode": "plan_only",
        "automatic_purge": False,
        "minimum_free_bytes": int(config["minimum_free_bytes"]),
        "warning_free_bytes": int(config["warning_free_bytes"]),
        "output_headroom_multiplier": float(config["output_headroom_multiplier"]),
        "artifact_roots": config["artifact_roots"],
        "authoritative_paths": config["authoritative_paths"],
        "candidate_categories": config["candidate_categories"],
        "protected_categories": config["protected_categories"],
        "config_sha256": core.canonical_sha256(config),
    }


def resolve_declared_root(root_id: str, config_path: Path | None = None) -> RootDeclaration:
    config = load_adapter_config(config_path)
    entry = next((item for item in config["artifact_roots"] if item.get("root_id") == root_id), None)
    if entry is None:
        raise ArtifactLifecycleAdapterError("ARTIFACT_ROOT_NOT_DECLARED", root_id)
    source = str(entry.get("path_source") or "")
    if source == "runtime_state_dir":
        path = runtime_state_dir()
    elif source == "project_relative":
        relative = Path(str(entry.get("path") or ""))
        if relative.is_absolute() or ".." in relative.parts:
            raise ArtifactLifecycleAdapterError("ARTIFACT_ROOT_PATH_INVALID", str(relative))
        path = project_root() / relative
    else:
        raise ArtifactLifecycleAdapterError("ARTIFACT_ROOT_SOURCE_INVALID", source)
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise ArtifactLifecycleAdapterError("ARTIFACT_ROOT_UNAVAILABLE", f"{root_id}: {exc}") from exc
    if not resolved.is_dir() or resolved == project_root().resolve():
        raise ArtifactLifecycleAdapterError("ARTIFACT_ROOT_INVALID", str(resolved))
    max_depth = int(entry.get("max_depth") or 0)
    max_items = int(entry.get("max_items") or 0)
    if max_depth < 1 or max_items < 1:
        raise ArtifactLifecycleAdapterError("ARTIFACT_ROOT_LIMITS_INVALID", root_id)
    return RootDeclaration(root_id=root_id, path=resolved, max_depth=max_depth, max_items=max_items)


def _path_is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=True))
        return True
    except (OSError, ValueError):
        return False


def _bounded_snapshot(root: Path, max_depth: int, max_items: int, core: Any) -> dict[str, Any]:
    core.assert_no_reparse(root, root)
    root_volume = core.volume_identity(root)
    stack: list[tuple[Path, int]] = [(root, 0)]
    files: list[dict[str, Any]] = []
    directory_count = 0
    while stack:
        directory, depth = stack.pop()
        directory_count += 1
        core.assert_no_reparse(directory, root)
        try:
            entries = os.scandir(directory)
        except OSError as exc:
            raise ArtifactLifecycleAdapterError("SCAN_PATH_ACCESS_FAILED", f"{directory}: {exc}") from exc
        with entries:
            for entry in entries:
                path = Path(entry.path)
                core.assert_no_reparse(path, root)
                if core.volume_identity(path) != root_volume:
                    raise ArtifactLifecycleAdapterError("CROSS_VOLUME_PATH_BLOCKED", str(path))
                if entry.is_dir(follow_symlinks=False):
                    if depth >= max_depth:
                        raise ArtifactLifecycleAdapterError("SCAN_DEPTH_LIMIT_REACHED", str(path))
                    stack.append((path, depth + 1))
                    continue
                if not entry.is_file(follow_symlinks=False):
                    raise ArtifactLifecycleAdapterError("UNSUPPORTED_FILESYSTEM_ENTRY", str(path))
                info = entry.stat(follow_symlinks=False)
                files.append(
                    {
                        "path": path.relative_to(root).as_posix(),
                        "bytes": int(info.st_size),
                        "mtime_ns": int(info.st_mtime_ns),
                        "volume_id": int(info.st_dev),
                        "file_id": int(info.st_ino),
                        "hardlink_count": int(info.st_nlink),
                    }
                )
                if len(files) > max_items:
                    raise ArtifactLifecycleAdapterError("SCAN_ITEM_LIMIT_EXCEEDED", str(max_items))
    return {
        "file_count": len(files),
        "directory_count": directory_count,
        "fingerprint": core.canonical_sha256(files),
        "files": files,
    }


def _metadata_sidecar(path: Path) -> dict[str, Any]:
    sidecar = path.with_name(f"{path.name}.lifecycle.json")
    if not sidecar.exists():
        return {}
    if sidecar.stat().st_size > 65536:
        raise ArtifactLifecycleAdapterError("SIDECAR_TOO_LARGE", str(sidecar))
    try:
        value = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ArtifactLifecycleAdapterError("SIDECAR_INVALID", f"{sidecar}: {exc}") from exc
    if not isinstance(value, dict):
        raise ArtifactLifecycleAdapterError("SIDECAR_OBJECT_REQUIRED", str(sidecar))
    allowed = {"depends_on", "referenced_by", "reference_status", "rebuild_proof", "minimum_keep_until", "sensitivity", "pin"}
    return {key: value[key] for key in allowed if key in value}


def _matches_authoritative(relative: str, patterns: list[str]) -> bool:
    path = PurePosixPath(relative)
    return any(
        path.match(pattern) or (pattern.startswith("**/") and path.match(pattern[3:]))
        for pattern in patterns
    )


def _classify(relative: str, digest_seen: bool, authoritative_patterns: list[str]) -> tuple[str, str, str, bool, str]:
    lowered = relative.casefold()
    path = PurePosixPath(relative)
    name = path.name.casefold()
    suffix = Path(name).suffix.casefold()
    parts = {part.casefold() for part in path.parts}
    if any(token in lowered for token in ("cookie", "browser-profile", "authorization", "credential", "login-state", "session-token")):
        return "account_or_authorization_state", "secret_or_account_state", "locked", True, "restricted"
    if suffix in {".sqlite", ".sqlite3", ".wal", ".shm", ".lock", ".pid"} or any(token in parts for token in {"active", "checkpoints", "leases"}):
        return "active_run_or_checkpoint", "runtime_state", "locked", True, "local_private"
    if _matches_authoritative(relative, authoritative_patterns):
        role = "final_deliverable" if any(token in parts for token in {"final", "deliverables", "archive", "成品"}) else "evidence_receipt_manifest"
        category = "successful_archive" if role == "final_deliverable" else "source_platform_evidence"
        return category, role, "verified", True, "local_private"
    if any(token in parts for token in {"keep", "retained", "approved-clips", "人工保留"}):
        return "human_retained_clip", "authoritative_input", "locked", True, "local_private"
    if suffix in FAILED_SUFFIXES or ".part-frag" in name:
        return "failed_download", "cache_build_test_temp", "failed", False, "local_private"
    if suffix in FRAGMENT_SUFFIXES or any(token in parts for token in {"fragments", "segments", "temp", "tmp"}):
        return "temporary_fragment", "cache_build_test_temp", "superseded", False, "local_private"
    if any(token in parts for token in {"cache", ".cache"}):
        return "cache", "cache_build_test_temp", "expired", False, "local_private"
    if any(token in parts for token in {"proxy", "proxies"}):
        return "proxy_media", "reproducible_derived", "superseded", False, "local_private"
    if any(token in parts for token in {"converted", "transcoded", "remux", "remuxed"}):
        return "rebuildable_remux", "reproducible_derived", "superseded", False, "local_private"
    if digest_seen and (re.search(r"(?:copy|duplicate|\(\d+\)|副本)", name) is not None):
        return "duplicate_conversion", "duplicate_or_obsolete", "superseded", False, "local_private"
    if suffix in EVIDENCE_SUFFIXES or name.endswith(".lifecycle.json"):
        return "source_platform_evidence", "evidence_receipt_manifest", "verified", True, "sensitive_metadata"
    if suffix in MEDIA_SUFFIXES:
        return "original_download", "authoritative_input", "verified", True, "local_private"
    return "source_platform_evidence", "evidence_receipt_manifest", "locked", True, "local_private"


def _string_list(value: Any, field: str, path: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise ArtifactLifecycleAdapterError("SIDECAR_FIELD_INVALID", f"{path}: {field}")
    return list(value)


def _enrich_manifest(manifest: dict[str, Any], root: Path, config: dict[str, Any], core: Any, snapshot: dict[str, Any]) -> dict[str, Any]:
    digest_seen: set[str] = set()
    categories: dict[str, int] = {}
    candidate_categories = set(config["candidate_categories"])
    for item in manifest["artifacts"]:
        relative = str(item["path"])
        digest_value = str(item["digest"]["value"])
        category, role, state, pin, sensitivity = _classify(relative, digest_value in digest_seen, list(config["authoritative_paths"]))
        digest_seen.add(digest_value)
        metadata = _metadata_sidecar(root / relative)
        depends_on = _string_list(metadata.get("depends_on"), "depends_on", relative)
        referenced_by = _string_list(metadata.get("referenced_by"), "referenced_by", relative)
        reference_status = str(metadata.get("reference_status") or "unknown")
        if reference_status not in {"unknown", "verified_none", "verified_references"}:
            raise ArtifactLifecycleAdapterError("REFERENCE_STATUS_INVALID", relative)
        rebuild_proof = metadata.get("rebuild_proof") or {
            "status": "unknown",
            "command": None,
            "input_hashes": [],
            "dependency_lock": None,
            "verification_ref": None,
        }
        if not isinstance(rebuild_proof, dict):
            raise ArtifactLifecycleAdapterError("REBUILD_PROOF_INVALID", relative)
        item.update(
            {
                "artifact_role": role,
                "lifecycle_state": state,
                "depends_on": depends_on,
                "referenced_by": referenced_by,
                "sensitivity": str(metadata.get("sensitivity") or sensitivity),
                "rebuild_proof": rebuild_proof,
                "minimum_keep_until": metadata.get("minimum_keep_until"),
                "pin": bool(metadata.get("pin", pin)),
                "adapter_evidence": {
                    "category": category,
                    "candidate": category in candidate_categories,
                    "reference_status": reference_status,
                    "metadata_sidecar": f"{relative}.lifecycle.json" if metadata else None,
                },
            }
        )
        categories[category] = categories.get(category, 0) + 1
    result = dict(manifest)
    result.update(
        {
            "adapter_contract": ADAPTER_CONTRACT,
            "adapter_config_sha256": core.canonical_sha256(config),
            "root_declaration_id": snapshot["root_id"],
            "artifact_roots": [manifest["root_identity"]],
            "authoritative_paths": list(config["authoritative_paths"]),
            "scan_bounds": {
                "max_depth": snapshot["max_depth"],
                "max_items": snapshot["max_items"],
                "file_count": snapshot["file_count"],
                "directory_count": snapshot["directory_count"],
                "snapshot_fingerprint": snapshot["fingerprint"],
            },
            "category_counts": categories,
            "filesystem_mutation_performed": False,
        }
    )
    return core.attach_hash(result, "manifest_sha256")


def scan_media_root(
    declaration: RootDeclaration,
    *,
    output: Path,
    checkpoint: Path,
    closure_run_id: str,
    config_path: Path | None = None,
) -> dict[str, Any]:
    core = load_core()
    config = load_adapter_config(config_path)
    root = declaration.path.resolve(strict=True)
    output = output.resolve(strict=False)
    checkpoint = checkpoint.resolve(strict=False)
    if _path_is_within(output, root) or _path_is_within(checkpoint, root):
        raise ArtifactLifecycleAdapterError("CONTROL_ARTIFACT_INSIDE_SCAN_ROOT")
    before = _bounded_snapshot(root, declaration.max_depth, declaration.max_items, core)
    before["root_id"] = declaration.root_id
    before["max_depth"] = declaration.max_depth
    before["max_items"] = declaration.max_items
    reused = _load_incremental_manifest(
        checkpoint,
        output,
        declaration.root_id,
        before["fingerprint"],
        closure_run_id,
        core.canonical_sha256(config),
        core,
    )
    if reused is not None:
        return reused
    manifest = core.scan_root(
        root,
        profile_id="media",
        producer_tool_id=str(config["producer_tool_id"]),
        owner_lane=str(config["owner_lane"]),
        closure_run_id=closure_run_id,
        max_files=declaration.max_items,
        checkpoint=checkpoint,
    )
    after = _bounded_snapshot(root, declaration.max_depth, declaration.max_items, core)
    if before["fingerprint"] != after["fingerprint"] or before["file_count"] != after["file_count"]:
        raise ArtifactLifecycleAdapterError("SCAN_DRIFT_DETECTED")
    payload = _enrich_manifest(manifest, root, config, core, before)
    errors = core.validate_manifest(payload)
    if errors:
        raise ArtifactLifecycleAdapterError("CORE_MANIFEST_REJECTED", ";".join(errors))
    core.atomic_write_json(output, payload)
    _record_incremental_checkpoint(checkpoint, declaration.root_id, before["fingerprint"], closure_run_id, payload, core)
    return payload


def _load_incremental_manifest(
    checkpoint: Path,
    output: Path,
    root_id: str,
    fingerprint: str,
    closure_run_id: str,
    config_sha256: str,
    core: Any,
) -> dict[str, Any] | None:
    if not checkpoint.is_file() or not output.is_file():
        return None
    connection = sqlite3.connect(checkpoint)
    try:
        table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='vdo_adapter_runs'"
        ).fetchone()
        if not table:
            return None
        record = connection.execute(
            "SELECT fingerprint, manifest_sha256 FROM vdo_adapter_runs WHERE root_id=? AND closure_run_id=?",
            (root_id, closure_run_id),
        ).fetchone()
    finally:
        connection.close()
    if not record or record[0] != fingerprint:
        return None
    manifest = core.load_manifest(output)
    if (
        manifest.get("manifest_sha256") != record[1]
        or manifest.get("adapter_config_sha256") != config_sha256
        or manifest.get("root_declaration_id") != root_id
    ):
        return None
    return manifest


def _record_incremental_checkpoint(checkpoint: Path, root_id: str, fingerprint: str, closure_run_id: str, manifest: dict[str, Any], core: Any) -> None:
    connection = sqlite3.connect(checkpoint)
    try:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS vdo_adapter_runs(root_id TEXT, fingerprint TEXT, closure_run_id TEXT, manifest_sha256 TEXT, PRIMARY KEY(root_id, closure_run_id))"
        )
        connection.execute(
            "INSERT OR REPLACE INTO vdo_adapter_runs VALUES(?,?,?,?)",
            (root_id, fingerprint, closure_run_id, manifest["manifest_sha256"]),
        )
        connection.commit()
        if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ArtifactLifecycleAdapterError("CHECKPOINT_INTEGRITY_FAILED")
    finally:
        connection.close()


def explain_artifact(manifest_path: Path, artifact_id: str) -> dict[str, Any]:
    core = load_core()
    manifest = core.load_manifest(manifest_path.resolve(strict=True))
    item = next((entry for entry in manifest["artifacts"] if entry["artifact_id"] == artifact_id), None)
    if item is None:
        raise ArtifactLifecycleAdapterError("ARTIFACT_NOT_FOUND", artifact_id)
    evidence = dict(item.get("adapter_evidence") or {})
    blockers = []
    if item.get("pin"):
        blockers.append("artifact_pinned")
    if evidence.get("reference_status") == "unknown":
        blockers.append("references_unknown")
    if item.get("artifact_role") in core.PROTECTED_ROLES:
        blockers.append(f"protected_role:{item.get('artifact_role')}")
    if evidence.get("category") not in set(load_adapter_config()["candidate_categories"]):
        blockers.append("adapter_not_gc_candidate")
    return {
        "contract": ADAPTER_CONTRACT,
        "status": "protected" if blockers else "candidate",
        "artifact": item,
        "blocking_gates": sorted(set(blockers)),
        "will_mutate_files": False,
    }


def inspect_media_processes() -> dict[str, Any]:
    try:
        import psutil  # type: ignore
    except ImportError:
        return {"state": "unknown", "active": [], "error": "psutil_unavailable"}
    active = []
    access_error = False
    for process in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            name = str(process.info.get("name") or "").casefold().removesuffix(".exe")
            command = " ".join(str(part) for part in (process.info.get("cmdline") or [])).casefold()
        except (psutil.AccessDenied, psutil.NoSuchProcess, OSError):
            access_error = True
            continue
        matched = name in PROCESS_NAMES or "yt_dlp" in command or "n_m3u8dl-re" in command
        if matched:
            active.append({"pid": int(process.info["pid"]), "name": name})
    if active:
        return {"state": "active", "active": active, "error": None}
    if access_error:
        return {"state": "unknown", "active": [], "error": "process_access_incomplete"}
    return {"state": "idle_confirmed", "active": [], "error": None}


def plan_gc(
    manifest_path: Path,
    declaration: RootDeclaration,
    *,
    quarantine_root: Path,
    output: Path,
    owner_state: str,
    dispatch_state: str,
    process_probe: dict[str, Any] | None = None,
    config_path: Path | None = None,
) -> dict[str, Any]:
    core = load_core()
    config = load_adapter_config(config_path)
    manifest = core.load_manifest(manifest_path.resolve(strict=True))
    root = declaration.path.resolve(strict=True)
    quarantine = quarantine_root.resolve(strict=True)
    if manifest.get("adapter_contract") != ADAPTER_CONTRACT or manifest.get("profile_id") != "media":
        raise ArtifactLifecycleAdapterError("MANIFEST_NOT_VDO_MEDIA")
    if manifest.get("adapter_config_sha256") != core.canonical_sha256(config):
        raise ArtifactLifecycleAdapterError("ADAPTER_CONFIG_DRIFT")
    if manifest.get("root_identity") != core.directory_identity(root):
        raise ArtifactLifecycleAdapterError("ROOT_IDENTITY_DRIFT")
    process = process_probe or inspect_media_processes()
    process_state = "idle_confirmed" if process.get("state") == "idle_confirmed" else "unknown"
    plan = core.plan_operation(
        manifest,
        root,
        quarantine,
        "quarantine",
        owner_state=owner_state,
        process_state=process_state,
        dispatch_state=dispatch_state,
    )
    manifest_index = {item["artifact_id"]: item for item in manifest["artifacts"]}
    candidate_categories = set(config["candidate_categories"])
    for entry in plan["entries"]:
        item = manifest_index[entry["artifact_id"]]
        evidence = dict(item.get("adapter_evidence") or {})
        blockers = set(entry.get("blocking_gates") or [])
        if evidence.get("category") not in candidate_categories:
            blockers.add("adapter_not_gc_candidate")
        if evidence.get("reference_status") == "unknown":
            blockers.add("references_unknown")
        if evidence.get("category") == "active_run_or_checkpoint":
            blockers.add("active_run_or_checkpoint")
        entry["blocking_gates"] = sorted(blockers)
        entry["eligible"] = not blockers
    plan.update(
        {
            "adapter_contract": ADAPTER_CONTRACT,
            "plan_kind": "gc_quarantine_plan_only",
            "process_probe": process,
            "filesystem_mutation_performed": False,
            "next_action": "Review the immutable plan. This adapter has no real apply or purge command.",
        }
    )
    plan = core.attach_hash(plan, "plan_sha256")
    core.atomic_write_json(output.resolve(strict=False), plan)
    return plan


def verify_gc_plan(plan_path: Path, manifest_path: Path, declaration: RootDeclaration, quarantine_root: Path) -> dict[str, Any]:
    core = load_core()
    plan = core.read_json(plan_path.resolve(strict=True))
    manifest = core.load_manifest(manifest_path.resolve(strict=True))
    result = core.verify_plan(plan, manifest, declaration.path.resolve(strict=True), quarantine_root.resolve(strict=True))
    result.update({"contract": ADAPTER_CONTRACT, "filesystem_mutation_performed": False})
    return result


def restore_synthetic(
    *,
    synthetic_root: Path,
    quarantine_root: Path,
    plan_path: Path,
    receipt_path: Path,
    output: Path,
    config_path: Path | None = None,
) -> dict[str, Any]:
    core = load_core()
    config = load_adapter_config(config_path)
    root = synthetic_root.resolve(strict=True)
    quarantine = quarantine_root.resolve(strict=True)
    configured_test_root = os.environ.get("TEST_TMP_ROOT")
    fixture_boundary_path = (
        Path(configured_test_root)
        if configured_test_root
        else runtime_state_dir().parent / "artifact-lifecycle-fixtures"
    )
    try:
        fixture_boundary = fixture_boundary_path.resolve(strict=True)
    except OSError as exc:
        raise ArtifactLifecycleAdapterError("SYNTHETIC_FIXTURE_BOUNDARY_UNAVAILABLE", str(fixture_boundary_path)) from exc
    bounded_paths = {
        "synthetic_root": root,
        "quarantine_root": quarantine,
        "plan_path": plan_path.resolve(strict=False),
        "receipt_path": receipt_path.resolve(strict=False),
        "output": output.resolve(strict=False),
    }
    outside = [name for name, candidate in bounded_paths.items() if not _path_is_within(candidate, fixture_boundary)]
    if outside:
        raise ArtifactLifecycleAdapterError("SYNTHETIC_RESTORE_OUTSIDE_FIXTURE_BOUNDARY", ",".join(outside))
    core.assert_no_reparse(fixture_boundary, fixture_boundary)
    core.assert_no_reparse(root, fixture_boundary)
    core.assert_no_reparse(quarantine, fixture_boundary)
    marker = root / str(config["synthetic_restore_marker"])
    if not marker.is_file():
        raise ArtifactLifecycleAdapterError("SYNTHETIC_RESTORE_MARKER_REQUIRED", str(marker))
    if not _path_is_within(quarantine, root.parent):
        raise ArtifactLifecycleAdapterError("SYNTHETIC_QUARANTINE_OUTSIDE_FIXTURE")
    plan = core.read_json(plan_path.resolve(strict=True))
    receipt = core.read_json(receipt_path.resolve(strict=True))
    restored = core.restore_from_receipt(receipt, plan, root, quarantine, output.resolve(strict=False))
    restored.update({"contract": ADAPTER_CONTRACT, "synthetic_only": True})
    return restored


def media_operation_preflight(
    output_dir: Path,
    *,
    operation: str,
    expected_duration_seconds: float | None = None,
    estimated_output_bytes: int | None = None,
    config_path: Path | None = None,
) -> dict[str, Any]:
    config = load_adapter_config(config_path)
    core = load_core()
    target = output_dir.resolve(strict=False)
    existing = target
    while not existing.exists() and existing != existing.parent:
        existing = existing.parent
    if not existing.exists():
        raise ArtifactLifecycleAdapterError("OUTPUT_VOLUME_UNAVAILABLE", str(output_dir))
    if estimated_output_bytes is not None and (
        isinstance(estimated_output_bytes, bool)
        or not isinstance(estimated_output_bytes, int)
        or estimated_output_bytes <= 0
    ):
        raise ArtifactLifecycleAdapterError("ESTIMATED_OUTPUT_BYTES_INVALID")
    threshold = float(config["long_media_threshold_seconds"])
    estimate_required = operation in {"merge", "remux", "transcode"} or (
        operation == "download" and (expected_duration_seconds is None or expected_duration_seconds >= threshold)
    )
    disk = core.free_space_status(
        existing,
        minimum_free_bytes=int(config["minimum_free_bytes"]),
        warning_free_bytes=int(config["warning_free_bytes"]),
        estimated_output_bytes=estimated_output_bytes or 0,
        output_headroom_multiplier=float(config["output_headroom_multiplier"]),
    )
    if disk["status"] == "blocked_low_disk":
        status = "blocked_low_disk"
        reason_codes = ["DECLARED_OUTPUT_EXCEEDS_AVAILABLE_SPACE"]
        next_action = "Reduce the declared output, choose a volume with more free space, or free disk space."
    elif estimate_required and estimated_output_bytes is None:
        status = "blocked_output_estimate_required"
        reason_codes = ["ESTIMATED_OUTPUT_BYTES_REQUIRED"]
        next_action = "Provide a positive estimated_output_bytes value before long/unknown downloads or merge/remux/transcode."
    elif disk["status"] == "low_disk_allowed":
        status = "warning_only"
        reason_codes = ["LOW_DISK_WARNING_ONLY"]
        next_action = "The declared output fits the shared reserve policy, but less than 5 GiB remains; monitor the run."
    else:
        status = "ok"
        reason_codes = []
        next_action = None
    return {
        "contract": ADAPTER_CONTRACT,
        "operation": operation,
        "status": status,
        "allowed": status in {"ok", "warning_only"},
        "estimate_required": estimate_required,
        "estimated_output_bytes": estimated_output_bytes,
        "estimate_state": "declared" if estimated_output_bytes is not None else (
            "required_missing" if estimate_required else "not_required_for_bounded_small_download"
        ),
        "expected_duration_seconds": expected_duration_seconds,
        "long_media_threshold_seconds": threshold,
        "reason_codes": reason_codes,
        "disk": disk,
        "will_download": False,
        "filesystem_mutation_performed": False,
        "next_action": next_action,
    }


def doctor(config_path: Path | None = None) -> dict[str, Any]:
    config = load_adapter_config(config_path)
    core = load_core()
    profiles_path = _core_path().parent / "profiles" / "artifact-lifecycle-profiles.v1.json"
    profiles = json.loads(profiles_path.read_text(encoding="utf-8"))
    profile_errors = core.validate_profiles(profiles)
    root_checks = []
    for item in config["artifact_roots"]:
        root_id = str(item["root_id"])
        try:
            declaration = resolve_declared_root(root_id, config_path)
            core.assert_no_reparse(declaration.path, declaration.path)
            disk = core.free_space_status(
                declaration.path,
                minimum_free_bytes=int(config["minimum_free_bytes"]),
                warning_free_bytes=int(config["warning_free_bytes"]),
                output_headroom_multiplier=float(config["output_headroom_multiplier"]),
            )
            root_checks.append({"root_id": root_id, "status": disk["status"], "path": str(declaration.path), "disk": disk})
        except Exception as exc:
            root_checks.append({"root_id": root_id, "status": "unavailable", "error": str(exc)})
    process = inspect_media_processes()
    failed = bool(profile_errors) or any(item["status"] == "unavailable" for item in root_checks)
    degraded = any(item["status"] == "blocked_low_disk" for item in root_checks) or process["state"] != "idle_confirmed"
    return {
        "contract": ADAPTER_CONTRACT,
        "status": "failed" if failed else ("degraded" if degraded else "ready"),
        "lifecycle_profile": "media",
        "cleanup_mode": "plan_only",
        "automatic_purge": False,
        "core_path": str(_core_path()),
        "core_profile_errors": profile_errors,
        "artifact_roots": root_checks,
        "authoritative_paths": config["authoritative_paths"],
        "process_probe": process,
        "restore": "synthetic_only",
        "filesystem_mutation_performed": False,
        "current_online": False,
    }


def utc_run_id(prefix: str = "vdo-media") -> str:
    return f"{prefix}-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
