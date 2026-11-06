"""A wrong move in Learn openings: how they punish it, move by move, and what it costs."""

from datetime import UTC, datetime

import chess
import pytest

from chess_coach.coach.principles import line_leaves, line_notes
from chess_coach.db import Store
from chess_coach.engine.stockfish import Engine, EngineNotFoundError, LineEval
from chess_coach.models import CoachCard
from chess_coach.puzzles import review

from .test_opening_tree import NXC3, _client, no_engine


def fen_after(sans: str = "") -> str:
    board = chess.Board()
    for san in sans.split():
        board.push_san(san)
    return board.fen()


def play(fen: str, *ucis: str) -> str:
    board = chess.Board(fen)
    for uci in ucis:
        board.push_uci(uci)
    return board.fen()


def moves(ucis: str) -> list[chess.Move]:
    return [chess.Move.from_uci(u) for u in ucis.split()]


def line_eval(cp: int, ucis: str, mate: int | None = None) -> LineEval:
    pv = moves(ucis)
    return LineEval(cp, mate, pv[0] if pv else None, pv)


# 1.e4 e5 2.Nf3 f6?: you are Black, and 3.Nxe5! punishes it even though you win the knight.
DAMIANO = fen_after("e4 e5 Nf3")
F6 = "f7f6"
REFUTED = "f3e5 f6e5 d1h5 g7g6 h5e5 d8e7 e5h8"


def damiano() -> dict[str, LineEval]:
    return {
        DAMIANO: line_eval(-20, "b8c6"),
        play(DAMIANO, F6): line_eval(300, REFUTED),
    }


def test_each_move_of_the_line_says_what_it_does():
    notes = line_notes(DAMIANO, [F6, *REFUTED.split()])
    assert notes == [
        "your move",
        "taking your pawn on e5",
        "your best answer, taking their knight on e5",
        "with check",
        "hitting their queen on h5",
        "taking your pawn on e5 with check",
        "hitting their queen on e5",
        "taking your rook on h8",
    ]


def test_a_quiet_move_says_what_the_board_shows():
    # A knight attacked by a pawn moves away
    attacked = "rnbqkbnr/pppp1ppp/8/8/4p3/5N2/PPPPPPPP/RNBQKB1R w KQkq - 0 3"
    assert line_notes(attacked, ["f3d4"]) == ["your move, moving your knight out of attack"]
    # A loose bishop gets a defender
    loose = "rnb1kbnr/pppp1ppp/4q3/8/2B5/8/PPPPPPPP/RNBQK1NR w KQkq - 0 1"
    assert line_notes(loose, ["b2b3"]) == ["your move, protecting your bishop on c4"]
    # Castling, developing and the centre, in the mover's own words
    italian = fen_after("e4 e5 Nf3 Nc6 Bc4 Bc5")
    assert line_notes(italian, ["e1g1"]) == ["your move, castling"]
    assert line_notes(chess.STARTING_FEN, ["a2a3", "g8f6", "b1c3", "e7e5"]) == [
        "your move",
        "developing their knight",
        "your best answer, developing your knight",
        "a pawn into the centre",
    ]


def test_a_check_with_a_fork_and_a_mate_are_named():
    fork = "r3k2r/8/8/3N4/8/8/8/4K3 w kq - 0 1"
    assert line_notes(fork, ["d5c7"]) == ["your move, a check that also hits their rook on a8"]
    scholars = fen_after("e4 e5 Bc4 Nc6 Qh5")
    assert line_notes(scholars, ["g8f6", "h5f7"]) == [
        "your move, hitting their queen on h5",
        "checkmate",
    ]
    # Their reply is the one whose mate the engine counts
    assert line_notes(DAMIANO, [F6, "d1e2"], mate=3)[1] == "with mate in 3"


def test_getting_out_of_check_is_named():
    assert line_notes("4r1k1/8/8/8/8/8/8/4K3 w - - 0 1", ["e1d1"]) == [
        "your move, answering the check"
    ]
    # ...unless the answer does more than that
    assert line_notes(fen_after("e4 f6 Qh5+"), ["g7g6"]) == ["your move, hitting their queen on h5"]


