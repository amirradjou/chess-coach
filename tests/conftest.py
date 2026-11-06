from pathlib import Path

import pytest

DATA_DIR = Path(__file__).parent / "data"


@pytest.fixture(autouse=True)
def no_personal_config(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch):
    """No test may find the real database through the config file or the environment."""
    monkeypatch.setenv("HOME", str(tmp_path_factory.mktemp("home")))
    monkeypatch.delenv("CHESS_COACH_DB", raising=False)


@pytest.fixture(scope="session")
def sample_pgn_text() -> str:
    return (DATA_DIR / "sample_games.pgn").read_text()
