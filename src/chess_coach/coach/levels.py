"""The next level: how often you go wrong early in a game, against public Lichess players by rating.

Your opponents can't show this: rating matching makes you level with them almost everywhere. So
data/levels.json holds the same count for public games at each rating, measured the same way
(scripts/level_reference.py): the same engine depth, the same bullet and short-on-time rules. On
those games what most separates one rating from the next is accuracy early on.

The wording never claims more than the numbers allow. Your rate carries a 95% interval, and the
interval is read off the ladder as a range of ratings: when that range is wider than a 300-point
step you get "not enough games to place you", not a rating.
"""

from __future__ import annotations

import json
import math
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from importlib import resources
from statistics import median

from chess_coach.engine.evaluation import MISTAKE_WP_LOSS
from chess_coach.models import Game, MoveFact, Speed
from chess_coach.patterns.features import counted, rushed
from chess_coach.patterns.mining import wilson

MAX_PLY = 40  # move 20: the last segment any speed has
WINDOW_DAYS = 365
STEP = 300  # rating points: the next level, and how wide a place can be and still be one
STALE_DAYS = 30  # no game at this speed for this long: say the numbers are from back then
SPEEDS = {"bullet": Speed.BULLET, "blitz": Speed.BLITZ}


def reference() -> dict:
    text = resources.files("chess_coach.data").joinpath("levels.json").read_text(encoding="utf-8")
    return json.loads(text)


@dataclass(frozen=True, slots=True)
class Rate:
    """Mistakes and blunders over moves, with a 95% interval that allows for moves of one game
    being alike (`effect`, the design effect: n is divided by it first)."""

    errors: int
    moves: int
    low: float
    high: float

    @property
    def rate(self) -> float:
        return self.errors / self.moves if self.moves else 0.0


def _rate(errors: int, moves: int, effect: float) -> Rate:
    if not moves:
        return Rate(0, 0, 0.0, 1.0)
    low, high = wilson(errors / effect, moves / effect)
    return Rate(errors, moves, low, high)


def _pooled(points: Sequence[tuple[float, float, float]]) -> list[tuple[float, float]]:
    """(rating, rate) from (rating, rate, weight), lowest rating first, with neighbours pooled until
    the rate never rises with the rating (pool adjacent violators). Sampled bands wobble, and a
    ladder that goes back up has no one rating for a rate."""
    blocks: list[list[float]] = []  # [weighted rate sum, weight, how many points]
    for _, rate, weight in points:
        blocks.append([rate * weight, weight, 1])
        while len(blocks) > 1 and blocks[-2][0] / blocks[-2][1] < blocks[-1][0] / blocks[-1][1]:
            total, w, n = blocks.pop()
            blocks[-1][0] += total
            blocks[-1][1] += w
            blocks[-1][2] += n
    rates = [b[0] / b[1] for b in blocks for _ in range(int(b[2]))]
    return [(p[0], r) for p, r in zip(points, rates, strict=True)]


def ladder(
    bands: Sequence[Mapping], segment: str, mix: Mapping[str, int] | None = None
) -> list[tuple[float, float]]:
    """(rating, rate of mistakes and blunders) per band, as a rate that never rises with rating.
    With `mix` (moves of yours per time control) a band's rate is its time controls' rates weighted
    the way you play them."""
    mix = mix or {}
    points = []
    for band in bands:
        row = band["rows"][segment]
        errors, moves = row["errors"]
        rate = errors / moves
        by_control = band.get("by_time_control")
        if by_control and (total := sum(mix.get(tc, 0) for tc in by_control)):
            rate = 0.0
            for tc, rows in by_control.items():
                e, m = rows["rows"][segment]["errors"]
                rate += mix.get(tc, 0) / total * e / m
        points.append(((band["lo"] + band["hi"]) / 2, rate, moves))
    return _pooled(points)


