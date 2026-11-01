import chess
import pytest

from chess_coach.engine.analysis import analyze_game
from chess_coach.engine.motifs import pass_turn
from chess_coach.engine.stockfish import Engine, LineEval
from chess_coach.engine.threats import (
    IGNORED,
    UNSAFE,
    classify,
    look,
    recapture_pending,
    same_threat,
    won,
)
from chess_coach.models import Color, Judgement, MoveFact, Phase
from chess_coach.sources.pgn import games_from_pgn_text

# From the club game in tests/data: 14.a5 (axb6 next, opening the a-file onto the rook on a8),
# then 14...Ne7?, and two moves later 16.Nc4?? Bxc4.
AFTER_A5 = "r5nr/2p2kpp/pp1bbp2/P1p5/4P3/1NN1BP2/1PP3PP/R2R2K1 b - - 0 14"
BEFORE_A5 = "r5nr/2p2kpp/pp1bbp2/2p5/P3P3/1NN1BP2/1PP3PP/R2R2K1 w - - 1 14"
AFTER_NE7 = "r6r/2p1nkpp/pp1bbp2/P1p5/4P3/1NN1BP2/1PP3PP/R2R2K1 w - - 1 15"
BEFORE_NC4 = "r2r4/2p1nkpp/pp1bbp2/P1p5/4P3/2N1BP2/1PPN2PP/R2R2K1 w - - 3 16"
U = chess.Move.from_uci


def line(cp: int, moves: str, mate: int | None = None) -> LineEval:
    pv = [U(m) for m in moves.split()]
    return LineEval(cp, mate, pv[0], pv)


def fact(ply: int, fen: str, uci: str, before: int, after: int, **kw) -> MoveFact:
    board = chess.Board(fen)
    fields = dict(
        game_id="pgn:club",
        ply=ply,
        move_number=board.fullmove_number,
        color=Color.WHITE if board.turn == chess.WHITE else Color.BLACK,
        is_user=board.turn == chess.WHITE,
        san=board.san(U(uci)),
        uci=uci,
        fen_before=fen,
        eval_before=before,
        eval_after=after,
        best_move=uci,
        best_san=board.san(U(uci)),
        cp_loss=max(0, before - after),
        wp_before=50.0,
        wp_after=20.0,
        wp_loss=30.0,
        judgement=Judgement.BLUNDER,
        phase=Phase.MIDDLEGAME,
        depth=12,
    )
    fields.update(kw)
    return MoveFact(**fields)  # type: ignore[arg-type]


def ne7() -> tuple[MoveFact, MoveFact]:
    """14...Ne7? and the move before it, 14.a5, from White's side (+0.07 before it)."""
    return fact(28, AFTER_A5, "g8e7", -20, -286), fact(27, BEFORE_A5, "a4a5", 7, 20)


# White's best had Black passed instead of 14...Ne7 (Stockfish, depth 8): two pawns, b6 and c7
AXB6 = line(519, "a5b6 e6b3 c2b3 g8e7 b6c7 d6c7 e3c5")
# ...and after 14...Ne7: still axb6, winning a pawn
AXB6_AFTER_NE7 = line(241, "a5b6 h8b8 b3c5 d6c5 e3c5 c7b6")
CALM = line(20, "b6b5")  # nothing happens


def test_passing_hands_the_move_over_without_a_null_move_in_the_history():
    board = chess.Board("rnbqkbnr/ppp1pppp/8/3pP3/8/8/PPPP1PPP/RNBQKBNR w KQkq d6 0 3")
    theirs = pass_turn(board)
    assert theirs.turn == chess.BLACK and theirs.move_stack == [] and theirs.ep_square is None
    assert theirs.board_fen() == board.board_fen()


