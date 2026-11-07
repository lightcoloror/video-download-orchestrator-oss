from datetime import UTC, datetime, timedelta

from video_orchestrator.runtime.openclaw_security import OpenClawReplayGuard


def test_replay_guard_requires_request_id_and_timestamp():
    guard = OpenClawReplayGuard()
    now = datetime(2026, 7, 11, 12, 0, tzinfo=UTC)

    missing_id = guard.check_and_claim(actor_id="42", request_id=None, requested_at=now.isoformat(), now=now)
    missing_time = guard.check_and_claim(actor_id="42", request_id="telegram-001", requested_at=None, now=now)

    assert missing_id["reason"] == "request_id_required"
    assert missing_time["reason"] == "requested_at_required"


def test_replay_guard_blocks_stale_and_duplicate_requests():
    guard = OpenClawReplayGuard(max_age_seconds=300)
    now = datetime(2026, 7, 11, 12, 0, tzinfo=UTC)

    stale = guard.check_and_claim(
        actor_id="42",
        request_id="telegram-stale",
        requested_at=(now - timedelta(seconds=301)).isoformat(),
        now=now,
    )
    first = guard.check_and_claim(
        actor_id="42",
        request_id="telegram-valid",
        requested_at=now.isoformat(),
        now=now,
    )
    duplicate = guard.check_and_claim(
        actor_id="42",
        request_id="telegram-valid",
        requested_at=now.isoformat(),
        now=now,
    )

    assert stale["reason"] == "stale_request"
    assert first["status"] == "accepted"
    assert duplicate["reason"] == "duplicate_request"


def test_replay_guard_scopes_request_ids_by_actor():
    guard = OpenClawReplayGuard()
    now = datetime(2026, 7, 11, 12, 0, tzinfo=UTC)

    first = guard.check_and_claim(actor_id="42", request_id="telegram-shared", requested_at=now.isoformat(), now=now)
    other_actor = guard.check_and_claim(actor_id="43", request_id="telegram-shared", requested_at=now.isoformat(), now=now)

    assert first["status"] == "accepted"
    assert other_actor["status"] == "accepted"
