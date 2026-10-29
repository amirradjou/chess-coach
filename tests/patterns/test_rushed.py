from datetime import UTC, datetime
from pathlib import Path

from chess_coach.coach.digest import build, render
from chess_coach.db import Store
from chess_coach.models import Speed
from chess_coach.patterns.features import move_rows, rushed, rushed_rows
from chess_coach.patterns.mining import repeated_under_pressure
from chess_coach.puzzles.make import candidates, prune_rushed

from .test_patterns import game, move


def bullet(i: int):
    g = game(i, speed=Speed.BULLET)
    g.time_control = "60+0"
    return g


def test_short_on_time():
    b = bullet(1)
    assert rushed(move(b.id, 41, clock=10.0), b)
    assert not rushed(move(b.id, 41, clock=20.0), b)
    blitz = game(2)
    blitz.time_control = "300+0"
    assert rushed(move(blitz.id, 41, clock=25.0), blitz)  # under a tenth of 5 minutes
    assert not rushed(move(blitz.id, 41, clock=40.0), blitz)
    assert not rushed(move(b.id, 41, clock=None), b)


def test_rushed_moves_are_left_out_unless_asked_for():
    b = bullet(1)
    moves = [move(b.id, 1, clock=50.0), move(b.id, 41, error=True, clock=5.0)]
    assert [r.fact.ply for r in move_rows([b], moves)] == [1]
    assert [(r.fact.ply, r.rushed) for r in rushed_rows([b], moves)] == [(41, True)]


def _rushed_blunders(games: int, per_game: int, tag: str = "hanging_piece"):
    gs, ms = [], []
    for i in range(games):
        g = bullet(i)
        gs.append(g)
        for k in range(per_game):
            ms.append(move(g.id, 41 + 2 * k, error=True, clock=5.0, motifs=(tag, "time_trouble")))
    return gs, ms


def test_a_rushed_mistake_counts_only_when_it_keeps_repeating():
    gs, ms = _rushed_blunders(8, 2)  # 16 moves in 8 games
    assert [m.tag for m in repeated_under_pressure(rushed_rows(gs, ms))] == ["hanging_piece"]
    gs, ms = _rushed_blunders(4, 4)  # 16 moves but only 4 games
    assert repeated_under_pressure(rushed_rows(gs, ms)) == []


def test_puzzles_skip_rushed_mistakes_that_dont_repeat():
    gs, ms = _rushed_blunders(2, 1)
    ms[0].wp_loss = ms[1].wp_loss = 40.0
    assert candidates(gs, ms, set()) == []
    assert len(candidates(gs, ms, set(), frozenset({"hanging_piece"}))) == 2


def test_digest_says_what_it_left_out():
    gs, ms = _rushed_blunders(8, 2)
    for g in gs:
        ms.append(move(g.id, 1, clock=55.0))
    text = render(build(gs, ms))
    assert "16 moves played short on time" in text
    assert "## Short on time, but it keeps happening" in text
    assert "| leaving a piece where it can be taken | 16 | 8 |" in text


def test_prune_removes_untried_rushed_puzzles(tmp_path: Path):
    from chess_coach.models import Puzzle

    gs, ms = _rushed_blunders(2, 1)
    with Store(tmp_path / "c.db") as store:
        for g in gs:
            store.upsert_game(g)
            store.replace_moves(g.id, [m for m in ms if m.game_id == g.id], depth=12)
        for m in ms:
            store.add_puzzle(
                Puzzle(
                    f"{m.game_id}:{m.ply}",
                    m.game_id,
                    m.ply,
                    m.fen_before,
                    "g1f3",
                    "Nf3",
                    m.san,
                    "",
                    200,
                    18,
                    ["hanging_piece"],
                )
            )
        tried = store.review(f"{ms[0].game_id}:{ms[0].ply}")
        store.save_attempt(tried, "?", False, datetime.now(UTC))
        assert prune_rushed(store) == [f"{ms[1].game_id}:{ms[1].ply}"]
        assert [p.id for p in store.puzzles()] == [f"{ms[0].game_id}:{ms[0].ply}"]
