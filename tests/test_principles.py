import chess

from chess_coach.coach.principles import (
    allows,
    doubled,
    explain,
    isolated,
    opening_idea,
    passed,
)

AFTER_NF3_NC6 = "r1bqkbnr/pppp1ppp/2n5/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 2 3"
ITALIAN = "r1bqk1nr/pppp1ppp/2n5/2b1p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 4 4"
AFTER_E5 = "rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2"
NF6_BLUNDER = "r1bqkb1r/pppp1ppp/2n2n2/4p2Q/2B1P3/8/PPPP1PPP/RNB1K1NR w KQkq - 4 4"
CASTLED = "r1bq1rk1/pppp1ppp/2n2n2/2b1p3/2B1P3/2NP1N2/PPP2PPP/R1BQ1RK1 w - - 0 7"


def test_a_piece_left_hanging():
    r = explain(AFTER_NF3_NC6, "f3g5", "f1b5")  # 3.Ng5? Qxg5
    assert r.faults[0] == "It leaves your knight on g5 hanging (attacked and not defended)."
    # the queen on h5 was already attacked; any move but taking on f7 leaves it there
    assert explain(NF6_BLUNDER, "b1c3", "h5f7").faults == [
        "It leaves your queen on h5 hanging (attacked and not defended)."
    ]


def test_an_even_trade_is_not_hanging():
    # Scotch, 4.Nxd4 Nc6 on the board: Nxc6 can be taken back, but it took a knight
    scotch = "r1bqkbnr/pppp1ppp/2n5/8/3NP3/8/PPP2PPP/RNBQKB1R w KQkq - 1 5"
    assert not any("c6" in f for f in explain(scotch, "d4c6", "c1e3").faults)


def test_king_safety():
    r = explain(ITALIAN, "e1f1", "e1g1")
    assert r.faults == [
        "It gives up your right to castle, so your king may be stuck in the centre."
    ]
    assert r.ideas == ["The better move castles: get your king safe before anything else."]
    shelter = explain(CASTLED, "g2g4", "c1g5")
    assert "It pushes the g-pawn in front of your castled king, loosening its cover." in (
        shelter.faults
    )


def test_development_and_the_queen():
    r = explain(AFTER_E5, "d1h5", "g1f3")
    assert r.faults[0].startswith("It brings your queen out while 4 of your minor pieces")
    assert r.ideas == ["The better move develops a piece: get the knights and bishops out early."]
    wing = explain(AFTER_E5, "h2h3", "g1f3")
    assert wing.faults[0].startswith("It spends a move on a wing pawn")


def test_their_mistakes_are_explained_in_their_voice():
    after_nf3 = "rnbqkbnr/pppp1ppp/8/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R b KQkq - 1 2"
    r = explain(after_nf3, "d8f6", "b8c6", theirs=True)  # 2...Qf6 instead of developing
    assert r.faults[0] == (
        "It brings their queen out while 4 of their minor pieces are still at home: the queen "
        "gets chased and they lose time."
    )
    assert r.ideas == []  # what their better move does is not yours to learn


def test_what_their_mistake_allows():
    danish = chess.Board()
    for san in "e4 e5 d4 exd4 c3 dxc3 Nxc3 Nf6".split():
        danish.push_san(san)
    assert allows(danish.fen(), "e4e5") == ["It lets e5 hit their knight on f6."]
    fork = "r1bqkb1r/pppp1ppp/2n5/4p3/2B1n3/5N2/PPPP1PPP/RNBQK2R w KQkq - 0 4"  # 3...Nxe4?
    assert allows(fork, "c4f7") == ["It lets Bxf7+ come with check."]
    mate = "rnbqkbnr/ppppp2p/5p2/6p1/4P3/8/PPPP1PPP/RNBQKBNR w KQkq g6 0 3"  # 2...g5??
    assert allows(mate, "d1h5") == ["It allows Qh5#, checkmate."]
    assert allows(chess.STARTING_FEN, "e2e4") == []  # nothing to name
    assert allows(chess.STARTING_FEN, "e2e5") == []  # not a legal move


