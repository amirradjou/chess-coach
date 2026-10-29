import chess
import pytest

from chess_coach.engine.stockfish import LineEval
from chess_coach.models import Puzzle
from chess_coach.puzzles.review import (
    Score,
    amount,
    analysis,
    attempt,
    best_line,
    build,
    legal,
    material,
    played_uci,
    review,
)

# 1.e4 e5 2.Bc4 Nc6 3.Qh5 Nf6??: Qxf7 is mate. In the game White played 4.Nc3.
FEN = "r1bqkb1r/pppp1ppp/2n2n2/4p2Q/2B1P3/8/PPPP1PPP/RNB1K1NR w KQkq - 4 4"
M = chess.Move.from_uci


def after(*moves: str) -> str:
    board = chess.Board(FEN)
    for uci in moves:
        board.push(M(uci))
    return board.fen()


def puzzle(**kw) -> Puzzle:
    values = dict(
        id="g1:7",
        game_id="g1",
        ply=7,
        fen=FEN,
        solution="h5f7",
        solution_san="Qxf7#",
        played_san="Nc3",
        line="",
        gap_cp=9000,
        depth=18,
        tags=["mate"],
    )
    values.update(kw)
    return Puzzle(**values)


# Scores are from the side to move, as the engine gives them.
ENGINE = {
    after("h5f7"): LineEval(cp=-10_000, mate=0, move=None),
    # 4.Qxe5+? Nxe5: Black is a queen for a pawn up.
    after("h5e5"): LineEval(cp=850, mate=None, move=M("c6e5"), pv=[M("c6e5"), M("c4b3")]),
    # 4.Nc3 g6 5.Qf3: about level.
    after("b1c3"): LineEval(cp=-20, mate=None, move=M("g7g6"), pv=[M("g7g6"), M("h5f3")]),
}


def analyse(fen: str) -> LineEval | None:
    return ENGINE.get(fen)


def no_engine(_fen: str) -> None:
    return None


def test_scores_read_from_the_solvers_side():
    assert [Score(213).text, Score(-40).text, Score(9997, 3).text, Score(-9998, -2).text] == [
        "+2.1",
        "-0.4",
        "#3",
        "#-2",
    ]
    assert Score(-9998, -2).words == "mate in 2 for them"
    assert Score(10_000, 0).words == "checkmate"


def test_legal_moves_and_promotion():
    board = chess.Board("8/P7/8/8/8/8/8/k6K w - - 0 1")
    assert legal(board, "a7a8").promotion == chess.QUEEN
    with pytest.raises(ValueError):
        legal(board, "a7a6")
    with pytest.raises(ValueError):
        legal(board, "nonsense")


def test_material_counts_points():
    assert material(chess.Board(), chess.WHITE) == 0
    assert material(chess.Board(after("h5e5", "c6e5")), chess.WHITE) == -8
    assert [amount(1), amount(3), amount(9), amount(8)] == [
        "a pawn",
        "a piece",
        "the queen",
        "8 points of material",
    ]


def test_the_best_line_explains_the_mate():
    line = best_line(puzzle(), analyse)
    assert line.label == "Best: Qxf7#" and line.score.text == "#"
    assert line.why == "Qxf7# is checkmate."
    assert [p.text for p in line.plies] == ["4.Qxf7#"]


def test_a_wrong_try_shows_their_reply_and_the_cost():
    line = attempt(puzzle(), "h5e5", analyse, reveal=False)
    assert line.label == "Your try: Qxe5+"
    assert [(p.text, p.engine) for p in line.plies] == [
        ("4.Qxe5+", False),
        ("4...Nxe5", True),
        ("5.Bb3", True),
    ]
    assert line.why.startswith("They answer 4...Nxe5. By 5.Bb3 you have lost 8 points")
    assert "-8.5 for you" in line.why and "while a better move gets checkmate" in line.why
    assert "Qxf7" not in line.why  # not revealed while you can still try again
    shown = attempt(puzzle(), "h5e5", analyse)
    assert "after Qxf7#" in shown.why


def test_review_has_the_best_line_your_tries_and_the_game_move():
    lines = review(puzzle(), ["h5e5", "h5e5", "h5f7"], analyse)
    assert [(x.kind, x.label) for x in lines] == [
        ("best", "Best: Qxf7#"),
        ("tried", "Your try: Qxe5+"),
        ("game", "In the game: Nc3"),
    ]
    assert lines[2].why.startswith("They answer 4...g6. Material stays level")
    # the game move was one of the tries: no second line for it
    assert [x.kind for x in review(puzzle(played_san="Qxe5+"), ["h5e5"], analyse)] == [
        "best",
        "tried",
    ]
    assert played_uci(puzzle(played_san="Qh9")) is None


def test_an_accepted_move_is_labelled_as_good():
    p = puzzle(accept=["b1c3"])
    assert attempt(p, "b1c3", analyse).label == "Your move: Nc3 (also good)"


def test_the_best_line_uses_the_stored_continuation():
    p = puzzle(
        fen=chess.STARTING_FEN, solution="e2e4", solution_san="e4", line="e7e5 g1f3", gap_cp=150
    )
    line = best_line(p, no_engine)
    assert [x.text for x in line.plies] == ["1.e4", "1...e5", "2.Nf3"]
    assert line.score is None
    assert line.why == (
        "e4 doesn't win material in this line: what it gains is the position. "
        "It's 1.5 pawns better than any other move."
    )
    assert "No other move comes close." in best_line(puzzle(), no_engine).why


def test_without_an_engine_a_try_says_so():
    line = attempt(puzzle(), "h5e5", no_engine)
    assert [p.san for p in line.plies] == ["Qxe5+"] and line.score is None
    assert "Stockfish isn't available" in line.why


def test_continuations_run_on_until_the_captures_stop():
    # A long engine line ending in a run of captures is shown until they're over.
    board = chess.Board()
    pv = []
    for san in "e4 e5 Nf3 Nc6 d4 exd4 Nxd4 Nxd4 Qxd4 Nf6 Bc4 Be7".split():
        move = board.parse_san(san)
        pv.append(move)
        board.push(move)
    line = build(
        chess.STARTING_FEN,
        [],
        lambda _fen: LineEval(30, None, pv[0], pv),
        kind="analysis",
        label="",
    )
    assert len(line.plies) == 10  # 8, then on through Qxd4 and one quiet move
    assert [p.san for p in line.plies][-3:] == ["Nxd4", "Qxd4", "Nf6"]


def test_analysis_scores_your_own_moves():
    line = analysis(FEN, ["b1c3"], analyse)
    assert line.kind == "analysis" and [p.engine for p in line.plies] == [False, True, True]
    assert line.why == "After 4.Nc3: +0.2 for you. The engine's move: 4...g6."
    with pytest.raises(ValueError):
        analysis(FEN, ["e1e3"], analyse)
