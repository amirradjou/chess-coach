"""Progress over time: error rate and centipawn loss by month or week, puzzle solve rates, and
your rating week by week next to how much you practised."""

from __future__ import annotations

from bisect import bisect_left
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from statistics import mean

from chess_coach.models import Game, MoveFact, Puzzle
from chess_coach.patterns.features import ERRORS, counted, is_bullet, result_of, rushed

TREND_WEEKS = 12  # weeks in the rating trend
EFFECT_WEEKS = 4  # weeks needed with and without practice before comparing them


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


@dataclass(slots=True)
class Week:
    """One ISO week: your rating at one speed next to the practice you did."""

    label: str
    games: int = 0
    rating: int | None = None  # in your last game that week (the rating before that game)
    change: int | None = None  # against your last game before this week
    puzzles: int = 0
    solved: int = 0
    answers: int = 0  # opening training (Learn openings)
    right: int = 0

    @property
    def practised(self) -> bool:
        return self.puzzles + self.answers > 0


def trend_speeds(games: Sequence[Game]) -> list[str]:
    """Speeds with a rating to follow, most games first. Bullet is left out: the clock decides
    those games (features.is_bullet)."""
    counts = Counter(
        g.speed.value for g in games if not is_bullet(g) and g.user_elo and g.played_at
    )
    return [speed for speed, _ in counts.most_common()]


def weekly(
    games: Sequence[Game],
    speed: str,
    puzzle_attempts: Sequence[tuple[str, str, bool]],
    coach_attempts: Sequence[tuple[str, str, bool]],
    *,
    today: date,
    weeks: int = TREND_WEEKS,
) -> list[Week]:
    """The last `weeks` ISO weeks up to `today`, oldest first: your games and rating at `speed`,
    the puzzles you tried and your opening training answers. Ratings come from your games, which
    store each player's rating before the game."""
    monday = today - timedelta(days=today.weekday())
    starts = [monday - timedelta(weeks=weeks - 1 - i) for i in range(weeks)]
    labels = [_label(datetime.combine(s, datetime.min.time()), "week") for s in starts]
    rows = {label: Week(label) for label in labels}
    rated = sorted(
        (g.played_at.astimezone(UTC), g.user_elo)
        for g in games
        if g.speed.value == speed and g.user_elo and g.played_at
    )
    for when, elo in rated:
        if (w := rows.get(_label(when, "week"))) is not None:
            w.games += 1
            w.rating = elo
    times = [when for when, _ in rated]
    for start, w in zip(starts, rows.values(), strict=True):
        i = bisect_left(times, datetime.combine(start, datetime.min.time(), UTC))
        if w.rating is not None and i > 0:
            w.change = w.rating - rated[i - 1][1]
    for _, at, correct in puzzle_attempts:
        if (w := rows.get(_label(datetime.fromisoformat(at), "week"))) is not None:
            w.puzzles += 1
            w.solved += correct
    for _, at, correct in coach_attempts:
        if (w := rows.get(_label(datetime.fromisoformat(at), "week"))) is not None:
            w.answers += 1
            w.right += correct
    return list(rows.values())


def practice_effect(weeks: Sequence[Week]) -> tuple[float, int, float, int] | None:
    """Average rating change in weeks you practised and in weeks you didn't (weeks with games
    only), once each side has EFFECT_WEEKS weeks; None before that. A comparison, not proof."""
    practised = [w.change for w in weeks if w.practised and w.change is not None]
    idle = [w.change for w in weeks if not w.practised and w.change is not None]
    if len(practised) < EFFECT_WEEKS or len(idle) < EFFECT_WEEKS:
        return None
    return mean(practised), len(practised), mean(idle), len(idle)
