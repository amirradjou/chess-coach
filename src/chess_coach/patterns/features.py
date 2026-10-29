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

from chess_coach.models import Game, Judgement, MoveFact, Speed
from chess_coach.sources.pgn import parse_time_control

ERRORS = (Judgement.MISTAKE, Judgement.BLUNDER)
CASTLED_BY = 10  # "castled by move 10"
# Short on time: moves played this fast are reflexes, so their mistakes only count when the same
# thing keeps happening (see REPEAT_MOVES / REPEAT_GAMES).
RUSHED_BULLET_SECONDS = 15.0  # bullet: the last quarter of a 1-minute game
RUSHED_FRACTION = 0.10  # any speed: under a tenth of the starting time
REPEAT_MOVES = 15  # a rushed mistake counts once it happens this often...
REPEAT_GAMES = 8  # ...across this many games


def rushed(m: MoveFact, game: Game) -> bool:
    """Played short on time: under 15 s left in bullet, or under 10% of the starting time."""
    if m.clock_after is None:
        return False
    if game.speed in (Speed.BULLET, Speed.ULTRABULLET) and m.clock_after < RUSHED_BULLET_SECONDS:
        return True
    tc = parse_time_control(game.time_control)
    return tc is not None and m.clock_after < RUSHED_FRACTION * tc[0]


@dataclass(frozen=True, slots=True)
class MoveRow:
    """One move the user played, with the slices it belongs to."""

    fact: MoveFact
    game: Game
    features: dict[str, str]
    rushed: bool = False

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


_PIECES = {"K": "king", "Q": "queen", "R": "rook", "B": "bishop", "N": "knight"}


def piece_of(san: str) -> str:
    """Which piece a SAN move moves: pawn, knight, bishop, rook, queen or king (castling = king)."""
    if san.startswith("O-O"):
        return "king"
    return _PIECES.get(san[:1], "pawn")


def kind_of(san: str) -> str:
    """capture, check, castle or quiet (a capturing check counts as a capture)."""
    if san.startswith("O-O"):
        return "castle"
    if "x" in san:
        return "capture"
    if "+" in san or "#" in san:
        return "check"
    return "quiet"


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


def move_rows(
    games: Iterable[Game], moves: Iterable[MoveFact], *, include_rushed: bool = False
) -> list[MoveRow]:
    """The user's moves in analysed games, sliced by phase, colour, speed, opening, move, clock."""
    by_id = {g.id: g for g in games}
    rows = []
    for m in moves:
        game = by_id.get(m.game_id)
        if game is None or not m.is_user:
            continue
        hurried = rushed(m, game)
        if hurried and not include_rushed:
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
        feats["piece"] = piece_of(m.san)
        feats["kind"] = kind_of(m.san)
        rows.append(MoveRow(m, game, feats, hurried))
    return rows


def rushed_rows(games: Iterable[Game], moves: Iterable[MoveFact]) -> list[MoveRow]:
    """Only the moves played short on time."""
    return [r for r in move_rows(games, moves, include_rushed=True) if r.rushed]


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
