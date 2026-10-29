from collections.abc import Iterator

import pytest

from chess_coach.config import find_stockfish
from chess_coach.engine.stockfish import Engine

requires_engine = pytest.mark.skipif(find_stockfish() is None, reason="no Stockfish binary found")


@pytest.fixture(scope="session")
def engine() -> Iterator[Engine]:
    if find_stockfish() is None:
        pytest.skip("no Stockfish binary found")
    with Engine(threads=1, hash_mb=16) as eng:
        yield eng
