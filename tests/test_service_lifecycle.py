import json
import os
from pathlib import Path

from video_orchestrator.runtime.service_lifecycle import build_service_lifecycle_status
from video_orchestrator.runtime.service_lifecycle import _process_exists


def _write_receipt(path: Path, root: Path, *, pid: int = 1234, status: str = "running") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "contract": "vdo_ui_service_lifecycle_v1",
                "receipt_id": "fixture-receipt",
                "status": status,
                "host": "127.0.0.1",
                "port": 8921,
                "pid": pid,
                "process_start_time_utc": "2026-08-20T00:00:00.0000000Z",
                "process_path": "C:\\Python313\\python.exe",
                "project_root": str(root),
                "started_at": "2026-08-20T08:00:00",
            }
        ),
        encoding="utf-8",
    )


def test_matching_listener_process_and_receipt_is_managed(tmp_path):
    receipt = tmp_path / ".vdo" / "ui-server.json"
    _write_receipt(receipt, tmp_path)

    payload = build_service_lifecycle_status(
        metadata_path=receipt,
        expected_host="127.0.0.1",
        expected_port=8921,
        expected_project_root=tmp_path,
        listener_ready=True,
        process_probe=lambda pid: pid == 1234,
    )

    assert payload["status"] == "managed_running"
    assert payload["safe_stop_available"] is True


def test_listener_without_owned_receipt_is_reported_as_unmanaged(tmp_path):
    payload = build_service_lifecycle_status(
        metadata_path=tmp_path / ".vdo" / "ui-server.json",
        expected_host="127.0.0.1",
        expected_port=8921,
        expected_project_root=tmp_path,
        listener_ready=True,
        process_probe=lambda pid: False,
    )

    assert payload["status"] == "unmanaged_listener"
    assert payload["safe_stop_available"] is False


def test_dead_process_with_running_receipt_is_stale(tmp_path):
    receipt = tmp_path / ".vdo" / "ui-server.json"
    _write_receipt(receipt, tmp_path)

    payload = build_service_lifecycle_status(
        metadata_path=receipt,
        expected_host="127.0.0.1",
        expected_port=8921,
        expected_project_root=tmp_path,
        listener_ready=False,
        process_probe=lambda pid: False,
    )

    assert payload["status"] == "stale_receipt"
    assert payload["pid"] == 1234


def test_alive_process_without_listener_is_detached(tmp_path):
    receipt = tmp_path / ".vdo" / "ui-server.json"
    _write_receipt(receipt, tmp_path)

    payload = build_service_lifecycle_status(
        metadata_path=receipt,
        expected_host="127.0.0.1",
        expected_port=8921,
        expected_project_root=tmp_path,
        listener_ready=False,
        process_probe=lambda pid: True,
    )

    assert payload["status"] == "detached_process"
    assert payload["safe_stop_available"] is True


def test_invalid_contract_cannot_claim_listener_or_safe_stop(tmp_path):
    receipt = tmp_path / ".vdo" / "ui-server.json"
    _write_receipt(receipt, tmp_path)
    payload = json.loads(receipt.read_text(encoding="utf-8"))
    payload["contract"] = "unknown_contract"
    receipt.write_text(json.dumps(payload), encoding="utf-8")

    status = build_service_lifecycle_status(
        metadata_path=receipt,
        expected_host="127.0.0.1",
        expected_port=8921,
        expected_project_root=tmp_path,
        listener_ready=True,
        process_probe=lambda pid: True,
    )

    assert status["status"] == "unmanaged_listener"
    assert status["receipt_contract_matches"] is False
    assert status["safe_stop_available"] is False


def test_legacy_receipt_without_process_identity_cannot_be_stopped_safely(tmp_path):
    receipt = tmp_path / ".vdo" / "ui-server.json"
    _write_receipt(receipt, tmp_path)
    payload = json.loads(receipt.read_text(encoding="utf-8"))
    payload.pop("process_start_time_utc")
    payload.pop("process_path")
    receipt.write_text(json.dumps(payload), encoding="utf-8")

    status = build_service_lifecycle_status(
        metadata_path=receipt,
        expected_host="127.0.0.1",
        expected_port=8921,
        expected_project_root=tmp_path,
        listener_ready=True,
        process_probe=lambda pid: True,
    )

    assert status["status"] == "managed_running"
    assert status["process_identity_present"] is False
    assert status["safe_stop_available"] is False


def test_process_probe_recognizes_current_process():
    assert _process_exists(os.getpid()) is True
