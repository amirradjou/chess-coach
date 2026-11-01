from contextlib import nullcontext
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from rich.console import Console
from typer.testing import CliRunner

from chess_coach import cli
from chess_coach.cli import app
from chess_coach.config import find_stockfish
from chess_coach.db import Store
from chess_coach.models import Color, Speed
from chess_coach.puzzles.make import MakeResult
from chess_coach.sources.pgn import games_from_pgn_text

runner = CliRunner()
PGN = Path(__file__).parent / "data" / "sample_games.pgn"


def _import(db: Path) -> None:
    result = runner.invoke(app, ["--db", str(db), "import", "pgn", str(PGN), "--user", "coachme"])
    assert result.exit_code == 0, result.output


def test_accounts_are_the_stored_sources_with_their_newest_game(tmp_path: Path) -> None:
    db = tmp_path / "coach.db"
    _import(db)
    with Store(db) as store:
        assert store.accounts() == [
            ("chesscom", "coachme", datetime(2026, 9, 2, 20, 15, 33, tzinfo=UTC)),
            ("lichess", "coachme", datetime(2026, 9, 1, 18, 4, 11, tzinfo=UTC)),
            ("pgn", "coachme", datetime(2026, 9, 3, tzinfo=UTC)),
        ]


def test_sync_needs_an_import_first(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "console", Console(width=200))
    result = runner.invoke(app, ["--db", str(tmp_path / "coach.db"), "sync"])
    assert result.exit_code == 1
    assert "import your games once first" in result.output


def test_sync_fetches_since_the_newest_game_and_analyses_newest_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sample_pgn_text: str
) -> None:
    monkeypatch.setattr(cli, "console", Console(width=200))
    db = tmp_path / "coach.db"
    _import(db)
    known = next(
        g for g in games_from_pgn_text(sample_pgn_text, "coachme") if g.source == "lichess"
    )
    loss = "0-1" if known.user_color is Color.WHITE else "1-0"
    blitz = replace(
        known,
        id="lichess:Blitz001",
        source_id="Blitz001",
        result=loss,
        played_at=datetime(2026, 9, 4, tzinfo=UTC),
    )
    bullet = replace(
        known,
        id="lichess:Bullet01",
        source_id="Bullet01",
        speed=Speed.BULLET,
        time_control="60+0",
        played_at=datetime(2026, 9, 5, tzinfo=UTC),
    )
    calls = []

    def fake_lichess(user, *, max_games, since, token=None, **kw):
        calls.append(("lichess", user, max_games, since))
        return iter([bullet, blitz, known])  # newest first, like the site

    def fake_chesscom(user, *, max_games, since, **kw):
        calls.append(("chesscom", user, max_games, since))
        yield replace(known, id="chesscom:999", source="chesscom", source_id="999")
        raise httpx.ConnectError("connection reset")

    analysed = []
    monkeypatch.setattr("chess_coach.sources.lichess.fetch_games", fake_lichess)
    monkeypatch.setattr("chess_coach.sources.chesscom.fetch_games", fake_chesscom)
    monkeypatch.setattr(cli, "_engine", lambda settings, threads: nullcontext(object()))
    monkeypatch.setattr(
        cli, "_analyze_games", lambda store, engine, pending, depth: analysed.extend(pending)
    )
    monkeypatch.setattr(
        "chess_coach.puzzles.make.make", lambda store, engine, limit: MakeResult(4, 1)
    )
    monkeypatch.setattr(
        "chess_coach.puzzles.make.make_opening_fixes", lambda store, engine: MakeResult(2, 0)
    )
    monkeypatch.setattr("chess_coach.coach.threats.check", lambda store, engine: 7)

    result = runner.invoke(app, ["--db", str(db), "sync"])
    assert result.exit_code == 0, result.output
    assert calls == [
        ("chesscom", "coachme", None, datetime(2026, 9, 2, 20, 15, 33, tzinfo=UTC)),
        ("lichess", "coachme", None, datetime(2026, 9, 1, 18, 4, 11, tzinfo=UTC)),
    ]
    # A failed fetch stores nothing, so the next sync still starts from the same game
    assert "Chess.com failed for coachme, nothing stored" in result.output
    with Store(db) as store:
        assert store.get_game("chesscom:999") is None
        assert store.get_game("lichess:Bullet01") is not None
    assert "coachme on Lichess: 2 new games" in result.output
    # Bullet results don't count, so the bullet win is not reported
    assert "2 new games: 1 blitz (lost 1); 1 bullet" in result.output
    assert "won" not in result.output
    assert [g.id for g in analysed][:2] == ["lichess:Bullet01", "lichess:Blitz001"]
    assert len(analysed) == 5
    assert "Analysed 5 games. Checked 6 moves, made 1 puzzles (0 in all)." in result.output
    assert "Looked for their threats before 7 new mistakes and blunders" in result.output
    assert "Due today: 0 puzzles" in result.output


@pytest.mark.engine
@pytest.mark.skipif(find_stockfish() is None, reason="no Stockfish binary found")
def test_sync_analyses_and_makes_puzzles(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "console", Console(width=200))
    monkeypatch.setenv("CHESS_COACH_DEPTH", "6")
    monkeypatch.setattr("chess_coach.sources.lichess.fetch_games", lambda *a, **kw: iter([]))
    monkeypatch.setattr("chess_coach.sources.chesscom.fetch_games", lambda *a, **kw: iter([]))
    db = tmp_path / "coach.db"
    _import(db)

    result = runner.invoke(app, ["--db", str(db), "sync", "--puzzles", "3"])
    assert result.exit_code == 0, result.output
    assert "coachme on Lichess: 0 new games" in result.output
    assert "Analysed 3 games." in result.output
    with Store(db) as store:
        assert not list(store.iter_games(unanalyzed_below_depth=6))
        assert len(store.checked_moves()) >= 3

    # Nothing new: a second sync analyses nothing
    result = runner.invoke(app, ["--db", str(db), "sync", "--puzzles", "3"])
    assert "Analysed 0 games." in result.output
