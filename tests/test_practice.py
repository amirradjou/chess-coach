"""Practice: what gets served, what is remembered, and which day it counts for."""

import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import chess
import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from chess_coach.cli import app
from chess_coach.coach.repertoire import Fix
from chess_coach.db import Store
from chess_coach.engine.stockfish import LineEval
from chess_coach.models import CoachCard, Color, Game, Judgement, MoveFact, Phase, Puzzle, Speed
from chess_coach.openings import epd
from chess_coach.puzzles.make import verify_fix
from chess_coach.puzzles.srs import schedule
from chess_coach.puzzles.today import (
    Facts,
    Item,
    Session,
    accuracy,
    days_practised,
    new_first,
    plan,
    read_facts,
    session,
    status_line,
    upcoming,
)
from chess_coach.util import practice_day
from chess_coach.web.app import create_app

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
    store.save_attempt(schedule(review, correct, practice_day(at)), "e2e4", correct, at)


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


def _bullet_puzzles(db: Path) -> tuple[str, str]:
    """A practised puzzle from bullet move 13, which the bullet rule leaves out, and one from
    bullet move 8, which it keeps."""
    late, early = puzzle("lichess:b", 25), puzzle("lichess:b", 15)
    with Store(db) as store:
        store.upsert_game(game("lichess:b", Speed.BULLET))
        store.replace_moves("lichess:b", [move("lichess:b", 25), move("lichess:b", 15)], depth=12)
        for p in (late, early):
            store.add_puzzle(p)
            attempt(store, p.id, correct=False)
    return late.id, early.id


def test_practice_never_serves_a_puzzle_the_bullet_rule_leaves_out(tmp_path: Path) -> None:
    db = tmp_path / "c.db"
    late, early = _bullet_puzzles(db)
    client = TestClient(create_app(db))

    assert client.get("/api/drill/next").json()["id"] == early
    assert client.get("/api/drill/next").json()["again"] == 1
    tags = {t["tag"]: t for t in client.get("/api/drill/tags").json()}
    assert (tags["middlegame"]["puzzles"], tags["middlegame"]["due"]) == (1, 1)

    drilled = runner.invoke(app, ["--db", str(db), "drill"], input="?\n")
    assert drilled.exit_code == 0, drilled.output
    assert "Solved 0 of 1." in drilled.output
    with Store(db) as store:  # kept, with its history
        assert {p.id for p in store.puzzles()} == {late, early}
        assert sum(a[0] == late for a in store.attempts()) == 1
    assert client.get("/api/drill/next").json()["done"]


