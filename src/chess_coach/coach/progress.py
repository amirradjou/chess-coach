"""Progress over time: error rate and centipawn loss by month or week, and puzzle solve rates."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from chess_coach.models import Game, MoveFact, Puzzle
from chess_coach.patterns.features import ERRORS, counted, result_of, rushed


@dataclass(slots=True)
class Period:
    label: str
    games: int
    moves: int
    errors: int
    cp_loss: int
    points: float  # 1 per win, 0.5 per draw
    decided: int = 0  # games whose result counts (not bullet: see features.result_of)

    @property
    def error_rate(self) -> float:
        return self.errors / self.moves if self.moves else 0.0

    @property
    def acpl(self) -> float:
        return self.cp_loss / self.moves if self.moves else 0.0

    @property
    def score(self) -> float | None:
        return self.points / self.decided if self.decided else None


def _label(when: datetime, by: str) -> str:
    if by == "week":
        year, week, _ = when.isocalendar()
        return f"{year}-W{week:02d}"
    return when.strftime("%Y-%m")


def periods(
    games: Sequence[Game], moves: Sequence[MoveFact], by: str = "month", speed: str | None = None
) -> list[Period]:
    """One row per month (or ISO week) with analysed games, oldest first."""
    chosen = {
        g.id: g
        for g in games
        if g.analyzed_depth and g.played_at and (speed is None or g.speed.value == speed)
    }
    rows: dict[str, Period] = {}
    for g in chosen.values():
        p = rows.setdefault(_label(g.played_at, by), Period(_label(g.played_at, by), 0, 0, 0, 0, 0))
        p.games += 1
        if (result := result_of(g)) != "unknown":
            p.decided += 1
            p.points += {"win": 1.0, "draw": 0.5}.get(result, 0.0)
    for m in moves:
        g = chosen.get(m.game_id)
        if g is None or not m.is_user or not counted(m, g) or rushed(m, g):
            continue
        p = rows[_label(g.played_at, by)]
        p.moves += 1
        p.errors += m.judgement in ERRORS
        p.cp_loss += m.cp_loss
    return [rows[k] for k in sorted(rows)]


@dataclass(slots=True)
class SolveRate:
    name: str
    attempts: int
    solved: int

    @property
    def rate(self) -> float:
        return self.solved / self.attempts if self.attempts else 0.0


def solve_rates(
    puzzles: Sequence[Puzzle], attempts: Sequence[tuple[str, str, bool]]
) -> tuple[SolveRate, list[SolveRate]]:
    """Overall and per-tag solve rates over every attempt so far."""
    tags = {p.id: p.tags for p in puzzles}
    overall = SolveRate("all", 0, 0)
    by_tag: dict[str, SolveRate] = defaultdict(lambda: SolveRate("", 0, 0))
    for puzzle_id, _, correct in attempts:
        overall.attempts += 1
        overall.solved += correct
        for tag in tags.get(puzzle_id, []):
            r = by_tag[tag]
            r.name, r.attempts, r.solved = tag, r.attempts + 1, r.solved + correct
    return overall, sorted(by_tag.values(), key=lambda r: (-r.attempts, r.name))
