"""Turn the user's worst moves into puzzles, keeping only positions with one clearly best move.

A candidate is a move of the user's that lost at least 20 points of win probability. The position
before it is searched again, deeper, with three lines: it becomes a puzzle only if the best move is
the only mate, or beats the second-best by at least 150 centipawns, so there is one answer to find.
A position already drilled with the same answer is never stored twice (`Stored`). A position is
its EPD (`openings.epd`): the FEN's move counters differ when another move order reached it.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass

import chess

from chess_coach.db import Store
from chess_coach.engine.stockfish import Engine
from chess_coach.models import Game, MoveFact, Puzzle
from chess_coach.openings import epd
from chess_coach.patterns.features import counted, rushed, rushed_rows
from chess_coach.patterns.mining import repeated_under_pressure

MIN_WP_LOSS = 20.0
MIN_GAP_CP = 150
VERIFY_DEPTH = 18


@dataclass(slots=True)
class MakeResult:
    checked: int = 0
    made: int = 0


def candidates(
    games: Iterable[Game],
    moves: Iterable[MoveFact],
    skip: set[str],
    repeated: frozenset[str] = frozenset(),
) -> list[MoveFact]:
    """The user's costly moves not checked yet: newest games first, worst move first in a game."""
    by_id = {g.id: g for g in games}
    order = {g.id: (g.played_at.isoformat() if g.played_at else "") for g in by_id.values()}
    picked = [
        m
        for m in moves
        if m.is_user
        and m.wp_loss >= MIN_WP_LOSS
        and m.game_id in order
        and f"{m.game_id}:{m.ply}" not in skip
        and keep(m, by_id[m.game_id], repeated)
    ]
    return sorted(picked, key=lambda m: (order[m.game_id], m.wp_loss), reverse=True)


def keep(m: MoveFact, game: Game, repeated: frozenset[str]) -> bool:
    """Never a bullet move after move 10; a mistake made short on time only when it's one you
    keep repeating."""
    if not counted(m, game):
        return False
    return not rushed(m, game) or any(tag in repeated for tag in m.motifs)


def repeated_tags(games: Iterable[Game], moves: Iterable[MoveFact]) -> frozenset[str]:
    return frozenset(m.tag for m in repeated_under_pressure(rushed_rows(games, moves)))


def verify(engine: Engine, m: MoveFact, depth: int = VERIFY_DEPTH) -> Puzzle | None:
    """The puzzle for this move, or None when the position has no single clear answer."""
    board = chess.Board(m.fen_before)
    if board.legal_moves.count() < 2:
        return None
    lines = engine.evaluate(board, depth=depth, multipv=3)
    best = lines[0]
    if best.move is None or best.move.uci() == m.uci:
        return None
    second = lines[1] if len(lines) > 1 else None
    only_mate = (
        best.mate is not None
        and best.mate > 0
        and (second is None or second.mate is None or second.mate <= 0)
    )
    gap = best.cp - second.cp if second is not None else MIN_GAP_CP
    if not only_mate and gap < MIN_GAP_CP:
        return None
    tags = [*m.motifs, m.phase.value]
    if only_mate:
        tags.append("mate")
    return Puzzle(
        id=f"{m.game_id}:{m.ply}",
        game_id=m.game_id,
        ply=m.ply,
        fen=m.fen_before,
        solution=best.move.uci(),
        solution_san=board.san(best.move),
        played_san=m.san,
        line=" ".join(mv.uci() for mv in best.pv[1:8]),
        gap_cp=gap,
        depth=depth,
        tags=sorted(set(tags)),
    )


FIX_TAG = "opening-fix"


def merged_tags(kept: Sequence[str], other: Sequence[str]) -> list[str]:
    """`kept` and then the tags of `other` it doesn't have. "opening-fix" is a kind of drill, not
    a theme (it changes the prompt and which moves count), so it is never added to a puzzle that
    isn't one."""
    return list(dict.fromkeys([*kept, *(t for t in other if t != FIX_TAG)]))


