"""The tables patterns are mined over: one row per move the user played, one row per game.

Every feature is a plain (name, value) pair computed in code from the stored facts, so a pattern
is always a statement about counted moves or games, never a guess.
"""

from __future__ import annotations

import io
from collections.abc import Iterable
from dataclasses import dataclass, field

import chess
import chess.pgn

from chess_coach.models import Game, Judgement, MoveFact

ERRORS = (Judgement.MISTAKE, Judgement.BLUNDER)
CASTLED_BY = 10  # "castled by move 10"


@dataclass(frozen=True, slots=True)
class MoveRow:
    """One move the user played, with the slices it belongs to."""

    fact: MoveFact
    game: Game
    features: dict[str, str]

    @property
    def error(self) -> bool:
        return self.fact.judgement in ERRORS


@dataclass(frozen=True, slots=True)
class GameRow:
    """One analysed game, with the game-level slices it belongs to."""

    game: Game
    features: dict[str, str]
    moves: list[MoveFact] = field(default_factory=list)

    @property
    def lost(self) -> bool:
        return self.game.user_result == "loss"


def move_bucket(move_number: int) -> str:
    if move_number <= 10:
        return "1-10"
    if move_number <= 20:
        return "11-20"
    if move_number <= 30:
        return "21-30"
    return "31+"


def clock_bucket(seconds: float | None) -> str | None:
    if seconds is None:
        return None
    if seconds < 30:
        return "under 30s"
    if seconds < 60:
        return "30-60s"
    if seconds < 180:
        return "1-3 min"
    return "over 3 min"


def castling_move(game: Game) -> int | None:
    """The move number on which the user castled; None if they never did or the PGN won't parse."""
    parsed = chess.pgn.read_game(io.StringIO(game.pgn))
    if parsed is None:
        return None
    board = parsed.board()
    color = chess.WHITE if game.user_color.value == "white" else chess.BLACK
    for move in parsed.mainline_moves():
        if board.turn == color and board.is_castling(move):
            return board.fullmove_number
        board.push(move)
    return None


def castling(game: Game) -> str:
    n = castling_move(game)
    if n is None:
        return "never castled"
    return (
        f"castled by move {CASTLED_BY}" if n <= CASTLED_BY else f"castled after move {CASTLED_BY}"
    )


def move_rows(games: Iterable[Game], moves: Iterable[MoveFact]) -> list[MoveRow]:
    """The user's moves in analysed games, sliced by phase, colour, speed, opening, move, clock."""
    by_id = {g.id: g for g in games}
    rows = []
    for m in moves:
        game = by_id.get(m.game_id)
        if game is None or not m.is_user:
            continue
        feats = {
            "phase": m.phase.value,
            "colour": m.color.value,
            "speed": game.speed.value,
            "moves": move_bucket(m.move_number),
        }
        if family := game.opening_family:
            feats["opening"] = family
        if clock := clock_bucket(m.clock_after):
            feats["clock"] = clock
        rows.append(MoveRow(m, game, feats))
    return rows


def game_rows(games: Iterable[Game], moves: Iterable[MoveFact]) -> list[GameRow]:
    """Analysed games with a known result, with colour, speed, opening and castling slices."""
    by_game: dict[str, list[MoveFact]] = {}
    for m in moves:
        if m.is_user:
            by_game.setdefault(m.game_id, []).append(m)
    rows = []
    for g in games:
        if g.user_result == "unknown" or g.id not in by_game:
            continue
        feats = {"colour": g.user_color.value, "speed": g.speed.value, "castling": castling(g)}
        if family := g.opening_family:
            feats["opening"] = family
        if any("queen_trade_behind" in m.motifs for m in by_game[g.id]):
            feats["queens"] = "traded queens behind in development"
        rows.append(GameRow(g, feats, by_game[g.id]))
    return rows
