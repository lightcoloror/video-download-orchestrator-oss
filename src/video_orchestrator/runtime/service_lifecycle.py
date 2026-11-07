from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Callable


ProcessProbe = Callable[[int], bool]


def build_service_lifecycle_status(
    *,
    metadata_path: Path,
    expected_host: str,
    expected_port: int,
    expected_project_root: Path,
    listener_ready: bool,
    process_probe: ProcessProbe | None = None,
) -> dict[str, Any]:
    receipt, receipt_error = _read_receipt(metadata_path)
    base = {
        "contract": "vdo_ui_service_lifecycle_v1",
        "metadata_path": str(metadata_path),
        "configured_host": expected_host,
        "configured_port": expected_port,
        "listener_ready": listener_ready,
        "status_command": "powershell -ExecutionPolicy Bypass -File scripts/status-ui.ps1",
        "stop_command": "powershell -ExecutionPolicy Bypass -File scripts/stop-ui.ps1 -ConfirmStop",
        "will_download": False,
        "execute": False,
    }
    if receipt is None:
        return {
            **base,
            "status": "unmanaged_listener" if listener_ready else "stopped",
            "reason": receipt_error or ("listener_without_receipt" if listener_ready else "no_listener_or_receipt"),
            "pid": None,
            "process_alive": False,
            "receipt_matches_config": False,
            "safe_stop_available": False,
        }

    pid = _positive_int(receipt.get("pid"))
    process_alive = bool(pid and (process_probe or _process_exists)(pid))
    receipt_matches_config = (
        str(receipt.get("host") or "") == expected_host
        and _positive_int(receipt.get("port")) == expected_port
        and _same_path(receipt.get("project_root"), expected_project_root)
    )
    receipt_status = str(receipt.get("status") or "unknown")
    receipt_contract_matches = receipt.get("contract") == "vdo_ui_service_lifecycle_v1"
    receipt_id = receipt.get("receipt_id")
    receipt_id_valid = isinstance(receipt_id, str) and bool(receipt_id.strip())
    process_identity_present = bool(
        isinstance(receipt.get("process_start_time_utc"), str)
        and receipt["process_start_time_utc"].strip()
        and isinstance(receipt.get("process_path"), str)
        and receipt["process_path"].strip()
    )
    receipt_matches_config = receipt_matches_config and receipt_contract_matches and receipt_id_valid

    if listener_ready and process_alive and receipt_matches_config:
        status, reason = "managed_running", "listener_process_and_receipt_match"
    elif listener_ready:
        status, reason = "unmanaged_listener", "listener_not_owned_by_valid_receipt"
    elif process_alive and receipt_matches_config:
        status, reason = "detached_process", "recorded_process_alive_but_port_not_listening"
    elif receipt_status == "stopped":
        status, reason = "stopped", "receipt_records_clean_stop"
    else:
        status, reason = "stale_receipt", "recorded_process_not_alive_and_port_not_listening"

    return {
        **base,
        "status": status,
        "reason": reason,
        "pid": pid,
        "process_alive": process_alive,
        "receipt_status": receipt_status,
        "receipt_matches_config": receipt_matches_config,
        "receipt_contract_matches": receipt_contract_matches,
        "safe_stop_available": bool(process_alive and receipt_matches_config and process_identity_present),
        "process_identity_present": process_identity_present,
        "started_at": receipt.get("started_at"),
        "stopped_at": receipt.get("stopped_at"),
        "receipt_id": receipt_id if receipt_id_valid else None,
    }


def _read_receipt(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    if not path.exists():
        return None, None
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None, "invalid_or_unreadable_receipt"
    return (payload, None) if isinstance(payload, dict) else (None, "invalid_receipt_shape")


def _process_exists(pid: int) -> bool:
    if sys.platform == "win32":
        return _windows_process_exists(pid)
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _windows_process_exists(pid: int) -> bool:
    import ctypes
    from ctypes import wintypes

    process_query_limited_information = 0x1000
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
    if not handle:
        return False
    kernel32.CloseHandle(handle)
    return True


def _positive_int(value: object) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _same_path(value: object, expected: Path) -> bool:
    if not isinstance(value, str) or not value:
        return False
    try:
        return Path(value).resolve() == expected.resolve()
    except OSError:
        return False
