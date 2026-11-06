from datetime import UTC, date, datetime, timedelta

import chess
from typer.testing import CliRunner

from chess_coach.cli import app as cli_app
from chess_coach.coach import training
from chess_coach.coach import tree as optree
from chess_coach.db import Store
from chess_coach.engine.stockfish import LineEval
from chess_coach.models import CoachCard
from chess_coach.util import practice_day

from .test_opening_tree import NXC3, _client, games_and_facts, no_engine

TODAY = date(2026, 10, 31)
NF6 = [*NXC3, "g8f6"]  # their 4...Nf6: you're to move, and 5.e5 punishes it
Q = {"family": "Italian Game", "color": "white"}


def _today() -> date:
    return practice_day()


def test_an_answer_schedules_the_card():
    right = training.answer(None, "Danish", "white", NF6, "punish", True, TODAY)
    assert (right.kind, right.reps, right.due) == ("punish", 1, "2026-11-01")
    again = training.answer(right, "Danish", "white", NF6, "punish", True, TODAY)
    assert (again.reps, again.interval) == (2, 6)
    missed = training.answer(again, "Danish", "white", NF6, "punish", False, TODAY)
    assert (missed.reps, missed.lapses, missed.due) == (0, 1, "2026-11-01")
    assert right.id == "white|Danish|" + ",".join(NF6)


def test_the_deck_puts_due_cards_first_then_new_ones():
    edge = optree.mistakes(optree.build(*games_and_facts()))[0][1]
    mistakes = [(["a"], edge), (["b"], edge), (["c"], edge), (["d"], edge)]

    def card(path, due):
        return CoachCard("Danish", "white", [path], "punish", due)

    cards = {
        c.id: c for c in (card("b", "2026-10-30"), card("c", "2026-11-05"), card("d", "2026-10-29"))
    }
    last = {card(path, "").id: path != "c" for path in "bcd"}  # c was missed last time
    deck = training.deck(mistakes, "Danish", "white", cards, last, "2026-10-31")
    assert [(d.path[0], d.state) for d in deck] == [
        ("d", "due"),  # most overdue first
        ("b", "due"),
        ("a", "new"),
        ("c", "later"),
    ]
    s = training.summary(deck, sorted(cards.values(), key=lambda c: c.due), "2026-10-31")
    assert (s.found, s.total, s.due, s.lines, s.due_paths) == (2, 4, 2, 0, [["d"], ["b"]])


def test_cards_and_answers_are_stored(tmp_path):
    now = datetime.now(UTC)
    with Store(tmp_path / "c.db") as store:
        card = training.answer(None, "Danish", "white", NF6, "punish", False, now.date())
        store.save_coach_attempt(card, "g1f3", False, now)
        later = training.answer(card, "Danish", "white", NF6, "punish", True, now.date())
        store.save_coach_attempt(later, "e4e5", True, now + timedelta(seconds=1))
        assert store.coach_card(card.id) == later
        assert store.coach_cards("Danish", "white") == [later]
        assert store.coach_cards("Danish", "black") == []
        assert store.coach_last_answers("Danish", "white") == {card.id: True}
        assert store.coach_answered(card.id, practice_day(now).isoformat())
        assert not store.coach_answered(card.id, "2020-01-01")


def test_training_answers_are_remembered(tmp_path):
    client = _client(tmp_path, no_engine)
    tomorrow = (_today() + timedelta(days=1)).isoformat()
    r = client.post("/api/coach/answer", json={**Q, "path": NF6, "uci": "e4e5", "correct": True})
    got = r.json()
    assert (got["recorded"], got["kind"], got["next_due"]) == (True, "punish", tomorrow)
    assert (got["found"], got["total"], got["due"], got["lines"]) == (1, 1, 0, 0)
    (card,) = client.get("/api/coach/punish", params=Q).json()
    assert (card["state"], card["due"], card["found"]) == ("later", tomorrow, True)
    # One answer a day counts: going over the line again changes nothing.
    again = {**Q, "path": NF6, "uci": "g1f3", "correct": False}
    assert client.post("/api/coach/answer", json=again).json()["recorded"] is False
    assert client.get("/api/coach/cards", params=Q).json()["found"] == 1
    # Your own move on the line is a line card; showing the move counts as a miss.
    start = client.post("/api/coach/answer", json={**Q, "path": [], "uci": "?", "correct": False})
    assert (start.json()["kind"], start.json()["lines"]) == ("line", 1)


def test_bad_training_answers_are_refused(tmp_path):
    client = _client(tmp_path, no_engine)
    theirs = {**Q, "path": NXC3, "uci": "g8f6", "correct": True}  # Black to move: not yours
    assert client.post("/api/coach/answer", json=theirs).status_code == 400
    illegal = {**Q, "path": ["e2e5"], "uci": "?", "correct": True}
    assert client.post("/api/coach/answer", json=illegal).status_code == 400
    red = {**Q, "color": "red", "path": [], "uci": "e2e4", "correct": True}
    assert client.post("/api/coach/answer", json=red).status_code == 400


