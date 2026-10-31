from datetime import UTC, datetime
from pathlib import Path

import chess
from fastapi.testclient import TestClient

from chess_coach.coach.habits import lesson_for, missed_kinds
from chess_coach.coach.repertoire import favorites, fixes, is_book, usual_line
from chess_coach.db import Store
from chess_coach.engine.stockfish import EngineNotFoundError, LineEval
from chess_coach.models import Color, Judgement, MoveFact, Phase
from chess_coach.patterns.features import kind_of, move_rows, piece_of
from chess_coach.puzzles.make import make, verify_fix
from chess_coach.web.app import create_app

from .test_patterns import game, planted

ITALIAN = "1. e4 e5 2. Nf3 Nc6 3. Bc4 Bc5 *"
AFTER_ITALIAN = "r1bqk1nr/pppp1ppp/2n5/2b1p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 4 4"


class FakeEngine:
    def __init__(self, lines):
        self.lines = lines

    def evaluate(self, board, *, depth, multipv=1):
        return self.lines


def kf1(game_id: str, cp_loss: int = 120) -> MoveFact:
    """A habitual, unnamed 4.Kf1 in the Italian, with castling as the engine's choice."""
    return MoveFact(
        game_id=game_id,
        ply=7,
        move_number=4,
        color=Color.WHITE,
        is_user=True,
        san="Kf1",
        uci="e1f1",
        fen_before=AFTER_ITALIAN,
        eval_before=30,
        eval_after=30 - cp_loss,
        best_move="e1g1",
        best_san="O-O",
        cp_loss=cp_loss,
        wp_before=52.0,
        wp_after=45.0,
        wp_loss=7.0,
        judgement=Judgement.INACCURACY,
        phase=Phase.OPENING,
        depth=12,
    )


def italian_games(n: int = 6):
    games = []
    for i in range(n):
        g = game(i, result="0-1" if i % 2 else "1-0", pgn=ITALIAN)
        g.opening = "Italian Game: Giuoco Piano"
        games.append(g)
    return games


# -- analysis -------------------------------------------------------------------------------


def test_piece_and_kind():
    assert [piece_of(s) for s in ("e4", "Nf3", "Bxf7+", "O-O", "Qh5", "Kf1")] == [
        "pawn",
        "knight",
        "bishop",
        "king",
        "queen",
        "king",
    ]
    assert [kind_of(s) for s in ("e4", "Nxe5", "Qh5+", "O-O-O", "Qxf7#")] == [
        "quiet",
        "capture",
        "check",
        "castle",
        "capture",
    ]


def test_missed_captures_are_a_habit():
    games, moves = planted()
    for m in moves:  # every error skipped a capture the engine wanted
        if m.judgement is Judgement.BLUNDER:
            m.best_san = "Nxe5"
    habits = missed_kinds(move_rows(games, moves))
    assert [h.title for h in habits] == ["You miss captures"]
    assert habits[0].mistakes == 30 and len(habits[0].examples) == 3


def test_lessons_prefer_the_most_specific_feature():
    assert "queen" in lesson_for((("phase", "middlegame"), ("piece", "queen")))
    assert "Around move 12" in lesson_for((("moves", "11-20"),))
    assert "Sicilian Defense" in lesson_for((("opening", "Sicilian Defense"),))


def test_fixes_skip_book_moves_and_find_habits():
    games = italian_games()
    moves = [kf1(g.id) for g in games[:3]]
    assert is_book(chess.STARTING_FEN, "e2e4")  # 1.e4 is named theory, never a "fix"
    assert not is_book(AFTER_ITALIAN, "e1f1")
    found = fixes(games, moves)
    assert [(f.move, f.best_san, f.times) for f in found] == [("4.Kf1", "O-O", 3)]
    assert found[0].games[0][2] == f"{games[0].url}#7"
    assert fixes(games, [kf1(g.id, cp_loss=20) for g in games[:3]]) == []  # too small to matter


def test_favorites_and_usual_line():
    games = italian_games()
    fav = favorites(games, [kf1(g.id) for g in games[:3]], min_games=5)
    assert [f.family for f in fav["white"]] == ["Italian Game"] and fav["black"] == []
    f = fav["white"][0]
    assert (f.games, f.wins, f.losses, f.line) == (6, 3, 3, "1. e4 e5 2. Nf3 Nc6 3. Bc4 Bc5")
    assert f.fixes and f.book and f.book.startswith("1. e4 e5")
    assert usual_line(games[:1]) == "1. e4 e5 2. Nf3 Nc6 3. Bc4 Bc5"


