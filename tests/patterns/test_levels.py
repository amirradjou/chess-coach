"""The next level: your early errors against public players, and what may be said about them."""

import math
from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from chess_coach.cli import app
from chess_coach.coach.digest import build, render
from chess_coach.coach.levels import (
    Place,
    Rate,
    Row,
    headline,
    ladder,
    place,
    rate_at,
    rating_at,
    reference,
    report,
)
from chess_coach.db import Store
from chess_coach.models import Color, Game, Judgement, MoveFact, Phase, Speed
from chess_coach.web.app import create_app

TODAY = date(2026, 11, 5)
WHEN = datetime(2026, 10, 20, 12, tzinfo=UTC)
LADDER = [(1000.0, 0.10), (1100.0, 0.08), (1200.0, 0.06), (1300.0, 0.04), (1400.0, 0.02)]


def band(centre: int, rate_6_10: float, *, tc: dict | None = None) -> dict:
    n = 10_000
    row = {
        "errors": [round(rate_6_10 * n / 2), n],
        "inaccuracies": [round(rate_6_10 * n), n],
        "design_effect": [1.0, 1.0],
    }
    out = {"lo": centre - 100, "hi": centre + 100, "games": 1000, "rows": {"1-5": row, "6-10": row}}
    if tc:
        out["by_time_control"] = {
            name: {"games": 1, "rows": {"1-5": _row(r), "6-10": _row(r)}} for name, r in tc.items()
        }
    return out


def _row(rate: float) -> dict:
    return {"errors": [round(rate * 10_000), 10_000], "inaccuracies": [0, 10_000]}


def table(**overrides) -> dict:
    """A small ladder: moves 1-5 and 6-10, errors falling from 10% at 1000 to 2% at 1400."""
    bands = [
        band(c, r * 2)
        for c, r in zip((1000, 1100, 1200, 1300, 1400), (0.1, 0.08, 0.06, 0.04, 0.02), strict=True)
    ]
    return {
        "built": "2026-11-01",
        "segments": {"bullet": ["1-5", "6-10"], "blitz": ["1-5", "6-10"]},
        "speeds": {
            "bullet": {"time_controls": ["60+0"], "bands": bands},
            "blitz": {"time_controls": ["300+3", "180+2"], "bands": bands},
        },
        **overrides,
    }


def game(i: int, *, speed=Speed.BULLET, tc="60+0", when=WHEN, analysed=True, elo=1100) -> Game:
    return Game(
        id=f"lichess:g{i}",
        source="lichess",
        source_id=f"g{i}",
        user="me",
        user_color=Color.WHITE,
        white="me",
        black="them",
        result="1-0",
        pgn="1. e4 e5 *",
        white_elo=elo,
        black_elo=elo,
        played_at=when,
        speed=speed,
        time_control=tc,
        analyzed_depth=12 if analysed else None,
    )


def move(game_id: str, ply: int, *, user: bool | None = None, error=False, clock=50.0) -> MoveFact:
    mine = ply % 2 == 1 if user is None else user
    return MoveFact(
        game_id=game_id,
        ply=ply,
        move_number=(ply + 1) // 2,
        color=Color.WHITE if ply % 2 else Color.BLACK,
        is_user=mine,
        san="Nf3",
        uci="g1f3",
        fen_before="8/8/8/8/8/8/8/K6k w - - 0 1",
        eval_before=0,
        eval_after=0,
        best_move="g1f3",
        best_san="Nf3",
        cp_loss=0,
        wp_before=50.0,
        wp_after=30.0 if error else 50.0,
        wp_loss=20.0 if error else 0.0,
        judgement=Judgement.MISTAKE if error else Judgement.BEST,
        phase=Phase.OPENING,
        depth=12,
        clock_after=clock,
    )


def row(rep, speed: str, segment: str):
    s = next(s for s in rep.speeds if s.speed == speed)
    return next(r for r in s.rows if r.segment == segment)