def rating_at(rate: float, points: Sequence[tuple[float, float]]) -> float:
    """The rating a rate falls at on a ladder: -inf when it's worse than the ladder's lowest band,
    +inf when it's better than its highest."""
    if rate > points[0][1]:
        return -math.inf
    if rate < points[-1][1]:
        return math.inf
    level = [r for r, p in points if p == rate]
    if level:  # on the ladder's own points, the middle of any flat stretch
        return (level[0] + level[-1]) / 2
    for (r1, p1), (r2, p2) in zip(points, points[1:], strict=False):
        if p2 < rate < p1:
            return r1 + (p1 - rate) / (p1 - p2) * (r2 - r1)
    return points[-1][0]


def rate_at(rating: float, points: Sequence[tuple[float, float]]) -> float | None:
    """The ladder's rate at a rating; None off either end, where there is nothing to read."""
    if rating < points[0][0] or rating > points[-1][0]:
        return None
    for (r1, p1), (r2, p2) in zip(points, points[1:], strict=False):
        if r1 <= rating <= r2:
            return p1 + (rating - r1) / (r2 - r1) * (p2 - p1)
    return points[-1][1]


@dataclass(frozen=True, slots=True)
class Place:
    """Your interval read off a ladder: a range of ratings, -inf or +inf past an end."""

    low: float
    high: float
    bottom: float  # the ladder's lowest and highest ratings
    top: float

    @property
    def clear(self) -> bool:
        """Close enough to call a place: no wider than a 300-point step, on the ladder."""
        return min(self.high, self.top) - max(self.low, self.bottom) <= STEP

    @property
    def words(self) -> str | None:
        """ "around 1400-1500", "below about 1200", "above about 2100"; None when it says nothing:
        an interval running off both ends fits every rating there is."""
        if self.low == -math.inf and self.high == math.inf:
            return None
        if self.low == -math.inf:
            return f"below about {int(self.bottom) if self.high == -math.inf else _up(self.high)}"
        if self.high == math.inf:
            return f"above about {int(self.top) if self.low == math.inf else _down(self.low)}"
        low, high = _down(self.low), _up(self.high)
        return f"around {low}" if low == high else f"around {low}-{high}"


def _down(rating: float) -> int:
    return math.floor(rating / 100) * 100


def _up(rating: float) -> int:
    return math.ceil(rating / 100) * 100


def place(you: Rate, points: Sequence[tuple[float, float]]) -> Place:
    """The ratings your interval spans: its worse end gives the low one."""
    low, high = rating_at(you.high, points), rating_at(you.low, points)
    return Place(low, high, points[0][0], points[-1][0])


@dataclass(frozen=True, slots=True)
class Row:
    segment: str  # "6-10": move numbers
    you: Rate
    them: Rate
    place: Place | None  # None with no moves of yours
    above: float | None  # the rate of players 300 points above your rating
    gap: bool  # your interval is clear of `above`: this is a real difference

    @property
    def placed(self) -> str | None:
        """Where you stand, when the interval allows saying so."""
        return self.place.words if self.place and self.place.clear else None

    @property
    def sentence(self) -> str:
        where = f"Moves {self.segment}"
        if self.place is None:
            return f"{where}: no moves of yours to count yet."
        if self.place.clear and self.place.words:
            return f"{where}: you play like players {self.place.words}."
        if self.place.words:
            return f"{where}: not enough games to place you more closely than {self.place.words}."
        return f"{where}: not enough games to place you."


@dataclass(slots=True)
class SpeedReport:
    speed: str
    games: int  # in the window, at time controls the reference covers
    other_games: int  # in the window, at time controls it doesn't
    rating: int | None  # your median rating in those games
    last: date | None  # your last game at this speed, whatever its time control
    stale: bool
    controls: list[str] = field(default_factory=list)
    rows: list[Row] = field(default_factory=list)

    @property
    def stale_note(self) -> str | None:
        if not self.stale or self.last is None:
            return None
        return f"Your last {self.speed} game was on {self.last}, so these numbers are from then."


@dataclass(slots=True)
class Report:
    speeds: list[SpeedReport]
    built: str  # when the public ladder was built


