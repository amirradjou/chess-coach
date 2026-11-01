"""When they blunder: your punish rate against your opponents' (coach/punish.py)."""

import chess

from chess_coach.coach.punish import (
    LESSONS,
    Compare,
    Rate,
    compare,
    gifts,
    headline,
    label,
    report,
    trend,
)
from chess_coach.models import Color, Judgement, MoveFact, Phase, Speed
from chess_coach.patterns.features import punish_kind

from .test_patterns import game


def bullet(i: int):
    g = game(i, speed=Speed.BULLET)
    g.time_control = "60+0"
    return g


def fact(game_id: str, ply: int, **overrides) -> MoveFact:
    """A move in a game where the user has White: odd plies are theirs."""
    fields = dict(
        game_id=game_id,
        ply=ply,
        move_number=(ply + 1) // 2,
        color=Color.WHITE if ply % 2 else Color.BLACK,
        is_user=ply % 2 == 1,
        san="Qh5",
        uci="d1h5",
        fen_before="",
        eval_before=0,
        eval_after=0,
        best_move="g1f3",
        best_san="Nf3",
        cp_loss=0,
        wp_before=50.0,
        wp_after=49.0,
        wp_loss=1.0,
        judgement=Judgement.GOOD,
        phase=Phase.OPENING,
        depth=12,
        clock_after=200.0,
    )
    fields.update(overrides)
    return MoveFact(**fields)


def chance(g, reply_move: int, *, yours=True, missed=False, best_san="Nxe5", clock=200.0):
    """A blunder and the reply on move `reply_move`: theirs and yours, or (yours=False) yours and
    theirs. A missed reply carries missed_tactic, as the engine tags it (and costs less than a
    blunder, so it isn't a gift of its own)."""
    ply = 2 * reply_move - 1 if yours else 2 * reply_move
    blunder = fact(g.id, ply - 1, wp_loss=30.0, judgement=Judgement.BLUNDER)
    reply = fact(
        g.id,
        ply,
        wp_loss=15.0 if missed else 2.0,
        judgement=Judgement.MISTAKE if missed else Judgement.GOOD,
        motifs=["missed_tactic"] if missed else [],
        best_san=best_san,
        clock_after=clock,
    )
    return blunder, reply


def chances(games, n: int, missed: int, *, yours=True, best_san="Nxe5"):
    """`n` chances spread over `games`, one a move from move 2 on in each, the first `missed` of
    them missed."""
    return [
        chance(
            games[i % len(games)],
            2 + i // len(games),
            yours=yours,
            missed=i < missed,
            best_san=best_san,
        )
        for i in range(n)
    ]


# -- the verdict -----------------------------------------------------------------------------


def test_a_gap_counts_only_when_its_interval_leaves_out_zero():
    worse = Compare("x", Rate(60, 100), Rate(30, 100))
    level = Compare("x", Rate(40, 100), Rate(38, 100))
    better = Compare("x", Rate(30, 100), Rate(60, 100))
    assert [c.verdict for c in (worse, level, better)] == ["worse", "same", "better"]
    assert "You miss it more often than your opponents do: +30.0 points" in headline(worse)
    assert "That's about the same as your opponents: +2.0 points" in headline(level)
    assert "includes zero" in headline(level)
    assert "You miss it less often" in headline(better)
    assert headline(Compare("x", Rate(3, 5), Rate(0, 0))).startswith("Not enough")
    assert Compare("x", Rate(3, 5), Rate(0, 0)).verdict == "same"


# -- which chances count ----------------------------------------------------------------------


def test_missed_is_the_engine_motif_and_both_sides_are_counted():
    g = game(1)
    pairs = [
        chance(g, 3, missed=True),
        chance(g, 4),
        chance(g, 5, yours=False, missed=True),
        chance(g, 6, yours=False),
        chance(g, 7, yours=False),
    ]
    found = gifts([g], pairs)
    assert [(x.yours, x.missed) for x in found] == [
        (True, True),
        (True, False),
        (False, True),
        (False, False),
        (False, False),
    ]
    c = compare("all", found)
    assert (c.yours, c.theirs) == (Rate(1, 2), Rate(1, 3))


def test_bullet_counts_moves_one_to_ten_for_both_sides():
    b = bullet(1)
    pairs = [
        chance(b, 10),
        chance(b, 11),  # your move 11: the clock decides
        chance(b, 10, yours=False),
        chance(b, 11, yours=False),  # their move 11: the same rule
    ]
    assert [(x.yours, x.reply.move_number) for x in gifts([b], pairs)] == [(True, 10), (False, 10)]


def test_replies_made_short_on_time_dont_count_on_either_side():
    b = bullet(1)
    blitz = game(2)
    blitz.time_control = "300+0"
    pairs = [
        chance(b, 5, clock=10.0),  # under 15 s in bullet
        chance(b, 6, yours=False, clock=10.0),
        chance(blitz, 30, clock=20.0),  # under a tenth of 5 minutes
        chance(blitz, 31, clock=200.0),
    ]
    assert [x.reply.move_number for x in gifts([b, blitz], pairs)] == [31]


def test_small_slips_and_other_games_are_not_gifts():
    g, other = game(1), game(2)
    small = chance(g, 5)
    small[0].wp_loss = 19.0
    assert gifts([g], [small, chance(other, 5)]) == []


