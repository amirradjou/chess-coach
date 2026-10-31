from datetime import UTC, date, datetime, timedelta

from chess_coach.coach import training
from chess_coach.coach import tree as optree
from chess_coach.db import Store
from chess_coach.models import CoachCard

from .test_opening_tree import NXC3, _client, games_and_facts, no_engine

TODAY = date(2026, 10, 31)
NF6 = [*NXC3, "g8f6"]  # their 4...Nf6: you're to move, and 5.e5 punishes it
Q = {"family": "Italian Game", "color": "white"}


def _today() -> date:
    return datetime.now(UTC).date()  # the app schedules by the UTC day


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
        assert store.coach_answered(card.id, now.date().isoformat())
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
