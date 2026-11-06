"""What to practise, and in what order: reviews first, then new puzzles from the positions that
come up most in your games. Today is a session of a size you can finish, and it keeps no state of
its own: it is worked out from today's answers, wherever you gave them."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date

from chess_coach.db import Store
from chess_coach.models import Puzzle
from chess_coach.openings import epd
from chess_coach.patterns.features import counted
from chess_coach.puzzles.make import left_out

REACHED_OFTEN = 2  # games: a position you keep reaching is worth knowing first
RECENT_DAYS = 90  # then your recent games: the openings you play now


@dataclass(frozen=True, slots=True)
class Facts:
    """What serving and ordering need from your games. Slow to read, so the web app keeps it."""

    left_out: frozenset[str]  # puzzles kept for their history, never served
    reached: Mapping[str, int]  # position (EPD) -> how many games you reached it in
    played: Mapping[str, date]  # game id -> the day it was played


def read_facts(store: Store) -> Facts:
    games = list(store.iter_games())
    moves = list(store.iter_moves(user_only=True))
    by_id = {g.id: g for g in games}
    seen: dict[str, set[str]] = {}
    for m in moves:
        # By the bullet rule, a bullet position after move 10 isn't one you're coached on.
        if (game := by_id.get(m.game_id)) is not None and counted(m, game):
            seen.setdefault(epd(m.fen_before), set()).add(m.game_id)
    return Facts(
        left_out=frozenset(left_out(store.puzzles(), games, moves)),
        reached={position: len(ids) for position, ids in seen.items()},
        played={g.id: g.played_at.date() for g in games if g.played_at},
    )


def new_first(puzzles: Sequence[Puzzle], facts: Facts, today: date) -> list[Puzzle]:
    """New puzzles in the order to meet them: positions you reached in 2+ games (the most games
    first), then from games of the last 90 days, then the rest; newest game first within each."""

    def rank(p: Puzzle) -> tuple[int, int, int, str]:
        reached = facts.reached.get(epd(p.fen), 0)
        played = facts.played.get(p.game_id)
        if reached >= REACHED_OFTEN:
            group = 0
        elif played is not None and (today - played).days <= RECENT_DAYS:
            group = 1
        else:
            group = 2
        return group, -reached if group == 0 else 0, -played.toordinal() if played else 0, p.id

    return sorted(puzzles, key=rank)


def practice_queue(
    store: Store, facts: Facts, today: date, tag: str | None = None
) -> tuple[list[Puzzle], list[Puzzle]]:
    """What Practice serves: the puzzles due again (most overdue first), and the new ones."""
    again = [p for p in store.due(today.isoformat(), tag) if p.id not in facts.left_out]
    new = [p for p in store.untried(tag) if p.id not in facts.left_out]
    return again, new_first(new, facts, today)
