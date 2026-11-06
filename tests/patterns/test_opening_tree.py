from pathlib import Path

import chess
from fastapi.testclient import TestClient

from chess_coach.coach import tree as optree
from chess_coach.db import Store
from chess_coach.engine.stockfish import EngineNotFoundError
from chess_coach.models import Color, Judgement, MoveFact, Phase
from chess_coach.web.app import create_app

from .test_patterns import game

DANISH = "e4 e5 d4 exd4 c3 dxc3 Nxc3".split()
NXC3 = ["e2e4", "e7e5", "d2d4", "e5d4", "c2c3", "d4c3", "b1c3"]


def facts(game_id: str, sans: list[str], loss=None, best=None) -> list[MoveFact]:
    """Facts for a game from the start, the user White. `loss` maps ply -> win probability lost,
    `best` ply -> the SAN the engine wanted (default: the move played)."""
    board, out = chess.Board(), []
    for ply, san in enumerate(sans, 1):
        move = board.parse_san(san)
        best_san = (best or {}).get(ply, san)
        wp = (loss or {}).get(ply, 0.0)
        mover = Color.WHITE if board.turn == chess.WHITE else Color.BLACK
        out.append(
            MoveFact(
                game_id=game_id,
                ply=ply,
                move_number=board.fullmove_number,
                color=mover,
                is_user=mover is Color.WHITE,
                san=san,
                uci=move.uci(),
                fen_before=board.fen(),
                eval_before=100 if mover is Color.WHITE else -100,
                eval_after=100,
                best_move=board.parse_san(best_san).uci(),
                best_san=best_san,
                cp_loss=150 if wp else 0,
                wp_before=50.0,
                wp_after=50.0 - wp,
                wp_loss=wp,
                judgement=Judgement.INACCURACY if wp else Judgement.BEST,
                phase=Phase.OPENING,
                depth=12,
            )
        )
        board.push(move)
    return out


def games_and_facts():
    """Three games with 4...Nf6 (an inaccuracy; 5.e5 punishes it, you played 5.Nf3), two with
    4...Nc6, one Sicilian."""
    games, moves = [], []
    for i in range(3):
        games.append(game(i, result="0-1" if i else "1-0"))
        moves += facts(games[-1].id, [*DANISH, "Nf6", "Nf3"], {8: 15.0, 9: 12.0}, {9: "e5"})
    for i in range(3, 5):
        games.append(game(i))
        moves += facts(games[-1].id, [*DANISH, "Nc6", "Nf3"])
    games.append(game(5))
    moves += facts(games[-1].id, ["e4", "c5", "Nf3"])
    return games, moves


def test_the_tree_follows_your_games():
    games, moves = games_and_facts()
    root = optree.build(games, moves)
    assert len(root.games) == 6
    first = optree.walk(root, ["e2e4"])
    assert {u: len(e.facts) for u, e in first.edges.items()} == {"e7e5": 5, "c7c5": 1}
    node = optree.walk(root, NXC3)
    nf6, nc6 = node.edges["g8f6"], node.edges["b8c6"]
    assert (len(nf6.facts), nf6.verdict, nf6.punishable) == (3, "inaccuracy", True)
    assert (nc6.verdict, nc6.punishable) == ("best", False)
    assert nf6.child.best.best_san == "e5"  # the punishment
    assert optree.score(nf6.child.games, {g.id: g.user_result for g in games}) == 1 / 3
    assert optree.walk(root, ["d2d4"]) is None


def test_mistakes_are_listed_with_their_path():
    games, moves = games_and_facts()
    found = optree.mistakes(optree.build(games, moves))
    assert [(path, edge.san) for path, edge in found] == [([*NXC3, "g8f6"], "Nf6")]


def test_only_games_from_the_standard_start():
    games, moves = games_and_facts()
    for m in moves:
        if m.game_id == games[5].id and m.ply == 1:
            m.fen_before = "8/8/8/8/8/8/8/K6k w - - 0 1"
    assert len(optree.build(games, moves).games) == 5


def test_openings_need_enough_games():
    games, _ = games_and_facts()
    found = optree.openings(games)
    assert [(o.family, o.color, o.games) for o in found] == [("Italian Game", "white", 6)]
    assert optree.openings(games, min_games=7) == []


