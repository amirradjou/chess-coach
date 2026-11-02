"""How an opening comes out by the engine after both sides' 10th moves: no results, so bullet
counts."""

from pathlib import Path

import chess
from fastapi.testclient import TestClient
from rich.console import Console
from typer.testing import CliRunner

from chess_coach import cli
from chess_coach.coach import tree as optree
from chess_coach.coach.repertoire import after_ten, book_ply, evals_after_ten
from chess_coach.db import Store
from chess_coach.models import Color, Judgement, MoveFact, Phase, Speed
from chess_coach.web.app import create_app

from .test_opening_tree import no_engine
from .test_patterns import game

# Named up to 6.O-O O-O, out of the table for 7.Re1 a6 8.Bb3 Ba7, named again after 9.h3.
MAIN = "e4 e5 Nf3 Nc6 Bc4 Bc5 c3 Nf6 d3 d6 O-O O-O Re1 a6 Bb3 Ba7 h3 h6 Nbd2 Re8".split()
# Named up to 3...Bc5; 4.Kf1 and everything after it isn't.
KF1 = "e4 e5 Nf3 Nc6 Bc4 Bc5 Kf1 Nf6 d3 d6 Nc3 O-O h3 a6 Bb3 Ba7 g4 h6 Kg2 Re8".split()
# Named only after 1.b4 (the Polish Opening).
POLISH = "b4 h6 a3 a6 h3 b6 Nf3 Nf6 e3 e6 Be2 Be7 O-O O-O d3 d6 Nbd2 Nbd7 c4 c5".split()
NO_A_PAWN = "rnbqkbnr/pppppppp/8/8/8/8/1PPPPPPP/RNBQKBNR w KQkq - 0 1"  # a set-up position