def test_opening_moves_are_read_from_lichess_style_pgn():
    from chess_coach.coach.repertoire import _san_moves

    pgn = (
        '[Event "Rated bullet game"]\n\n1. e4 { [%clk 0:01:00] } 1... c5 { [%clk 0:01:00] } '
        "2.d4 cxd4 3. c3!? dxc3 (3... d3) 4. Nxc3 Nc6 1-0"
    )
    assert _san_moves(pgn, 10) == ["e4", "c5", "d4", "cxd4", "c3", "dxc3", "Nxc3", "Nc6"]


def test_an_opening_fix_accepts_every_good_move():
    games = italian_games()
    fix = fixes(games, [kf1(g.id) for g in games[:3]])[0]
    O_O, C3, D3, KF1 = (chess.Move.from_uci(u) for u in ("e1g1", "c2c3", "d2d3", "e1f1"))
    engine = FakeEngine([LineEval(40, None, O_O), LineEval(25, None, C3), LineEval(-10, None, D3)])
    p = verify_fix(engine, fix, "Italian Game")
    assert (p.solution_san, p.accept, p.tags) == ("O-O", ["c2c3"], ["opening-fix", "Italian Game"])
    assert p.is_right("c2c3") and not p.is_right("d2d3")
    fine = FakeEngine([LineEval(40, None, O_O), LineEval(30, None, KF1)])
    assert verify_fix(fine, fix, "Italian Game") is None  # deeper, the habit is fine


# -- web ------------------------------------------------------------------------------------


class FirstMoves:
    """Answers every position with its first two legal moves in UCI order, White a little better."""

    started = 0

    def __init__(self):
        FirstMoves.started += 1

    def evaluate(self, board, *, depth, multipv=1):
        scratch, pv = board.copy(), []
        for _ in range(2):
            moves = sorted(scratch.legal_moves, key=lambda m: m.uci())
            if not moves:
                break
            pv.append(moves[0])
            scratch.push(moves[0])
        cp = 30 if board.turn == chess.WHITE else -30
        return [LineEval(cp, None, pv[0] if pv else None, pv)]


def _app(tmp_path: Path, engine=FirstMoves) -> TestClient:
    db = tmp_path / "coach.db"
    games, moves = planted()
    with Store(db) as store:
        for g in games:
            store.upsert_game(g)
            store.replace_moves(g.id, [m for m in moves if m.game_id == g.id], depth=12)
        e2e4, d2d4 = chess.Move.from_uci("e2e4"), chess.Move.from_uci("d2d4")
        # the planted moves are from the start position: e4 is the answer, d4 a clear second
        for m in moves:
            m.fen_before, m.uci = chess.STARTING_FEN, "a2a3"
        store.conn.execute("UPDATE moves SET fen_before = ?, uci = 'a2a3'", (chess.STARTING_FEN,))
        store.conn.commit()
        make(store, FakeEngine([LineEval(300, None, e2e4), LineEval(0, None, d2d4)]), limit=2)
    return TestClient(create_app(db, engine))


def test_pages_and_static_files(tmp_path):
    client = _app(tmp_path)
    assert "Chess coach" in client.get("/").text
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/style.css").headers["content-type"].startswith("text/css")
    assert client.get("/static/index.html").status_code == 404
    assert client.get("/static/nope.js").status_code == 404


def test_overview_has_boards_and_lessons(tmp_path):
    o = _app(tmp_path).get("/api/overview").json()
    assert (o["games"], o["moves"]) == (10, 200)
    titles = [h["title"] for h in o["situations"]]
    assert "In the endgame" in titles
    endgame = o["situations"][titles.index("In the endgame")]
    assert "Activate your king" in endgame["lesson"]
    assert endgame["examples"][0]["svg"].startswith("<svg") and "#" in endgame["examples"][0]["url"]
    titles = {h["title"] for h in o["habits"]}
    assert "Leaving a piece where it can be taken" in titles
    assert "Going wrong with little time left" not in titles  # discounted; see test_rushed