def _client(tmp_path: Path, engine) -> TestClient:
    db = tmp_path / "coach.db"
    games, moves = games_and_facts()
    with Store(db) as store:
        for g in games:
            store.upsert_game(g)
            store.replace_moves(g.id, [m for m in moves if m.game_id == g.id], depth=12)
    return TestClient(create_app(db, engine))


def no_engine():
    raise EngineNotFoundError("no engine")


def test_coach_pages(tmp_path):
    client = _client(tmp_path, no_engine)
    o = client.get("/api/coach/openings").json()
    assert [(x["family"], x["games"]) for x in o["white"]] == [("Italian Game", 6)]
    assert o["white"][0]["idea"].startswith("Both sides develop fast")
    q = {"family": "Italian Game", "color": "white"}
    node = client.get("/api/coach/node", params={**q, "path": ",".join(NXC3)}).json()
    assert (node["turn"], node["games"], node["target"]) == ("them", 5, None)
    nf6 = node["moves"][0]
    assert (nf6["san"], nf6["number"], nf6["games"], nf6["verdict"]) == (
        "Nf6",
        "4...",
        3,
        "inaccuracy",
    )
    assert nf6["punish"]["san"] == "e5" and nf6["punish"]["score"] == "+1.0"
    assert nf6["answers"] == [{"san": "Nf3", "games": 3, "right": False}]
    assert nf6["reasons"][0] == "It lets e5 hit their knight on f6."  # what their mistake allows
    after = client.get("/api/coach/node", params={**q, "path": ",".join([*NXC3, "g8f6"])}).json()
    assert after["turn"] == "you" and after["target"]["san"] == "e5"  # Nf3 isn't sound here
    assert after["moves"][0]["reasons"]  # why Nf3 falls short, in chess terms
    deck = client.get("/api/coach/punish", params=q).json()
    assert [(x["san"], x["punish"]["san"]) for x in deck] == [("Nf6", "e5")]
    assert client.get("/api/coach/node", params={**q, "path": "e2e5"}).status_code == 400
    assert client.get("/api/coach/node", params={**q, "color": "red"}).status_code == 400
    off = client.get("/api/coach/node", params={**q, "path": "d2d4"}).json()
    assert (off["games"], off["moves"], off["best"]) == (0, [], None)  # no games, no engine


def test_a_hanging_piece_is_named_once(tmp_path):
    """Their 2...Nd4?? leaves the knight to 3.Nxd4: explain() says so, and allows() (which names
    such captures for When they blunder) must not say it a second time."""
    db = tmp_path / "coach.db"
    sans = ["e4", "Nc6", "Nf3", "Nd4", "Nxd4"]
    with Store(db) as store:
        for i in range(2):
            g = game(i)
            store.upsert_game(g)
            store.replace_moves(g.id, facts(g.id, sans, {4: 30.0}, {4: "e5"}), depth=12)
    client = TestClient(create_app(db, no_engine))
    path = ",".join(["e2e4", "b8c6", "g1f3"])
    q = {"family": "Italian Game", "color": "white", "path": path}
    (nd4,) = client.get("/api/coach/node", params=q).json()["moves"]
    assert (nd4["san"], nd4["verdict"], nd4["punish"]["san"]) == ("Nd4", "blunder", "Nxd4")
    assert nd4["reasons"] == ["It leaves their knight on d4 hanging (attacked and not defended)."]


def test_coach_check_without_an_engine(tmp_path):
    client = _client(tmp_path, no_engine)
    board = chess.Board()
    for uci in [*NXC3, "g8f6"]:
        board.push_uci(uci)
    fen = board.fen()
    miss = client.post("/api/coach/check", json={"fen": fen, "uci": "g1f3", "best": "e4e5"}).json()
    assert (miss["ok"], miss["san"], miss["best"]["san"], miss["loss"]) == (
        False,
        "Nf3",
        "e5",
        None,
    )
    assert miss["ideas"] == [
        "The better move attacks their knight on f6, so they have to spend a move on it."
    ]
    hit = client.post("/api/coach/check", json={"fen": fen, "uci": "e4e5", "best": "e4e5"}).json()
    assert hit["ok"] and hit["faults"] == []
    bad = client.post("/api/coach/check", json={"fen": fen, "uci": "e4e6", "best": "e4e5"})
    assert bad.status_code == 400