def line(
    game_id,
    sans,
    *,
    user=Color.WHITE,
    start=chess.STARTING_FEN,
    evals=None,
    judged=None,
    clock=None,
):
    """Facts for a game, stored as `analyze` stores them. `evals` maps a ply to your eval (your
    side) in the position after it, ply 0 being the start; 10 x ply where it isn't given. Each
    move's evals are from the mover's side, so a move's eval_before is the previous ply's eval.
    `judged` and `clock` set a ply's judgement and clock."""

    def yours_after(ply):
        return (evals or {}).get(ply, 10 * ply)

    board, out = chess.Board(start), []
    for ply, san in enumerate(sans, 1):
        move = board.parse_san(san)
        mover = Color.WHITE if board.turn == chess.WHITE else Color.BLACK
        sign = 1 if mover is user else -1
        out.append(
            MoveFact(
                game_id=game_id,
                ply=ply,
                move_number=board.fullmove_number,
                color=mover,
                is_user=mover is user,
                san=san,
                uci=move.uci(),
                fen_before=board.fen(),
                eval_before=sign * yours_after(ply - 1),
                eval_after=sign * yours_after(ply),
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


def black_game(i):
    g = game(i)
    g.user_color = Color.BLACK
    return g


def test_since_book_starts_from_an_even_ply_at_or_before_the_last_named_position():
    assert book_ply(yours(line("g", MAIN))) == 16  # named after your 9.h3 (17): from 8...Ba7
    assert book_ply(yours(line("g", KF1))) == 6  # named after their 3...Bc5, before 4.Kf1
    assert book_ply(yours(line("g", MAIN, user=Color.BLACK))) == 16  # their 9.h3
    assert book_ply(yours(line("g", KF1, user=Color.BLACK))) == 6  # your 3...Bc5
    assert book_ply(yours(line("g", POLISH))) == 0  # named only after 1.b4: the start
    assert book_ply(yours(line("g", POLISH, user=Color.BLACK))) == 0
    assert book_ply(yours(line("g", MAIN, start=NO_A_PAWN))) == 8  # never named: after 4...Nf6


def test_since_book_from_the_start_position_reads_the_eval_before_the_first_move():
    g = game(1)
    moves = line(g.id, POLISH, evals={0: 20, 20: 50})
    (a,) = after_ten([g], moves, min_games=1)["white"]
    assert (a.median_cp, a.since_book) == (50, 30)


def test_a_slip_on_your_10th_move_that_their_reply_gives_back_reads_level():
    """As White your 10th move is ply 19: read there, a slip their reply gives back would count
    as -1. After both sides' 10th moves it is level. As Black your 10th move is ply 20, so the
    same slip still counts: the reply that could give it back is White's 11th move."""
    flat = dict.fromkeys(range(21), 0)
    white, black = game(1), black_game(2)
    moves = line(white.id, MAIN, evals={**flat, 19: -100}) + line(
        black.id, MAIN, user=Color.BLACK, evals={**flat, 20: -100}
    )
    assert evals_after_ten([white, black], moves) == {white.id: 0, black.id: -100}
    (a,) = after_ten([white], moves, min_games=1)["white"]
    assert (a.median_cp, a.bad, a.since_book) == (0, 0, 0)


def test_a_game_that_ends_on_your_10th_move_is_left_out():
    """19 plies: your 10th move was the last (they resigned, say), so there is no position after
    both sides' 10th moves. Its moves 1-10 still count towards the mistake rate."""
    full, short = game(1), game(2)
    moves = line(full.id, MAIN, evals={20: 50}) + line(short.id, MAIN[:19], evals={19: -300})
    assert evals_after_ten([full, short], moves) == {full.id: 50}
    (a,) = after_ten([full, short], moves, min_games=1)["white"]
    assert (a.games, a.median_cp, a.moves) == (1, 50, 20)


def test_a_mate_after_both_sides_10th_moves_is_clamped():
    """Their 10th move walks into a forced mate (mate in 3 for you, +9997 from your side): it
    counts as +10, like any won game. As Black, your own 10th move allowing mate counts as -10."""
    white, black = game(1), black_game(2)
    moves = line(white.id, MAIN, evals={19: 50, 20: 9997})
    assert moves[19].eval_after == -9997  # their move, stored from their side
    moves += line(black.id, MAIN, user=Color.BLACK, evals={20: -9999})
    assert evals_after_ten([white, black], moves) == {white.id: 1000, black.id: -1000}
    (a,) = after_ten([white], moves, min_games=1)["white"]
    assert (a.median_cp, a.since_book) == (1000, 1000 - 160)  # book point: after 8...Ba7


def test_since_book_is_the_same_whether_the_book_ends_after_your_move_or_theirs():
    """Two games with the same evals: each of your moves drops 1 pawn and each reply gives it
    back. The last named position follows your move in one (9.h3 for White) and theirs in the
    other (3...Bc5 for White). Read right after the book position, one lost nothing since book and
    the other a pawn, only because of whose move came last; from an even ply both lose nothing."""
    for user, slipped in ((Color.WHITE, 1), (Color.BLACK, 0)):  # plies after your moves
        zigzag = {p: -100 if p and p % 2 == slipped else 0 for p in range(21)}
        mine, theirs = (game(1), game(2)) if user is Color.WHITE else (black_game(1), black_game(2))
        theirs.opening = "Scotch Game"  # a row of its own
        your_book, their_book = (MAIN, KF1) if user is Color.WHITE else (KF1, MAIN)
        moves = line(mine.id, your_book, user=user, evals=zigzag)
        moves += line(theirs.id, their_book, user=user, evals=zigzag)
        rows = after_ten([mine, theirs], moves, min_games=1)[user.value]
        assert {a.family: a.since_book for a in rows} == {"Italian Game": 0, "Scotch Game": 0}


def bullet_italians():
    """Six bullet Italians. Five reach both sides' 10th moves at -100 (one mated, clamped to
    -10), with a mistake and a blunder in moves 1-10, one more played with 10 s left (doesn't
    count) and an inaccuracy (not a mistake); the sixth ends after your 9th move."""
    games, moves = [], []
    levels = [-10_000, -150, -100, 30, 200]
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
            moves += line(g.id, MAIN, evals={20: levels[i]}, judged=judged[i], clock=clock)
        else:
            moves += line(g.id, MAIN[:17])
    return games, moves


def test_after_ten_counts_bullet_by_the_engine_and_skips_short_games():
    games, moves = bullet_italians()
    (a,) = after_ten(games, moves)["white"]
    assert (a.family, a.games, a.median_cp, a.bad) == ("Italian Game", 5, -100, 3)
    assert (a.errors, a.moves) == (2, 58)  # 6 games' moves 1-10, less the rushed one
    assert a.since_book == -260  # median of -1160, -310, -260, -130, 40 (book: +1.6, ply 16)
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
        moves += line(g.id, KF1, evals={20: -900})
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
        "-2.6",
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
    assert "Engine eval after both sides' 10th moves (not results)" in result.output
    row = next(r for r in result.output.splitlines() if "Italian Game" in r and "│" in r)
    assert [c.strip() for c in row.split("│")[1:-1]] == [
        "Italian Game",
        "5",
        "-1.0",
        "60% (23%-88%)",
        "3.4% (1.0%-11.7%)",
        "-2.6",
    ]


def test_the_openings_command_says_when_no_opening_has_enough_games_at_move_ten(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setattr(cli, "console", Console(width=200))
    # six Italians, but only five got to both sides' 10th moves
    result = CliRunner().invoke(
        cli.app, ["--db", str(_db(tmp_path)), "openings", "--min-games", "6"]
    )
    assert result.exit_code == 0, result.output
    assert "Italian Game" in result.output  # the favourites part is there
    assert "Engine eval after both sides' 10th moves" not in result.output
    assert result.output.rstrip().endswith(
        "No opening has 6 games that got to both sides' 10th moves yet, so none is judged by the "
        "engine there."
    )
