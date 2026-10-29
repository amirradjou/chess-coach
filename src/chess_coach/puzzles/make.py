"""Turn the user's worst moves into puzzles, keeping only positions with one clearly best move.

A candidate is a move of the user's that lost at least 20 points of win probability. The position
before it is searched again, deeper, with three lines: it becomes a puzzle only if the best move is
the only mate, or beats the second-best by at least 150 centipawns, so there is one answer to find.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass

import chess

from chess_coach.db import Store
from chess_coach.engine.stockfish import Engine
from chess_coach.models import Game, MoveFact, Puzzle

MIN_WP_LOSS = 20.0
MIN_GAP_CP = 150
VERIFY_DEPTH = 18


@dataclass(slots=True)
class MakeResult:
    checked: int = 0
    made: int = 0


def candidates(games: Iterable[Game], moves: Iterable[MoveFact], skip: set[str]) -> list[MoveFact]:
    """The user's costly moves not checked yet: newest games first, worst move first in a game."""
    order = {g.id: (g.played_at.isoformat() if g.played_at else "") for g in games}
    picked = [
        m
        for m in moves
        if m.is_user
        and m.wp_loss >= MIN_WP_LOSS
        and m.game_id in order
        and f"{m.game_id}:{m.ply}" not in skip
    ]
    return sorted(picked, key=lambda m: (order[m.game_id], m.wp_loss), reverse=True)


def verify(engine: Engine, m: MoveFact, depth: int = VERIFY_DEPTH) -> Puzzle | None:
    """The puzzle for this move, or None when the position has no single clear answer."""
    board = chess.Board(m.fen_before)
    if board.legal_moves.count() < 2:
        return None
    lines = engine.evaluate(board, depth=depth, multipv=3)
    best = lines[0]
    if best.move is None or best.move.uci() == m.uci:
        return None
    second = lines[1] if len(lines) > 1 else None
    only_mate = (
        best.mate is not None
        and best.mate > 0
        and (second is None or second.mate is None or second.mate <= 0)
    )
    gap = best.cp - second.cp if second is not None else MIN_GAP_CP
    if not only_mate and gap < MIN_GAP_CP:
        return None
    tags = [*m.motifs, m.phase.value]
    if only_mate:
        tags.append("mate")
    return Puzzle(
        id=f"{m.game_id}:{m.ply}",
        game_id=m.game_id,
        ply=m.ply,
        fen=m.fen_before,
        solution=best.move.uci(),
        solution_san=board.san(best.move),
        played_san=m.san,
        line=" ".join(mv.uci() for mv in best.pv[1:8]),
        gap_cp=gap,
        depth=depth,
        tags=sorted(set(tags)),
    )


def make(
    store: Store,
    engine: Engine,
    *,
    depth: int = VERIFY_DEPTH,
    limit: int = 50,
    on_check: Callable[[int, int], None] | None = None,
) -> MakeResult:
    """Check up to `limit` new candidates and store the ones that make good puzzles."""
    games = [g for g in store.iter_games() if g.analyzed_depth]
    todo = candidates(games, store.iter_moves(user_only=True), store.checked_moves())[:limit]
    result = MakeResult()
    for i, m in enumerate(todo, 1):
        puzzle = verify(engine, m, depth)
        if puzzle is not None:
            store.add_puzzle(puzzle)
            result.made += 1
        store.record_check(f"{m.game_id}:{m.ply}", puzzle is not None)
        result.checked += 1
        if on_check:
            on_check(i, len(todo))
    return result
