"""Practice: what gets served, what is remembered, and which day it counts for."""

from datetime import UTC, datetime
from pathlib import Path

import chess
from typer.testing import CliRunner

from chess_coach.coach.repertoire import Fix
from chess_coach.db import Store
from chess_coach.engine.stockfish import LineEval
from chess_coach.models import Color, Game, Judgement, MoveFact, Phase, Puzzle, Speed
from chess_coach.puzzles.make import verify_fix
from chess_coach.puzzles.srs import schedule

runner = CliRunner()
START = chess.STARTING_FEN
WHEN = datetime(2026, 10, 1, 12, tzinfo=UTC)


def game(game_id: str, speed: Speed = Speed.BLITZ, played_at: datetime = WHEN) -> Game:
    return Game(
        id=game_id,
        source="lichess",
        source_id=game_id.split(":")[-1],
        user="me",
        user_color=Color.WHITE,
        white="me",
        black="them",
        result="1-0",
        pgn="1. e4 e5 *",
        url=f"https://lichess.org/{game_id.split(':')[-1]}",
        played_at=played_at,
        speed=speed,
        time_control="60+0" if speed is Speed.BULLET else "300+0",
    )


def move(game_id: str, ply: int, fen: str = START, *, clock: float | None = None) -> MoveFact:
    return MoveFact(
        game_id=game_id,
        ply=ply,
        move_number=(ply + 1) // 2,
        color=Color.WHITE,
        is_user=True,
        san="Nf3",
        uci="g1f3",
        fen_before=fen,
        eval_before=100,
        eval_after=-100,
        best_move="e2e4",
        best_san="e4",
        cp_loss=200,
        wp_before=60.0,
        wp_after=40.0,
        wp_loss=20.0,
        judgement=Judgement.MISTAKE,
        phase=Phase.OPENING,
        depth=12,
        clock_after=clock,
    )


def puzzle(game_id: str, ply: int, fen: str = START, *, tags: list[str] | None = None) -> Puzzle:
    return Puzzle(
        id=f"{game_id}:{ply}",
        game_id=game_id,
        ply=ply,
        fen=fen,
        solution="e2e4",
        solution_san="e4",
        played_san="Nf3",
        line="",
        gap_cp=200,
        depth=18,
        tags=tags or ["middlegame"],
    )


def attempt(store: Store, puzzle_id: str, at: datetime = WHEN, correct: bool = True) -> None:
    review = store.review(puzzle_id)
    assert review is not None
    store.save_attempt(schedule(review, correct, at.date()), "e2e4", correct, at)


def test_adding_a_puzzle_again_keeps_its_practice_history(tmp_path: Path) -> None:
    with Store(tmp_path / "c.db") as store:
        store.upsert_game(game("lichess:a"))
        p = puzzle("lichess:a", 1)
        store.add_puzzle(p)
        attempt(store, p.id)
        before = store.review(p.id)
        assert before is not None and before.reps == 1

        store.add_puzzle(p)

        assert len(store.attempts()) == 1
        assert store.review(p.id) == before


def _fix() -> Fix:
    return Fix(
        fen=START,
        color="white",
        move_number=1,
        played_san="Nf3",
        played_uci="g1f3",
        best_san="e4",
        best_uci="e2e4",
        times=3,
        avg_cp_loss=60.0,
        games=[("lichess:a", 1, None)],
    )


def test_an_opening_fix_and_a_game_puzzle_on_the_same_move_are_two_puzzles(tmp_path: Path) -> None:
    class Lines:
        def evaluate(self, board, *, depth, multipv=1):
            return [LineEval(30, None, chess.Move.from_uci("e2e4"))]

    fix = verify_fix(Lines(), _fix(), "Italian Game")
    assert fix is not None and fix.id == "fix:lichess:a:1"
    with Store(tmp_path / "c.db") as store:
        store.upsert_game(game("lichess:a"))
        store.add_puzzle(puzzle("lichess:a", 1))
        store.add_puzzle(fix)
        assert sorted(p.id for p in store.puzzles()) == ["fix:lichess:a:1", "lichess:a:1"]


def test_stored_opening_fixes_get_their_own_ids_on_open(tmp_path: Path) -> None:
    db = tmp_path / "c.db"
    old = puzzle("lichess:a", 5, tags=["opening-fix", "Pirc Defense"])
    with Store(db) as store:
        store.upsert_game(game("lichess:a"))
        store.add_puzzle(old)  # as verify_fix stored them, under the game move's id
        store.add_puzzle(puzzle("lichess:a", 7))
        attempt(store, old.id)
    with Store(db) as store:
        assert sorted(p.id for p in store.puzzles()) == ["fix:lichess:a:5", "lichess:a:7"]
        assert store.attempts()[0][0] == "fix:lichess:a:5"
        review = store.review("fix:lichess:a:5")
        assert review is not None and review.reps == 1
        assert store.review("lichess:a:5") is None
        assert store.conn.execute("PRAGMA foreign_key_check").fetchall() == []
        before = (store.puzzles(), store.attempts())
    with Store(db) as store:  # once only: the second open changes nothing
        assert (store.puzzles(), store.attempts()) == before
