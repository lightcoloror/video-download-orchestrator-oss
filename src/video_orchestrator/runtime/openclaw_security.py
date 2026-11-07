from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import re


REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")


@dataclass
class OpenClawReplayGuard:
    max_age_seconds: int = 300
    max_entries: int = 512
    _seen: dict[str, datetime] = field(default_factory=dict)

    def check_and_claim(
        self,
        *,
        actor_id: str,
        request_id: str | None,
        requested_at: str | None,
        now: datetime | None = None,
    ) -> dict[str, object]:
        current = now or datetime.now(UTC)
        self._prune(current)
        if not request_id or not REQUEST_ID_RE.fullmatch(request_id):
            return _blocked("request_id_required", "执行请求缺少有效 request_id，已拒绝。")
        timestamp = _parse_timestamp(requested_at)
        if timestamp is None:
            return _blocked("requested_at_required", "执行请求缺少有效 requested_at，已拒绝。")
        age = (current - timestamp).total_seconds()
        if age < -30 or age > self.max_age_seconds:
            return _blocked("stale_request", "执行请求已过期或时间异常，已拒绝。")
        key = f"{actor_id}:{request_id}"
        if key in self._seen:
            return _blocked("duplicate_request", "重复执行请求已被幂等门禁拒绝。")
        if len(self._seen) >= self.max_entries:
            oldest = min(self._seen, key=self._seen.__getitem__)
            self._seen.pop(oldest, None)
        self._seen[key] = current
        fingerprint = sha256(key.encode("utf-8")).hexdigest()[:12]
        return {"status": "accepted", "reason": "request_claimed", "request_fingerprint": fingerprint}

    def _prune(self, now: datetime) -> None:
        cutoff = now - timedelta(seconds=self.max_age_seconds)
        self._seen = {key: seen_at for key, seen_at in self._seen.items() if seen_at >= cutoff}


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def _blocked(reason: str, next_action: str) -> dict[str, object]:
    return {"status": "blocked", "reason": reason, "next_action": next_action}
