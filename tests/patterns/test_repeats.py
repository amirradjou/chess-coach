"""The same mistake in the same position: counted like everything else, with what it allows."""

from contextlib import nullcontext
from pathlib import Path

from fastapi.testclient import TestClient
from rich.console import Console
from typer.testing import CliRunner

from chess_coach import cli
from chess_coach.coach.digest import build, render
from chess_coach.coach.repeats import again, alert, ordinal, repeats
from chess_coach.db import Store
from chess_coach.models import Puzzle, Speed
from chess_coach.puzzles.make import MakeResult
from chess_coach.web.app import create_app

from .test_opening_tree import DANISH, facts, no_engine
from .test_patterns import game

# 5.Nf3?! (5.e5 hits the knight) after 1.e4 e5 2.d4 exd4 3.c3 dxc3 4.Nxc3 Nf6; 2.d4, the
# Center Game, is marked an inaccuracy too, but it's a named book move.
LOSS, BEST = {3: 12.0, 9: 12.0}, {3: "Nf3", 9: "e5", 10: "Nxe4"}


def store_games(db: Path) -> list:
    """Six games: three blitz with 5.Nf3, one with it short on time, one bullet (move 5 counts
    there), one without it. Their answer is 5...Nxe4 except in game 2 (5...Nc6)."""
    games = []
    with Store(db) as store:
        for i in range(6):
            g = game(i, speed=Speed.BULLET if i == 4 else Speed.BLITZ)
            g.time_control = "60+0" if i == 4 else "300+0"
            g.opening = "Danish Gambit Accepted"
            line = [*DANISH, "Nf6", "Nf3", "Nc6" if i == 2 else "Nxe4"]
            moves = facts(g.id, line if i < 5 else [*DANISH, "Nf6", "e5"], LOSS, BEST)
            if i == 3:
                moves[8].clock_after = 5.0  # under a tenth of 5 minutes
            store.upsert_game(g)
            store.replace_moves(g.id, moves, depth=12)
            games.append(g)
    return games


def found_in(db: Path, puzzles=()):
    with Store(db) as store:
        games = list(store.iter_games())
        moves = list(store.iter_moves(user_only=True))
        return repeats(games, moves, store.replies_to_errors(), puzzles)


def test_a_repeat_is_a_counted_error_from_one_position_in_three_games(tmp_path: Path):
    db = tmp_path / "c.db"
    games = store_games(db)
    (r,) = found_in(db)  # not 2.d4: book
    assert (r.move, r.better, r.games, r.family) == ("5.Nf3", "5.e5", 4, "Danish Gambit Accepted")
    assert r.game_ids == ["lichess:g0", "lichess:g1", "lichess:g2", "lichess:g4"]  # g3 rushed
    assert r.last == games[4].played_at
    assert r.urls[0] == "https://lichess.org/g4#9"  # newest first, at the move
    assert (r.punish, r.punished, r.replies) == ("5...Nxe4", 3, 4)
    assert r.reasons == [
        "The better move attacks their knight on f6, so they have to spend a move on it."
    ]
    assert r.puzzle is None


def test_a_repeat_knows_its_puzzle(tmp_path: Path):
    db = tmp_path / "c.db"
    store_games(db)
    fen = found_in(db)[0].fen

    def puzzle(solution: str) -> Puzzle:
        return Puzzle("lichess:g1:9", "lichess:g1", 9, fen, solution, "?", "Nf3", "", 200, 18)

    assert found_in(db, [puzzle("e4e5")])[0].puzzle == "lichess:g1:9"
    assert found_in(db, [puzzle("g1f3")])[0].puzzle is None  # that one says Nf3 is right


def test_fewer_than_three_games_is_not_a_repeat(tmp_path: Path):
    db = tmp_path / "c.db"
    store_games(db)
    with Store(db) as store:
        store.conn.execute("DELETE FROM moves WHERE game_id IN ('lichess:g0', 'lichess:g1')")
        store.conn.commit()
    assert found_in(db) == []


