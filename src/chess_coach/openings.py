"""Opening names from the vendored lichess-org/chess-openings table, matched by position.

Matching on the position after each ply (not the move order) means transpositions get the
same name Lichess would give them. The deepest matching position wins.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources

import chess
import chess.pgn

MAX_OPENING_PLIES = 40


@dataclass(frozen=True, slots=True)
class Opening:
    eco: str
    name: str

    @property
    def family(self) -> str:
        """'Italian Game' for 'Italian Game: Two Knights Defense, Fried Liver Attack'."""
        return self.name.split(":", 1)[0].strip()


def epd(fen: str) -> str:
    """The FEN without its move counters (halfmove clock, move number), as the table keys it:
    the same position however the game got there, so a transposition counts as the same."""
    return " ".join(fen.split()[:4])


def _epd_of(pgn_moves: str) -> str:
    board = chess.Board()
    for token in pgn_moves.split():
        if token[0].isdigit():
            continue
        board.push_san(token)
    return board.epd()


@lru_cache(maxsize=1)
def opening_table() -> dict[str, Opening]:
    """EPD -> Opening for every line in the table; built once per process (~4k lines)."""
    table: dict[str, Opening] = {}
    text = resources.files("chess_coach.data").joinpath("openings.tsv").read_text(encoding="utf-8")
    for line in text.splitlines():
        if not line or line.startswith("#") or line.startswith("eco\t"):
            continue
        eco, name, pgn = line.split("\t", 2)
        table[_epd_of(pgn)] = Opening(eco=eco, name=name)
    return table


@lru_cache(maxsize=1)
def opening_families() -> frozenset[str]:
    """Every family the table names ("Italian Game", "Danish Gambit Accepted")."""
    return frozenset(o.family for o in opening_table().values())


def classify_board_sequence(moves: list[chess.Move]) -> Opening | None:
    table = opening_table()
    board = chess.Board()
    found: Opening | None = None
    for move in moves[:MAX_OPENING_PLIES]:
        board.push(move)
        if (hit := table.get(board.epd())) is not None:
            found = hit
    return found


def classify_pgn(pgn_text: str) -> Opening | None:
    game = chess.pgn.read_game(io.StringIO(pgn_text))
    if game is None:
        return None
    return classify_board_sequence(list(game.mainline_moves()))


def family_of(name: str | None) -> str | None:
    return Opening(eco="", name=name).family if name else None


@lru_cache(maxsize=1)
def _lines_by_name() -> dict[str, str]:
    text = resources.files("chess_coach.data").joinpath("openings.tsv").read_text(encoding="utf-8")
    out: dict[str, str] = {}
    for line in text.splitlines():
        if not line or line.startswith("#") or line.startswith("eco\t"):
            continue
        _, name, pgn = line.split("\t", 2)
        out.setdefault(name, pgn)
    return out


def book_line(name: str) -> str | None:
    """The table's move sequence for an exact opening name, e.g. '1. e4 e5 2. Nf3 Nc6 3. Bc4'."""
    return _lines_by_name().get(name)
