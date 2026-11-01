"""Ignored threats and unsafe moves across your games, next to your opponents' as the baseline.

`check` runs engine.threats over every counted mistake and blunder not checked yet, yours and your
opponents', and stores one row per move. `report` gives the share of each label among your checked
mistakes and blunders and among theirs, with 95% Wilson intervals and an interval for the
difference: a step is only called yours to work on when you clearly do it more often than they
do. The explanations come from coach.principles, in chess terms.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from itertools import groupby

import chess

from chess_coach.coach.principles import ignored_threat, unsafe_move
from chess_coach.db import Store
from chess_coach.engine.stockfish import Engine, LineEval
from chess_coach.engine.threats import DEPTH, IGNORED, LABELS, UNSAFE, look
from chess_coach.models import Game, MoveFact, ThreatFact
from chess_coach.patterns.features import ERRORS, counted, rushed
from chess_coach.patterns.mining import Evidence, difference, wilson

STEPS: dict[str, tuple[str, str, str]] = {
    # label: (title, what happened, what to practise)
    IGNORED: (
        "Ignored their threat",
        "their last move threatened something and your move didn't meet it",
        "after every move of theirs, ask what it threatens before you look for your own move",
    ),
    UNSAFE: (
        "Unsafe move",
        "nothing was threatened, but your move allowed a winning reply",
        "before you play a move, check every capture and check they will have after it",
    ),
}

Key = tuple[str, int]  # (game id, ply)


def errors(games: Iterable[Game], moves: Iterable[MoveFact]) -> list[MoveFact]:
    """Counted mistakes and blunders in analysed games, both sides', not played short on time."""
    by_id = {g.id: g for g in games if g.analyzed_depth}
    return [
        m
        for m in moves
        if m.judgement in ERRORS
        and m.game_id in by_id
        and counted(m, by_id[m.game_id])
        and not rushed(m, by_id[m.game_id])
    ]


def pending(
    games: Iterable[Game], moves: Iterable[MoveFact], done: Iterable[Key]
) -> list[MoveFact]:
    """The errors not checked yet: newest games first, in move order within a game."""
    games = list(games)
    order = {g.id: (g.played_at.isoformat() if g.played_at else "") for g in games}
    skip = set(done)
    todo = [m for m in errors(games, moves) if (m.game_id, m.ply) not in skip]
    return sorted(todo, key=lambda m: (order[m.game_id], m.game_id, -m.ply), reverse=True)


def check(
    store: Store,
    engine: Engine,
    *,
    depth: int = DEPTH,
    limit: int | None = None,
    on_check: Callable[[int, int], None] | None = None,
) -> int:
    """Check up to `limit` errors not checked yet and store a row for each; returns how many.
    Positions are searched once per run (FEN cache), and each game is stored as it is done, so
    an interrupted run picks up where it stopped."""
    games = list(store.iter_games())
    todo = pending(games, store.iter_moves(judgements=ERRORS), store.threats())[:limit]
    cache: dict[str, LineEval] = {}

    def search(board: chess.Board) -> LineEval:
        fen = board.fen()
        if fen not in cache:
            cache[fen] = engine.evaluate(board, depth=depth)[0]
        return cache[fen]

    done = 0
    for game_id, group in groupby(todo, key=lambda m: m.game_id):
        facts = {f.ply: f for f in store.iter_moves(game_id)}
        rows = []
        for m in group:
            rows.append(look(m, facts.get(m.ply - 1), search, depth))
            done += 1
            if on_check:
                on_check(done, len(todo))
        store.save_threats(rows)
    return done


def reason(fen: str, uci: str, t: ThreatFact) -> str | None:
    """The labelled move in chess terms: what they threatened, or the reply it allowed."""
    if not t.threat or not fen or not uci:
        return None
    if t.label == IGNORED:
        return ignored_threat(
            fen, uci, t.threat, last_san=t.last_san, last_uci=t.last_uci, mate=t.mate
        )
    if t.label == UNSAFE:
        return unsafe_move(fen, uci, t.threat, mate=t.mate)
    return None


@dataclass(frozen=True, slots=True)
class Share:
    hits: int
    n: int

    @property
    def rate(self) -> float:
        return self.hits / self.n if self.n else 0.0

    @property
    def interval(self) -> tuple[float, float]:
        return wilson(self.hits, self.n)


@dataclass(slots=True)
class Example:
    evidence: Evidence
    threat: ThreatFact
    why: str


@dataclass(slots=True)
class Step:
    """One label: its share of your checked errors and of your opponents'."""

    label: str
    you: Share
    them: Share
    examples: list[Example] = field(default_factory=list)

    @property
    def title(self) -> str:
        return STEPS[self.label][0]

    @property
    def what(self) -> str:
        return STEPS[self.label][1]

    @property
    def tip(self) -> str:
        return STEPS[self.label][2]

    @property
    def diff(self) -> tuple[float, float]:
        """95% interval for your share minus theirs."""
        return difference(self.you.hits, self.you.n, self.them.hits, self.them.n)

    @property
    def verdict(self) -> str:
        low, high = self.diff
        if low > 0:
            return "more often than your opponents"
        if high < 0:
            return "less often than your opponents"
        return "same as your opponents"

    @property
    def yours(self) -> bool:
        """A peer gap: only then is this a step for you to work on."""
        return self.diff[0] > 0


@dataclass(slots=True)
class Report:
    you: int  # your counted mistakes and blunders that were checked
    them: int  # your opponents'
    unchecked: int  # errors (both sides) not checked yet: run `chess-coach threats`
    steps: list[Step]


def report(
    games: Sequence[Game],
    moves: Iterable[MoveFact],
    threats: Mapping[Key, ThreatFact],
    examples: int = 3,
) -> Report:
    """Shares of each label among the checked errors in `games`, yours against your opponents'.
    `moves` may hold every move or only the errors; the rest are ignored."""
    by_id = {g.id: g for g in games}
    found = errors(games, moves)
    checked = [m for m in found if (m.game_id, m.ply) in threats]
    yours = [m for m in checked if m.is_user]
    theirs = [m for m in checked if not m.is_user]
    steps = []
    for label in LABELS:

        def hits(ms: list[MoveFact], label: str = label) -> list[MoveFact]:
            return [m for m in ms if threats[(m.game_id, m.ply)].label == label]

        mine = hits(yours)
        step = Step(label, Share(len(mine), len(yours)), Share(len(hits(theirs)), len(theirs)))
        seen: set[str] = set()
        for m in sorted(mine, key=lambda m: (-m.wp_loss, m.game_id, m.ply)):
            if len(step.examples) >= examples:
                break
            t = threats[(m.game_id, m.ply)]
            why = None if m.game_id in seen else reason(m.fen_before, m.uci, t)
            if why is not None:  # the worst ones, one per game
                seen.add(m.game_id)
                step.examples.append(Example(Evidence.of(m, by_id[m.game_id]), t, why))
        steps.append(step)
    return Report(len(yours), len(theirs), len(found) - len(checked), steps)


def percent(share: Share) -> str:
    """The share and its 95% interval, as in "18% (15-21%)"."""
    low, high = share.interval
    return f"{share.rate:.0%} ({100 * low:.0f}-{high:.0%})"


def gap(step: Step) -> str:
    """Your share minus theirs in percentage points and its 95% interval, as in "+3.4 points,
    +0.2 to +6.5"."""
    low, high = step.diff
    d = step.you.rate - step.them.rate
    return f"{100 * d:+.1f} points, {100 * low:+.1f} to {100 * high:+.1f}"
