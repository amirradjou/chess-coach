"""Practice: what gets served, what is remembered, and which day it counts for."""

from datetime import UTC, datetime
from pathlib import Path

import chess
from typer.testing import CliRunner

from chess_coach.db import Store
from chess_coach.models import Color, Game, Judgement, MoveFact, Phase, Puzzle, Speed
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
