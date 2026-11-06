"""Remembering opening training: each position you answer in Learn openings becomes a card,
scheduled with SM-2 like a puzzle, so the punishments and lines you miss come back first.

Only your first answer at a position counts (as with puzzles), and asking to see the move counts
as a miss. A move is right when it is your usual sound move, or within COACH_MARGIN_CP of the
engine's best."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import date

import chess

from chess_coach.coach.tree import Edge, Node, walk
from chess_coach.engine.stockfish import LineEval
from chess_coach.models import CoachCard, card_id
from chess_coach.puzzles.review import Analyse
from chess_coach.puzzles.srs import schedule

PUNISH = "punish"  # right after the opponent's mistake: find the punishment
LINE = "line"  # your move on your own line
COACH_MARGIN_CP = 30  # a move this close to the engine's best counts


def answer(
    card: CoachCard | None,
    family: str,
    color: str,
    path: Sequence[str],
    kind: str,
    correct: bool,
    today: date,
) -> CoachCard:
    """The card after this answer; a new card the first time you answer here."""
    if card is None:
        card = CoachCard(family, color, list(path), kind, today.isoformat())
    return schedule(replace(card, kind=kind), correct, today)  # the tree grows with your games


def kind_at(root: Node, path: Sequence[str]) -> str:
    """Which kind of card a position is: right after a mistake of theirs you can punish, or not."""
    if not path:
        return LINE
    parent = walk(root, path[:-1])
    edge = parent.edges.get(path[-1]) if parent else None
    return PUNISH if edge is not None and edge.punishable else LINE


def sound_moves(node: Node | None) -> list[Edge]:
    """Your moves here that are the engine's or close to it, the most played first."""
    edges = sorted(node.edges.values(), key=lambda e: -len(e.facts)) if node else []
    return [e for e in edges if e.by_user and e.verdict in ("best", "good")]


@dataclass(slots=True)
class Check:
    ok: bool
    best: chess.Move | None  # the engine's, or without one the move stored for the position
    loss: int | None = None  # centipawns against the best; None without an engine
    reply: LineEval | None = None  # the engine's line after your move


def check_move(
    board: chess.Board, move: chess.Move, analyse: Analyse, stored: chess.Move | None = None
) -> Check:
    """Is this move good enough here? Within COACH_MARGIN_CP of the engine's best; without an
    engine, the stored move (with none stored, any move stands)."""
    after = board.copy(stack=False)
    after.push(move)
    ev, reply = analyse(board.fen()), analyse(after.fen())
    if ev is not None and ev.move is not None and reply is not None:
        loss = max(0, ev.cp + reply.cp)  # the reply is scored for the other side
        return Check(move == ev.move or loss <= COACH_MARGIN_CP, ev.move, loss, reply)
    return Check(stored is None or move == stored, stored)


@dataclass(slots=True)
class Judged:
    correct: bool
    target: chess.Move | None  # the move to learn here
    check: Check | None = None  # the engine's view, when it was asked


def judge(
    kind: str, node: Node | None, board: chess.Board, move: chess.Move | None, analyse: Analyse
) -> Judged:
    """A due card's answer (`move` is None when you asked to see it). On your own line your usual
    sound move is right and is the one to learn, so training keeps to your opening; right after
    their mistake only the punishment counts, as in Learn openings."""
    stored = chess.Move.from_uci(node.best.best_move) if node and node.best else None
    sound = sound_moves(node) if kind == LINE else []
    target = chess.Move.from_uci(sound[0].uci) if sound else stored
    if move is None:
        if target is None and (ev := analyse(board.fen())) is not None:
            target = ev.move
        return Judged(False, target)
    if any(e.uci == move.uci() for e in sound):
        return Judged(True, target)
    check = check_move(board, move, analyse, stored)
    return Judged(check.ok, target or check.best, check)


@dataclass(slots=True)
class DeckCard:
    """One opponent mistake to punish, with where it stands in your practice."""

    path: list[str]  # to their mistake: you're to move
    edge: Edge
    state: str  # "due", "new" (never answered) or "later"
    due: str | None  # when it comes back; None before your first answer
    found: bool | None  # whether your latest answer was right; None before your first answer


def deck(
    mistakes: Sequence[tuple[list[str], Edge]],
    family: str,
    color: str,
    cards: dict[str, CoachCard],
    last: dict[str, bool],
    today: str,
) -> list[DeckCard]:
    """The punishments to practise: due ones first (most overdue first), then the ones you haven't
    tried (most often met first), then the rest by when they come back."""
    out = []
    for path, edge in mistakes:
        cid = card_id(color, family, path)
        card = cards.get(cid)
        state = "new" if card is None else "due" if card.due <= today else "later"
        out.append(DeckCard(path, edge, state, card.due if card else None, last.get(cid)))
    rank = {"due": 0, "new": 1, "later": 2}
    return sorted(out, key=lambda d: (rank[d.state], d.due or ""))


@dataclass(slots=True)
class Summary:
    found: int  # punishments whose latest answer was right
    total: int  # punishable mistakes you meet (in 2+ games)
    due: int  # cards due today, both kinds
    lines: int  # positions on your own lines you've answered
    due_paths: list[list[str]] = field(default_factory=list)  # most overdue first


def summary(punish: Sequence[DeckCard], cards: Sequence[CoachCard], today: str) -> Summary:
    """One opening's practice so far. `cards` come soonest due first (Store.coach_cards)."""
    due = [c for c in cards if c.due <= today]
    return Summary(
        found=sum(d.found is True for d in punish),
        total=len(punish),
        due=len(due),
        lines=sum(c.kind == LINE for c in cards),
        due_paths=[c.path for c in due],
    )