def test_material_won_along_the_line():
    theirs = pass_turn(chess.Board(AFTER_A5))  # White to move
    assert won(theirs, [U("a5b6")]) == 1
    assert won(theirs, [U("a5b6"), U("c7b6")]) == 0
    assert won(theirs, AXB6.pv) == 2  # the knight trade on b3 in between doesn't count
    quiet = [U("g1f2"), U("h7h6"), U("f2e2"), U("h6h5")]
    # past the first four moves the line stops at the first quiet move...
    assert won(theirs, [*quiet, U("e2d2"), U("a5b6")]) == 0
    # ...but goes on while the captures continue, so a pending recapture is counted
    assert won(theirs, [*quiet, U("a5b6")]) == 1
    assert won(theirs, [*quiet, U("a5b6"), U("c7b6")]) == 0
    assert won(theirs, [U("a5a7")]) == 0  # not a legal move: the line stops


def test_an_ignored_threat():
    m, previous = ne7()
    row = classify(m, previous, AXB6, AXB6_AFTER_NE7, depth=12)
    assert (row.label, row.threat, row.threat_san, row.gain) == (IGNORED, "a5b6", "axb6", 200)


def test_an_old_threat_or_a_met_one_is_not_ignored():
    m, previous = ne7()
    previous.eval_before = 400  # they were already this well off before their last move
    old = classify(m, previous, AXB6, AXB6_AFTER_NE7, 12)
    assert (old.label, old.threat_san) == ("", "axb6")  # the threat is kept, unlabelled
    m, previous = ne7()
    other = line(241, "b3d2")  # their best answer is something else
    met = classify(m, previous, AXB6, other, 12)
    assert (met.label, met.threat_san) == ("", "axb6")


def test_what_the_move_takes_pays_for_what_it_lets_them_take():
    _, previous = ne7()
    pawn = line(300, "a5b6")  # their answer: axb6, a pawn
    m = fact(28, AFTER_A5, "g8e7", -20, -286)  # 14...Ne7 took nothing: ignored
    assert classify(m, previous, AXB6, pawn, 12).label == IGNORED
    bxb3 = fact(28, AFTER_A5, "e6b3", -20, -100)  # 14...Bxb3 took a knight first: met
    assert classify(bxb3, previous, AXB6, pawn, 12).label == ""


def test_a_free_tempo_is_not_a_threat():
    m, previous = ne7()
    # a quiet king move worth a lot only because they get two moves in a row, winning nothing
    quiet = line(446, "g1f2 h7h6")
    assert classify(m, previous, quiet, AXB6_AFTER_NE7, 12).label == ""
    # a mate is always a threat, whatever the numbers; met only when they no longer mate
    mate = line(9998, "g1f2", mate=2)
    assert classify(m, previous, mate, line(9998, "g1f2", mate=2), 12).label == IGNORED
    assert classify(m, previous, mate, AXB6_AFTER_NE7, 12).label == ""


def test_the_movers_own_edge_is_not_their_threat():
    m, previous = ne7()
    # Black had a shot of their own (+3.95), so passing costs a lot whatever White threatens:
    # here White's best free move is axb6, which only trades pawns
    m.eval_before = 395
    trade = line(-8, "a5b6 c7b6")
    assert classify(m, previous, trade, line(-20, "b3d2"), 12).label == ""
    # ...unless the move makes it work: then the move was unsafe
    row = classify(m, previous, trade, line(449, "a5b6 h8b8 b6c7"), 12)
    assert (row.label, row.threat_san, row.gain) == (UNSAFE, "axb6", 200)


def test_an_unsafe_move():
    m = fact(31, BEFORE_NC4, "d2c4", -14, -454)
    previous = fact(
        30, "r6r/2p1nkpp/pp1bbp2/P1p5/4P3/2N1BP2/1PPN2PP/R2R2K1 b - - 2 15", "h8d8", 24, 14
    )
    row = classify(m, previous, CALM, line(434, "e6c4"), 12)  # passing would have cost nothing
    assert (row.label, row.threat, row.threat_san, row.gain) == (UNSAFE, "e6c4", "Bxc4", 300)
    # worse than passing, but their best answer is a quiet move that wins nothing: no label
    assert classify(m, previous, CALM, line(434, "f7e8"), 12).label == ""
    # an answer that wins the knight but leaves them barely better than passing: no label
    assert classify(m, previous, CALM, line(100, "e6c4"), 12).label == ""


