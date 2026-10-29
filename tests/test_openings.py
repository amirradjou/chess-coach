import chess

from chess_coach.openings import Opening, classify_board_sequence, classify_pgn, opening_table


def test_table_loads_every_volume() -> None:
    table = opening_table()
    assert len(table) > 3500
    assert Opening(eco="C50", name="Italian Game") in table.values()


def test_deepest_position_wins() -> None:
    board = chess.Board()
    moves = [board.push_san(s) or board.move_stack[-1] for s in "e4 e5 Nf3 Nc6 Bc4".split()]
    assert classify_board_sequence(moves) == Opening(eco="C50", name="Italian Game")
    assert classify_pgn("1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 4. Bxc6 *").name.startswith(  # type: ignore[union-attr]
        "Ruy Lopez: Exchange"
    )


def test_transpositions_share_a_name() -> None:
    direct = classify_pgn("1. d4 d5 2. c4 e6 3. Nc3 Nf6 4. Nf3 *")
    transposed = classify_pgn("1. Nf3 d5 2. d4 Nf6 3. c4 e6 4. Nc3 *")
    assert direct is not None and direct == transposed
    assert direct.family == "Queen's Gambit Declined"


def test_unknown_or_empty_games() -> None:
    assert classify_pgn("1. a3 h6 2. a4 h5 *") is not None  # still finds "1. a3" as a family
    assert classify_pgn("*") is None
    assert Opening(eco="C58", name="Italian Game: Two Knights Defense, Fried Liver").family == (
        "Italian Game"
    )