def test_what_the_line_leaves_you_with():
    # Your king has to move, so castling is gone
    after_f6 = play(DAMIANO, F6)
    end = play(after_f6, "f3e5", "f6e5", "d1h5", "e8e7")
    assert "By the end of it you can no longer castle." in line_leaves(after_f6, end)
    # Castling itself is no loss
    bishop = fen_after("e4 e5 Nf3 Nc6 Bc4")
    assert line_leaves(bishop, play(bishop, "f8c5", "e1g1")) == []
    # A capture on c3 wrecks your pawns
    knight = fen_after("e4 e5 Nc3")
    assert line_leaves(knight, play(knight, "f8b4", "g1f3", "b4c3", "b2c3")) == [
        "Your pawns end up doubled on the c-file.",
        "Your a-pawn ends up isolated.",
    ]
    # Behind in development, said only by a clear margin, and only about you
    lag = "r1bqk2r/pppp1ppp/2n2n2/2b1p3/4P3/8/PPPP1PPP/RNBQKBNR"
    assert line_leaves(f"{lag} b KQkq - 0 1", f"{lag} w KQkq - 0 1") == [
        "You end up behind in development: 4 of your minor pieces are still at home against 1 "
        "of theirs."
    ]
    assert line_leaves(f"{lag} w KQkq - 0 1", f"{lag} b KQkq - 0 1") == []


def test_how_they_punish_it_says_what_it_costs_without_naming_the_better_move():
    line = review.punishment(DAMIANO, F6, "b8c6", damiano().get)
    assert line.label == "If you play 2...f6"
    assert [p.engine for p in line.plies] == [False, *[True] * 7]
    assert line.plies[1].note == "taking your pawn on e5"
    assert line.punishes
    assert line.why == (
        "If you play 2...f6, they answer 3.Nxe5, taking your pawn on e5. "
        "By 6.Qxh8 you have lost 4 points of material. "
        "The engine scores it -3.0 for you, while a better move gets -0.2 for you."
    )
    assert "Nc6" not in line.why and line.ideas == []  # you can still try again


def test_development_is_only_said_once():
    fen = fen_after("e4 e5 Nf3")
    lines = {fen: line_eval(-20, "b8c6"), play(fen, F6): line_eval(150, "b1c3 a7a6 f1c4 a6a5")}
    faults = review.punishment(fen, F6, "b8c6", lines.get).faults
    assert sum("minor pieces" in f for f in faults) == 1


def test_a_mate_is_said_plainly():
    fen = fen_after("e4 e5 Bc4 Nc6 Qh5")
    after = play(fen, "g8f6")
    lines = {
        fen: line_eval(-50, "g7g6"),
        after: line_eval(10_000, "h5f7", mate=1),
        play(after, "h5f7"): LineEval(-10_000, 0, None),
    }
    line = review.punishment(fen, "g8f6", "g7g6", lines.get)
    assert line.punishes
    assert line.why == (
        "If you play 3...Nf6, they answer 4.Qxf7#, checkmate. "
        "The engine scores it mate in 1 for them, while a better move gets -0.5 for you."
    )


def test_winning_material_and_still_going_wrong_is_said_too():
    # You keep the knight here, three points for two pawns, and are still clearly worse
    fen = DAMIANO
    lines = {
        fen: line_eval(-20, "b8c6"),
        play(fen, F6): line_eval(250, "f3e5 f6e5 d1h5 g7g6 h5e5"),
    }
    line = review.punishment(fen, F6, "b8c6", lines.get)
    assert line.punishes
    assert "By 5.Qxe5+ you have won a pawn, but that isn't enough." in line.why


def test_with_level_material_and_a_clearly_worse_position_the_position_is_the_cost():
    fen = chess.STARTING_FEN
    lines = {fen: line_eval(30, "e2e4"), play(fen, "a2a3"): line_eval(150, "e7e5 g1f3")}
    line = review.punishment(fen, "a2a3", "e2e4", lines.get)
    assert line.punishes
    assert "Material stays level over this line, so what it costs is the position." in line.why


