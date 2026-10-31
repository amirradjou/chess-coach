from datetime import UTC, date, datetime

from fastapi.testclient import TestClient

from chess_coach.coach.progress import Week, practice_effect, trend_speeds, weekly
from chess_coach.db import Store
from chess_coach.engine.stockfish import EngineNotFoundError
from chess_coach.models import Speed
from chess_coach.web.app import create_app

from .test_patterns import game

TODAY = date(2026, 10, 31)  # a Saturday in ISO week 44


def rated(i: int, day: str, elo: int, speed: Speed = Speed.BLITZ):
    g = game(i, speed=speed)
    g.played_at = datetime.fromisoformat(f"{day}T12:00:00+00:00")
    g.white_elo = elo  # the user is White in these games
    return g


def test_bullet_has_no_rating_trend():
    games = [rated(1, "2026-10-01", 1500, Speed.BULLET)] * 3 + [
        rated(2, "2026-10-01", 1400),
        rated(3, "2026-10-02", 1450, Speed.RAPID),
        rated(4, "2026-10-03", 1460, Speed.RAPID),
    ]
    assert trend_speeds(games) == ["rapid", "blitz"]


def test_rating_and_practice_week_by_week():
    games = [
        rated(1, "2026-10-01", 1290),  # before the three weeks shown
        rated(2, "2026-10-14", 1300),
        rated(3, "2026-10-15", 1310),
        rated(4, "2026-10-28", 1330),
        rated(5, "2026-10-28", 1800, Speed.BULLET),  # another speed
    ]
    puzzles = [
        ("p1", "2026-10-29T10:00:00+00:00", True),
        ("p2", "2026-10-30T10:00:00+00:00", False),
    ]
    answers = [
        ("c1", "2026-10-20T10:00:00+00:00", True),
        ("c2", "2026-10-27T10:00:00+00:00", True),
        ("c3", "2026-10-27T10:01:00+00:00", False),
    ]
    weeks = weekly(games, "blitz", puzzles, answers, today=TODAY, weeks=3)
    assert [(w.label, w.games, w.rating, w.change) for w in weeks] == [
        ("2026-W42", 2, 1310, 20),
        ("2026-W43", 0, None, None),
        ("2026-W44", 1, 1330, 20),
    ]
    assert [(w.puzzles, w.solved, w.answers, w.right) for w in weeks] == [
        (0, 0, 0, 0),
        (0, 0, 1, 1),
        (2, 1, 2, 1),
    ]


def test_practice_is_compared_only_with_enough_weeks():
    practised = [Week(f"p{i}", rating=1, change=10, puzzles=1) for i in range(4)]
    idle = [Week(f"i{i}", rating=1, change=-2) for i in range(4)]
    assert practice_effect(practised + idle[:3]) is None
    assert practice_effect(practised + idle) == (10, 4, -2, 4)


def no_engine():
    raise EngineNotFoundError("no engine")


def test_progress_page_has_the_trend(tmp_path):
    db = tmp_path / "c.db"
    now = datetime.now(UTC)
    with Store(db) as store:
        for g in [rated(1, "2026-01-05", 1400), rated(2, "2026-01-06", 1500, Speed.RAPID)]:
            g.played_at = now
            store.upsert_game(g)
    client = TestClient(create_app(db, no_engine))
    trend = client.get("/api/progress").json()["trend"]
    assert (trend["speeds"], trend["speed"], len(trend["weeks"])) == (
        ["blitz", "rapid"],
        "blitz",
        12,
    )
    assert trend["weeks"][-1]["rating"] == 1400 and trend["effect"] is None
    rapid = client.get("/api/progress", params={"speed": "rapid"}).json()["trend"]
    assert (rapid["speed"], rapid["weeks"][-1]["rating"]) == ("rapid", 1500)
    assert (
        client.get("/api/progress", params={"speed": "bullet"}).json()["trend"]["speed"] == "blitz"
    )
