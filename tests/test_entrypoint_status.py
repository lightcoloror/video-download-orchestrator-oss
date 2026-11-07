from datetime import UTC, datetime

from video_orchestrator.runtime.entrypoint_status import build_entrypoint_status
from video_orchestrator.runtime.entrypoint_status import evaluate_execute_gate


NOW = datetime(2026, 7, 11, 12, 0, tzinfo=UTC)


def test_cli_ready_listener_stopped_remains_planning_ready():
    payload = build_entrypoint_status(listener_probe=lambda host, port: False, checked_at=NOW)

    assert payload["status"] == "cli_planning_ready"
    assert payload["planning_cli"]["status"] == "ready"
    assert payload["http_listener"]["status"] == "stopped"
    assert payload["execute_gate"]["status"] == "blocked"
    assert payload["will_download"] is False
    assert payload["execute"] is False


def test_listener_healthy_does_not_open_execute_gate():
    payload = build_entrypoint_status(listener_probe=lambda host, port: True, checked_at=NOW)

    assert payload["status"] == "ready_for_planning"
    assert payload["http_listener"]["status"] == "healthy"
    assert payload["execute_gate"]["reason"] == "no_execution_request"
    assert payload["execute_gate"]["execute"] is False


def test_all_stopped_is_unavailable_without_execution():
    payload = build_entrypoint_status(
        listener_probe=lambda host, port: False,
        planning_cli_ready=False,
        checked_at=NOW,
    )

    assert payload["status"] == "unavailable"
    assert payload["planning_cli"]["available_interfaces"] == []
    assert payload["http_listener"]["available_interfaces"] == []
    assert payload["will_download"] is False


def test_stale_execute_request_stays_blocked_independent_of_entrypoints():
    gate = evaluate_execute_gate(
        {
            "allowlist_configured": True,
            "actor_authorized": True,
            "confirm": True,
            "confirm_sensitive": True,
            "stale_request": True,
        },
        checked_at=NOW.isoformat(),
    )

    assert gate["status"] == "blocked"
    assert gate["reason"] == "stale_request"
    assert gate["will_download"] is False


def test_even_satisfied_gate_requires_independent_execute_recheck():
    gate = evaluate_execute_gate(
        {
            "allowlist_configured": True,
            "actor_authorized": True,
            "confirm": True,
            "confirm_sensitive": True,
        },
        checked_at=NOW.isoformat(),
    )

    assert gate["status"] == "eligible_for_explicit_recheck"
    assert gate["reason"] == "independent_execute_call_required"
    assert gate["execute"] is False
    assert gate["will_download"] is False
