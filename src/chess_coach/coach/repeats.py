"""The same mistake in the same position, game after game.

A repeat is one of your counted errors (an inaccuracy or worse; in bullet only your first 10
moves, and never a move made short on time) played from the same position in 3 or more games.
Named book moves are left out, as in coach.repertoire: 2.d4 in the Blackmar-Diemer is a choice of
gambit, not a slip, whatever the engine thinks of it.
Each comes with the better move, their best answer to yours (the stored best move of the next
ply, and how often they found it), the reasons in chess terms (coach.principles), links to the
games and whether a puzzle drills the position. Nothing here runs the engine.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime

from chess_coach.coach.principles import allows, explain
from chess_coach.coach.repertoire import is_book
from chess_coach.models import Game, Judgement, MoveFact, Puzzle
from chess_coach.patterns.features import MoveRow, move_rows

MIN_GAMES = 3
ERRORS = (Judgement.INACCURACY, Judgement.MISTAKE, Judgement.BLUNDER)
LINKS = 5  # games linked per repeat, newest first


def _number(move_number: int, color: str) -> str:
    return f"{move_number}{'.' if color == 'white' else '...'}"


@dataclass(slots=True)
class Repeat:
    fen: str  # before your move
    uci: str
    move: str  # "3...Bg7"
    better: str  # "3...Nf6"
    best_uci: str
    game_ids: list[str]  # oldest first
    last: datetime | None
    family: str | None  # the opening it comes up in most
    urls: list[str]  # at the move, newest first
    punish: str | None = None  # their best answer, "4.Qxf7+"
    punish_uci: str | None = None
    punished: int = 0  # the games where they played it...
    replies: int = 0  # ...of those where they answered at all
    reasons: list[str] = field(default_factory=list)
    puzzle: str | None = None  # a puzzle that drills this position

    @property
    def games(self) -> int:
        return len(self.game_ids)


def _when(game: Game) -> str:
    return game.played_at.isoformat() if game.played_at else ""


def repeats(
    games: Sequence[Game],
    moves: Sequence[MoveFact],
    replies: Mapping[tuple[str, int], MoveFact],
    puzzles: Sequence[Puzzle] = (),
    min_games: int = MIN_GAMES,
) -> list[Repeat]:
    """Your errors repeated from the same position in `min_games`+ games, most games first, then
    the most recent. `replies` holds the opponent's move after yours by (game id, your ply), as
    `Store.replies_to_errors` gives it; without one a repeat has no punishment."""
    groups: dict[tuple[str, str], list[MoveRow]] = {}
    for r in move_rows([g for g in games if g.analyzed_depth], moves):
        if r.fact.judgement in ERRORS:
            groups.setdefault((r.fact.fen_before, r.fact.uci), []).append(r)
    out = [
        _repeat(sorted(rows, key=lambda r: _when(r.game)), replies, puzzles)
        for (fen, uci), rows in groups.items()
        if len({r.game.id for r in rows}) >= min_games and not is_book(fen, uci)
    ]
    return sorted(out, key=lambda r: (r.games, r.last.isoformat() if r.last else ""), reverse=True)


def _repeat(
    rows: list[MoveRow], replies: Mapping[tuple[str, int], MoveFact], puzzles: Sequence[Puzzle]
) -> Repeat:
    first = rows[0].fact
    best_uci, _ = Counter(r.fact.best_move for r in rows).most_common(1)[0]
    best_san = next(r.fact.best_san for r in rows if r.fact.best_move == best_uci)
    number = _number(first.move_number, first.color.value)
    families = Counter(r.game.opening_family for r in rows if r.game.opening_family)
    out = Repeat(
        fen=first.fen_before,
        uci=first.uci,
        move=f"{number}{first.san}",
        better=f"{number}{best_san}",
        best_uci=best_uci,
        game_ids=list(dict.fromkeys(r.game.id for r in rows)),
        last=rows[-1].game.played_at,
        family=families.most_common(1)[0][0] if families else None,
        urls=[url for r in reversed(rows) if (url := r.game.move_url(r.fact.ply))][:LINKS],
        puzzle=next(
            (p.id for p in puzzles if p.fen == first.fen_before and not p.is_right(first.uci)),
            None,
        ),
    )
    theirs = [m for r in rows if (m := replies.get((r.game.id, r.fact.ply))) is not None]
    let: list[str] = []
    if theirs:
        uci, found = Counter(m.best_move for m in theirs).most_common(1)[0]
        reply = next(m for m in theirs if m.best_move == uci)
        out.punish = f"{_number(reply.move_number, reply.color.value)}{reply.best_san}"
        out.punish_uci = uci
        out.punished = sum(m.uci == uci for m in theirs)
        out.replies = len(theirs)
        let = allows(reply.fen_before, uci, yours=True)
    out.reasons = [*let, *explain(first.fen_before, first.uci, best_uci).all()][:3]
    return out


def again(found: Sequence[Repeat], game_ids: set[str]) -> list[Repeat]:
    """The repeats these games (newly synced ones) played once more."""
    return [r for r in found if game_ids.intersection(r.game_ids)]


def ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        return f"{n}th"
    return f"{n}{ {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th') }"


def alert(r: Repeat) -> str:
    """'You played 3...Bg7 again (12th time); it has a puzzle.'"""
    drill = "it has a puzzle" if r.puzzle else f"the better move is {r.better}"
    return f"You played {r.move} again ({ordinal(r.games)} time); {drill}."
