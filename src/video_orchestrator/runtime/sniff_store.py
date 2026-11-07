from __future__ import annotations

import json
import os
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from video_orchestrator.runtime.config import runtime_state_dir
from video_orchestrator.runtime.redaction import redact_headers
from video_orchestrator.runtime.redaction import redact_ephemeral_headers
from video_orchestrator.runtime.redaction import redact_ephemeral_url
from video_orchestrator.runtime.redaction import redact_manifest
from video_orchestrator.runtime.redaction import redact_url


SNIFF_STORE_FILES = {
    "captures": "sniffed-media.jsonl",
    "rejections": "sniffed-media-rejections.jsonl",
}


def append_sniff_record(kind: str, record: dict[str, Any], base_dir: Path | str | None = None) -> Path:
    path = _record_path(kind, base_dir)
    redacted = redact_sniff_record(record)
    try:
        _append_record(path, redacted)
    except PermissionError:
        fallback = _test_fallback_path(kind, base_dir)
        if fallback is None:
            raise
        path = fallback
        _append_record(path, redacted)
    return path


def read_recent_sniff_records(kind: str, limit: int = 100, base_dir: Path | str | None = None) -> list[dict[str, Any]]:
    path = _record_path(kind, base_dir)
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except PermissionError:
        fallback = _test_fallback_path(kind, base_dir)
        if fallback is None or not fallback.exists():
            raise
        lines = fallback.read_text(encoding="utf-8").splitlines()
    records = []
    for line in lines[-limit:]:
        if not line.strip():
            continue
        records.append(json.loads(line))
    return records


def read_sniff_diagnostics(limit: int = 100, base_dir: Path | str | None = None) -> dict[str, Any]:
    return {
        "captures": read_recent_sniff_records("captures", limit=limit, base_dir=base_dir),
        "rejections": read_recent_sniff_records("rejections", limit=limit, base_dir=base_dir),
    }


def cleanup_sniff_records(older_than_days: int, dry_run: bool = False, base_dir: Path | str | None = None) -> dict[str, Any]:
    cutoff = datetime.now(UTC) - timedelta(days=older_than_days)
    results = {}
    removed_count = 0
    for kind in SNIFF_STORE_FILES:
        path = _record_path(kind, base_dir)
        records = _read_all_records(path)
        keep = []
        remove = []
        for record in records:
            timestamp = _record_timestamp(record)
            if timestamp is not None and timestamp < cutoff:
                remove.append(record)
            else:
                keep.append(record)
        removed_count += len(remove)
        results[kind] = {
            "path": str(path),
            "before": len(records),
            "removed": len(remove),
            "kept": len(keep),
        }
        if not dry_run and records:
            _write_records(path, keep)
    return {
        "status": "ok",
        "dry_run": dry_run,
        "older_than_days": older_than_days,
        "removed_count": removed_count,
        "files": results,
    }


def redact_sniff_record(record: dict[str, Any]) -> dict[str, Any]:
    redacted = deepcopy(record)
    for key in ("url", "page_url", "initiator", "document_url", "origin_url"):
        if isinstance(redacted.get(key), str):
            redacted[key] = (
                redact_ephemeral_url(redacted[key])
                if key == "url" and redacted.get("capture_id")
                else redact_url(redacted[key])
            )
    if isinstance(redacted.get("dedupe_key"), str):
        parts = redacted["dedupe_key"].split("|")
        if redacted.get("capture_id"):
            redacted["dedupe_key"] = "|".join(redact_ephemeral_url(part) for part in parts)
        else:
            redacted["dedupe_key"] = "|".join(redact_url(part) for part in parts)
    for key in ("headers", "request_headers", "response_headers"):
        if isinstance(redacted.get(key), dict):
            redacted[key] = (
                redact_ephemeral_headers(redacted[key])
                if redacted.get("capture_id")
                else redact_headers(redacted[key])
            )
    if isinstance(redacted.get("manifest"), dict):
        redacted["manifest"] = redact_manifest(redacted["manifest"])
        _redact_nested_sniff_context(redacted["manifest"])
    return redacted


def _redact_nested_sniff_context(manifest: dict[str, Any]) -> None:
    sniffed_media = manifest.get("sniffed_media")
    if not isinstance(sniffed_media, dict):
        return
    for key in ("url", "page_url", "initiator", "document_url", "origin_url"):
        if isinstance(sniffed_media.get(key), str):
            sniffed_media[key] = (
                redact_ephemeral_url(sniffed_media[key]) if key == "url" else redact_url(sniffed_media[key])
            )
    if isinstance(sniffed_media.get("dedupe_key"), str):
        parts = sniffed_media["dedupe_key"].split("|")
        sniffed_media["dedupe_key"] = "|".join(redact_ephemeral_url(part) for part in parts)


def _record_path(kind: str, base_dir: Path | str | None) -> Path:
    try:
        filename = SNIFF_STORE_FILES[kind]
    except KeyError as exc:
        raise ValueError(f"unsupported sniff record kind: {kind}") from exc
    return _resolved_base_dir(base_dir) / filename


def _resolved_base_dir(base_dir: Path | str | None) -> Path:
    if base_dir is not None:
        return Path(base_dir)
    configured = os.environ.get("VDO_SNIFF_STORE_DIR")
    if configured:
        return Path(configured)
    return runtime_state_dir() / "sniff-store"


def _append_record(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def _test_fallback_path(kind: str, base_dir: Path | str | None) -> Path | None:
    if base_dir is not None:
        return None
    test_root = os.environ.get("TEST_TMP_ROOT")
    if not test_root:
        return None
    return Path(test_root) / "vdo-sniff-store" / SNIFF_STORE_FILES[kind]


def _read_all_records(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        records.append(json.loads(line))
    return records


def _write_records(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = "".join(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n" for record in records)
    path.write_text(content, encoding="utf-8")


def _record_timestamp(record: dict[str, Any]) -> datetime | None:
    raw = record.get("captured_at") or record.get("rejected_at") or record.get("detected_at")
    if not isinstance(raw, str) or not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
