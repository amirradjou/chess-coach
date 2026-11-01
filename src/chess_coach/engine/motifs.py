"""Tag *why* a move went wrong, from the board and the engine's facts alone.

Error motifs are only attached to moves that lost win probability; trait motifs describe a
habit (e.g. trading queens while behind in development) regardless of whether it cost anything.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import chess

from chess_coach.engine.evaluation import INACCURACY_WP_LOSS
from chess_coach.models import MoveFact, Phase

PIECE_VALUES = {
    chess.PAWN: 1,
    chess.KNIGHT: 3,
    chess.BISHOP: 3,
    chess.ROOK: 5,
    chess.QUEEN: 9,
    chess.KING: 0,
}
MISSED_MATE_MAX_DISTANCE = 6
TIME_TROUBLE_SECONDS = 20.0
TIME_TROUBLE_FRACTION = 0.10
ENDGAME_WINNING_CP = 200
ENDGAME_THROWN_CP = 100
DEVELOPMENT_DEFICIT = 2

_MINOR_HOME = {
    chess.WHITE: (chess.B1, chess.G1, chess.C1, chess.F1),
    chess.BLACK: (chess.B8, chess.G8, chess.C8, chess.F8),
}
_KING_HOME = {chess.WHITE: chess.E1, chess.BLACK: chess.E8}


@dataclass(slots=True)
class MoveContext:
    """Everything the tagger needs about one move; built by the analysis loop."""

    fact: MoveFact
    board_before: chess.Board
    move: chess.Move
    board_after: chess.Board
    best_move: chess.Move | None = None
    best_pv: list[chess.Move] = field(default_factory=list)
    best_reply: chess.Move | None = None
    reply_pv: list[chess.Move] = field(default_factory=list)
    previous: MoveFact | None = None
    initial_time: int | None = None


def value(piece: chess.Piece | None) -> int:
    return PIECE_VALUES[piece.piece_type] if piece else 0


def material(board: chess.Board, color: chess.Color) -> int:
    """`color`'s material minus the other side's, in the usual points (pawn 1 ... queen 9)."""
    return sum(
        PIECE_VALUES[p.piece_type] * (1 if p.color == color else -1)
        for p in board.piece_map().values()
    )


def after_move(board: chess.Board, move: chess.Move) -> chess.Board:
    """The position after `move` (legal in `board`), with only that move in its history."""
    after = board.copy(stack=False)
    after.push(move)
    return after


def pass_turn(board: chess.Board) -> chess.Board:
    """The same position with the other side to move, as if the side to move had passed. Built
    from the FEN, so there is no null move in its history: UCI has none to send an engine."""
    turn = board.copy(stack=False)
    turn.push(chess.Move.null())
    return chess.Board(turn.fen())


def hangs_piece(board: chess.Board, capture: chess.Move | None, owner: chess.Color) -> str | None:
    """'hanging_piece' / 'hanging_pawn' when `capture` wins material outright for the opponent."""
    if capture is None or not board.is_capture(capture) or board.is_en_passant(capture):
        return None
    victim = board.piece_at(capture.to_square)
    attacker = board.piece_at(capture.from_square)
    if victim is None or victim.piece_type == chess.KING:
        return None
    defended = bool(board.attackers(owner, capture.to_square))
    if defended and value(victim) <= value(attacker):
        return None
    return "hanging_pawn" if victim.piece_type == chess.PAWN else "hanging_piece"


def forks(board: chess.Board, square: chess.Square, victim: chess.Color) -> bool:
    """True when the piece on `square` attacks two or more valuable/undefended enemy pieces."""
    piece = board.piece_at(square)
    if piece is None:
        return False
    targets = 0
    for target in board.attacks(square) & board.occupied_co[victim]:
        hit = board.piece_at(target)
        if hit is None or hit.piece_type == chess.PAWN:
            continue
        if hit.piece_type == chess.KING or value(hit) > value(piece):
            targets += 1
        elif not board.attackers(victim, target):
            targets += 1
    return targets >= 2


def mates_on_back_rank(board: chess.Board, line: list[chess.Move]) -> bool:
    """Replay `line`; True if it ends in mate delivered along the loser's back rank."""
    scratch = board.copy(stack=False)
    for move in line:
        if move not in scratch.legal_moves:
            return False
        scratch.push(move)
    if not scratch.is_checkmate() or not line:
        return False
    loser = scratch.turn
    king = scratch.king(loser)
    mating_piece = scratch.piece_at(line[-1].to_square)
    if king is None or mating_piece is None:
        return False
    back_rank = 0 if loser == chess.WHITE else 7
    return (
        chess.square_rank(king) == back_rank
        and chess.square_rank(line[-1].to_square) == back_rank
        and mating_piece.piece_type in (chess.ROOK, chess.QUEEN)
    )


