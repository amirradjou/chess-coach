"""Score arithmetic: centipawns, mates, win probability and Lichess-style move judgements."""

from __future__ import annotations

import math

from chess_coach.models import Judgement

MATE_CP = 10_000
# Centipawn loss is computed on values clamped to this, as Lichess does for ACPL, so one missed
# mate does not weigh as much as a hundred blunders.
CP_LOSS_CEILING = 1_000
# Lichess's logistic model mapping centipawns to winning chances (lila/ui/ceval/winningChances).
_WIN_K = -0.00368208

INACCURACY_WP_LOSS = 10.0
MISTAKE_WP_LOSS = 20.0
BLUNDER_WP_LOSS = 30.0


def mate_to_cp(mate_in: int) -> int:
    """Map a signed mate distance (mover's view) onto the cp scale; shorter mates score higher."""
    if mate_in == 0:
        return -MATE_CP
    sign = 1 if mate_in > 0 else -1
    return sign * (MATE_CP - abs(mate_in))


def win_probability(cp: int) -> float:
    """Percentage (0-100) that the side to move wins, from Lichess's centipawn model."""
    chances = 2 / (1 + math.exp(_WIN_K * cp)) - 1
    return round(50 + 50 * chances, 2)


def judge(
    *,
    played_uci: str,
    best_uci: str,
    wp_before: float,
    wp_after: float,
    forced: bool = False,
) -> tuple[Judgement, float]:
    """Classify a move by win-probability lost, Lichess thresholds. Returns (judgement, wp_loss)."""
    if forced or played_uci == best_uci:
        # Search horizons differ between the two positions, so even the best move can show a
        # small drift; by definition the mover lost nothing.
        return Judgement.BEST, 0.0
    wp_loss = round(max(0.0, wp_before - wp_after), 2)
    if wp_loss >= BLUNDER_WP_LOSS:
        return Judgement.BLUNDER, wp_loss
    if wp_loss >= MISTAKE_WP_LOSS:
        return Judgement.MISTAKE, wp_loss
    if wp_loss >= INACCURACY_WP_LOSS:
        return Judgement.INACCURACY, wp_loss
    return Judgement.GOOD, wp_loss


def cp_loss(eval_before: int, eval_after: int) -> int:
    """Centipawns lost by the move, both evals clamped to +-CP_LOSS_CEILING first."""
    before = max(-CP_LOSS_CEILING, min(CP_LOSS_CEILING, eval_before))
    after = max(-CP_LOSS_CEILING, min(CP_LOSS_CEILING, eval_after))
    return max(0, before - after)