def test_the_kind_of_punishment():
    assert [punish_kind(s) for s in ("Nxe5", "Bxf7+", "Qh5+", "O-O", "Nf3")] == [
        "capture",
        "capture",  # a capturing check counts as a capture
        "check",
        "quiet",  # castling punishes quietly
        "quiet",
    ]
    g = game(1)
    (x,) = gifts([g], [chance(g, 5, best_san="Bb5+")])
    assert x.kind == "check"


# -- the report -------------------------------------------------------------------------------


def _season():
    """Bullet: you miss 48 of 80, they miss 24 of 80. Blitz: level at 32 of 80 each. Your chances
    and theirs are in separate games, so their plies never overlap."""
    bullets = [bullet(i) for i in range(20)]
    blitzes = [game(i) for i in range(20, 40)]
    for i, g in enumerate(bullets):
        g.opening = "Danish Gambit Accepted" if i % 10 < 5 else "Queen's Pawn Game"
    for g in blitzes:
        g.opening = "Modern Defense"
    pairs = (
        chances(bullets[:10], 80, 48)
        + chances(bullets[10:], 80, 24, yours=False)
        + chances(blitzes[:10], 80, 32)
        + chances(blitzes[10:], 80, 32, yours=False)
    )
    return bullets + blitzes, pairs


def test_bullet_and_blitz_are_compared_apart():
    games, pairs = _season()
    r = report(games, pairs)
    assert [(c.label, c.yours, c.theirs, c.verdict) for c in r.groups] == [
        ("Bullet, moves 1-10", Rate(48, 80), Rate(24, 80), "worse"),
        ("Blitz and rapid", Rate(32, 80), Rate(32, 80), "same"),
    ]
    assert r.gifts == 320


def test_openings_and_examples_come_from_where_the_gap_is():
    games, pairs = _season()
    r = report(games, pairs)
    assert r.focus == ["Bullet, moves 1-10"]
    # The Modern Defense (blitz) has misses too, but you're level with your opponents there.
    assert {f.compare.label for f in r.families} == {"Danish Gambit Accepted", "Queen's Pawn Game"}
    assert all(g.game.speed is Speed.BULLET for f in r.families for g in f.examples)
    assert all(len(f.examples) == 2 and all(g.missed for g in f.examples) for f in r.families)
    assert len(r.worst) == 3 and all(g.yours and g.missed for g in r.worst)


def test_a_kind_lesson_needs_you_to_miss_it_more_than_your_opponents():
    g = [game(i) for i in range(10)]
    # Everyone misses quiet punishments more than captures: that alone is no lesson.
    generic = (
        chances(g, 100, 50, best_san="Nf5")
        + chances(g, 100, 30, best_san="Nxe5")
        + chances(g, 100, 50, yours=False, best_san="Nf5")
        + chances(g, 100, 30, yours=False, best_san="Nxe5")
    )
    r = report(g, generic)
    assert [(c.label, c.verdict) for c in r.kinds] == [
        ("capture", "same"),
        ("check", "same"),
        ("quiet", "same"),
    ]
    assert r.lessons == []
    personal = chances(g, 100, 70, best_san="Nf5") + chances(
        g, 100, 40, yours=False, best_san="Nf5"
    )
    assert report(g, personal).lessons == [LESSONS["quiet"]]


def test_the_trend_is_stretches_of_games_oldest_first():
    g1, g2, g3 = game(1), game(2), game(3)  # played on October 2, 3 and 4
    pairs = [chance(g, m, missed=m == 2) for g in (g1, g2, g3) for m in (2, 3)]
    pairs.append(chance(g3, 4, yours=False))
    by_two = trend(gifts([g1, g2, g3], pairs), size=2)
    assert [c.label for c in by_two] == [
        "2026-10-02 to 2026-10-02",
        "2026-10-03 to 2026-10-03",
        "2026-10-04 to 2026-10-04",
    ]
    assert [(c.yours, c.theirs) for c in by_two][-1] == (Rate(1, 2), Rate(0, 1))
    # A stretch ends at the end of a game, counted back from the newest; the oldest may be short.
    by_three = trend(gifts([g1, g2, g3], pairs), size=3)
    assert [(c.label, c.yours.n) for c in by_three] == [
        ("2026-10-02 to 2026-10-02", 2),
        ("2026-10-03 to 2026-10-04", 4),
    ]


def test_examples_say_what_the_blunder_allowed():
    g = game(1)
    board = chess.Board()
    for san in "d4 g6 Bh6".split():
        board.push_san(san)
    blunder = fact(g.id, 3, san="Bh6", wp_loss=51.0, is_user=False)
    reply = fact(
        g.id,
        4,
        san="Bg7",
        uci="f8g7",
        fen_before=board.fen(),
        best_move="g8h6",
        best_san="Nxh6",
        wp_loss=92.6,
        motifs=["missed_tactic"],
        is_user=True,
    )
    (x,) = gifts([g], [(blunder, reply)])
    assert (label(x.blunder), label(x.reply), label(x.reply, x.reply.best_san)) == (
        "2.Bh6",
        "2...Bg7",
        "2...Nxh6",
    )
    assert x.url == "https://lichess.org/g1#4"
    assert x.reasons() == [
        "It lets Nxh6 take their bishop on h6, which was hanging (attacked and not defended)."
    ]