def test_checks_and_pending_recaptures_are_not_looked_at():
    def search(board):
        raise AssertionError("no search here")

    board = chess.Board()
    for san in "e4 e5 Nf3 Nc6 Bb5 a6 Bxc6".split():
        board.push_san(san)
    bxc6 = fact(
        7, "r1bqkbnr/1ppp1ppp/p1n5/1B2p3/4P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 0 4", "b5c6", 0, 0
    )
    after = board.fen()  # Black takes back on c6
    assert recapture_pending(board, bxc6)
    row = look(fact(8, after, "d7c6", 0, -300), bxc6, search)
    assert row.label == "" and row.threat == ""
    checked = "rnbqkbnr/pppp1ppp/8/4Q3/4P3/8/PPPP1PPP/RNB1KBNR b KQkq - 0 3"
    assert look(fact(6, checked, "f8e7", 0, -300), None, search).label == ""


def test_two_searches_the_null_move_board_then_after_the_move():
    m, previous = ne7()
    lines = {pass_turn(chess.Board(AFTER_A5)).fen(): AXB6, AFTER_NE7: AXB6_AFTER_NE7}
    seen = []

    def search(board):
        seen.append(board.fen())
        return lines[board.fen()]

    assert look(m, previous, search).label == IGNORED
    assert seen == list(lines)


def test_a_guess_at_their_threat_is_graded_leniently():
    theirs = pass_turn(chess.Board(AFTER_A5))
    assert same_threat(theirs, U("a5b6"), U("a5b6"))
    assert not same_threat(theirs, U("a5a6"), U("a5b6"))
    scholar = pass_turn(
        chess.Board("r1bqkbnr/pppp1ppp/2n5/4p2Q/2B1P3/8/PPPP1PPP/RNB1K1NR w KQkq - 4 4")
    )
    assert same_threat(scholar, U("h5f7"), U("h5f7"))


# -- with Stockfish -----------------------------------------------------------------------


@pytest.mark.engine
def test_the_club_game(sample_pgn_text: str, engine: Engine) -> None:
    club = list(games_from_pgn_text(sample_pgn_text, "coachme"))[2]
    facts = {f.ply: f for f in analyze_game(club, engine, depth=8)}

    def search(board):
        return engine.evaluate(board, depth=8)[0]

    # 16.Nc4?? with nothing threatened hangs the knight to Bxc4
    nc4 = look(facts[31], facts[30], search, 8)
    assert facts[31].is_user is True
    assert (nc4.label, nc4.threat_san) == (UNSAFE, "Bxc4")
    # 15.Nd2 missed axb6 themselves: a missed shot is neither
    assert look(facts[29], facts[28], search, 8).label == ""
    # (14...Ne7? is left out here: whether axb6 wins one pawn or two depends on the line the
    # engine picks at this depth; the unit tests above pin its lines down)


QUEEN_LEFT = """[Event "Casual"]
[White "coachme"]
[Black "rival"]
[Result "0-1"]

1. e4 e5 2. Qh5 Nc6 3. Bc4 g6 4. Nf3 gxh5 0-1
"""


@pytest.mark.engine
def test_a_queen_left_where_their_pawn_attacks_it(engine: Engine) -> None:
    game = next(iter(games_from_pgn_text(QUEEN_LEFT, "coachme")))
    facts = {f.ply: f for f in analyze_game(game, engine, depth=8)}
    nf3 = look(facts[7], facts[6], lambda board: engine.evaluate(board, depth=8)[0], 8)
    assert (nf3.label, nf3.threat, nf3.threat_san) == (IGNORED, "g6h5", "gxh5")
