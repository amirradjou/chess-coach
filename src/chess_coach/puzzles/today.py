"""What to practise, and in what order: reviews first, then new puzzles from the positions that
come up most in your games. Today is a session of a size you can finish, and it keeps no state of
its own: it is worked out from today's answers, wherever you gave them."""

from __future__ import annotations

from collections import Counter
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from math import ceil

from chess_coach.db import Store
from chess_coach.models import CoachCard, Puzzle
from chess_coach.openings import epd
from chess_coach.patterns.features import counted
from chess_coach.puzzles.make import left_out
from chess_coach.util import practice_day

SESSION = 10  # items a day: few enough to finish
MAX_NEW = 5  # of them new, at most: reviews are what make it stick
SECONDS_PER_ITEM = 30
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


@dataclass(frozen=True, slots=True)
class Item:
    """One thing to practise: a puzzle, or an opening card (which belongs to no one game)."""

    id: str
    game_id: str | None = None
    due: str | None = None  # None for a new puzzle
    puzzle: Puzzle | None = None
    card: CoachCard | None = None

    @property
    def new(self) -> bool:
        return self.due is None


def _spaced(items: Sequence[Item], last_game: str | None) -> list[Item]:
    """`items` in their order, but never two from one game back to back. Only when nothing else is
    left does a game follow itself."""
    left, out = list(items), []
    while left:
        counts = Counter(i.game_id for i in left if i.game_id)
        # A game holding more than half of what's left has to go now, or two of its items touch.
        crowded = next((g for g, n in counts.items() if 2 * n > len(left) and g != last_game), None)
        if crowded:
            choices = [i for i in left if i.game_id == crowded]
        else:
            choices = [i for i in left if i.game_id is None or i.game_id != last_game]
        pick = choices[0] if choices else left[0]
        left.remove(pick)
        out.append(pick)
        last_game = pick.game_id
    return out


def plan(
    again: Sequence[Item],
    new: Sequence[Item],
    *,
    done: int = 0,
    new_done: int = 0,
    games_used: Collection[str] = (),
    last_game: str | None = None,
) -> list[Item]:
    """What is left of today's session, next first: `again` (most overdue first) up to its size,
    then at most MAX_NEW new items over the day, never two from one game. `done`, `new_done`,
    `games_used` (by today's new items) and `last_game` say what today's answers already did."""
    slots = max(0, SESSION - done)
    picked = list(again[:slots])
    room = min(slots - len(picked), MAX_NEW - new_done)
    used = set(games_used)
    for item in new:
        if room <= 0:
            break
        if item.game_id in used:
            continue
        used.add(item.game_id or "")
        picked.append(item)
        room -= 1
    return _spaced(picked, last_game)


@dataclass(slots=True)
class Session:
    items: list[Item]  # what is left, next first
    done: int = 0  # answered today

    @property
    def total(self) -> int:
        return self.done + len(self.items)

    @property
    def again(self) -> int:
        return sum(not i.new for i in self.items)

    @property
    def new(self) -> int:
        return sum(i.new for i in self.items)

    @property
    def minutes(self) -> int:
        return ceil(len(self.items) * SECONDS_PER_ITEM / 60)


def _when(at: str) -> date:
    return practice_day(datetime.fromisoformat(at))


def _candidates(store: Store, facts: Facts, day: date) -> tuple[list[Item], list[Item]]:
    """Everything due on `day` (puzzles and opening cards, most overdue first), and the new
    puzzles."""
    puzzles, new = practice_queue(store, facts, day)
    again = []
    for p in puzzles:
        review = store.review(p.id)
        again.append(Item(p.id, p.game_id, review.due if review else day.isoformat(), puzzle=p))
    again += [
        Item(c.id, due=c.due, card=c) for c in store.coach_cards() if c.due <= day.isoformat()
    ]
    again.sort(key=lambda i: (i.due, i.id))
    return again, [Item(p.id, p.game_id, puzzle=p) for p in new]


def session(store: Store, facts: Facts, day: date) -> Session:
    """Today's session as today's answers leave it. An answer in Practice or Learn openings counts
    as much as one given here."""
    attempts, answers = store.attempts(), store.coach_attempts()
    game_of = {p.id: p.game_id for p in store.puzzles()}
    first_tried: dict[str, date] = {}
    for puzzle_id, at, _ in attempts:
        first_tried.setdefault(puzzle_id, _when(at))
    new_today = {pid for pid, when in first_tried.items() if when == day}
    todays = sorted((at, item) for item, at, _ in [*attempts, *answers] if _when(at) == day)
    done = {item for _, item in todays}
    again, new = _candidates(store, facts, day)
    items = plan(
        again,
        new,
        done=len(done),
        new_done=len(new_today),
        games_used={game_of[pid] for pid in new_today if pid in game_of},
        last_game=game_of.get(todays[-1][1]) if todays else None,  # a card has no game
    )
    return Session(items, len(done))


def upcoming(store: Store, facts: Facts, day: date) -> Session:
    """`day`'s session as it stands with no answers yet: what tomorrow looks like."""
    return Session(plan(*_candidates(store, facts, day)))


def status_line(today: Session, tomorrow: Session) -> str:
    """One line for the day, such as: Today: 10 to practise (about 5 min): 5 due again, 5 new"""
    if today.items:
        left = len(today.items)
        what = f"{left} left of {today.total}" if today.done else f"{left} to practise"
        counts = f"{today.again} due again, {today.new} new"
        return f"Today: {what} (about {today.minutes} min): {counts}"
    tail = f"Tomorrow: {tomorrow.again} due again, {tomorrow.new} new"
    if today.done:
        return f"Today: done, {today.done} practised. {tail}"
    return f"Today: nothing to practise. {tail}"


def days_practised(
    attempts: Iterable[tuple[str, str, bool]],
    answers: Iterable[tuple[str, str, bool]],
    today: date,
    days: int = 7,
) -> int:
    """On how many of the last `days` days (today included) you answered anything."""
    practised = {_when(at) for _, at, _ in [*attempts, *answers]}
    return sum(today - timedelta(days=n) in practised for n in range(days))


@dataclass(frozen=True, slots=True)
class Accuracy:
    right: int = 0
    tried: int = 0


def accuracy(
    attempts: Iterable[tuple[str, str, bool]], answers: Iterable[tuple[str, str, bool]]
) -> tuple[Accuracy, Accuracy]:
    """How you do on reviews and on first tries, as (reviews, first tries). A first try tests what
    you already knew; a review tests whether the practice stuck."""
    seen: set[str] = set()
    review = [0, 0]
    first = [0, 0]
    for item, _, correct in [*attempts, *answers]:
        counts = review if item in seen else first
        seen.add(item)
        counts[0] += correct
        counts[1] += 1
    return Accuracy(*review), Accuracy(*first)
