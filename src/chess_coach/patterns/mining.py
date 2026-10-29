"""Find the slices where the user does clearly worse than their own baseline.

A slice is one feature value ("phase = endgame") or a pair ("phase = endgame, clock = under
30s"). It is kept when it is big enough to mean something, its rate is well above the baseline,
and the lower end of its 95% Wilson interval is still above the baseline, so a handful of unlucky
moves cannot make a pattern. Slices are ranked by excess: how many more errors (or losses)
happened there than the baseline rate predicts.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from itertools import combinations

from chess_coach.patterns.features import GameRow, MoveRow

Feature = tuple[str, str]


def wilson_lower(hits: int, n: int, z: float = 1.96) -> float:
    """Lower bound of the Wilson score interval for hits/n."""
    if n == 0:
        return 0.0
    p = hits / n
    centre = p + z * z / (2 * n)
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (centre - margin) / (1 + z * z / n)


@dataclass(frozen=True, slots=True)
class Evidence:
    game_id: str
    ply: int
    move_number: int
    color: str
    san: str
    best_san: str
    wp_loss: float
    url: str | None

    @property
    def move(self) -> str:
        return f"{self.move_number}{'.' if self.color == 'white' else '...'}{self.san}"


@dataclass(slots=True)
class Pattern:
    kind: str  # "moves" (error rate) or "games" (loss rate)
    features: tuple[Feature, ...]
    n: int
    hits: int
    baseline: float
    evidence: list[Evidence] = field(default_factory=list)
    games: int = 0  # distinct games the slice spans (moves patterns)

    @property
    def rate(self) -> float:
        return self.hits / self.n if self.n else 0.0

    @property
    def lift(self) -> float:
        return self.rate / self.baseline if self.baseline else math.inf

    @property
    def low(self) -> float:
        return wilson_lower(self.hits, self.n)

    @property
    def excess(self) -> float:
        return self.hits - self.n * self.baseline


def _slices(
    features: Sequence[dict[str, str]], pairs: bool
) -> dict[tuple[Feature, ...], list[int]]:
    """Index of the rows in each single-feature and (optionally) two-feature slice."""
    out: dict[tuple[Feature, ...], list[int]] = {}
    for i, feats in enumerate(features):
        items = sorted(feats.items())
        for one in items:
            out.setdefault((one,), []).append(i)
        if pairs:
            for a, b in combinations(items, 2):
                out.setdefault((a, b), []).append(i)
    return out


def _mine(
    flags: Sequence[bool],
    features: Sequence[dict[str, str]],
    min_n: int,
    min_lift: float,
    pairs: bool,
    kind: str,
) -> list[Pattern]:
    total = len(flags)
    if total == 0:
        return []
    baseline = sum(flags) / total
    if baseline == 0:
        return []
    slices = _slices(features, pairs)
    rate = {k: sum(flags[i] for i in idx) / len(idx) for k, idx in slices.items() if len(k) == 1}
    # A value nearly every row shares ("speed = blitz" when all games are blitz) says nothing
    # in a pair.
    common = {k[0] for k, idx in slices.items() if len(k) == 1 and len(idx) >= 0.9 * total}
    kept: dict[tuple[Feature, ...], Pattern] = {}
    for key, idx in slices.items():
        if len(idx) < min_n:
            continue
        if len(key) == 2:
            if any(f in common for f in key):
                continue
            # A pair is only news if it is clearly worse than each half on its own.
            if any(sum(flags[i] for i in idx) / len(idx) < 1.2 * rate[(f,)] for f in key):
                continue
        p = Pattern(kind, key, len(idx), sum(flags[i] for i in idx), baseline)
        if p.lift >= min_lift and p.low > baseline:
            kept[key] = p
    return sorted(kept.values(), key=lambda p: (-p.excess, -p.lift, p.features))


def _evidence(rows: Sequence[MoveRow], limit: int, errors_only: bool = True) -> list[Evidence]:
    pool = [r for r in rows if r.error] if errors_only else list(rows)
    worst = sorted(pool, key=lambda r: (-r.fact.wp_loss, r.fact.game_id, r.fact.ply))[:limit]
    return [
        Evidence(
            r.fact.game_id,
            r.fact.ply,
            r.fact.move_number,
            r.fact.color.value,
            r.fact.san,
            r.fact.best_san,
            r.fact.wp_loss,
            _ply_url(r.game.url, r.fact.ply),
        )
        for r in worst
    ]


def _ply_url(url: str | None, ply: int) -> str | None:
    if url and "lichess.org" in url:
        return f"{url}#{ply}"
    return url


def mine_moves(
    rows: Sequence[MoveRow], min_n: int = 30, min_lift: float = 1.5, examples: int = 3
) -> list[Pattern]:
    """Slices of the user's moves where their mistake + blunder rate is well above their own."""
    found = _mine(
        [r.error for r in rows], [r.features for r in rows], min_n, min_lift, True, "moves"
    )
    for p in found:
        members = [r for r in rows if all(r.features.get(k) == v for k, v in p.features)]
        p.games = len({r.fact.game_id for r in members})
        p.evidence = _evidence(members, examples)
    return found


def mine_games(rows: Sequence[GameRow], min_n: int = 8, min_lift: float = 1.25) -> list[Pattern]:
    """Slices of the user's games they lose clearly more often than usual (single features).

    The bar is a lower lift than for moves because a loss rate starts near 50%: 1.5x would
    need slices lost four times in five."""
    found = _mine(
        [r.lost for r in rows], [r.features for r in rows], min_n, min_lift, False, "games"
    )
    for p in found:
        members = [r for r in rows if all(r.features.get(k) == v for k, v in p.features) and r.lost]
        p.evidence = [
            Evidence(r.game.id, 0, 0, r.game.user_color.value, "", "", 0.0, r.game.url)
            for r in members[:3]
        ]
    return found


@dataclass(slots=True)
class MotifCount:
    tag: str
    moves: int
    games: int
    evidence: list[Evidence]


def motif_counts(rows: Sequence[MoveRow], examples: int = 2) -> list[MotifCount]:
    """How often each motif tag shows up in the user's moves, most common first."""
    by_tag: dict[str, list[MoveRow]] = {}
    for r in rows:
        for tag in r.fact.motifs:
            by_tag.setdefault(tag, []).append(r)
    counts = Counter({tag: len(rs) for tag, rs in by_tag.items()})
    return [
        MotifCount(
            tag,
            n,
            len({r.fact.game_id for r in by_tag[tag]}),
            _evidence(by_tag[tag], examples, False),
        )
        for tag, n in counts.most_common()
    ]


def baseline(rows: Sequence[MoveRow], flag: Callable[[MoveRow], bool] = lambda r: r.error) -> float:
    return sum(flag(r) for r in rows) / len(rows) if rows else 0.0
