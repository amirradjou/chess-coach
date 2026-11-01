"""How an opening comes out by the engine after your 10th move: no results, so bullet counts."""

from pathlib import Path

import chess
from fastapi.testclient import TestClient
from rich.console import Console
from typer.testing import CliRunner

from chess_coach import cli
from chess_coach.coach import tree as optree
from chess_coach.coach.repertoire import after_ten, book_eval, evals_after_ten
from chess_coach.db import Store
from chess_coach.models import Color, Judgement, MoveFact, Phase, Speed
from chess_coach.web.app import create_app

from .test_opening_tree import no_engine
from .test_patterns import game

# Named up to 6.O-O O-O, out of the table for 7.Re1 a6 8.Bb3 Ba7, named again after 9.h3.
MAIN = "e4 e5 Nf3 Nc6 Bc4 Bc5 c3 Nf6 d3 d6 O-O O-O Re1 a6 Bb3 Ba7 h3 h6 Nbd2 Re8".split()
# Named up to 3...Bc5; 4.Kf1 and everything after it isn't.
KF1 = "e4 e5 Nf3 Nc6 Bc4 Bc5 Kf1 Nf6 d3 d6 Nc3 O-O h3 a6 Bb3 Ba7 g4 h6 Kg2 Re8".split()
NO_A_PAWN = "rnbqkbnr/pppppppp/8/8/8/8/1PPPPPPP/RNBQKBNR w KQkq - 0 1"  # a set-up position


def line(game_id, sans, *, start=chess.STARTING_FEN, tenth=None, judged=None, clock=None):
    """Facts for a game, you White. Your eval is 100 + ply before your move and 10 x ply after it
    (`tenth` after your 10th); `judged` and `clock` set a ply's judgement and clock."""
    board, out = chess.Board(start), []
    for ply, san in enumerate(sans, 1):
        move = board.parse_san(san)
        mine = board.turn == chess.WHITE
        after = 10 * ply if mine else -10 * ply
        if mine and board.fullmove_number == 10 and tenth is not None:
            after = tenth
        out.append(
            MoveFact(
                game_id=game_id,
                ply=ply,
                move_number=board.fullmove_number,
                color=Color.WHITE if mine else Color.BLACK,
                is_user=mine,
                san=san,
                uci=move.uci(),
                fen_before=board.fen(),
                eval_before=100 + ply if mine else -100 - ply,
                eval_after=after,
                best_move=move.uci(),
                best_san=san,
                cp_loss=0,
                wp_before=50.0,
                wp_after=50.0,
                wp_loss=0.0,
                judgement=(judged or {}).get(ply, Judgement.BEST),
                phase=Phase.OPENING,
                depth=12,
                clock_after=(clock or {}).get(ply),
            )
        )
        board.push(move)
    return out


def yours(facts):
    return [m for m in facts if m.is_user]


def test_book_eval_is_read_at_the_last_named_position():
    assert book_eval(yours(line("g", MAIN))) == 170  # after 9.h3: back in the table
    assert book_eval(yours(line("g", KF1))) == 107  # before 4.Kf1, after 3...Bc5
    assert book_eval(yours(line("g", MAIN, start=NO_A_PAWN))) == 70  # never named: move 4


def bullet_italians():
    """Six bullet Italians. Five reach your 10th move at -100 (one mated, clamped to -10), with a
    mistake and a blunder in moves 1-10, one more played with 10 s left (doesn't count) and an
    inaccuracy (not a mistake); the sixth ends after your 9th move."""
    games, moves = [], []
    tenths = [-10_000, -150, -100, 30, 200]
    judged = [
        {3: Judgement.MISTAKE},
        {5: Judgement.BLUNDER},
        {7: Judgement.MISTAKE},
        {9: Judgement.INACCURACY},
        {},
    ]
    for i in range(6):
        g = game(i, speed=Speed.BULLET)
        g.time_control = "60+0"
        games.append(g)
        if i < 5:
            clock = {7: 10.0} if i == 2 else None  # game 2's mistake: short on time
            moves += line(g.id, MAIN, tenth=tenths[i], judged=judged[i], clock=clock)
        else:
            moves += line(g.id, MAIN[:17])
    return games, moves


