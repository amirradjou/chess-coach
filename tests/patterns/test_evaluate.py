from datetime import UTC, datetime
from pathlib import Path

from typer.testing import CliRunner

from chess_coach.cli import app
from chess_coach.coach import narrate
from chess_coach.coach.digest import build
from chess_coach.coach.evaluate import Row, build_sheet, read_sheet, rows_for, score, write_sheet
from chess_coach.db import Store

from .test_patterns import planted

NOW = datetime(2026, 10, 31, tzinfo=UTC)  # the planted games are from October 2026


def test_every_sentence_is_numbered_and_checked():
    text = "You rush endgames. Look at 17.Qh5 again.\n\nAnd 31.Rxe8+ lost it."
    rows = rows_for("all", text, {"17.Qh5"})
    assert [(r.sentence, r.moves, r.check) for r in rows] == [
        (1, "", "kept"),
        (2, "17.Qh5", "kept"),
        (3, "31.Rxe8+", "dropped"),
    ]


def test_the_model_summarises_each_slice():
    games, moves = planted()
    ev = build(games, moves).move_patterns[0].evidence[0]
    prompts = []

    def complete(prompt):
        prompts.append(prompt)
        return f"Your clock costs you. Look at {ev.move}. Also 99.Kh1 was bad."

    rows, given = build_sheet(games, moves, complete, now=NOW)
    assert [name for name, _ in given] == ["all", "1y", "6m", "3m", "1m"]
    assert given[0][1] == prompts[0] and "Baseline" in prompts[0]
    assert [r.check for r in rows if r.sample == "all"] == ["kept", "kept", "dropped"]


def test_a_graded_sheet_is_scored(tmp_path: Path):
    rows = [
        Row("all", 1, "a", "", "kept", "y"),
        Row("all", 2, "b", "", "kept", "Yes"),
        Row("all", 3, "c", "", "kept", "n"),
        Row("all", 4, "d", "99.Kh1", "dropped"),
        Row("1m", 1, "e", "", "kept", "?"),
        Row("1m", 2, "f", "", "kept"),
    ]
    write_sheet(tmp_path / "s.csv", rows)
    assert read_sheet(tmp_path / "s.csv") == rows
    overall, (all_, month) = score(rows)
    assert (overall.right, overall.wrong, overall.unsure, overall.ungraded) == (2, 1, 1, 1)
    assert (overall.dropped, overall.accuracy) == (1, 2 / 3)
    assert (all_.accuracy, month.accuracy) == (2 / 3, None)


def test_the_eval_commands(tmp_path: Path, monkeypatch):
    db = tmp_path / "c.db"
    games, moves = planted()
    with Store(db) as store:
        for g in games:
            store.upsert_game(g)
            store.replace_moves(g.id, [m for m in moves if m.game_id == g.id], depth=12)
    monkeypatch.setattr(narrate, "complete", lambda model, prompt, timeout=600: "You rush.")
    out = tmp_path / "eval"
    made = CliRunner().invoke(app, ["--db", str(db), "eval", "narrate", "--out", str(out)])
    assert made.exit_code == 0, made.output
    (sheet,) = out.glob("*.csv")
    assert sheet.name.endswith("-qwen2.5-7b.csv") and list(out.glob("*-facts.md"))
    rows = read_sheet(sheet)
    write_sheet(sheet, [Row(r.sample, r.sentence, r.text, r.moves, r.check, "y") for r in rows])
    scored = CliRunner().invoke(app, ["eval", "score", str(sheet)])
    assert scored.exit_code == 0 and "100%" in scored.output