def _speed_report(
    speed: str,
    table: Mapping,
    segments: Sequence[str],
    games: Sequence[Game],
    moves: Mapping[str, list[MoveFact]],
    today: date,
) -> SpeedReport:
    controls = table["time_controls"]
    bands = table["bands"]
    since = today - timedelta(days=WINDOW_DAYS)
    same_speed = [g for g in games if g.speed is SPEEDS[speed] and g.played_at]
    in_window = [g for g in same_speed if g.played_at.date() >= since]
    chosen = [g for g in in_window if g.time_control in controls]
    last = max((g.played_at.date() for g in same_speed), default=None)
    ratings = [g.user_elo for g in chosen if g.user_elo]
    rating = round(median(ratings)) if ratings else None
    tallies = {name: Counter() for name in segments}
    mix = {name: Counter() for name in segments}
    spans = {name: tuple(int(x) for x in name.split("-")) for name in segments}
    for game in chosen:
        for m in moves.get(game.id, ()):
            if not counted(m, game) or rushed(m, game):
                continue
            name = next((n for n, (a, b) in spans.items() if a <= m.move_number <= b), None)
            if name is None:
                continue
            who = "you" if m.is_user else "them"
            tallies[name][f"{who}_moves"] += 1
            tallies[name][f"{who}_errors"] += m.wp_loss >= MISTAKE_WP_LOSS
            if m.is_user:
                mix[name][game.time_control] += 1
    rows = []
    for name in segments:
        t = tallies[name]
        # The public bands' typical design effect: this player's own would need more games.
        effect = median(b["rows"][name]["design_effect"][0] or 1.0 for b in bands)
        you = _rate(t["you_errors"], t["you_moves"], effect)
        them = _rate(t["them_errors"], t["them_moves"], effect)
        points = ladder(bands, name, mix[name])
        where = place(you, points) if you.moves else None
        above = rate_at(rating + STEP, points) if rating else None
        gap = above is not None and you.moves > 0 and you.low > above
        rows.append(Row(name, you, them, where, above, gap))
    return SpeedReport(
        speed=speed,
        games=len(chosen),
        other_games=len(in_window) - len(chosen),
        rating=rating,
        last=last,
        stale=last is not None and (today - last).days > STALE_DAYS,
        controls=list(controls),
        rows=rows,
    )


def report(
    games: Iterable[Game], moves: Iterable[MoveFact], today: date, table: Mapping | None = None
) -> Report:
    """`moves` are both players'. Only your last 12 months count, at the time controls the public
    games were sampled at, so like is compared with like."""
    table = table or reference()
    analysed = [g for g in games if g.analyzed_depth]
    ids = {g.id for g in analysed}
    by_game: dict[str, list[MoveFact]] = {}
    for m in moves:
        if m.game_id in ids:
            by_game.setdefault(m.game_id, []).append(m)
    speeds = [
        _speed_report(
            speed, table["speeds"][speed], table["segments"][speed], analysed, by_game, today
        )
        for speed in SPEEDS
    ]
    return Report([s for s in speeds if s.games or s.last], table["built"])


def headline(rep: Report) -> str | None:
    """The one line for the digest: your widest gap to players 300 points above you that the
    interval leaves no doubt about; otherwise where you stand, if anywhere can be said."""
    rows = [(s, r) for s in rep.speeds if not s.stale for r in s.rows if r.you.moves]
    if not rows:
        return None
    gaps = [(s, r, r.above) for s, r in rows if r.gap and r.above is not None]
    if gaps:
        s, r, above = max(gaps, key=lambda g: g[1].you.rate - g[2])
        return (
            f"In {s.speed}, moves {r.segment} are your widest clear gap to the next level: "
            f"mistakes or blunders on {r.you.rate:.1%} of them (95% interval {r.you.low:.1%} to "
            f"{r.you.high:.1%}), against {above:.1%} for players 300 points above you."
        )
    placed = [(s, r) for s, r in rows if r.placed]
    if placed:
        s, r = placed[0]
        return f"In {s.speed}, you play moves {r.segment} like players {r.placed}."
    return "Not enough recent games to place you against public players yet."
