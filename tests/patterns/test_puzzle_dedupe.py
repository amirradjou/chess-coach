"""The same drill is stored once: fixes shared by two openings, repeated slips, `puzzles dedupe`."""

from datetime import UTC, datetime
from pathlib import Path

import chess
import pytest
from rich.console import Console
from typer.testing import CliRunner

from chess_coach import cli
from chess_coach.db import Store
from chess_coach.engine.stockfish import LineEval
from chess_coach.models import Puzzle
from chess_coach.puzzles.make import Merge, Stored, dedupe, make_opening_fixes

from .test_coach_web import AFTER_ITALIAN, italian_games, kf1

O_O, D3 = chess.Move.from_uci("e1g1"), chess.Move.from_uci("d2d3")


def _fen(sans: str) -> str:
    board = chess.Board()
    for san in sans.split():
        board.push_san(san)
    return board.fen()


# AFTER_ITALIAN by another move order: the same position, its halfmove clock 2 instead of 4
ITALIAN_BY_NF3 = _fen("Nf3 Nc6 e4 e5 Bc4 Bc5")


class CountingEngine:
    def __init__(self, lines):
        self.lines = lines
        self.calls = 0

    def evaluate(self, board, *, depth, multipv=1):
        self.calls += 1
        return self.lines


def two_openings_one_fix(db: Path, transposed: bool = False) -> None:
    """Ten Italian-position games in two opening families, each with the habitual 4.Kf1 twice;
    `transposed`: the second family gets there by another move order."""
    games = italian_games(10)
    for g in games[5:]:
        g.opening = "Two Knights Defense"
    with Store(db) as store:
        for i, g in enumerate(games):
            store.upsert_game(g)
            facts = [kf1(g.id)] if i % 5 < 2 else []
            for m in facts:
                m.fen_before = ITALIAN_BY_NF3 if transposed and i >= 5 else AFTER_ITALIAN
            store.replace_moves(g.id, facts, 12)


@pytest.mark.parametrize("transposed", [False, True])
def test_a_fix_shared_by_two_openings_is_checked_and_stored_once(tmp_path: Path, transposed):
    db = tmp_path / "c.db"
    two_openings_one_fix(db, transposed)
    engine = CountingEngine([LineEval(40, None, O_O), LineEval(-10, None, D3)])
    with Store(db) as store:
        result = make_opening_fixes(store, engine)
        assert (result.checked, result.made, engine.calls) == (1, 1, 1)
        (p,) = store.puzzles()
        # one drill, practised from either opening
        assert p.tags == ["opening-fix", "Italian Game", "Two Knights Defense"]
        assert make_opening_fixes(store, engine).checked == 0


def test_a_fix_checked_before_fixes_were_keyed_by_position_is_not_checked_again(tmp_path: Path):
    db = tmp_path / "c.db"
    two_openings_one_fix(db)
    engine = CountingEngine([LineEval(40, None, O_O), LineEval(-10, None, D3)])
    with Store(db) as store:
        store.record_check(f"fix:{AFTER_ITALIAN}:e1f1", False)  # the old key: the whole FEN
        assert (make_opening_fixes(store, engine).checked, engine.calls) == (0, 0)


def test_a_fix_that_is_already_a_puzzle_only_adds_its_opening(tmp_path: Path):
    db = tmp_path / "c.db"
    two_openings_one_fix(db)
    engine = CountingEngine([LineEval(40, None, O_O), LineEval(-10, None, D3)])
    with Store(db) as store:
        own = Puzzle(
            id="lichess:g9:7",
            game_id="lichess:g9",
            ply=7,
            fen=AFTER_ITALIAN,
            solution="e1g1",
            solution_san="O-O",
            played_san="Kf1",
            line="",
            gap_cp=200,
            depth=18,
            tags=["hanging_piece", "opening"],
        )
        store.add_puzzle(own)
        assert make_opening_fixes(store, engine).made == 0
        (p,) = store.puzzles()
        # "opening-fix" would change its prompt and the moves that count: only the opening joins
        assert (p.id, p.tags) == (
            own.id,
            ["hanging_piece", "opening", "Italian Game", "Two Knights Defense"],
        )


def copy(i: int, fen: str = AFTER_ITALIAN, solution: str = "e1g1", tags=("opening",)) -> Puzzle:
    return Puzzle(
        id=f"lichess:g{i}:7",
        game_id=f"lichess:g{i}",
        ply=7,
        fen=fen,
        solution=solution,
        solution_san="O-O",
        played_san="Kf1",
        line="",
        gap_cp=200,
        depth=18,
        tags=list(tags),
    )


def italian_games_stored(db: Path, n: int) -> None:
    with Store(db) as store:
        for g in italian_games(n):
            store.upsert_game(g)