@pytest.fixture
def toronto(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("TZ", "America/Toronto")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


@pytest.mark.parametrize(
    ("utc", "day"),
    [
        (datetime(2026, 11, 3, 8, 59, tzinfo=UTC), date(2026, 11, 2)),  # 03:59 local
        (datetime(2026, 11, 3, 9, 0, tzinfo=UTC), date(2026, 11, 3)),  # 04:00 local
        (
            datetime(2026, 11, 4, 3, 0, tzinfo=UTC),
            date(2026, 11, 3),
        ),  # 22:00 local, UTC is next day
    ],
)
def test_the_practice_day_is_local_and_changes_at_4_am(toronto, utc: datetime, day: date) -> None:
    assert practice_day(utc) == day


def test_one_opening_answer_counts_per_practice_day(toronto, tmp_path: Path) -> None:
    card = CoachCard("Danish", "white", ["e2e4"], "line", "2026-11-03")
    with Store(tmp_path / "c.db") as store:
        # 22:00 local on the 3rd: the UTC date has already moved on to the 4th
        store.save_coach_attempt(card, "e7e5", True, datetime(2026, 11, 4, 3, 0, tzinfo=UTC))
        assert store.coach_answered(card.id, "2026-11-03")
        assert not store.coach_answered(card.id, "2026-11-04")


def test_a_late_evening_answer_is_scheduled_from_the_local_day(toronto, tmp_path: Path) -> None:
    db = tmp_path / "c.db"
    with Store(db) as store:
        store.upsert_game(game("lichess:a"))
        store.add_puzzle(puzzle("lichess:a", 1))
    late = datetime(2026, 11, 4, 3, 0, tzinfo=UTC)  # 22:00 local on the 3rd
    with patch("chess_coach.web.app.datetime", wraps=datetime) as clock:
        clock.now.return_value = late
        answered = TestClient(create_app(db)).post(
            "/api/drill/answer", json={"id": "lichess:a:1", "uci": "e2e4"}
        )
    assert answered.json()["next_due"] == "2026-11-04"  # tomorrow, not the day after


def test_a_new_puzzle_isnt_due_and_practice_counts_them_apart(tmp_path: Path) -> None:
    db = tmp_path / "c.db"
    with Store(db) as store:
        store.upsert_game(game("lichess:a"))
        store.add_puzzle(puzzle("lichess:a", 1))
        store.add_puzzle(puzzle("lichess:a", 3))
        attempt(store, "lichess:a:1", datetime(2026, 1, 1, 12, tzinfo=UTC), correct=False)
        today = practice_day().isoformat()
        assert [p.id for p in store.due(today)] == ["lichess:a:1"]
        assert [p.id for p in store.untried()] == ["lichess:a:3"]
    client = TestClient(create_app(db))
    p = client.get("/api/drill/next").json()
    assert (p["id"], p["again"], p["new"]) == ("lichess:a:1", 1, 1)  # due again comes first
    tags = {t["tag"]: t for t in client.get("/api/drill/tags").json()}
    assert (tags["middlegame"]["due"], tags["middlegame"]["new"]) == (1, 1)


FEN_A = "r1bqkbnr/pppppppp/2n5/8/8/2N5/PPPPPPPP/R1BQKBNR w KQkq - 2 2"
FEN_B = "rnbqkb1r/pppppppp/5n2/8/8/5N2/PPPPPPPP/RNBQKB1R w KQkq - 2 2"
TODAY = date(2026, 11, 5)


def _facts(**played: date) -> Facts:
    return Facts(frozenset(), {}, {f"lichess:{k}": v for k, v in played.items()})


def test_new_puzzles_come_from_what_you_keep_reaching_then_recent_games_then_the_rest() -> None:
    old, recent, newer = (puzzle(f"lichess:{g}", 1) for g in ("old", "recent", "newer"))
    often, oftener = puzzle("lichess:old", 3, FEN_A), puzzle("lichess:older", 3, FEN_B)
    facts = Facts(
        frozenset(),
        {epd(FEN_A): 2, epd(FEN_B): 5, epd(START): 1},
        {
            "lichess:old": date(2026, 1, 1),
            "lichess:older": date(2025, 1, 1),
            "lichess:recent": date(2026, 9, 1),
            "lichess:newer": date(2026, 10, 1),
        },
    )
    in_order = new_first([old, recent, newer, often, oftener], facts, TODAY)
    assert [p.id for p in in_order] == [oftener.id, often.id, newer.id, recent.id, old.id]


def test_90_days_ago_is_still_recent() -> None:
    edge, past = puzzle("lichess:edge", 1), puzzle("lichess:past", 1)
    facts = _facts(edge=TODAY - timedelta(days=90), past=TODAY - timedelta(days=91))
    assert [p.id for p in new_first([past, edge], facts, TODAY)] == [edge.id, past.id]
    # and one with no date comes last among the rest
    undated = puzzle("lichess:undated", 1)
    assert new_first([undated, past], facts, TODAY)[-1] == undated


def test_a_bullet_position_after_move_10_doesnt_count_as_reached(tmp_path: Path) -> None:
    db = tmp_path / "c.db"
    with Store(db) as store:
        for name in ("x", "y"):
            store.upsert_game(game(f"lichess:{name}", Speed.BULLET))
            store.replace_moves(
                f"lichess:{name}",
                [move(f"lichess:{name}", 15), move(f"lichess:{name}", 25, FEN_A)],
                12,
            )
        facts = read_facts(store)
    assert facts.reached.get(epd(START)) == 2  # move 8, twice
    assert epd(FEN_A) not in facts.reached  # move 13: bullet doesn't count from move 11


def _new(game_id: str, n: int = 1) -> Item:
    return Item(f"{game_id}:{n}", game_id)


def _due(game_id: str | None, n: int = 1, on: str = "2026-11-01") -> Item:
    return Item(f"{game_id}:{n}", game_id, on)


def _no_game_twice_in_a_row(items: list[Item]) -> bool:
    games = [i.game_id for i in items]
    return all(a is None or a != b for a, b in zip(games, games[1:], strict=False))


def test_a_session_is_10_items_with_reviews_first_and_at_most_5_new() -> None:
    twelve = [_due(f"g{i}") for i in range(12)]
    assert [i.id for i in plan(twelve, [_new("n")])] == [i.id for i in twelve[:10]]

    three = [_due(f"g{i}") for i in range(3)]
    many = [_new(f"n{i}") for i in range(9)]
    got = plan(three, many)
    assert len(got) == 8 and sum(i.new for i in got) == 5
    assert got[:3] == three  # the reviews first

    eight = [_due(f"g{i}") for i in range(8)]
    assert sum(i.new for i in plan(eight, many)) == 2  # only 2 places left


def test_todays_answers_use_up_the_session_and_its_new_items() -> None:
    many = [_new(f"n{i}") for i in range(9)]
    assert len(plan([], many, done=7, new_done=2)) == 3
    assert len(plan([], many, done=3, new_done=3)) == 2  # 5 new a day, not 5 more
    assert plan([], many, done=10) == []
    assert plan([], many, done=14) == []


def test_never_two_new_items_from_one_game() -> None:
    new = [_new("a", 1), _new("a", 2), _new("b", 1), _new("a", 3), _new("c", 1)]
    assert [i.id for i in plan([], new)] == ["a:1", "b:1", "c:1"]
    # nor one more from a game whose new item you already did today
    assert [i.id for i in plan([], new, new_done=1, games_used={"a"})] == ["b:1", "c:1"]


def test_never_two_from_one_game_back_to_back() -> None:
    items = [_due("a", 1), _due("a", 2), _due("b", 1), _due("b", 2)]
    assert _no_game_twice_in_a_row(plan(items, []))
    # the first item isn't from the game you just answered
    assert plan([_due("a"), _due("b")], [], last_game="a")[0].game_id == "b"
    # a review and a new item from one game are kept apart too
    got = plan([_due("a", 1), _due("b", 1)], [_new("a", 2)])
    assert _no_game_twice_in_a_row(got)
    # the order that works is found even when the plain order would run out of other games
    crowded = [_due("x"), _due("a", 1), _due("a", 2), _due("a", 3), _due("y")]
    assert _no_game_twice_in_a_row(plan(crowded, []))


def test_a_game_may_follow_itself_only_when_nothing_else_is_left() -> None:
    items = [_due("a", 1), _due("a", 2)]
    assert [i.id for i in plan(items, [])] == ["a:1", "a:2"]


def test_opening_cards_have_no_game_to_keep_apart() -> None:
    assert [i.id for i in plan([_due(None, 1), _due(None, 2)], [])] == ["None:1", "None:2"]


def _practice_db(db: Path, games: int = 8) -> None:
    with Store(db) as store:
        for i in range(games):
            store.upsert_game(game(f"lichess:g{i}"))
            store.add_puzzle(puzzle(f"lichess:g{i}", 1))


def test_session_state_comes_from_todays_answers(tmp_path: Path) -> None:
    db = tmp_path / "c.db"
    _practice_db(db, 8)
    with Store(db) as store:
        facts = read_facts(store)
        day = practice_day()
        start = session(store, facts, day)
        assert (start.done, start.total, start.again, start.new) == (0, 5, 0, 5)

        first = start.items[0].id
        attempt(store, first, datetime.now(UTC), correct=False)
        now = session(store, facts, day)
        assert (now.done, now.total, now.new) == (1, 5, 4)
        assert first not in [i.id for i in now.items]
        # the miss comes back tomorrow, with the next new ones after it
        next_day = upcoming(store, facts, day + timedelta(days=1))
        assert next_day.items[0].id == first and (next_day.again, next_day.new) == (1, 5)


def test_a_practice_answer_yesterday_doesnt_use_up_today(tmp_path: Path) -> None:
    db = tmp_path / "c.db"
    _practice_db(db, 3)
    with Store(db) as store:
        yesterday = datetime.now(UTC) - timedelta(days=1)
        attempt(store, "lichess:g0:1", yesterday, correct=False)
        now = session(store, read_facts(store), practice_day())
        assert (
            now.done == 0 and now.items[0].id == "lichess:g0:1" and (now.again, now.new) == (1, 2)
        )


def test_opening_cards_come_with_the_reviews_most_overdue_first(tmp_path: Path) -> None:
    db = tmp_path / "c.db"
    _practice_db(db, 2)
    day = practice_day()
    long_ago = (day - timedelta(days=30)).isoformat()
    with Store(db) as store:
        attempt(store, "lichess:g0:1", datetime.now(UTC) - timedelta(days=20), correct=False)
        card = CoachCard("Danish", "white", ["e2e4", "e7e5"], "line", long_ago)
        store.save_coach_attempt(card, "g1f3", False, datetime.now(UTC) - timedelta(days=31))
        s = session(store, read_facts(store), day)
    assert [i.id for i in s.items] == [card.id, "lichess:g0:1", "lichess:g1:1"]
    assert (s.items[0].card, s.again, s.new) == (card, 2, 1)


def test_the_status_line_says_what_is_left() -> None:
    ten = Session([*(_due(f"g{i}") for i in range(5)), *(_new(f"n{i}") for i in range(5))])
    tomorrow = Session([_due("g"), _new("n")])
    assert status_line(ten, tomorrow) == "Today: 10 to practise (about 5 min): 5 due again, 5 new"
    assert status_line(Session(ten.items[:6], 4), tomorrow) == (
        "Today: 6 left of 10 (about 3 min): 5 due again, 1 new"
    )
    assert status_line(Session([], 10), tomorrow) == (
        "Today: done, 10 practised. Tomorrow: 1 due again, 1 new"
    )
    assert status_line(Session([]), Session([])) == (
        "Today: nothing to practise. Tomorrow: 0 due again, 0 new"
    )


def test_days_practised_of_the_last_week_and_accuracy_apart() -> None:
    day = practice_day()

    def on(days_ago: int) -> str:
        return (datetime.now(UTC) - timedelta(days=days_ago)).isoformat()

    attempts = [
        ("p1", on(9), False),
        ("p1", on(6), True),
        ("p2", on(6), True),
        ("p2", on(0), False),
    ]
    answers = [("c1", on(2), True), ("c1", on(0), True)]
    assert days_practised(attempts, answers, day) == 3  # 6 days ago, 2 days ago, today
    reviews, first = accuracy(attempts, answers)
    assert (reviews.right, reviews.tried) == (2, 3)
    assert (first.right, first.tried) == (2, 3)


def test_today_serves_a_puzzle_then_says_how_practice_is_going(tmp_path: Path) -> None:
    db = tmp_path / "c.db"
    _practice_db(db, 2)
    client = TestClient(create_app(db))
    first = client.get("/api/today/next").json()
    assert (first["done"], first["kind"]) == (False, "puzzle") and first["played"] == "Nf3"
    assert first["progress"] == {"done": 0, "total": 2, "again": 0, "new": 2, "minutes": 1}

    client.post("/api/drill/answer", json={"id": first["id"], "uci": "e2e4"})  # right
    second = client.get("/api/today/next").json()
    assert second["id"] != first["id"] and second["progress"]["done"] == 1

    client.post("/api/drill/answer", json={"id": second["id"], "uci": "?"})  # a miss
    done = client.get("/api/today/next").json()
    assert done["done"] and done["progress"]["done"] == 2 and done["puzzles"] == 2
    # A first solve comes back after a day too (SM-2), and no new ones are left
    assert done["tomorrow"] == {"again": 2, "new": 0}
    assert done["days"] == {"practised": 1, "of": 7}
    assert done["first"] == {"right": 1, "tried": 2} and done["reviews"] == {"right": 0, "tried": 0}


def test_today_works_on_an_empty_database(tmp_path: Path) -> None:
    done = TestClient(create_app(tmp_path / "c.db")).get("/api/today/next").json()
    assert done["done"] and done["puzzles"] == 0 and done["progress"]["total"] == 0