def test_drill_round_trip(tmp_path):
    client = _app(tmp_path)
    tags = {t["tag"]: t for t in client.get("/api/drill/tags").json()}
    assert sum(tags[t]["due"] for t in ("middlegame", "endgame") if t in tags) == 2
    p = client.get("/api/drill/next").json()
    assert not p["done"] and p["played"] == "Qh5" and p["side"] == "white" and "<svg" in p["svg"]
    assert client.post("/api/drill/answer", json={"id": p["id"], "uci": "e2e5"}).status_code == 400
    r = client.post("/api/drill/answer", json={"id": p["id"], "uci": "e2e4"}).json()
    today = datetime.now(UTC).date().isoformat()  # the app schedules by the UTC day
    assert r["correct"] and r["solution"] == "e4" and r["next_due"] > today
    q = client.get("/api/drill/next").json()
    miss = client.post("/api/drill/answer", json={"id": q["id"], "uci": "?"}).json()
    assert not miss["correct"] and miss["answer"] is None
    assert client.get("/api/drill/next").json() == {"done": True, "puzzles": 2}
    prog = client.get("/api/progress").json()
    assert prog["puzzles"] == {"attempts": 2, "solved": 1}
    assert client.post("/api/drill/answer", json={"id": "nope", "uci": "?"}).status_code == 404


def test_a_wrong_answer_shows_why_and_can_be_tried_again(tmp_path):
    FirstMoves.started = 0
    client = _app(tmp_path)
    p = client.get("/api/drill/next").json()
    miss = client.post("/api/drill/answer", json={"id": p["id"], "uci": "a2a3"}).json()
    assert (miss["correct"], miss["answer"], miss["recorded"]) == (False, "a3", True)
    tried = miss["tried"]
    assert [(x["san"], x["engine"]) for x in tried["plies"]] == [
        ("a3", False),
        ("a5", True),
        ("Ra2", True),
    ]
    assert tried["why"].startswith("They answer 1...a5.") and "e4" not in tried["why"]
    assert tried["faults"][0].startswith("It spends a move on a wing pawn")
    assert tried["ideas"] == []  # what the better move does would give it away
    # a second try doesn't count: the miss stands
    hit = client.post(
        "/api/drill/answer", json={"id": p["id"], "uci": "e2e4", "record": False}
    ).json()
    assert hit["correct"] and not hit["recorded"] and hit["next_due"] == miss["next_due"]
    assert client.get("/api/progress").json()["puzzles"] == {"attempts": 1, "solved": 0}
    lines = client.get(
        "/api/drill/review", params={"id": p["id"], "tried": ["a2a3", "e2e4"]}
    ).json()["lines"]
    assert [(x["kind"], x["label"], x["score"]) for x in lines] == [
        ("best", "Best: e4", "+0.3"),
        ("tried", "Your try: a3", "+0.3"),
    ]
    assert FirstMoves.started == 1  # one engine for the app


def test_any_move_can_be_explored(tmp_path):
    client = _app(tmp_path)
    fen = chess.STARTING_FEN
    a = client.post("/api/explore", json={"fen": fen, "moves": ["e2e4", "e7e5"]}).json()
    assert a["kind"] == "analysis" and a["score"] == "+0.3"
    assert [x["engine"] for x in a["plies"]] == [False, False, True, True]
    assert a["why"] == "After 1...e5: +0.3 for you. The engine's move: 2.a3."
    assert client.post("/api/explore", json={"fen": fen, "moves": ["e2e5"]}).status_code == 400
    assert client.post("/api/explore", json={"fen": "nope", "moves": []}).status_code == 400
    assert client.get("/api/drill/review", params={"id": "nope"}).status_code == 404


def test_board_images(tmp_path):
    client = _app(tmp_path)
    start = chess.STARTING_FEN
    r = client.get(
        "/api/board",
        params={"fen": start, "orientation": "black", "last": "e2e4", "arrow": "e7e5"},
    )
    assert r.headers["content-type"].startswith("image/svg+xml") and r.text.startswith("<svg")
    assert client.get("/api/board", params={"fen": start, "color": "red"}).status_code == 400
    assert client.get("/api/board", params={"fen": "nope"}).status_code == 400
    assert client.get("/api/board", params={"fen": start, "arrow": "zzzz"}).status_code == 400


def test_lines_without_stockfish(tmp_path):
    def missing():
        raise EngineNotFoundError("no engine")

    client = _app(tmp_path, engine=missing)
    p = client.get("/api/drill/next").json()
    miss = client.post("/api/drill/answer", json={"id": p["id"], "uci": "a2a3"}).json()
    assert [x["san"] for x in miss["tried"]["plies"]] == ["a3"]
    assert "Stockfish isn't available" in miss["tried"]["why"]
    lines = client.get("/api/drill/review", params={"id": p["id"]}).json()["lines"]
    assert [(x["label"], x["score"]) for x in lines] == [("Best: e4", None)]
