from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from video_orchestrator.runtime.redaction import redact_ephemeral_url
from video_orchestrator.runtime.redaction import redact_ephemeral_headers
from video_orchestrator.runtime.redaction import redact_manifest


DEFAULT_CAPTURE_LEASE_SECONDS = 10 * 60


def new_capture_lease(*, captured_at: datetime | None = None, ttl_seconds: int = DEFAULT_CAPTURE_LEASE_SECONDS) -> dict[str, Any]:
    started = (captured_at or datetime.now(UTC)).astimezone(UTC)
    expires = started + timedelta(seconds=ttl_seconds)
    return {
        "status": "active",
        "active": True,
        "captured_at": started.isoformat(),
        "expires_at": expires.isoformat(),
        "ttl_seconds": ttl_seconds,
        "remaining_seconds": ttl_seconds,
        "sensitive_data_available": True,
        "will_download": False,
    }


def capture_lease_summary(capture: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    current = (now or datetime.now(UTC)).astimezone(UTC)
    expires = _parse_timestamp(capture.get("expires_at"))
    if expires is None:
        captured = _parse_timestamp(capture.get("captured_at"))
        expires = captured + timedelta(seconds=DEFAULT_CAPTURE_LEASE_SECONDS) if captured is not None else None
    if expires is None:
        return {
            "status": "invalid",
            "active": False,
            "expires_at": None,
            "remaining_seconds": 0,
            "sensitive_data_available": False,
            "will_download": False,
        }
    remaining = max(0, int((expires - current).total_seconds()))
    active = remaining > 0 and capture.get("sensitive_data_available", True) is not False
    return {
        "status": "active" if active else "expired",
        "active": active,
        "expires_at": expires.isoformat(),
        "remaining_seconds": remaining,
        "sensitive_data_available": active,
        "will_download": False,
    }


def expire_capture_secrets(capture: dict[str, Any], *, now: datetime | None = None) -> bool:
    lease = capture_lease_summary(capture, now=now)
    if lease["active"] or capture.get("sensitive_data_available") is False:
        return False
    if isinstance(capture.get("url"), str):
        capture["url"] = redact_ephemeral_url(str(capture["url"]))
    for key in ("request_headers", "response_headers"):
        if isinstance(capture.get(key), dict):
            capture[key] = redact_ephemeral_headers(capture[key])
    if isinstance(capture.get("manifest"), dict):
        capture["manifest"] = redact_manifest(capture["manifest"])
    capture["sensitive_data_available"] = False
    capture["lease_status"] = "expired"
    return True


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
