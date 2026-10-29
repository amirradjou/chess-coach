import pytest

from chess_coach.coach import narrate
from chess_coach.coach.digest import build

from .test_patterns import planted


def test_sentences_citing_moves_outside_the_evidence_are_dropped():
    allowed = {"17.Qh5", "17.Nf3"}
    text = (
        "In the endgame you rush. Look again at 17.Qh5, where 17. Nf3 was the move.\n\n"
        "Also 31.Rxe8+ lost the game. Practise checking captures."
    )
    kept, dropped = narrate.check(text, allowed)
    assert "17.Qh5" in kept and "Rxe8" not in kept and "Practise checking captures." in kept
    assert dropped == 1


def test_numbers_that_are_not_moves_are_left_alone():
    kept, dropped = narrate.check("You blunder 2.4× more, on 15.0% of moves.", set())
    assert dropped == 0 and "2.4×" in kept


def test_narrate_appends_a_checked_section(monkeypatch):
    games, moves = planted()
    d = build(games, moves)
    seen = {}

    def fake_complete(model, prompt, timeout=600):
        seen["prompt"] = prompt
        ev = d.move_patterns[0].evidence[0]
        return f"Your clock costs you. Look at {ev.move}. Also 99.Kh1 was bad."

    monkeypatch.setattr(narrate, "complete", fake_complete)
    section = narrate.narrate(d, narrate.model_for("ollama"))
    assert section.startswith("## In plain words")
    assert "99.Kh1" not in section and "1 sentence(s)" in section
    assert "Baseline: mistakes or blunders on 15.0%" in seen["prompt"]


def test_model_presets():
    assert narrate.model_for("ollama").model == "qwen2.5:7b"
    with pytest.raises(ValueError):
        narrate.model_for("openrouter")  # needs a model
    with pytest.raises(ValueError):
        narrate.model_for("nope")
