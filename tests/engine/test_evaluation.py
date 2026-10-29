import pytest

from chess_coach.engine.evaluation import MATE_CP, cp_loss, judge, mate_to_cp, win_probability
from chess_coach.models import Judgement


def test_win_probability_is_symmetric_and_saturates() -> None:
    assert win_probability(0) == 50.0
    assert win_probability(100) + win_probability(-100) == pytest.approx(100.0)
    assert 58 < win_probability(100) < 60  # +1 pawn ~ 59%
    assert win_probability(MATE_CP) == 100.0
    assert win_probability(-MATE_CP) == 0.0


def test_mate_to_cp_keeps_shorter_mates_stronger() -> None:
    assert mate_to_cp(1) > mate_to_cp(5) > 0
    assert mate_to_cp(-1) < mate_to_cp(-5) < 0
    assert mate_to_cp(0) == -MATE_CP  # side to move has been mated


@pytest.mark.parametrize(
    ("wp_before", "wp_after", "expected"),
    [
        (60.0, 55.0, Judgement.GOOD),
        (60.0, 49.0, Judgement.INACCURACY),
        (60.0, 39.0, Judgement.MISTAKE),
        (60.0, 29.0, Judgement.BLUNDER),
        (60.0, 65.0, Judgement.GOOD),  # engine liked the move more than its own line
    ],
)
def test_judge_thresholds(wp_before: float, wp_after: float, expected: Judgement) -> None:
    judgement, _ = judge(played_uci="a2a3", best_uci="e2e4", wp_before=wp_before, wp_after=wp_after)
    assert judgement is expected


def test_judge_best_and_forced_moves() -> None:
    assert judge(played_uci="e2e4", best_uci="e2e4", wp_before=60, wp_after=40)[0] is (
        Judgement.BEST
    )
    assert judge(played_uci="a2a3", best_uci="e2e4", wp_before=60, wp_after=20, forced=True)[0] is (
        Judgement.BEST
    )
    assert judge(played_uci="a2a3", best_uci="e2e4", wp_before=60, wp_after=20)[1] == 40.0
    # best moves report zero loss even when the two searches disagree slightly
    assert judge(played_uci="e2e4", best_uci="e2e4", wp_before=60, wp_after=58)[1] == 0.0


def test_cp_loss_is_clamped_like_lichess_acpl() -> None:
    assert cp_loss(30, -70) == 100
    assert cp_loss(30, 80) == 0
    assert cp_loss(MATE_CP - 1, 300) == 700  # a missed mate counts as losing "+10" not "+100"
    assert cp_loss(-50, -MATE_CP) == 950
    assert cp_loss(MATE_CP, -MATE_CP) == 2000
