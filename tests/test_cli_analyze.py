from pathlib import Path

import pytest
from rich.console import Console
from typer.testing import CliRunner

from chess_coach import cli
from chess_coach.cli import app
from chess_coach.config import find_stockfish
from chess_coach.db import Store

runner = CliRunner()
pytestmark = pytest.mark.engine


@pytest.mark.skipif(find_stockfish() is None, reason="no Stockfish binary found")
def test_analyze_then_stats(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "console", Console(width=200))
    db = tmp_path / "coach.db"
    pgn = Path(__file__).parent / "data" / "sample_games.pgn"
    assert (
        runner.invoke(
            app, ["--db", str(db), "import", "pgn", str(pgn), "--user", "coachme"]
        ).exit_code
        == 0
    )

    result = runner.invoke(app, ["--db", str(db), "analyze", "--depth", "6", "--limit", "2"])
    assert result.exit_code == 0, result.output
    assert "Analyzed 2 games" in result.output
    with Store(db) as store:
        assert store.count_moves() == 90
        assert len(list(store.iter_games(unanalyzed_below_depth=6))) == 1

    # re-running only picks up the remaining game; a deeper run would redo all of them
    result = runner.invoke(app, ["--db", str(db), "analyze", "--depth", "6"])
    assert "Analyzed 1 games" in result.output
    result = runner.invoke(app, ["--db", str(db), "analyze", "--depth", "6"])
    assert "Nothing to do" in result.output

    result = runner.invoke(app, ["--db", str(db), "stats"])
    assert result.exit_code == 0, result.output
    assert "3 analyzed games" in result.output
    assert "chesscom:123456789" in result.output
    assert "best fxe6" in result.output or "best h6" in result.output


def test_analyze_without_engine_fails_cleanly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("chess_coach.config.find_stockfish", lambda explicit=None: None)
    monkeypatch.setattr("chess_coach.engine.stockfish.find_stockfish", lambda explicit=None: None)
    db = tmp_path / "coach.db"
    pgn = Path(__file__).parent / "data" / "sample_games.pgn"
    runner.invoke(app, ["--db", str(db), "import", "pgn", str(pgn), "--user", "coachme"])
    result = runner.invoke(app, ["--db", str(db), "analyze"])
    assert result.exit_code == 1
    assert "No Stockfish binary found" in result.output
