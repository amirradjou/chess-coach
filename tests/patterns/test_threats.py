from dataclasses import replace
from pathlib import Path

import chess
from fastapi.testclient import TestClient

from chess_coach.coach.digest import build, render
from chess_coach.coach.threats import Share, check, gap, pending, percent, report
from chess_coach.db import Store
from chess_coach.engine.motifs import pass_turn
from chess_coach.engine.stockfish import LineEval
from chess_coach.engine.threats import IGNORED, UNSAFE
from chess_coach.models import Color, Judgement, MoveFact, Phase, Puzzle, Speed, ThreatFact
from chess_coach.web.app import THREAT, create_app

from .test_patterns import game, move

BEFORE_A5 = "r5nr/2p2kpp/pp1bbp2/2p5/P3P3/1NN1BP2/1PP3PP/R2R2K1 w - - 1 14"
AFTER_A5 = "r5nr/2p2kpp/pp1bbp2/P1p5/4P3/1NN1BP2/1PP3PP/R2R2K1 b - - 0 14"
AFTER_NE7 = "r6r/2p1nkpp/pp1bbp2/P1p5/4P3/1NN1BP2/1PP3PP/R2R2K1 w - - 1 15"
BEFORE_NC4 = "r2r4/2p1nkpp/pp1bbp2/P1p5/4P3/2N1BP2/1PPN2PP/R2R2K1 w - - 3 16"


def fact(game_id: str, ply: int, fen: str, uci: str, best: str, **kw) -> MoveFact:
    """A move from a real position; a blunder by the side to move unless told otherwise."""
    board = chess.Board(fen)
    fields = dict(
        game_id=game_id,
        ply=ply,
        move_number=board.fullmove_number,
        color=Color.WHITE if board.turn == chess.WHITE else Color.BLACK,
        is_user=board.turn == chess.WHITE,
        san=board.san(chess.Move.from_uci(uci)),
        uci=uci,
        fen_before=fen,
        eval_before=0,
        eval_after=-400,
        best_move=best,
        best_san=board.san(chess.Move.from_uci(best)),
        cp_loss=400,
        wp_before=50.0,
        wp_after=15.0,
        wp_loss=35.0,
        judgement=Judgement.BLUNDER,
        phase=Phase.MIDDLEGAME,
        depth=12,
        clock_after=200.0,
    )
    fields.update(kw)
    return MoveFact(**fields)  # type: ignore[arg-type]


def theirs(m: MoveFact) -> MoveFact:
    return replace(m, is_user=False, color=Color.BLACK, ply=m.ply + 1)