def test_ordinals():
    numbers = (1, 2, 3, 4, 11, 12, 13, 21, 22, 112)
    assert " ".join(ordinal(n) for n in numbers) == "1st 2nd 3rd 4th 11th 12th 13th 21st 22nd 112th"


def test_alert_wording(tmp_path: Path):
    db = tmp_path / "c.db"
    store_games(db)
    (r,) = found_in(db)
    assert again([r], {"lichess:g4", "lichess:g5"}) == [r]
    assert again([r], {"lichess:g5"}) == []
    assert alert(r) == "You played 5.Nf3 again (4th time); the better move is 5.e5."
    r.puzzle = "lichess:g1:9"
    assert alert(r) == "You played 5.Nf3 again (4th time); it has a puzzle."


def test_the_repeats_command(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(cli, "console", Console(width=200))
    db = tmp_path / "c.db"
    store_games(db)
    result = CliRunner().invoke(cli.app, ["--db", str(db), "repeats"])
    assert result.exit_code == 0, result.output
    assert "1 mistakes you repeat from the same position, in 3 or more games" in result.output
    assert "5.Nf3 (Danish Gambit Accepted) in 4 games, last on 2026-10-05." in result.output
    assert "Their best answer: 5...Nxe4, found 3 of 4 times." in result.output
    assert "No puzzle for it yet." in result.output and "https://lichess.org/g4#9" in result.output
    none = CliRunner().invoke(cli.app, ["--db", str(db), "repeats", "--min-games", "5"])
    assert "No mistake of yours repeats from the same position in 5+ games." in none.output


def test_the_digest_lists_repeats(tmp_path: Path):
    db = tmp_path / "c.db"
    games = store_games(db)
    with Store(db) as store:
        moves = list(store.iter_moves(user_only=True))
        replies = store.replies_to_errors()
    text = render(build(games, moves, replies=replies))
    assert "## The same mistake in the same position" in text
    assert (
        "1. **5.Nf3** (Danish Gambit Accepted) in 4 games, last on 2026-10-05; better 5.e5. "
        "Their best answer is 5...Nxe4, and they found it 3 of 4 times." in text
    )
    assert "[1](https://lichess.org/g4#9)" in text and "No puzzle for it yet." in text
    # --since: only the ones played again since then, still counted over every game
    assert "played again since 2026-10-05" in render(build(games, moves, since=games[4].played_at))
    assert "## The same mistake" not in render(build(games, moves, since=games[5].played_at))


def test_the_web_shows_repeats_without_stockfish(tmp_path: Path):
    db = tmp_path / "c.db"
    store_games(db)
    (r,) = TestClient(create_app(db, no_engine)).get("/api/repeats").json()
    assert (r["move"], r["better"], r["games"], r["punish"], r["puzzle"]) == (
        "5.Nf3",
        "5.e5",
        4,
        "5...Nxe4",
        False,
    )
    assert r["svg"].startswith("<svg") and r["last"] == "2026-10-05" and r["reasons"]


def test_sync_says_when_a_new_game_repeats_one(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(cli, "console", Console(width=200))
    db = tmp_path / "c.db"
    store_games(db)
    new = game(9)
    new.opening, new.analyzed_depth = "Danish Gambit Accepted", None
    line = [*DANISH, "Nf6", "Nf3", "Nxe4"]
    monkeypatch.setattr("chess_coach.sources.lichess.fetch_games", lambda *a, **kw: iter([new]))
    monkeypatch.setattr(cli, "_engine", lambda settings, threads: nullcontext(object()))
    monkeypatch.setattr(
        cli,
        "_analyze_games",
        lambda store, engine, pending, depth: [
            store.replace_moves(g.id, facts(g.id, line, LOSS, BEST), depth) for g in pending
        ],
    )
    monkeypatch.setattr("chess_coach.puzzles.make.make", lambda store, engine, limit: MakeResult())
    monkeypatch.setattr(
        "chess_coach.puzzles.make.make_opening_fixes", lambda store, engine: MakeResult()
    )
    result = CliRunner().invoke(cli.app, ["--db", str(db), "sync"])
    assert result.exit_code == 0, result.output
    assert "You played 5.Nf3 again (5th time); the better move is 5.e5." in result.output