class Stored:
    """The stored puzzles by position (EPD) and answer, so the same drill is never stored twice,
    whatever move order led to it: a copy only adds its tags to the puzzle already there."""

    def __init__(self, store: Store) -> None:
        self.store = store
        self.by_answer: dict[tuple[str, str], Puzzle] = {}
        for p in store.puzzles():  # oldest first: with copies already stored, the oldest stands
            self.by_answer.setdefault((epd(p.fen), p.solution), p)

    def add(self, puzzle: Puzzle) -> bool:
        """Store the puzzle; False when this position already has one with the same answer."""
        key = (epd(puzzle.fen), puzzle.solution)
        same = self.by_answer.get(key)
        if same is not None:
            self.tag(same, puzzle.tags)
            return False
        self.store.add_puzzle(puzzle)
        self.by_answer[key] = puzzle
        return True

    def tag(self, puzzle: Puzzle, tags: Sequence[str]) -> None:
        merged = merged_tags(puzzle.tags, tags)
        if merged != puzzle.tags:
            puzzle.tags = merged
            self.store.set_puzzle_tags(puzzle.id, merged)

    def at(self, fen: str, wrong: str) -> Puzzle | None:
        """A stored drill for this position whose answer isn't `wrong` (UCI)."""
        where = epd(fen)
        return next(
            (p for (at, _), p in self.by_answer.items() if at == where and not p.is_right(wrong)),
            None,
        )


def make(
    store: Store,
    engine: Engine,
    *,
    depth: int = VERIFY_DEPTH,
    limit: int = 50,
    on_check: Callable[[int, int], None] | None = None,
) -> MakeResult:
    """Check up to `limit` new candidates and store the ones that make good puzzles (a slip you
    repeat from the same position is one puzzle, not one per game)."""
    games = [g for g in store.iter_games() if g.analyzed_depth]
    moves = list(store.iter_moves(user_only=True))
    todo = candidates(games, moves, store.checked_moves(), repeated_tags(games, moves))[:limit]
    stored = Stored(store)
    result = MakeResult()
    for i, m in enumerate(todo, 1):
        puzzle = verify(engine, m, depth)
        if puzzle is not None and stored.add(puzzle):
            result.made += 1
        store.record_check(f"{m.game_id}:{m.ply}", puzzle is not None)
        result.checked += 1
        if on_check:
            on_check(i, len(todo))
    return result


FIX_MARGIN_CP = 30  # opening fixes: any move this close to the best also counts


def verify_fix(engine: Engine, fix, family: str, depth: int = VERIFY_DEPTH) -> Puzzle | None:
    """A drill for an opening fix: find a better move than your habitual one. Every move within
    30 cp of the best counts; None when, searched deeper, your habit is fine after all."""
    board = chess.Board(fix.fen)
    lines = engine.evaluate(board, depth=depth, multipv=4)
    best = lines[0]
    if best.move is None:
        return None
    good = [ln.move for ln in lines if ln.move is not None and best.cp - ln.cp <= FIX_MARGIN_CP]
    habit = chess.Move.from_uci(fix.played_uci)
    if habit in good:
        return None
    game_id, ply, _ = fix.games[0]
    return Puzzle(
        id=f"{game_id}:{ply}",
        game_id=game_id,
        ply=ply,
        fen=fix.fen,
        solution=best.move.uci(),
        solution_san=board.san(best.move),
        played_san=fix.played_san,
        line=" ".join(mv.uci() for mv in best.pv[1:8]),
        gap_cp=round(fix.avg_cp_loss),
        depth=depth,
        tags=[FIX_TAG, family],
        accept=[m.uci() for m in good if m != best.move],
    )


def fix_key(fen: str, uci: str) -> str:
    """An opening fix's check key: the position and your habitual move."""
    return f"fix:{epd(fen)}:{uci}"


