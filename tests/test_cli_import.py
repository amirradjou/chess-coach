from datetime import UTC, datetime
from pathlib import Path

import pytest
from rich.console import Console
from typer.testing import CliRunner

from chess_coach import cli
from chess_coach.cli import app
from chess_coach.db import Store
from chess_coach.util import parse_since

runner = CliRunner()


def test_parse_since_relative_and_absolute() -> None:
    now = datetime(2026, 9, 17, tzinfo=UTC)
    assert parse_since("7d", now=now) == datetime(2026, 9, 10, tzinfo=UTC)
    assert parse_since("2w", now=now) == datetime(2026, 9, 3, tzinfo=UTC)
    assert parse_since("2026-09-01", now=now) == datetime(2026, 9, 1, tzinfo=UTC)
    assert parse_since(None) is None


def test_import_pgn_then_list_games(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "console", Console(width=200))
    db = tmp_path / "coach.db"
    pgn = Path(__file__).parent / "data" / "sample_games.pgn"
    result = runner.invoke(app, ["--db", str(db), "import", "pgn", str(pgn), "--user", "coachme"])
    assert result.exit_code == 0, result.output
    assert "3 new games stored, 0 already present" in result.output

    # importing again is a no-op
    result = runner.invoke(app, ["--db", str(db), "import", "pgn", str(pgn), "--user", "coachme"])
    assert "0 new games stored, 3 already present" in result.output

    with Store(db) as store:
        assert store.count_games() == 3

    result = runner.invoke(app, ["--db", str(db), "games"])
    assert result.exit_code == 0, result.output
    assert "lichess:AbCdEfGh" in result.output
    assert "chesscom:123456789" in result.output
    assert "Local Player" in result.output
