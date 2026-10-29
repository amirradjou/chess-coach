"""Plain data records shared by importers, the analysis pipeline and the store."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class Color(StrEnum):
    WHITE = "white"
    BLACK = "black"

    @property
    def other(self) -> Color:
        return Color.BLACK if self is Color.WHITE else Color.WHITE


class Speed(StrEnum):
    """Lichess's time-control classes, so stats line up with what players already know."""

    ULTRABULLET = "ultrabullet"
    BULLET = "bullet"
    BLITZ = "blitz"
    RAPID = "rapid"
    CLASSICAL = "classical"
    CORRESPONDENCE = "correspondence"
    UNKNOWN = "unknown"


class Phase(StrEnum):
    OPENING = "opening"
    MIDDLEGAME = "middlegame"
    ENDGAME = "endgame"


class Judgement(StrEnum):
    BEST = "best"
    GOOD = "good"
    INACCURACY = "inaccuracy"
    MISTAKE = "mistake"
    BLUNDER = "blunder"


@dataclass(slots=True)
class Game:
    """One imported game. `id` is globally unique across sources ("lichess:abc123")."""

    id: str
    source: str
    source_id: str
    user: str
    user_color: Color
    white: str
    black: str
    result: str
    pgn: str
    url: str | None = None
    white_elo: int | None = None
    black_elo: int | None = None
    played_at: datetime | None = None
    time_control: str | None = None
    speed: Speed = Speed.UNKNOWN
    rated: bool | None = None
    eco: str | None = None
    opening: str | None = None
    termination: str | None = None
    analyzed_depth: int | None = None

    @property
    def user_result(self) -> str:
        """'win' | 'loss' | 'draw' | 'unknown' from the coached user's point of view."""
        if self.result == "1/2-1/2":
            return "draw"
        if self.result == "1-0":
            return "win" if self.user_color is Color.WHITE else "loss"
        if self.result == "0-1":
            return "win" if self.user_color is Color.BLACK else "loss"
        return "unknown"

    @property
    def opening_family(self) -> str | None:
        """The part of the opening name before the colon, for grouping across variations."""
        return self.opening.split(":", 1)[0].strip() if self.opening else None

    @property
    def user_elo(self) -> int | None:
        return self.white_elo if self.user_color is Color.WHITE else self.black_elo

    @property
    def opponent_elo(self) -> int | None:
        return self.black_elo if self.user_color is Color.WHITE else self.white_elo


@dataclass(slots=True)
class MoveFact:
    """What the engine established about one move. Evals are centipawns from the mover's side."""

    game_id: str
    ply: int
    move_number: int
    color: Color
    is_user: bool
    san: str
    uci: str
    fen_before: str
    eval_before: int
    eval_after: int
    best_move: str
    best_san: str
    cp_loss: int
    wp_before: float
    wp_after: float
    wp_loss: float
    judgement: Judgement
    phase: Phase
    depth: int
    mate_before: int | None = None
    mate_after: int | None = None
    pv: str = ""
    clock_after: float | None = None
    motifs: list[str] = field(default_factory=list)


@dataclass(slots=True)
class Puzzle:
    """A position from the user's own game where one move clearly stood out and they missed it."""

    id: str  # "<game_id>:<ply>"
    game_id: str
    ply: int
    fen: str  # before the user's move; the user is to move
    solution: str  # UCI of the move to find
    solution_san: str
    played_san: str  # what the user played in the game
    line: str  # the engine's continuation after the solution, UCI, space-separated
    gap_cp: int  # how much better the solution is than the second-best move
    depth: int
    tags: list[str] = field(default_factory=list)
    # Other moves that also count as right (opening fixes, where several moves are fine), UCI.
    accept: list[str] = field(default_factory=list)

    def is_right(self, uci: str) -> bool:
        return uci == self.solution or uci in self.accept


@dataclass(slots=True)
class Review:
    """SM-2 schedule for one puzzle."""

    puzzle_id: str
    due: str  # ISO date
    interval: int = 0  # days
    ease: float = 2.5
    reps: int = 0
    lapses: int = 0
