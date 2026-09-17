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
