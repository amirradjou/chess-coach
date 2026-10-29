"""Your recurring mistakes, each with what to learn and the positions that show it.

Everything is counted from the stored facts: which piece and which kind of move you get wrong most,
which kind of best move you keep missing, the motifs, and the situations (phase, clock, move
number, opening) where your error rate is clearly above your own. The lesson attached to each is a
general chess principle chosen for that situation, not a judgement about a specific position.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from chess_coach.coach.digest import MOTIFS, describe
from chess_coach.patterns.features import MoveRow, kind_of
from chess_coach.patterns.mining import Evidence, MotifCount, Pattern, _evidence

LESSONS: dict[tuple[str, str], str] = {
    ("phase", "opening"): (
        "Know the first eight to ten moves of your main lines and the plan behind them. When you "
        "leave the book, develop and castle before anything else."
    ),
    ("phase", "middlegame"): (
        "Before every move, check the forcing moves for both sides: checks, captures and threats. "
        "Most middlegame mistakes are a one-move tactic."
    ),
    ("phase", "endgame"): (
        "Activate your king, push passed pawns, trade pieces rather than pawns when ahead, and "
        "slow down: endgames reward care more than speed."
    ),
    ("moves", "11-20"): (
        "This is where the opening ends. Around move 12, stop and choose a plan (a pawn break, a "
        "weak square, a target) instead of making moves that only look useful."
    ),
    ("moves", "21-30"): (
        "The middlegame peaks here. Count the pieces attacking and defending each square before "
        "you commit to a capture or an advance."
    ),
    ("moves", "31+"): (
        "Late in the game the clock and fatigue catch up. Simplify when you're ahead and keep "
        "checking what every capture allows."
    ),
    ("clock", "under 30s"): (
        "You rush critical moves. Spend less time early so you have it later, and keep the "
        "position simple when you're short."
    ),
    ("clock", "30-60s"): (
        "With under a minute you lose accuracy. Budget your time: aim to reach move 30 with at "
        "least a third of your clock."
    ),
    ("clock", "1-3 min"): (
        "Even with a few minutes left you start to hurry. Take five seconds on every capture and "
        "every move that changes the pawn structure."
    ),
    ("kind", "capture"): (
        "Before capturing, look at the recapture and at their best reply. Many of your captures "
        "lose material or hand over the initiative."
    ),
    ("kind", "check"): (
        "A check isn't automatically good. Ask where their king goes and what that position "
        "looks like."
    ),
    ("kind", "quiet"): (
        "You play quiet moves when something forcing was available. Look for checks and captures "
        "first, even when nothing seems to be happening."
    ),
    ("piece", "queen"): (
        "Your queen moves go wrong most. Before moving it, check it can't be trapped or chased "
        "with tempo, and what it stops defending."
    ),
    ("piece", "king"): (
        "King moves: watch for checks and forks on the squares around it, and keep it safe until "
        "the queens are off."
    ),
    ("piece", "knight"): (
        "Knight moves: check the squares it leaves undefended and any forks against it."
    ),
    ("piece", "bishop"): (
        "Bishop moves: check the diagonal it leaves and whether it can be trapped or exchanged "
        "for your good pieces."
    ),
    ("piece", "rook"): (
        "Rook moves: watch your back rank, and put rooks on open files before attacking."
    ),
    ("piece", "pawn"): (
        "Pawn moves can't be taken back: each one leaves squares weak. Ask what it gives up "
        "before you push."
    ),
}
_ORDER = ("piece", "kind", "moves", "clock", "phase", "opening", "colour", "speed")


def lesson_for(features: Sequence[tuple[str, str]]) -> str:
    """The lesson of the most specific feature in a pattern."""
    by_name = dict(features)
    for name in _ORDER:
        value = by_name.get(name)
        if value is None:
            continue
        if (name, value) in LESSONS:
            return LESSONS[(name, value)]
        if name == "opening":
            return f"In the {value}, study the usual plans: see your line and fixes in Openings."
        if name == "colour":
            return f"With {value.capitalize()} you go wrong more: review your openings with it."
        if name == "speed":
            return (
                f"In {value} you go wrong more: keep positions simple and play a few longer games."
            )
    return "Review the positions below and replay the better move."


@dataclass(slots=True)
class Habit:
    title: str
    detail: str
    lesson: str
    mistakes: int
    games: int
    examples: list[Evidence] = field(default_factory=list)
    practice: str | None = None  # puzzle tag to practise this habit


_MISSED = {"capture": "captures", "check": "checks"}


def missed_kinds(rows: Sequence[MoveRow], examples: int = 3, min_count: int = 8) -> list[Habit]:
    """Forcing moves you keep missing: errors where the best move was a capture (or check) and
    you played something else, when that happens more often than such moves come up at all."""
    errors = [r for r in rows if r.error]
    if not errors or not rows:
        return []
    out = []
    for kind, plural in _MISSED.items():
        missed = [
            r for r in errors if kind_of(r.fact.best_san) == kind and kind_of(r.fact.san) != kind
        ]
        everywhere = sum(kind_of(r.fact.best_san) == kind for r in rows) / len(rows)
        share = len(missed) / len(errors)
        if len(missed) < min_count or share < 1.3 * everywhere:
            continue
        n_games = len({r.fact.game_id for r in missed})
        out.append(
            Habit(
                title=f"You miss {plural}",
                detail=(
                    f"In {share:.0%} of your mistakes the best move was a {kind} you didn't play "
                    f"({len(missed)} moves in {n_games} games), while "
                    f"a {kind} is the best move in {everywhere:.0%} of all your positions."
                ),
                lesson=(
                    "Look at every capture and every check for both sides before choosing a move: "
                    "the forcing moves first, then the quiet ones."
                ),
                mistakes=len(missed),
                games=len({r.fact.game_id for r in missed}),
                examples=_evidence(missed, examples),
                practice="missed_tactic",
            )
        )
    return out


def from_patterns(patterns: Sequence[Pattern], top: int = 6) -> list[Habit]:
    return [
        Habit(
            title=describe(p.features),
            detail=(
                f"{p.hits} mistakes or blunders in {p.n} moves across {p.games} games: "
                f"{p.rate:.0%} "
                f"against your usual {p.baseline:.0%} ({p.lift:.1f}×)."
            ),
            lesson=lesson_for(p.features),
            mistakes=p.hits,
            games=p.games,
            examples=p.evidence,
            practice=dict(p.features).get("phase"),
        )
        for p in patterns[:top]
    ]


def from_motifs(motifs: Sequence[MotifCount], top: int = 5) -> list[Habit]:
    out = []
    for m in motifs[:top]:
        what, tip = MOTIFS.get(m.tag, (m.tag.replace("_", " "), ""))
        out.append(
            Habit(
                title=what[0].upper() + what[1:],
                detail=f"{m.moves} moves in {m.games} games.",
                lesson=(tip[0].upper() + tip[1:] + ".") if tip else "",
                mistakes=m.moves,
                games=m.games,
                examples=m.evidence,
                practice=m.tag,
            )
        )
    return out


def from_pressure(motifs: Sequence[MotifCount]) -> list[Habit]:
    """Mistakes made short on time that keep repeating (see mining.repeated_under_pressure)."""
    out = from_motifs(motifs, top=len(motifs))
    for habit in out:
        habit.detail = f"Short on time, but it keeps happening: {habit.detail}"
    return out