def test_moves_are_split_by_number_and_both_players_are_counted():
    g = game(1)
    moves = [
        move(g.id, 1),  # your move 1
        move(g.id, 2, error=True),  # their move 1, an error
        move(g.id, 9, error=True),  # your move 5, the last of 1-5
        move(g.id, 11),  # your move 6, the first of 6-10
        move(g.id, 19, error=True),  # your move 10, the last of 6-10
    ]
    rep = report([g], moves, TODAY, table())
    first, second = row(rep, "bullet", "1-5"), row(rep, "bullet", "6-10")
    assert (first.you.errors, first.you.moves) == (1, 2)
    assert (first.them.errors, first.them.moves) == (1, 1)
    assert (second.you.errors, second.you.moves) == (1, 2)


def test_the_bullet_rule_the_clock_rule_and_the_window_apply():
    g = game(1)
    past_ten = move(g.id, 21, error=True)  # bullet move 11 doesn't count at all
    rushed = move(g.id, 11, error=True, clock=5.0)  # under 15 s left in bullet
    old = game(2, when=datetime(2025, 11, 1, tzinfo=UTC))  # just over 12 months ago
    other = game(3, tc="0+1")  # no public yardstick for it
    unanalysed = game(4, analysed=False)
    moves = [
        past_ten,
        rushed,
        move(g.id, 13),
        move(old.id, 13, error=True),
        move(other.id, 13, error=True),
        move(unanalysed.id, 13, error=True),
    ]
    rep = report([g, old, other, unanalysed], moves, TODAY, table())
    r = row(rep, "bullet", "6-10")
    assert (r.you.errors, r.you.moves) == (0, 1)
    s = rep.speeds[0]
    assert (s.games, s.other_games) == (1, 1)


def test_the_window_includes_its_first_day():
    edge = game(1, when=datetime(2025, 11, 5, tzinfo=UTC))  # exactly 365 days before TODAY
    rep = report([edge], [move(edge.id, 13)], TODAY, table())
    assert rep.speeds[0].games == 1


def test_rating_at_and_rate_at_at_the_edges_of_the_ladder():
    assert rating_at(0.12, LADDER) == -math.inf  # worse than the lowest band
    assert rating_at(0.01, LADDER) == math.inf  # better than the highest
    assert rating_at(0.10, LADDER) == 1000
    assert rating_at(0.02, LADDER) == 1400
    assert rating_at(0.07, LADDER) == pytest.approx(1150)
    assert rate_at(999, LADDER) is None and rate_at(1401, LADDER) is None
    assert rate_at(1000, LADDER) == 0.10 and rate_at(1400, LADDER) == 0.02
    assert rate_at(1150, LADDER) == pytest.approx(0.07)


def test_a_flat_stretch_of_the_ladder_reads_as_its_middle():
    flat = [(1000.0, 0.10), (1100.0, 0.05), (1200.0, 0.05), (1300.0, 0.01)]
    assert rating_at(0.05, flat) == pytest.approx(1150)
    assert rating_at(0.03, flat) == pytest.approx(1250)
    assert rating_at(0.0999, [(1000.0, 0.1), (1100.0, 0.1), (1200.0, 0.0)]) == pytest.approx(1100.1)


def test_a_ladder_that_wobbles_is_pooled_so_it_never_rises():
    bands = [band(1000, 0.20), band(1100, 0.10), band(1200, 0.14), band(1300, 0.04)]
    rates = [p for _, p in ladder(bands, "6-10")]
    assert rates == sorted(rates, reverse=True)
    assert rates[1] == rates[2] == pytest.approx(0.06)  # 5% and 7% pooled, same weight
    assert [r for r, _ in ladder(bands, "6-10")] == [1000, 1100, 1200, 1300]


def test_blitz_is_weighted_to_your_time_controls():
    bands = [
        band(1000, 0.2, tc={"300+3": 0.10, "180+2": 0.30}),
        band(1100, 0.1, tc={"300+3": 0.05, "180+2": 0.15}),
    ]
    assert ladder(bands, "6-10", {"300+3": 3, "180+2": 1})[0][1] == pytest.approx(0.15)
    assert ladder(bands, "6-10", {"180+2": 4})[0][1] == pytest.approx(0.30)
    # With none of yours at those time controls, the band's own rate
    assert ladder(bands, "6-10", {})[0][1] == pytest.approx(0.10)


def test_place_words():
    words = lambda low, high: Place(low, high, 1000, 2300).words  # noqa: E731
    assert words(1420, 1480) == "around 1400-1500"
    assert words(1400, 1400) == "around 1400"
    assert words(-math.inf, 1150) == "below about 1200"
    assert words(-math.inf, -math.inf) == "below about 1000"
    assert words(2150, math.inf) == "above about 2100"
    assert words(math.inf, math.inf) == "above about 2300"
    assert words(-math.inf, math.inf) is None