def test_what_their_mistake_allows_to_take():
    bh6 = chess.Board()
    for san in "d4 g6 Bh6".split():  # 2.Bh6?? leaves the bishop to the knight and the bishop
        bh6.push_san(san)
    assert allows(bh6.fen(), "g8h6", captures=True) == [
        "It lets Nxh6 take their bishop on h6, which was hanging (attacked and not defended)."
    ]
    nd5 = "r1bqk2r/pppp1ppp/2n1pn2/3N4/1bB1P3/5N2/PPPP1PPP/R1BQK2R b KQkq - 5 5"  # 5.Nd5?
    assert allows(nd5, "e6d5", captures=True) == [
        "It lets exd5 take their knight on d5, which was attacked by a cheaper piece, and hit "
        "their bishop on c4."
    ]
    # Without `captures` (the opening tree, where explain() names the hanging piece) as before.
    assert allows(bh6.fen(), "g8h6") == []
    assert allows(nd5, "e6d5") == ["It lets exd5 hit their bishop on c4."]


def test_pawn_structure_and_the_centre():
    r = explain("rnbqkbnr/pp1ppppp/8/2p5/3P4/8/PPP1PPPP/RNBQKBNR w KQkq c6 0 2", "d4c5", "d4d5")
    assert r.faults == [
        "It takes a pawn off the d- and e-files: centre pawns control the squares that matter "
        "most.",
        "It doubles your pawns on the c-file.",
    ]
    lonely = explain("4k3/1pp5/2N5/8/8/8/8/4K3 b - - 0 1", "b7c6", "e8d7")
    assert lonely.faults == [
        "It doubles your pawns on the c-file.",
        "It leaves your c-pawn isolated: no pawn beside it can defend it.",
    ]


def test_forcing_moves_and_endgames():
    assert explain(NF6_BLUNDER, "b1c3", "h5f7").ideas[0].startswith("The better move is checkmate")
    smith_morra = "r2qkbnr/pp2pppp/2np4/8/2B1P1b1/2N2N2/PP3PPP/R1BQK2R w KQkq - 2 7"
    assert (
        explain(smith_morra, "e1g1", "c4f7")
        .ideas[0]
        .startswith("The better move is a check and a capture")
    )
    danish = "rnbqkb1r/pppp1ppp/5n2/8/4P3/2N5/PP3PPP/R1BQKBNR w KQkq - 1 5"
    assert explain(danish, "g1f3", "e4e5").ideas == [
        "The better move attacks their knight on f6, so they have to spend a move on it."
    ]
    king = explain("8/5p2/8/8/8/8/2P5/K7 w - - 0 1", "c2c3", "a1b2")
    assert king.ideas == [
        "In the endgame the king is a strong piece: the better move brings it toward the centre."
    ]
    push = explain("8/5p2/8/8/8/8/2P5/K7 w - - 0 1", "a1b1", "c2c4")
    assert push.ideas == ["The better move pushes your passed pawn: in endgames, passed pawns run."]


def test_pawn_helpers():
    board = chess.Board("4k3/p1p5/2p5/8/8/8/5P2/4K3 w - - 0 1")
    assert doubled(board, chess.BLACK) == {2}
    assert isolated(board, chess.BLACK) == {0, 2} and isolated(board, chess.WHITE) == {5}
    assert passed(board, chess.WHITE) == {chess.F2}


def test_nothing_to_say_about_an_illegal_move():
    assert explain(AFTER_E5, "e1e3", "g1f3").all() == []


def test_opening_ideas():
    assert opening_idea("Danish Gambit Accepted").startswith("White gives one or two pawns")
    assert opening_idea("Blackmar-Diemer Gambit Accepted").startswith("White gives a pawn")
    assert opening_idea("Sicilian Defense").startswith("Black trades the c-pawn")
    assert opening_idea("Bongcloud Attack") is None and opening_idea(None) is None