def test_with_no_real_punishment_it_doesnt_claim_one():
    fen = chess.STARTING_FEN
    lines = {fen: line_eval(30, "e2e4"), play(fen, "a2a3"): line_eval(10, "e7e5 g1f3")}
    line = review.punishment(fen, "a2a3", "e2e4", lines.get)
    assert not line.punishes
    assert line.why == (
        "If you play 1.a3, they answer 1...e5, a pawn into the centre. "
        "Material stays level over this line. There's no quick punishment here: you just get less "
        "from the position. The engine scores it -0.1 for you, while a better move gets +0.3 for "
        "you."
    )


class Scripted:
    """An engine that answers from a table of positions."""

    def __init__(self, lines: dict[str, LineEval]) -> None:
        self.lines = lines

    def evaluate(self, board, *, depth, multipv=1):
        return [self.lines.get(board.fen()) or LineEval(0, None, None)]

    def close(self) -> None:
        pass


def test_the_check_shows_the_line_after_a_wrong_move_only(tmp_path):
    client = _client(tmp_path, lambda: Scripted(damiano()))
    miss = client.post("/api/coach/check", json={"fen": DAMIANO, "uci": F6}).json()
    assert not miss["ok"] and miss["loss"] == 2.8
    line = miss["line"]
    assert line["punishes"] and line["why"].startswith("If you play 2...f6, they answer 3.Nxe5")
    assert [p["san"] for p in line["plies"]][:3] == ["f6", "Nxe5", "fxe5"]
    assert line["plies"][1]["note"] == "taking your pawn on e5"
    assert line["plies"][1]["fen"] == play(DAMIANO, F6, "f3e5")
    # The better move stays out of it, so you can still try again
    assert "Nc6" not in str(line) and line["ideas"] == []


def test_a_right_move_has_no_line(tmp_path):
    lines = {**damiano(), play(DAMIANO, "b8c6"): line_eval(20, "f1b5")}
    client = _client(tmp_path, lambda: Scripted(lines))
    hit = client.post("/api/coach/check", json={"fen": DAMIANO, "uci": "b8c6"}).json()
    assert hit["ok"] and hit["line"] is None


def test_without_an_engine_a_wrong_move_still_gets_an_answer(tmp_path):
    client = _client(tmp_path, no_engine)
    miss = client.post("/api/coach/check", json={"fen": DAMIANO, "uci": F6, "best": "b8c6"}).json()
    assert not miss["ok"] and miss["line"] is None


def test_a_due_card_answered_wrong_in_today_shows_the_line(tmp_path):
    path = [*NXC3, "g8f6"]
    fen = play(chess.STARTING_FEN, *path)
    lines = {fen: line_eval(150, "e4e5"), play(fen, "g1f3"): line_eval(-40, "f6e4 c3e4")}
    client = _client(tmp_path, lambda: Scripted(lines))
    card = CoachCard("Italian Game", "white", path, "punish", "2026-01-01")
    with Store(tmp_path / "coach.db") as store:
        store.save_coach_attempt(card, "g1f3", False, datetime(2026, 1, 1, 12, tzinfo=UTC))
    r = client.post("/api/today/card", json={"id": card.id, "uci": "g1f3"}).json()
    assert not r["correct"] and r["target"]["san"] == "e5"
    assert r["line"]["label"] == "If you play 5.Nf3"
    assert r["line"]["plies"][1]["note"] == "taking your pawn on e4"


@pytest.mark.engine
def test_the_real_engine_punishes_the_damiano():
    try:
        engine = Engine()
    except EngineNotFoundError:
        pytest.skip("no Stockfish")
    with engine:

        def analyse(fen: str) -> LineEval:
            return engine.evaluate(chess.Board(fen), depth=14)[0]

        line = review.punishment(DAMIANO, F6, "b8c6", analyse)
    # Whichever reply it prefers, the text follows its line
    assert line.punishes and line.score is not None and line.score.cp <= -100
    reply = line.plies[1]
    assert line.why.startswith(f"If you play 2...f6, they answer {reply.text}")
    assert all(p.fen for p in line.plies) and len(line.plies) > 2