def test_a_place_is_clear_only_within_a_300_point_step():
    def spans(low, high):
        return Place(low, high, 1000, 2300).clear

    assert spans(1200, 1500)
    assert not spans(1200, 1501)
    assert spans(-math.inf, 1300)  # off the bottom but within a step of it
    assert not spans(-math.inf, 1301)
    assert spans(2000, math.inf)
    assert not spans(1999, math.inf)
    assert spans(-math.inf, -math.inf) and spans(math.inf, math.inf)
    assert not spans(-math.inf, math.inf)


def test_place_reads_the_interval_off_the_ladder():
    p = place(Rate(7, 100, 0.05, 0.07), LADDER)
    assert (p.low, p.high) == (pytest.approx(1150), pytest.approx(1250))


def _many(n_games: int, every: int = 0, **kw) -> tuple[list[Game], list[MoveFact]]:
    """Games with moves 6-10 for both players; every `every`th move of yours is an error."""
    games = [game(i, **kw) for i in range(n_games)]
    moves = []
    mine = 0
    for g in games:
        for ply in range(11, 21):
            m = move(g.id, ply)
            if m.is_user:
                mine += 1
                if every and mine % every == 0:
                    m.wp_loss = 20.0
            moves.append(m)
    return games, moves


def test_few_games_say_not_enough_games_and_claim_nothing_more():
    games, moves = _many(3, 15)  # 1 error in 15 moves: the interval fits every rating on the ladder
    r = row(report(games, moves, TODAY, table()), "bullet", "6-10")
    assert r.place is not None and not r.place.clear and r.place.words is None
    assert r.placed is None
    assert r.sentence == "Moves 6-10: not enough games to place you."
    assert not r.gap  # 2% at the next level is inside that interval


def test_a_wide_interval_says_only_the_range_it_allows():
    rate = Rate(64, 1000, 0.05, 0.08)

    def sentence(low, high):
        return Row("6-10", rate, rate, Place(low, high, 1000, 2300), None, False).sentence

    assert sentence(1200, 1700) == (
        "Moves 6-10: not enough games to place you more closely than around 1200-1700."
    )
    assert sentence(1800, math.inf) == (
        "Moves 6-10: not enough games to place you more closely than above about 1800."
    )
    assert sentence(1400, 1500) == "Moves 6-10: you play like players around 1400-1500."
    no_moves = Row("6-10", Rate(0, 0, 0.0, 1.0), rate, None, None, False)
    assert no_moves.sentence == "Moves 6-10: no moves of yours to count yet."


def test_many_games_place_you_and_name_the_gap():
    games, moves = _many(4000, 17)  # about 6% of 20,000 moves: 1200 here, your rating 1100
    rep = report(games, moves, TODAY, table())
    r = row(rep, "bullet", "6-10")
    assert r.place is not None and r.place.clear
    assert r.sentence == "Moves 6-10: you play like players around 1100-1300."
    assert r.above == pytest.approx(0.02)  # at 1400, your 1100 + 300
    assert r.gap
    line = headline(rep)
    assert line is not None and "In bullet, moves 6-10 are your widest clear gap" in line
    assert "5.9%" in line and "2.0% for players 300 points above you" in line


def test_a_gap_is_only_claimed_when_your_interval_clears_the_next_level():
    games, moves = _many(3, 15)
    r = row(report(games, moves, TODAY, table()), "bullet", "6-10")
    assert r.above == pytest.approx(0.02) and r.you.low < 0.02 and not r.gap
    rep = report(games, moves, TODAY, table())
    assert headline(rep) == "Not enough recent games to place you against public players yet."


def test_with_no_gap_the_headline_says_where_you_stand():
    games, moves = _many(4000, 50)  # 2%: level with the next rung, and at the top of this ladder
    rep = report(games, moves, TODAY, table())
    r = row(rep, "bullet", "6-10")
    assert not r.gap and r.place is not None and r.place.clear
    assert headline(rep) == f"In bullet, you play moves 6-10 like players {r.place.words}."