def test_after_ten_counts_bullet_by_the_engine_and_skips_short_games():
    games, moves = bullet_italians()
    (a,) = after_ten(games, moves)["white"]
    assert (a.family, a.games, a.median_cp, a.bad) == ("Italian Game", 5, -100, 3)
    assert (a.errors, a.moves) == (2, 58)  # 6 games' moves 1-10, less the rushed one
    assert a.since_book == -270  # median of -1170, -320, -270, -140, 30 (book: +1.7 after 9.h3)
    low, high = a.error_interval
    assert low < a.error_rate < high and a.bad_interval[0] < 0.6 < a.bad_interval[1]
    assert after_ten(games, moves, min_games=6) == {"white": [], "black": []}


def test_an_opening_without_a_game_at_move_ten_is_left_out_even_with_no_minimum():
    games, moves = bullet_italians()
    g = game(20)
    g.opening = "Scotch Game"
    games.append(g)
    moves += line(g.id, KF1[:11])  # resigned after your 6th move
    assert [a.family for a in after_ten(games, moves, min_games=0)["white"]] == ["Italian Game"]


def test_openings_are_ranked_by_the_mistake_interval_not_the_eval():
    games, moves = bullet_italians()
    for i in range(10, 15):  # far worse by the eval, but no mistakes
        g = game(i)
        g.opening = "Scotch Game"
        games.append(g)
        moves += line(g.id, KF1, tenth=-900)
    ranked = after_ten(games, moves)["white"]
    assert [(a.family, a.median_cp) for a in ranked] == [
        ("Italian Game", -100),
        ("Scotch Game", -900),
    ]


def test_the_move_ten_map_for_the_tree():
    games, moves = bullet_italians()
    evals = evals_after_ten(games, moves)
    assert sorted(evals.values()) == [-1000, -150, -100, 30, 200]  # g5 ended before move 10
    assert evals["lichess:g0"] == -1000  # mated: clamped to -10
    assert optree.at_move_ten(["lichess:g0", "lichess:g5", "lichess:g1"], evals) == (2, -575)
    assert optree.at_move_ten(["lichess:g5"], evals) is None


def _db(tmp_path: Path) -> Path:
    db = tmp_path / "c.db"
    games, moves = bullet_italians()
    with Store(db) as store:
        for g in games:
            store.upsert_game(g)
            store.replace_moves(g.id, [m for m in moves if m.game_id == g.id], depth=12)
    return db


def test_the_openings_page_and_the_tree_show_move_ten_without_stockfish(tmp_path: Path):
    client = TestClient(create_app(_db(tmp_path), no_engine))
    o = client.get("/api/openings").json()
    (row,) = o["after_ten"]["white"]
    assert (row["family"], row["games"], row["median"], row["bad"], row["since_book"]) == (
        "Italian Game",
        5,
        "-1.0",
        60,
        "-2.7",
    )
    assert row["error_rate"] == 3.4 and row["error_low"] < 3.4 < row["error_high"]
    assert o["white"][0]["after_ten"] == row  # the favourite's card carries it too
    q = {"family": "Italian Game", "color": "white"}
    root = client.get("/api/coach/node", params={**q, "path": ""}).json()
    assert root["after_ten"] == {"games": 5, "median": "-1.0"}
    assert root["moves"][0]["after_ten"] == {"games": 5, "median": "-1.0"}  # 1.e4, every game


def test_the_openings_command_shows_the_table(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(cli, "console", Console(width=200))
    result = CliRunner().invoke(cli.app, ["--db", str(_db(tmp_path)), "openings"])
    assert result.exit_code == 0, result.output
    assert "Engine eval after your 10th move (not results)" in result.output
    row = next(r for r in result.output.splitlines() if "Italian Game" in r and "│" in r)
    assert [c.strip() for c in row.split("│")[1:-1]] == [
        "Italian Game",
        "5",
        "-1.0",
        "60% (23%-88%)",
        "3.4% (1.0%-11.7%)",
        "-2.7",
    ]


def test_the_openings_command_says_when_no_opening_has_enough_games_at_move_ten(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setattr(cli, "console", Console(width=200))
    # six Italians, but only five got to your 10th move
    result = CliRunner().invoke(
        cli.app, ["--db", str(_db(tmp_path)), "openings", "--min-games", "6"]
    )
    assert result.exit_code == 0, result.output
    assert "Italian Game" in result.output  # the favourites part is there
    assert "Engine eval after your 10th move" not in result.output
    assert result.output.rstrip().endswith(
        "No opening has 6 games that got to your 10th move yet, so none is judged by the engine "
        "there."
    )
