from typer.testing import CliRunner

from chess_coach import __version__
from chess_coach.cli import app


def test_version_command_prints_version() -> None:
    result = CliRunner().invoke(app, ["version"])
    assert result.exit_code == 0
    assert __version__ in result.output
