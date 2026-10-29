"""Opening / middlegame / endgame split, after Lichess's game divider (lila Divider.scala)."""

from __future__ import annotations

import chess

from chess_coach.models import Phase

_MAJORS_MINORS = (chess.KNIGHT, chess.BISHOP, chess.ROOK, chess.QUEEN)


def majors_and_minors(board: chess.Board) -> int:
    return sum(
        len(board.pieces(piece, color)) for piece in _MAJORS_MINORS for color in chess.COLORS
    )


def backrank_sparse(board: chess.Board) -> bool:
    """True when either side has fewer than four pieces left on its own back rank."""
    for color, rank in ((chess.WHITE, 0), (chess.BLACK, 7)):
        pieces_on_rank = sum(
            1
            for square in chess.SQUARES
            if chess.square_rank(square) == rank
            and (piece := board.piece_at(square)) is not None
            and piece.color == color
        )
        if pieces_on_rank < 4:
            return True
    return False


def phase_of(board: chess.Board, previous: Phase = Phase.OPENING) -> Phase:
    """Phase of the position; never earlier than `previous` so a game only moves forward."""
    count = majors_and_minors(board)
    if count <= 6 or previous is Phase.ENDGAME:
        return Phase.ENDGAME
    if previous is Phase.MIDDLEGAME or count <= 10 or backrank_sparse(board):
        return Phase.MIDDLEGAME
    return Phase.OPENING
