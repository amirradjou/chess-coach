from pathlib import Path

import pytest
from rich.console import Console
from typer.testing import CliRunner

from chess_coach import cli
from chess_coach.cli import app
from chess_coach.config import find_stockfish
from chess_coach.db import Store

runner = CliRunner()
PGN = Path(__file__).parent / "data" / "sample_games.pgn"


def _import(db: Path) -> None:
    result = runner.invoke(app, ["--db", str(db), "import", "pgn", str(PGN), "--user", "coachme"])
    assert result.exit_code == 0, result.output


def test_threats_needs_no_engine_when_nothing_is_left(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "console", Console(width=200))
    monkeypatch.setattr("chess_coach.engine.stockfish.find_stockfish", lambda explicit=None: None)
    db = tmp_path / "coach.db"
    _import(db)  # nothing analysed, so nothing to check
    result = runner.invoke(app, ["--db", str(db), "threats"])
    assert result.exit_code == 0, result.output
    assert "No mistakes checked yet" in result.output


@pytest.mark.engine
@pytest.mark.skipif(find_stockfish() is None, reason="no Stockfish binary found")
def test_threats_checks_both_sides_and_resumes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "console", Console(width=200))
    db = tmp_path / "coach.db"
    _import(db)
    assert runner.invoke(app, ["--db", str(db), "analyze", "--depth", "8"]).exit_code == 0

    result = runner.invoke(app, ["--db", str(db), "threats", "--depth", "8", "--limit", "3"])
    assert result.exit_code == 0, result.output
    assert "at depth 8 for 3 moves" in result.output
    assert "mistakes and blunders not checked yet" in result.output
    result = runner.invoke(app, ["--db", str(db), "threats", "--depth", "8"])
    assert result.exit_code == 0, result.output
    assert "How your mistakes happen" in result.output
    assert "Unsafe move, 16.Nc4: Nothing was threatened, but your Nc4 allows Bxc4" in result.output
    assert "not checked yet" not in result.output
    with Store(db) as store:
        rows = store.threats()
        assert {t.label for t in rows.values()} >= {"ignored_threat", "unsafe_move"}
        assert any(not m.is_user for m in store.iter_moves() if (m.game_id, m.ply) in rows)
    # nothing left: no engine needed, the summary again
    result = runner.invoke(app, ["--db", str(db), "threats", "--depth", "8"])
    assert "Using" not in result.output and "How your mistakes happen" in result.output
    digest = runner.invoke(app, ["--db", str(db), "digest"])
    assert "## Their threats and your blunder check" in digest.output