def planted(you: tuple[int, int], them: tuple[int, int], n: int = 100):
    """10 games with n/10 blunders each for you and for them; the first `you` = (ignored, unsafe)
    of yours and `them` of theirs are labelled, the rest checked without a label."""
    games = [game(i) for i in range(10)]
    mine = [move(games[i % 10].id, 2 * (i // 10) + 21, error=True) for i in range(n)]
    other = [theirs(m) for m in mine]
    threats = {}
    for ms, (ignored, unsafe) in ((mine, you), (other, them)):
        for i, m in enumerate(ms):
            label = IGNORED if i < ignored else UNSAFE if i < ignored + unsafe else ""
            threats[(m.game_id, m.ply)] = ThreatFact(m.game_id, m.ply, label, depth=12)
    return games, mine + other, threats


# -- the report -----------------------------------------------------------------------------


def test_shares_against_your_opponents():
    games, moves, threats = planted(you=(30, 10), them=(10, 10))
    unchecked = move(games[0].id, 61, error=True)
    inaccuracy = replace(move(games[0].id, 63, error=True), judgement=Judgement.INACCURACY)
    rushed = move(games[1].id, 65, error=True, clock=5.0)
    games[1].time_control = "300+0"
    r = report(games, [*moves, unchecked, inaccuracy, rushed], threats)
    assert (r.you, r.them, r.unchecked) == (100, 100, 1)
    ignored, unsafe = r.steps
    assert (ignored.label, ignored.you.hits, ignored.them.hits) == (IGNORED, 30, 10)
    assert ignored.verdict == "more often than your opponents" and ignored.yours
    assert unsafe.verdict == "same as your opponents" and not unsafe.yours
    assert percent(ignored.you) == "30% (22-40%)"
    assert percent(Share(0, 10)) == "0% (0-28%)" and percent(Share(5, 5)) == "100% (57-100%)"
    assert gap(ignored).startswith("+20.0 points, +9.")
    assert ignored.examples == []  # these planted moves have no position to explain


def test_bullet_after_move_10_does_not_count_for_either_side():
    games, moves, threats = planted(you=(5, 5), them=(5, 5), n=20)
    for g in games:
        g.speed = Speed.BULLET
    r = report(games, moves, threats)  # every planted blunder is on move 11 or later
    assert (r.you, r.them) == (0, 0)


def test_examples_say_what_the_threat_was():
    g = game(1)
    ne7 = fact(g.id, 28, AFTER_A5, "g8e7", "b6b5", is_user=True)
    g.user_color = Color.BLACK
    threats = {(g.id, 28): ThreatFact(g.id, 28, IGNORED, "a5b6", "axb6", 426, None, 12)}
    threats[(g.id, 28)].last_san, threats[(g.id, 28)].last_uci = "a5", "a4a5"
    r = report([g], [ne7], threats)
    (e,) = r.steps[0].examples
    assert e.why == "Their a5 attacked your pawn on b6, and your Ne7 left it there."
    assert e.evidence.url == "https://lichess.org/g1#28" and e.evidence.move == "14...Ne7"


def test_the_digest_compares_you_with_your_opponents():
    games, moves, threats = planted(you=(30, 10), them=(10, 10))
    errors = [m for m in moves if m.judgement is Judgement.BLUNDER]
    text = render(build(games, [m for m in moves if m.is_user], threats=threats, errors=errors))
    assert "## Their threats and your blunder check" in text
    assert "| **Ignored their threat**: their last move threatened something" in text
    assert "| 30% (22-40%) | 10% (6-17%) | more often than your opponents" in text
    assert "**Ignored their threat** is a step to work on" in text
    assert "Unsafe move** is a step" not in text  # the same as theirs: never called yours
    games, moves, threats = planted(you=(10, 10), them=(10, 10))
    text = render(build(games, [m for m in moves if m.is_user], threats=threats, errors=moves))
    assert "not a weakness of yours" in text
    assert "blunder check" not in render(build(games, [m for m in moves if m.is_user]))


# -- checking ------------------------------------------------------------------------------


def test_pending_newest_games_first_and_both_sides():
    old, new = game(1), game(2)
    moves = [
        move(old.id, 21, error=True),
        theirs(move(new.id, 21, error=True)),
        move(new.id, 21, error=True),
        move(new.id, 23),  # not an error
    ]
    todo = pending([old, new], moves, {(new.id, 21)})
    assert [(m.game_id, m.ply) for m in todo] == [(new.id, 22), (old.id, 21)]


class Lines:
    """A fake engine: a calm line everywhere except the positions it is told about."""

    def __init__(self, lines: dict[str, LineEval]):
        self.lines = lines
        self.asked: list[str] = []

    def evaluate(self, board, *, depth, multipv=1):
        self.asked.append(board.fen())
        quiet = sorted(board.legal_moves, key=lambda m: m.uci())[0]
        return [self.lines.get(board.fen(), LineEval(20, None, quiet))]


def club_store(db: Path) -> Store:
    """Two games through the same 16.Nc4?? (yours), one with 14...Ne7? (theirs) before it."""
    store = Store(db)
    for i in (1, 2):
        g = game(i, pgn="*")
        store.upsert_game(g)
        moves = [
            fact(
                g.id,
                30,
                "r6r/2p1nkpp/pp1bbp2/P1p5/4P3/2N1BP2/1PPN2PP/R2R2K1 b - - 2 15",
                "h8d8",
                "b6b5",
                is_user=False,
                judgement=Judgement.GOOD,
            ),
            fact(g.id, 31, BEFORE_NC4, "d2c4", "a5b6", eval_before=-14, eval_after=-454),
            fact(
                g.id,
                32,
                "r2r4/2p1nkpp/pp1bbp2/P1p5/2N1P3/2N1BP2/1PP3PP/R2R2K1 b - - 4 16",
                "e6c4",
                "e6c4",
                is_user=False,
                judgement=Judgement.BEST,
            ),
        ]
        if i == 1:
            moves += [
                fact(g.id, 27, BEFORE_A5, "a4a5", "b3d2", judgement=Judgement.GOOD, eval_before=7),
                fact(
                    g.id,
                    28,
                    AFTER_A5,
                    "g8e7",
                    "b6b5",
                    is_user=False,
                    eval_before=-20,
                    eval_after=-286,
                ),
                fact(
                    g.id,
                    29,
                    AFTER_NE7,
                    "b3d2",
                    "a5b6",
                    judgement=Judgement.MISTAKE,
                    eval_before=286,
                    eval_after=-24,
                    clock_after=None,
                ),
            ]
        store.replace_moves(g.id, sorted(moves, key=lambda m: m.ply), depth=12)
    return store


def line(cp: int, moves: str) -> LineEval:
    pv = [chess.Move.from_uci(m) for m in moves.split()]
    return LineEval(cp, None, pv[0], pv)


def test_check_stores_a_row_per_error_and_resumes(tmp_path):
    nc4 = chess.Board(BEFORE_NC4)
    nc4.push_uci("d2c4")
    engine = Lines(
        {
            # 14...Ne7 or passing: axb6 wins a pawn either way, two had Black passed
            pass_turn(chess.Board(AFTER_A5)).fen(): line(519, "a5b6 e6b3 c2b3 g8e7 b6c7 d6c7 e3c5"),
            AFTER_NE7: line(241, "a5b6 h8b8 b3c5 d6c5 e3c5 c7b6"),
            nc4.fen(): line(434, "e6c4"),  # 16.Nc4?? Bxc4
        }
    )
    with club_store(tmp_path / "coach.db") as store:
        seen = []
        assert check(store, engine, on_check=lambda i, n: seen.append((i, n))) == 4
        assert seen[-1] == (4, 4)
        rows = store.threats()
        assert {k: (t.label, t.threat_san) for k, t in rows.items()} == {
            ("lichess:g2", 31): (UNSAFE, "Bxc4"),
            ("lichess:g1", 31): (UNSAFE, "Bxc4"),
            ("lichess:g1", 29): ("", ""),  # 15.Nd2 missed axb6 itself: neither
            ("lichess:g1", 28): (IGNORED, "axb6"),  # theirs: the peer baseline
        }
        assert rows[("lichess:g1", 28)].last_san == "a5"
        # two searches a move (passing, and after it), each position once: the two games share
        # 16.Nc4's
        assert len(engine.asked) == len(set(engine.asked)) == 6
        assert check(store, engine) == 0 and len(engine.asked) == 6  # nothing left to do


# -- the web coach --------------------------------------------------------------------------


def threat_app(tmp_path: Path) -> tuple[TestClient, Path]:
    """Your 14...Ne7? (you have Black) ignoring 14.a5's threat, as a puzzle."""
    db = tmp_path / "coach.db"
    g = game(1, pgn="*")
    g.user_color = Color.BLACK
    with Store(db) as store:
        store.upsert_game(g)
        moves = [
            fact(g.id, 27, BEFORE_A5, "a4a5", "b3d2", is_user=False),
            fact(g.id, 28, AFTER_A5, "g8e7", "b6b5", is_user=True),
            fact(g.id, 29, AFTER_NE7, "b3d2", "a5b6", is_user=False, judgement=Judgement.GOOD),
        ]
        store.replace_moves(g.id, moves, depth=12)
        store.add_puzzle(Puzzle(f"{g.id}:28", g.id, 28, AFTER_A5, "b6b5", "b5", "Ne7", "", 300, 18))
    return TestClient(create_app(db, lambda: None)), db


def test_the_overview_has_a_threat_section_once_checked(tmp_path):
    client, db = threat_app(tmp_path)
    assert client.get("/api/overview").json()["threats"] is None
    with Store(db) as store:
        store.save_threats(
            [
                ThreatFact("lichess:g1", 28, IGNORED, "a5b6", "axb6", 426, None, 12),
                ThreatFact("lichess:g1", 27, "", depth=12),
            ]
        )
    t = client.get("/api/overview").json()["threats"]
    assert (t["you"], t["them"], t["unchecked"]) == (1, 1, 0)
    ignored = t["steps"][0]
    assert (ignored["title"], ignored["you"]["rate"], ignored["them"]["rate"]) == (
        "Ignored their threat",
        100,
        0,
    )
    assert ignored["verdict"] == "same as your opponents"  # one move each proves nothing
    (e,) = ignored["examples"]
    assert e["threat"] == "axb6" and THREAT in e["svg"]
    assert e["reasons"][0] == "Their a5 attacked your pawn on b6, and your Ne7 left it there."


def test_practice_asks_what_they_threaten_first(tmp_path):
    client, db = threat_app(tmp_path)
    p = client.get("/api/drill/next").json()
    assert p["ask_threat"] is None  # not checked yet
    assert client.post("/api/drill/threat", json={"id": p["id"], "uci": "a5b6"}).status_code == 404
    with Store(db) as store:
        store.save_threats([ThreatFact("lichess:g1", 28, IGNORED, "a5b6", "axb6", 426, None, 12)])
    p = client.get("/api/drill/next").json()
    assert p["ask_threat"] == {"last": "14.a5", "last_uci": "a4a5"}
    right = client.post("/api/drill/threat", json={"id": p["id"], "uci": "a5b6"}).json()
    assert right == {
        "right": True,
        "guess": "axb6",
        "threat": {"uci": "a5b6", "san": "axb6"},
        "why": "Their a5 attacked your pawn on b6.",
    }
    wrong = client.post("/api/drill/threat", json={"id": p["id"], "uci": "c3b5"}).json()
    assert (wrong["right"], wrong["guess"]) == (False, "Nb5")
    assert client.post("/api/drill/threat", json={"id": p["id"], "uci": "a5a7"}).status_code == 400
    # the question isn't recorded: the puzzle is still due, with no attempt
    assert client.get("/api/progress").json()["puzzles"]["attempts"] == 0
    board = client.get("/api/board", params={"fen": AFTER_A5, "arrow": "a5b6", "color": "threat"})
    assert board.status_code == 200 and THREAT in board.text