def test_a_speed_you_stopped_playing_is_said_to_be_old_and_left_out_of_the_headline():
    old = TODAY - timedelta(days=46)
    when = datetime(old.year, old.month, old.day, 12, tzinfo=UTC)
    games, moves = _many(5, speed=Speed.BLITZ, tc="180+2", when=when)
    rep = report(games, moves, TODAY, table())
    s = rep.speeds[0]
    assert s.speed == "blitz" and s.stale and s.last == old
    assert s.stale_note == f"Your last blitz game was on {old}, so these numbers are from then."
    assert headline(rep) is None


def test_30_days_since_your_last_game_isnt_old_yet():
    last = TODAY - timedelta(days=30)
    when = datetime(last.year, last.month, last.day, 12, tzinfo=UTC)
    rep = report(*_many(1, when=when), TODAY, table())
    assert not rep.speeds[0].stale and rep.speeds[0].stale_note is None


def test_no_games_no_report():
    rep = report([], [], TODAY, table())
    assert rep.speeds == [] and headline(rep) is None


def test_the_reference_has_what_the_card_reads():
    t = reference()
    assert t["segments"] == {"bullet": ["1-5", "6-10"], "blitz": ["1-5", "6-10", "11-20"]}
    assert set(t["speeds"]) == {"bullet", "blitz"}
    for speed, table_ in t["speeds"].items():
        assert table_["time_controls"]
        bands = table_["bands"]
        assert [b["lo"] for b in bands] == sorted(b["lo"] for b in bands)
        assert all(b["hi"] - b["lo"] == 200 for b in bands)
        for b in bands:
            for segment in t["segments"][speed]:
                r = b["rows"][segment]
                assert 0 < r["errors"][0] < r["errors"][1]
                assert r["errors"][0] <= r["inaccuracies"][0]
                assert r["design_effect"][0] is None or r["design_effect"][0] > 0.5
                if speed == "blitz":
                    assert set(b["by_time_control"]) == set(table_["time_controls"])
                    assert b["by_time_control"][table_["time_controls"][0]]["rows"][segment]
    # A real ladder errs less as the rating climbs, end to end
    for segment in ("1-5", "6-10"):
        points = ladder(t["speeds"]["bullet"]["bands"], segment)
        assert points[0][1] > points[-1][1]


def _db(path, games, moves) -> None:
    with Store(path) as store:
        for g in games:
            store.upsert_game(g)
            store.replace_moves(g.id, [m for m in moves if m.game_id == g.id], depth=12)


def _recent(n: int) -> tuple[list[Game], list[MoveFact]]:
    return _many(n, 7, when=datetime.now(UTC) - timedelta(days=2))


def test_the_command_says_what_it_can(tmp_path):
    db = tmp_path / "c.db"
    empty = CliRunner().invoke(app, ["--db", str(db), "levels"])
    assert empty.exit_code == 0 and "No analysed bullet or blitz games" in empty.output
    _db(db, *_recent(3))
    result = CliRunner().invoke(app, ["--db", str(db), "levels"])
    assert result.exit_code == 0, result.output
    joined = " ".join(result.output.split())
    assert "not enough games to place you" in joined and "Bullet: 3 games at 60+0" in joined


def test_the_progress_card(tmp_path):
    db = tmp_path / "c.db"
    client = TestClient(create_app(db))
    assert client.get("/api/progress").json()["levels"]["speeds"] == []
    _db(db, *_recent(3))
    client = TestClient(create_app(db))
    levels = client.get("/api/progress").json()["levels"]
    bullet = levels["speeds"][0]
    assert (bullet["speed"], bullet["games"], bullet["controls"]) == ("bullet", 3, ["60+0"])
    r = next(x for x in bullet["rows"] if x["segment"] == "6-10")
    assert r["you"]["moves"] == 15 and r["you"]["low"] < r["you"]["rate"] < r["you"]["high"]
    assert "not enough games" in r["sentence"]
    first = next(x for x in bullet["rows"] if x["segment"] == "1-5")
    assert first["you"] is None  # no moves there


def test_one_line_in_the_digest():
    games, moves = _many(1, 5)
    line = "In bullet, moves 6-10 are your widest clear gap."
    assert f"## The next level\n\n{line}" in render(build(games, moves, next_level=line))
    assert "The next level" not in render(build(games, moves))
