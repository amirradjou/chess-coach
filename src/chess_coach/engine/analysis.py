"""Run the engine over a game and turn the result into per-move facts."""

from __future__ import annotations

import io
from collections.abc import Callable

import chess
import chess.pgn

from chess_coach.engine.evaluation import cp_loss, judge, win_probability
from chess_coach.engine.phases import phase_of
from chess_coach.engine.stockfish import Engine, LineEval
from chess_coach.models import Color, Game, Judgement, MoveFact, Phase

PV_PLIES = 6


def _pv_san(board: chess.Board, pv: list[chess.Move]) -> str:
    """Render the first few moves of a variation in SAN, without mutating `board`."""
    scratch = board.copy(stack=False)
    return scratch.variation_san(pv[:PV_PLIES]) if pv else ""


def analyze_game(
    game: Game,
    engine: Engine,
    *,
    depth: int,
    on_ply: Callable[[int, int], None] | None = None,
) -> list[MoveFact]:
    """Evaluate every position of the mainline and describe each move that was played."""
    parsed = chess.pgn.read_game(io.StringIO(game.pgn))
    if parsed is None:
        return []
    nodes = list(parsed.mainline())
    total = len(nodes)
    board = parsed.board()

    # evals[i] is the evaluation of the position *before* ply i+1, from the side to move's view.
    evals: list[LineEval] = [engine.evaluate(board, depth=depth)[0]]
    boards: list[chess.Board] = [board.copy(stack=False)]
    for index, node in enumerate(nodes):
        board.push(node.move)
        evals.append(engine.evaluate(board, depth=depth)[0])
        boards.append(board.copy(stack=False))
        if on_ply:
            on_ply(index + 1, total)

    facts: list[MoveFact] = []
    phase = Phase.OPENING
    for index, node in enumerate(nodes):
        before = boards[index]
        mover = Color.WHITE if before.turn == chess.WHITE else Color.BLACK
        best = evals[index]
        # The next position is scored for the opponent; flip it to the mover's view.
        eval_after = -evals[index + 1].cp
        mate_after = None if evals[index + 1].mate is None else -evals[index + 1].mate
        phase = phase_of(before, phase)
        best_move_uci = best.move.uci() if best.move else node.move.uci()
        wp_before = win_probability(best.cp)
        wp_after = win_probability(eval_after)
        judgement, wp_loss = judge(
            played_uci=node.move.uci(),
            best_uci=best_move_uci,
            wp_before=wp_before,
            wp_after=wp_after,
            forced=before.legal_moves.count() == 1,
        )
        facts.append(
            MoveFact(
                game_id=game.id,
                ply=index + 1,
                move_number=before.fullmove_number,
                color=mover,
                is_user=mover is game.user_color,
                san=before.san(node.move),
                uci=node.move.uci(),
                fen_before=before.fen(),
                eval_before=best.cp,
                eval_after=eval_after,
                best_move=best_move_uci,
                best_san=before.san(best.move) if best.move else before.san(node.move),
                cp_loss=0 if judgement is Judgement.BEST else cp_loss(best.cp, eval_after),
                wp_before=wp_before,
                wp_after=wp_after,
                wp_loss=wp_loss,
                judgement=judgement,
                phase=phase,
                depth=depth,
                mate_before=best.mate,
                mate_after=mate_after,
                pv=_pv_san(before, best.pv),
                clock_after=node.clock(),
            )
        )
    return facts
