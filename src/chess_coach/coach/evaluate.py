"""The brief's second metric: how accurate are the model's summaries?

The automatic check (narrate.check) only drops sentences that cite moves outside the evidence;
whether a sentence is true to the facts takes a reader. So: the model summarises several digests
(all games, then the last year, 6, 3 and 1 month), every sentence goes into a sheet next to the
facts the model was given, a person grades the sentences the check kept (y: the facts support
it, n: they don't, ?: can't tell), and the score is y / (y + n).
"""

from __future__ import annotations

import csv
from collections.abc import Callable, Sequence
from dataclasses import astuple, dataclass, fields
from datetime import datetime
from pathlib import Path

from chess_coach.coach.digest import build
from chess_coach.coach.narrate import MOVE, allowed_moves, cites_evidence, facts, sentences
from chess_coach.models import Game, MoveFact
from chess_coach.util import parse_since

SLICES = ("all", "1y", "6m", "3m", "1m")  # the digests summarised
RIGHT, WRONG, UNSURE = ("y", "yes"), ("n", "no"), ("?",)


@dataclass(slots=True)
class Row:
    """One sentence the model wrote, to grade."""

    sample: str  # the digest it summarised: "all", "1y", ...
    sentence: int
    text: str
    moves: str  # the moves it cites, space-separated
    check: str  # "kept" or "dropped" by the automatic check
    grade: str = ""  # filled in by hand: y, n or ?
    note: str = ""


COLUMNS = [f.name for f in fields(Row)]


def rows_for(sample: str, text: str, allowed: set[str]) -> list[Row]:
    """The sentences of one summary, numbered, each marked as the automatic check sees it."""
    found = [s for paragraph in text.split("\n\n") for s in sentences(paragraph) if s]
    return [
        Row(
            sample,
            i,
            s,
            " ".join(MOVE.findall(s)),
            "kept" if cites_evidence(s, allowed) else "dropped",
        )
        for i, s in enumerate(found, 1)
    ]


def build_sheet(
    games: Sequence[Game],
    moves: Sequence[MoveFact],
    complete: Callable[[str], str],
    *,
    now: datetime,
    slices: Sequence[str] = SLICES,
) -> tuple[list[Row], list[tuple[str, str]]]:
    """The rows to grade, and (sample, facts given) for every slice with something to explain."""
    rows: list[Row] = []
    given: list[tuple[str, str]] = []
    for name in slices:
        since = None if name == "all" else parse_since(name, now=now)
        d = build(games, moves, since=since)
        if not d.move_patterns and not d.motifs:
            continue
        prompt = facts(d)
        rows += rows_for(name, complete(prompt), allowed_moves(d))
        given.append((name, prompt))
    return rows, given


def write_sheet(path: Path, rows: Sequence[Row]) -> None:
    with path.open("w", newline="") as f:
        out = csv.writer(f)
        out.writerow(COLUMNS)
        out.writerows(astuple(r) for r in rows)


def read_sheet(path: Path) -> list[Row]:
    with path.open(newline="") as f:
        return [Row(**{**r, "sentence": int(r["sentence"])}) for r in csv.DictReader(f)]


def write_facts(path: Path, given: Sequence[tuple[str, str]]) -> None:
    parts = [f"## {name}\n\n```\n{text}\n```\n" for name, text in given]
    path.write_text("# The facts each summary was given\n\n" + "\n".join(parts))


@dataclass(slots=True)
class Score:
    sample: str
    right: int = 0
    wrong: int = 0
    unsure: int = 0
    ungraded: int = 0
    dropped: int = 0  # by the automatic check: not graded

    @property
    def accuracy(self) -> float | None:
        graded = self.right + self.wrong
        return self.right / graded if graded else None


def score(rows: Sequence[Row]) -> tuple[Score, list[Score]]:
    """The score over every sample, and per sample in sheet order."""
    overall = Score("all samples")
    per: dict[str, Score] = {}
    for r in rows:
        grade = r.grade.strip().lower()
        for s in (overall, per.setdefault(r.sample, Score(r.sample))):
            if r.check == "dropped":
                s.dropped += 1
            elif grade in RIGHT:
                s.right += 1
            elif grade in WRONG:
                s.wrong += 1
            elif grade in UNSURE:
                s.unsure += 1
            else:
                s.ungraded += 1
    return overall, list(per.values())
