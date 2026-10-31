"""Remembering opening training: each position you answer in Learn openings becomes a card,
scheduled with SM-2 like a puzzle, so the punishments and lines you miss come back first.

Only your first answer at a position counts (as with puzzles), and asking to see the move counts
as a miss. The page judges the move: your usual sound move, or within COACH_MARGIN_CP of the
engine's best (web.app: /api/coach/check)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import date

from chess_coach.coach.tree import Edge
from chess_coach.models import CoachCard, card_id
from chess_coach.puzzles.srs import schedule

PUNISH = "punish"  # right after the opponent's mistake: find the punishment
LINE = "line"  # your move on your own line


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