def test_a_drill_reached_by_another_move_order_is_not_stored_again(tmp_path: Path):
    db = tmp_path / "c.db"
    italian_games_stored(db, 2)
    with Store(db) as store:
        stored = Stored(store)
        assert stored.add(copy(0))
        assert not stored.add(copy(1, fen=ITALIAN_BY_NF3, tags=("missed_tactic",)))
        (p,) = store.puzzles()
        assert (p.id, p.tags) == ("lichess:g0:7", ["opening", "missed_tactic"])
        assert stored.at(ITALIAN_BY_NF3, "e1f1").id == "lichess:g0:7"


def test_dedupe_merges_copies_reached_by_another_move_order(tmp_path: Path):
    db = tmp_path / "c.db"
    italian_games_stored(db, 2)
    with Store(db) as store:
        store.add_puzzle(copy(0, tags=("opening-fix", "Italian Game")))
        store.add_puzzle(copy(1, fen=ITALIAN_BY_NF3, tags=("opening-fix", "Two Knights Defense")))
        (m,) = dedupe(store)
        assert ([k.id for k in m.kept], m.removed) == (["lichess:g0:7"], ["lichess:g1:7"])
        assert [p.tags for p in store.puzzles()] == [
            ["opening-fix", "Italian Game", "Two Knights Defense"]
        ]


def _tried(store: Store, puzzle_id: str) -> None:
    store.save_attempt(store.review(puzzle_id), "e1f1", False, datetime.now(UTC))


def test_dedupe_keeps_the_tried_copy_or_the_oldest_with_every_tag(tmp_path: Path):
    db = tmp_path / "c.db"
    italian_games_stored(db, 9)
    other = "r1bqk1nr/pppp1ppp/2n5/2b1p3/2B1P3/2P2N2/PP1P1PPP/RNBQK2R b KQkq - 0 4"
    with Store(db) as store:
        # three untried copies; two copies, the newer tried; three copies, two tried; one alone
        for p in [
            copy(0, tags=("opening",)),
            copy(1, tags=("opening-fix", "Italian Game")),
            copy(2, tags=("hanging_piece", "opening")),
            copy(3, solution="c2c3"),
            copy(4, solution="c2c3", tags=("missed_tactic",)),
            copy(5, solution="d2d4"),
            copy(6, solution="d2d4"),
            copy(7, solution="d2d4", tags=("pawn_grab",)),
            copy(8, fen=other, solution="g8f6"),
        ]:
            store.add_puzzle(p)
        for pid in ("lichess:g4:7", "lichess:g5:7", "lichess:g6:7"):
            _tried(store, pid)
        merges = dedupe(store)
        found = [
            ([k.id[8:] for k in m.kept], [r[8:] for r in m.removed], m.added, m.tried)
            for m in merges
        ]  # ids without "lichess:"
        assert found == [
            (["g0:7"], ["g1:7", "g2:7"], ["Italian Game", "hanging_piece"], 0),
            (["g4:7"], ["g3:7"], ["opening"], 1),
            (["g5:7", "g6:7"], ["g7:7"], ["pawn_grab"], 2),
        ]
        left = {p.id: p.tags for p in store.puzzles()}
        assert left == {
            "lichess:g0:7": ["opening", "Italian Game", "hanging_piece"],
            "lichess:g4:7": ["missed_tactic", "opening"],
            "lichess:g5:7": ["opening", "pawn_grab"],
            "lichess:g6:7": ["opening"],
            "lichess:g8:7": ["opening"],
        }
        assert len(store.attempts()) == 3  # every attempt is still there
        # run again: the two tried copies are both still there, and it says so
        assert dedupe(store) == [Merge(merges[2].kept, [], [], 2)]


def test_puzzles_dedupe_says_what_it_did(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(cli, "console", Console(width=200))
    db = tmp_path / "c.db"
    italian_games_stored(db, 2)
    with Store(db) as store:
        store.add_puzzle(copy(0, tags=("opening-fix", "Italian Game")))
        store.add_puzzle(copy(1, tags=("opening-fix", "Two Knights Defense")))
    result = CliRunner().invoke(cli.app, ["--db", str(db), "puzzles", "dedupe"])
    assert result.exit_code == 0, result.output
    assert (
        "4.O-O (you played Kf1): kept lichess:g0:7, removed lichess:g1:7; it now also has "
        "Two Knights Defense" in result.output
    )
    assert "1 positions were stored more than once: removed 1 copies, 1 puzzles left." in (
        result.output
    )
    again = CliRunner().invoke(cli.app, ["--db", str(db), "puzzles", "dedupe"])
    assert "No puzzle is stored twice (1 puzzles)." in again.output