def development(board: chess.Board, color: chess.Color) -> int:
    """Minor pieces off their home squares plus one for a king that has left e1/e8."""
    developed = sum(
        1
        for home in _MINOR_HOME[color]
        if (piece := board.piece_at(home)) is None
        or piece.color != color
        or piece.piece_type not in (chess.KNIGHT, chess.BISHOP)
    )
    if board.king(color) != _KING_HOME[color]:
        developed += 1
    return developed


def is_queen_trade(board: chess.Board, move: chess.Move, reply: chess.Move | None) -> bool:
    """Queens come off: we take theirs and they can retake, or they take ours and we retake."""
    mover = board.turn
    piece = board.piece_at(move.from_square)
    if piece and piece.piece_type == chess.QUEEN and board.is_capture(move):
        victim = board.piece_at(move.to_square)
        if victim and victim.piece_type == chess.QUEEN:
            return bool(board.attackers(not mover, move.to_square))
    if reply is not None:
        after = board.copy(stack=False)
        after.push(move)
        taker = after.piece_at(reply.from_square)
        victim = after.piece_at(reply.to_square)
        if (
            taker
            and victim
            and taker.piece_type == chess.QUEEN
            and victim.piece_type == chess.QUEEN
            and after.attackers(mover, reply.to_square)
        ):
            return True
    return False


def tag_motifs(ctx: MoveContext) -> list[str]:
    fact = ctx.fact
    mover = ctx.board_before.turn
    opponent = not mover
    tags: list[str] = []
    is_error = fact.wp_loss >= INACCURACY_WP_LOSS

    # -- traits: habits worth counting even when the engine does not mind -------------------
    if fact.phase is not Phase.ENDGAME and is_queen_trade(
        ctx.board_before, ctx.move, ctx.best_reply
    ):
        behind = development(ctx.board_before, mover) + DEVELOPMENT_DEFICIT
        if behind <= development(ctx.board_before, opponent):
            tags.append("queen_trade_behind")

    if not is_error:
        return tags

    # -- mates ------------------------------------------------------------------------------
    if (
        fact.mate_after is not None
        and fact.mate_after < 0
        and (fact.mate_before is None or fact.mate_before > 0)
    ):
        tags.append("allowed_mate")
        if mates_on_back_rank(ctx.board_after, ctx.reply_pv):
            tags.append("back_rank")
    if (
        fact.mate_before is not None
        and 0 < fact.mate_before <= MISSED_MATE_MAX_DISTANCE
        and (fact.mate_after is None or fact.mate_after <= 0)
    ):
        tags.append("missed_mate")
        if mates_on_back_rank(ctx.board_before, ctx.best_pv):
            tags.append("back_rank")

    # -- material ---------------------------------------------------------------------------
    if hanging := hangs_piece(ctx.board_after, ctx.best_reply, mover):
        tags.append(hanging)
    if ctx.best_reply is not None and not ctx.board_after.is_capture(ctx.best_reply):
        replied = ctx.board_after.copy(stack=False)
        replied.push(ctx.best_reply)
        if forks(replied, ctx.best_reply.to_square, mover):
            tags.append("allowed_fork")
    if ctx.best_move is not None and ctx.best_move != ctx.move:
        improved = ctx.board_before.copy(stack=False)
        improved.push(ctx.best_move)
        if forks(improved, ctx.best_move.to_square, opponent):
            tags.append("missed_fork")
    if ctx.board_before.is_capture(ctx.move):
        victim = ctx.board_before.piece_at(ctx.move.to_square)
        if victim is not None and victim.piece_type == chess.PAWN:
            tags.append("pawn_grab")

    # -- context ----------------------------------------------------------------------------
    if ctx.previous is not None and ctx.previous.wp_loss >= 20:
        tags.append("missed_tactic")
    if fact.clock_after is not None:
        low = fact.clock_after < TIME_TROUBLE_SECONDS
        if ctx.initial_time:
            low = low or fact.clock_after < TIME_TROUBLE_FRACTION * ctx.initial_time
        if low:
            tags.append("time_trouble")
    if (
        fact.phase is Phase.ENDGAME
        and fact.eval_before >= ENDGAME_WINNING_CP
        and fact.eval_after < ENDGAME_THROWN_CP
    ):
        tags.append("endgame_conversion")
    return tags
