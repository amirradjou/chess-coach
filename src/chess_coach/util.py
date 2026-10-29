"""Small helpers shared by CLI commands."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

_RELATIVE = re.compile(r"^(\d+)([dwmy])$")


def parse_since(text: str | None, *, now: datetime | None = None) -> datetime | None:
    """'7d' / '2w' / '3m' / '1y' relative to now, or an ISO date such as 2026-09-01."""
    if not text:
        return None
    now = now or datetime.now(UTC)
    if m := _RELATIVE.match(text.strip().lower()):
        amount, unit = int(m.group(1)), m.group(2)
        days = {"d": 1, "w": 7, "m": 30, "y": 365}[unit] * amount
        return now - timedelta(days=days)
    parsed = datetime.fromisoformat(text)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
