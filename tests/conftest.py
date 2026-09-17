from pathlib import Path

import pytest

DATA_DIR = Path(__file__).parent / "data"


@pytest.fixture(scope="session")
def sample_pgn_text() -> str:
    return (DATA_DIR / "sample_games.pgn").read_text()