def checked_keys(store: Store) -> set[str]:
    """The checked keys. A fix key from before fixes were keyed by position holds the whole FEN:
    it is read as its position's key, so that fix isn't checked again."""
    out = set()
    for key in store.checked_moves():
        if key.startswith("fix:"):
            fen, _, uci = key.removeprefix("fix:").rpartition(":")  # a FEN has no colon
            key = fix_key(fen, uci)
        out.add(key)
    return out


def make_opening_fixes(store: Store, engine: Engine, *, depth: int = VERIFY_DEPTH) -> MakeResult:
    """Drills for the fixes in your favourite openings (see coach.repertoire), checked once each."""
    from chess_coach.coach.repertoire import favorites

    games = list(store.iter_games())
    moves = list(store.iter_moves(user_only=True))
    done = checked_keys(store)
    stored = Stored(store)
    result = MakeResult()
    for favs in favorites(games, moves).values():
        for fav in favs:
            for fix in fav.fixes:
                key = fix_key(fix.fen, fix.played_uci)
                if key in done:
                    # Checked already, maybe for another opening: its drill counts for this one too
                    if (drill := stored.at(fix.fen, fix.played_uci)) is not None:
                        stored.tag(drill, [fav.family])
                    continue
                puzzle = verify_fix(engine, fix, fav.family, depth)
                if puzzle is not None and stored.add(puzzle):
                    result.made += 1
                store.record_check(key, puzzle is not None)
                # Two openings can share a fix (Modern and Pirc 3...Bg7), even by different move
                # orders (the Scotch and the Danish at 7.O-O): check it once.
                done.add(key)
                result.checked += 1
    return result


@dataclass(slots=True)
class Merge:
    """A position (EPD) and answer stored more than once, and what `dedupe` did with its
    copies."""

    kept: list[Puzzle]  # one, or every copy you've tried when two or more have attempts
    removed: list[str]
    added: list[str]  # the tags the first kept copy took over
    tried: int  # how many copies have attempts


def dedupe(store: Store) -> list[Merge]:
    """Merge puzzles with the same position and answer, whatever move order led to it: keep the
    copy you've tried, otherwise the oldest, give it the other copies' tags and delete them. A
    puzzle with attempts is never deleted: when two or more copies have them, they are all
    kept."""
    tried = {pid for pid, _, _ in store.attempts()}
    groups: dict[tuple[str, str], list[Puzzle]] = {}
    for p in store.puzzles():  # oldest first
        groups.setdefault((epd(p.fen), p.solution), []).append(p)
    merges = []
    for copies in groups.values():
        if len(copies) < 2:
            continue
        kept = [p for p in copies if p.id in tried] or copies[:1]
        gone = [p for p in copies if p.id not in tried and p is not kept[0]]
        target, before = kept[0], kept[0].tags
        tags = before
        for p in gone:
            tags = merged_tags(tags, p.tags)
        if tags != before:
            store.set_puzzle_tags(target.id, tags)
            target.tags = tags
        for p in gone:
            store.delete_puzzle(p.id)
        added = [t for t in tags if t not in before]
        merges.append(Merge(kept, [p.id for p in gone], added, sum(p.id in tried for p in copies)))
    return merges


def prune(store: Store) -> list[str]:
    """Remove puzzles never attempted that `keep` would no longer make: from bullet after move
    10, or from a move made short on time and not repeated often enough. Returns the removed
    ids."""
    games = {g.id: g for g in store.iter_games()}
    moves = {(m.game_id, m.ply): m for m in store.iter_moves(user_only=True)}
    repeated = repeated_tags(games.values(), moves.values())
    tried = {pid for pid, _, _ in store.attempts()}
    removed = []
    for p in store.puzzles():
        m = moves.get((p.game_id, p.ply))
        if p.id in tried or FIX_TAG in p.tags or m is None or p.game_id not in games:
            continue
        if not keep(m, games[p.game_id], repeated):
            store.delete_puzzle(p.id)
            removed.append(p.id)
    return removed