def test_due_cards_come_first_and_are_counted(tmp_path):
    client = _client(tmp_path, no_engine)
    yesterday = _today() - timedelta(days=1)
    card = training.answer(
        None, "Italian Game", "white", NF6, "punish", False, yesterday - timedelta(days=1)
    )
    with Store(tmp_path / "coach.db") as store:
        store.save_coach_attempt(card, "g1f3", False, datetime.now(UTC) - timedelta(days=2))
    assert card.due == yesterday.isoformat()
    assert client.get("/api/coach/openings").json()["white"][0]["due"] == 1
    cards = client.get("/api/coach/cards", params=Q).json()
    assert (cards["found"], cards["due"], cards["due_paths"]) == (0, 1, [NF6])
    (deck_card,) = client.get("/api/coach/punish", params=Q).json()
    assert (deck_card["state"], deck_card["found"]) == ("due", False)


def _due_card(db, path=NF6, kind="punish"):
    """A card you answered and missed long ago, due today."""
    card = CoachCard("Italian Game", "white", list(path), kind, "2026-01-01")
    with Store(db) as store:
        store.save_coach_attempt(card, "g1f3", False, datetime(2026, 1, 1, 12, tzinfo=UTC))
    return card


def test_today_serves_a_due_opening_card_without_naming_the_move(tmp_path):
    client = _client(tmp_path, no_engine)
    card = _due_card(tmp_path / "coach.db")
    got = client.get("/api/today/next").json()
    assert (got["done"], got["kind"], got["id"], got["punish"]) == (False, "card", card.id, True)
    assert (got["side"], got["opening"], got["last"]) == ("white", "Italian Game", "g8f6")
    assert got["line"].startswith("1. e4 e5") and "e5" not in got["line"].split()[-1:]
    assert "target" not in got and "solution" not in got


def test_answering_a_due_card_in_today_records_it_once(tmp_path):
    client = _client(tmp_path, no_engine)
    card = _due_card(tmp_path / "coach.db")
    assert client.post("/api/today/card", json={"id": "nope", "uci": "e4e5"}).status_code == 404
    assert client.post("/api/today/card", json={"id": card.id, "uci": "a1a8"}).status_code == 400

    # Without an engine the stored best move decides: 5.e5, the punishment
    r = client.post("/api/today/card", json={"id": card.id, "uci": "e4e5"}).json()
    assert r["correct"] and r["answer"] == "e5" and r["target"]["san"] == "e5"
    assert r["next_due"] == (_today() + timedelta(days=1)).isoformat()
    with Store(tmp_path / "coach.db") as store:
        assert len(store.coach_attempts()) == 2  # January's miss and today's answer
    after = client.get("/api/today/next").json()
    assert after["done"] and after["progress"]["done"] == 1

    # A second answer the same day changes nothing
    client.post("/api/today/card", json={"id": card.id, "uci": "?"})
    with Store(tmp_path / "coach.db") as store:
        assert len(store.coach_attempts()) == 2


def test_asking_to_see_a_cards_move_is_a_miss_that_shows_it(tmp_path):
    client = _client(tmp_path, no_engine)
    card = _due_card(tmp_path / "coach.db")
    r = client.post("/api/today/card", json={"id": card.id, "uci": "?"}).json()
    assert not r["correct"] and r["answer"] is None and r["target"]["san"] == "e5"
    assert r["next_due"] == (_today() + timedelta(days=1)).isoformat()


def test_on_your_own_line_your_usual_sound_move_is_right(tmp_path):
    root = optree.build(*games_and_facts())
    path = NXC3[:0]  # the start: your 1.e4 is your move in every game
    node = optree.walk(root, path)
    board = chess.Board()
    played = chess.Move.from_uci("e2e4")
    judged = training.judge(training.LINE, node, board, played, lambda fen: None)
    assert judged.correct and judged.target == played and judged.check is None
    assert training.kind_at(root, path) == training.LINE
    assert training.kind_at(root, NF6) == training.PUNISH


def test_check_move_uses_the_engine_within_its_margin_and_the_stored_move_without():
    board = chess.Board()
    e4, a3 = chess.Move.from_uci("e2e4"), chess.Move.from_uci("a2a3")
    answers = {board.fen(): LineEval(30, None, e4)}

    def after(cp):
        def analyse(fen):
            return answers.get(fen) or LineEval(cp, None, None)

        return analyse

    # You keep 10 of the 30: within the margin. You fall to -30: out of it.
    assert training.check_move(board, a3, after(-10)).ok
    assert not training.check_move(board, a3, after(30)).ok
    assert training.check_move(board, e4, after(30)).ok  # the engine's move always stands
    assert not training.check_move(board, a3, lambda fen: None, e4).ok
    assert training.check_move(board, e4, lambda fen: None, e4).ok
    assert training.check_move(board, a3, lambda fen: None).ok  # nothing to compare it with


class _NoEngine:
    def __init__(self, _start):
        pass

    def __call__(self, fen):
        return None

    def close(self):
        pass


def test_today_in_the_terminal_asks_a_due_card(tmp_path, monkeypatch):
    _client(tmp_path, no_engine)  # stores the games
    db = tmp_path / "coach.db"
    card = _due_card(db)
    monkeypatch.setattr("chess_coach.web.app.Analyst", _NoEngine)
    result = CliRunner().invoke(cli_app, ["--db", str(db), "today", "--cli"], input="e5\n")
    assert result.exit_code == 0, result.output
    assert "Punish it." in result.output and "e5: good." in result.output
    with Store(db) as store:
        assert store.coach_answered(card.id, _today().isoformat())
        saved = store.coach_card(card.id)
    assert saved is not None and saved.due == (_today() + timedelta(days=1)).isoformat()
