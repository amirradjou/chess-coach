"""Which opening to study: where you score worst, and the slip you repeat there."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field

from chess_coach.models import Game, MoveFact, Phase
from chess_coach.openings import book_line
from chess_coach.patterns.features import ERRORS, counted, result_of


@dataclass(slots=True)
class Slip:
    move_number: int
    color: str
    played: str
    best: str
    games: int
    example_url: str | None

    @property
    def move(self) -> str:
        return f"{self.move_number}{'.' if self.color == 'white' else '...'}{self.played}"


@dataclass(slots=True)
class OpeningStat:
    family: str
    color: str
    games: int
    wins: int
    draws: int
    losses: int
    name: str  # the most common full name, for the book line
    book: str | None
    slips: list[Slip] = field(default_factory=list)

    @property
    def decided(self) -> int:
        return self.wins + self.draws + self.losses

    @property
    def score(self) -> float | None:
        """Over the games whose result counts; None when there are none (only bullet)."""
        return (self.wins + self.draws / 2) / self.decided if self.decided else None


def openings(
    games: Sequence[Game], moves: Sequence[MoveFact], min_games: int = 5
) -> list[OpeningStat]:
    """Opening families (by your colour) with at least `min_games` analysed games, worst score
    first, each with the opening mistakes you made in more than one game. Only families with
    `min_games` results that count (not bullet) are ranked by score; the rest come last."""
    by_key: dict[tuple[str, str], list[Game]] = {}
    by_id: dict[str, Game] = {}
    for g in games:
        if g.analyzed_depth and g.opening_family:
            by_key.setdefault((g.opening_family, g.user_color.value), []).append(g)
            by_id[g.id] = g
    errors: dict[str, list[MoveFact]] = {}
    for m in moves:
        g = by_id.get(m.game_id)
        if (
            g is not None
            and m.is_user
            and m.phase is Phase.OPENING
            and m.judgement in ERRORS
            and counted(m, g)
        ):
            errors.setdefault(m.game_id, []).append(m)
    out = []
    for (family, color), gs in by_key.items():
        if len(gs) < min_games:
            continue
        results = Counter(result_of(g) for g in gs)
        name = Counter(g.opening for g in gs if g.opening).most_common(1)[0][0]
        url = {g.id: g.url for g in gs}
        seen: dict[tuple[str, str], set[str]] = {}
        where: dict[tuple[str, str], MoveFact] = {}
        for g in gs:
            for m in errors.get(g.id, []):
                key = (m.fen_before, m.san)
                seen.setdefault(key, set()).add(g.id)
                where[key] = m
        slips = [
            Slip(
                where[k].move_number,
                where[k].color.value,
                where[k].san,
                where[k].best_san,
                len(ids),
                url[sorted(ids)[0]],
            )
            for k, ids in seen.items()
            if len(ids) > 1
        ]
        slips.sort(key=lambda s: (-s.games, s.move_number))
        out.append(
            OpeningStat(
                family,
                color,
                len(gs),
                results["win"],
                results["draw"],
                results["loss"],
                name,
                book_line(name),
                slips[:3],
            )
        )
    return sorted(out, key=lambda o: (o.decided < min_games, o.score or 0.0, -o.games))
