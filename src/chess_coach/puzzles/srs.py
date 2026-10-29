"""SM-2 spaced repetition: a puzzle you solve comes back later and later; one you miss comes back
tomorrow."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta

from chess_coach.models import Review

GOOD = 4  # solved
FAIL = 1  # missed


def schedule(review: Review, correct: bool, today: date) -> Review:
    """The review after one attempt (SM-2 with quality 4 for a solve and 1 for a miss)."""
    q = GOOD if correct else FAIL
    ease = max(1.3, review.ease + 0.1 - (5 - q) * (0.08 + (5 - q) * 0.02))
    if not correct:
        return replace(
            review,
            due=(today + timedelta(days=1)).isoformat(),
            interval=1,
            ease=ease,
            reps=0,
            lapses=review.lapses + 1,
        )
    reps = review.reps + 1
    interval = 1 if reps == 1 else 6 if reps == 2 else round(review.interval * ease)
    return replace(
        review,
        due=(today + timedelta(days=interval)).isoformat(),
        interval=interval,
        ease=ease,
        reps=reps,
    )
