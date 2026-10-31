"""The same drill is stored once: fixes shared by two openings, repeated slips, `puzzles dedupe`."""

from pathlib import Path

import chess

from chess_coach.db import Store
from chess_coach.engine.stockfish import LineEval
from chess_coach.puzzles.make import make_opening_fixes

from .test_coach_web import italian_games, kf1

O_O, D3 = chess.Move.from_uci("e1g1"), chess.Move.from_uci("d2d3")


class CountingEngine:
    def __init__(self, lines):
        self.lines = lines
        self.calls = 0

    def evaluate(self, board, *, depth, multipv=1):
        self.calls += 1
        return self.lines


def two_openings_one_fix(db: Path) -> None:
    """Ten Italian-position games in two opening families, each with the habitual 4.Kf1 twice."""
    games = italian_games(10)
    for g in games[5:]:
        g.opening = "Two Knights Defense"
    with Store(db) as store:
        for g in games:
            store.upsert_game(g)
            store.replace_moves(g.id, [kf1(g.id)] if int(g.source_id[1:]) % 5 < 2 else [], 12)


def test_a_fix_shared_by_two_openings_is_checked_and_stored_once(tmp_path: Path):
    db = tmp_path / "c.db"
    two_openings_one_fix(db)
    engine = CountingEngine([LineEval(40, None, O_O), LineEval(-10, None, D3)])
    with Store(db) as store:
        result = make_opening_fixes(store, engine)
        assert (result.checked, result.made, engine.calls) == (1, 1, 1)
        assert len(store.puzzles()) == 1
