import chess

from chess_coach.engine.phases import majors_and_minors, phase_of
from chess_coach.models import Phase


def test_start_position_is_opening() -> None:
    assert phase_of(chess.Board()) is Phase.OPENING
    assert majors_and_minors(chess.Board()) == 14


def test_sparse_back_rank_starts_the_middlegame() -> None:
    board = chess.Board()
    for san in "e4 e5 Nf3 Nc6 Bc4 Bc5 O-O Nf6 d3 d6 Nc3 O-O Bg5 h6".split():
        board.push_san(san)
    # white has king, queen, two rooks on rank 1 (4 pieces) and black the same => still opening
    assert phase_of(board) is Phase.OPENING
    board.push_san("Bh4")
    board.push_san("Bg4")  # back ranks: Ra8 Qd8 Rf8 Kg8 (4) and Ra1 Qd1 Rf1 Kg1 (4)
    assert phase_of(board) is Phase.OPENING
    board.push_san("Nd5")
    board.push_san("g5")
    board.push_san("Bg3")
    board.push_san("Nd4")
    board.push_san("c3")
    board.push_san("Nxf3+")
    board.push_san("gxf3")
    assert majors_and_minors(board) == 12  # still > 10 and both back ranks hold 4 pieces
    assert phase_of(board) is Phase.OPENING
    board.push_san("Qd7")  # black back rank drops to Ra8 Rf8 Kg8
    assert phase_of(board) is Phase.MIDDLEGAME


def test_few_pieces_is_endgame_and_phase_never_goes_backwards() -> None:
    board = chess.Board("8/8/4k3/8/8/4K3/4P3/8 w - - 0 1")
    assert phase_of(board) is Phase.ENDGAME
    assert phase_of(chess.Board(), previous=Phase.MIDDLEGAME) is Phase.MIDDLEGAME
    assert phase_of(chess.Board(), previous=Phase.ENDGAME) is Phase.ENDGAME
