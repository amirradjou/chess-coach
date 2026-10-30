"""Your favourite openings and how to improve them.

For each colour: the openings you play most, how you score, the line you usually play, and the
fixes: positions you reached in more than one game where your habitual move loses ground (by the
engine's numbers) and what it prefers instead.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field

import chess

from chess_coach.models import Game, MoveFact
from chess_coach.openings import book_line, opening_table
from chess_coach.patterns.features import ERRORS, counted, result_of

OPENING_MOVES = 15  # look for fixes up to this move number
MIN_CP_LOSS = 50  # an average loss this big makes a habitual move worth fixing


@dataclass(slots=True)
class Fix:
    fen: str
    color: str
    move_number: int
    played_san: str
    played_uci: str
    best_san: str
    best_uci: str
    times: int
    avg_cp_loss: float
    games: list[tuple[str, int, str | None]]  # (game id, ply, url at the move)

    @property
    def move(self) -> str:
        return f"{self.move_number}{'.' if self.color == 'white' else '...'}{self.played_san}"


@dataclass(slots=True)
class Favorite:
    family: str
    color: str
    games: int
    wins: int
    draws: int
    losses: int
    names: list[tuple[str, int]]
    line: str  # the moves you play most often, e.g. "1. e4 c5 2. Nf3 d6 3. d4"
    book: str | None
    opening_error_rate: float
    fixes: list[Fix] = field(default_factory=list)

    @property
    def score(self) -> float | None:
        """Over the games whose result counts; None when there are none (only bullet)."""
        decided = self.wins + self.draws + self.losses
        return (self.wins + self.draws / 2) / decided if decided else None


_COMMENT = re.compile(r"\{[^}]*\}|\([^)]*\)|;[^\n]*")
_NUMBER = re.compile(r"^\d+\.+")
_NOT_A_MOVE = re.compile(r"^(\$\d+|1-0|0-1|1/2-1/2|\*)$")


def _san_moves(pgn: str, plies: int) -> list[str]:
    """The first moves of a PGN as written (already SAN), without replaying the whole game."""
    body = "\n".join(line for line in pgn.splitlines() if not line.startswith("["))
    sans = []
    for token in _COMMENT.sub(" ", body).split():
        token = _NUMBER.sub("", token).rstrip("!?")  # "1.e4", "1...", "12." -> "e4", "", ""
        if token and not _NOT_A_MOVE.match(token):
            sans.append(token)
    return sans[:plies]


def _numbered(sans: Sequence[str]) -> str:
    parts = []
    for i, san in enumerate(sans):
        parts.append(f"{i // 2 + 1}. {san}" if i % 2 == 0 else san)
    return " ".join(parts)


def usual_line(games: Sequence[Game]) -> str:
    """The longest opening sequence (up to 10 plies) that most of these games share."""
    lines = [_san_moves(g.pgn, 10) for g in games]
    best: list[str] = []
    for length in range(1, 11):
        counts = Counter(tuple(line[:length]) for line in lines if len(line) >= length)
        if not counts:
            break
        seq, n = counts.most_common(1)[0]
        if n * 2 < len(games):  # fewer than half the games still follow it
            break
        best = list(seq)
    return _numbered(best)


def _url(game: Game, ply: int) -> str | None:
    if game.url and "lichess.org" in game.url:
        return f"{game.url}#{ply}"
    return game.url


def fixes(games: Sequence[Game], moves: Sequence[MoveFact], top: int = 5) -> list[Fix]:
    """Habitual opening moves (same position, same move, in 2+ games) that lose ground."""
    by_id = {g.id: g for g in games}
    groups: dict[tuple[str, str], list[MoveFact]] = defaultdict(list)
    for m in moves:
        g = by_id.get(m.game_id)
        if g is not None and m.is_user and m.move_number <= OPENING_MOVES and counted(m, g):
            groups[(m.fen_before, m.uci)].append(m)
    out: dict[tuple[str, str], Fix] = {}
    for (fen, uci), ms in groups.items():
        if len({m.game_id for m in ms}) < 2 or is_book(fen, uci):
            continue
        avg = sum(m.cp_loss for m in ms) / len(ms)
        best_uci, _ = Counter(m.best_move for m in ms).most_common(1)[0]
        if avg < MIN_CP_LOSS or best_uci == uci:
            continue
        first = ms[0]
        best_san = next(m.best_san for m in ms if m.best_move == best_uci)
        fix = Fix(
            fen=fen,
            color=first.color.value,
            move_number=first.move_number,
            played_san=first.san,
            played_uci=uci,
            best_san=best_san,
            best_uci=best_uci,
            times=len(ms),
            avg_cp_loss=avg,
            games=[(m.game_id, m.ply, _url(by_id[m.game_id], m.ply)) for m in ms][:5],
        )
        # The same move from slightly different positions reads as one habit: keep the bigger.
        key = (fix.move, fix.best_san)
        if key not in out or fix.times > out[key].times:
            out[key] = fix
    return sorted(out.values(), key=lambda f: (-f.times * f.avg_cp_loss, f.move_number))[:top]


def is_book(fen: str, uci: str) -> bool:
    """True when the move leads to a named position in the opening table: a theory choice, not a
    slip (1...g6 is the Modern Defense, not a mistake to fix)."""
    board = chess.Board(fen)
    move = chess.Move.from_uci(uci)
    if move not in board.legal_moves:
        return False
    board.push(move)
    return board.epd() in opening_table()


def favorites(
    games: Sequence[Game], moves: Sequence[MoveFact], min_games: int = 5, top: int = 4
) -> dict[str, list[Favorite]]:
    """Your most-played opening families for each colour, most games first."""
    analysed = [g for g in games if g.analyzed_depth and g.opening_family]
    by_key: dict[tuple[str, str], list[Game]] = defaultdict(list)
    for g in analysed:
        by_key[(g.opening_family, g.user_color.value)].append(g)
    by_id = {g.id: g for g in analysed}
    moves_by_game: dict[str, list[MoveFact]] = defaultdict(list)
    for m in moves:
        if m.is_user and m.game_id in by_id and counted(m, by_id[m.game_id]):
            moves_by_game[m.game_id].append(m)
    out: dict[str, list[Favorite]] = {"white": [], "black": []}
    for (family, color), gs in by_key.items():
        if len(gs) < min_games:
            continue
        results = Counter(result_of(g) for g in gs)
        names = Counter(g.opening for g in gs if g.opening).most_common(3)
        opening_moves = [m for g in gs for m in moves_by_game[g.id] if m.phase.value == "opening"]
        errors = sum(m.judgement in ERRORS for m in opening_moves)
        own_moves = [m for g in gs for m in moves_by_game[g.id]]
        out[color].append(
            Favorite(
                family=family,
                color=color,
                games=len(gs),
                wins=results["win"],
                draws=results["draw"],
                losses=results["loss"],
                names=names,
                line=usual_line(gs),
                book=book_line(names[0][0]) if names else None,
                opening_error_rate=errors / len(opening_moves) if opening_moves else 0.0,
                fixes=fixes(gs, own_moves),
            )
        )
    for color in out:
        out[color] = sorted(out[color], key=lambda f: (-f.games, f.family))[:top]
    return out
