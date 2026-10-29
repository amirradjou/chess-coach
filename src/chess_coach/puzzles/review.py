"""Lines for going over a puzzle: the best move, each move you tried and the move from the game,
each continued by the engine, with a plain reason taken from the material and the score.

Scores are from the solver's side (the side to move in the puzzle), so "+2.1" is good for you.
Without an engine (`analyse` returns None) the lines stop after the chosen moves and have no score.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

import chess

from chess_coach.coach.principles import explain
from chess_coach.engine.motifs import PIECE_VALUES
from chess_coach.engine.stockfish import LineEval
from chess_coach.models import Puzzle

LINE_PLIES = 8  # how much of the engine's continuation to show
SETTLE_PLIES = 12  # ...running on while captures and checks continue, so material is counted after

Analyse = Callable[[str], LineEval | None]  # FEN -> the engine's best line, None without an engine


@dataclass(slots=True)
class Ply:
    san: str
    uci: str
    fen: str  # after the move
    number: str  # "12." for White, "12..." for Black
    capture: bool
    check: bool
    engine: bool = False  # the engine's continuation, not a move someone chose

    @property
    def text(self) -> str:
        return f"{self.number}{self.san}"


@dataclass(slots=True)
class Score:
    """An engine score from the solver's side."""

    cp: int
    mate: int | None = None

    @property
    def text(self) -> str:
        """Short, as on Lichess: +2.1, #3 (you mate), #-3 (you get mated)."""
        if self.mate is None:
            return f"{self.cp / 100:+.1f}"
        if self.mate == 0:
            return "#" if self.cp > 0 else "#-"
        return f"#{self.mate}"

    @property
    def words(self) -> str:
        if self.mate is None:
            return f"{self.text} for you"
        if self.mate > 0:
            return f"mate in {self.mate} for you"
        if self.mate < 0:
            return f"mate in {-self.mate} for them"
        return "checkmate" if self.cp > 0 else "checkmate against you"


@dataclass(slots=True)
class Line:
    kind: str  # "best", "tried", "game" or "analysis"
    label: str
    start: str  # FEN before the first move
    plies: list[Ply] = field(default_factory=list)
    score: Score | None = None  # after the chosen moves, before the engine's continuation
    why: str = ""
    faults: list[str] = field(default_factory=list)  # in chess terms, see coach.principles
    ideas: list[str] = field(default_factory=list)  # what the better move does instead

    @property
    def chosen(self) -> list[Ply]:
        return [p for p in self.plies if not p.engine]


def legal(board: chess.Board, uci: str) -> chess.Move:
    """The move in this position; a pawn reaching the last rank becomes a queen unless told
    otherwise. Raises ValueError when it isn't a legal move here."""
    try:
        move = chess.Move.from_uci(uci)
    except ValueError as exc:
        raise ValueError(f"not a move: {uci}") from exc
    if move in board.legal_moves:
        return move
    promotion = chess.Move(move.from_square, move.to_square, chess.QUEEN)
    if promotion in board.legal_moves:
        return promotion
    raise ValueError(f"{uci} isn't legal here")


def _ply(board: chess.Board, move: chess.Move, engine: bool) -> Ply:
    """Describe `move` and play it on `board`."""
    number = f"{board.fullmove_number}{'.' if board.turn == chess.WHITE else '...'}"
    ply = Ply(
        san=board.san(move),
        uci=move.uci(),
        fen="",
        number=number,
        capture=board.is_capture(move),
        check=board.gives_check(move),
        engine=engine,
    )
    board.push(move)
    ply.fen = board.fen()
    return ply


def _continuation(board: chess.Board, pv: Sequence[chess.Move]) -> list[Ply]:
    """The engine's line from `board`, LINE_PLIES long, or longer while the captures and checks
    go on (up to SETTLE_PLIES). Stops at a move that doesn't fit the position."""
    scratch = board.copy(stack=False)
    out: list[Ply] = []
    for move in pv[:SETTLE_PLIES]:
        if len(out) >= LINE_PLIES and not (out[-1].capture or out[-1].check):
            break
        if move not in scratch.legal_moves:
            break
        out.append(_ply(scratch, move, engine=True))
    return out


def _score(ev: LineEval, board: chess.Board, solver: chess.Color) -> Score:
    sign = 1 if board.turn == solver else -1
    return Score(sign * ev.cp, None if ev.mate is None else sign * ev.mate)


def build(
    fen: str,
    moves: Sequence[str],
    analyse: Analyse,
    *,
    kind: str,
    label: str,
    continuation: Sequence[str] | None = None,
) -> Line:
    """`moves` from `fen`, then the engine's continuation (or `continuation`, stored UCI)."""
    board = chess.Board(fen)
    solver = board.turn
    plies = [_ply(board, legal(board, uci), engine=False) for uci in moves]
    ev = analyse(board.fen())
    if continuation is not None:
        pv = [chess.Move.from_uci(u) for u in continuation]
    else:
        pv = ev.pv if ev else []
    plies += _continuation(board, pv)
    score = _score(ev, board, solver) if ev else None
    return Line(kind=kind, label=label, start=fen, plies=plies, score=score)


def material(board: chess.Board, color: chess.Color) -> int:
    """`color`'s material minus the other side's, in the usual points (pawn 1 ... queen 9)."""
    return sum(
        PIECE_VALUES[p.piece_type] * (1 if p.color == color else -1)
        for p in board.piece_map().values()
    )


def material_change(line: Line) -> int:
    """What the whole line wins (+) or loses (-) for the solver, in points."""
    if not line.plies:
        return 0
    start = chess.Board(line.start)
    end = chess.Board(line.plies[-1].fen)
    return material(end, start.turn) - material(start, start.turn)


_AMOUNTS = {1: "a pawn", 2: "two pawns", 3: "a piece", 5: "a rook", 9: "the queen"}


def amount(points: int) -> str:
    return _AMOUNTS.get(points, f"{points} points of material")


def _material_sentence(line: Line) -> str:
    change = material_change(line)
    last = line.plies[-1].text
    if change > 0:
        return f"By {last} you have won {amount(change)}."
    if change < 0:
        return f"By {last} you have lost {amount(-change)}."
    return "Material stays level over this line."


def why_best(line: Line, puzzle: Puzzle | None = None) -> str:
    first = line.plies[0].san
    s = line.score
    if s is not None and s.mate == 0 and s.cp > 0:
        return f"{first} is checkmate."
    if s is not None and s.mate is not None and s.mate > 0:
        return f"{first} mates in {s.mate}: step through the line to see how."
    change = material_change(line)
    last = line.plies[-1].text
    if change > 0:
        parts = [f"{first} wins {amount(change)} by {last}."]
    elif change < 0:
        parts = [f"{first} gives up {amount(-change)} in this line, and the engine still wants it."]
    else:
        parts = [f"{first} doesn't win material in this line: what it gains is the position."]
    if puzzle is not None and puzzle.gap_cp > 0:
        if "opening-fix" in puzzle.tags:
            parts.append(
                f"Your usual {puzzle.played_san} costs {puzzle.gap_cp / 100:.1f} pawns on average."
            )
        elif puzzle.gap_cp >= 1000:
            parts.append("No other move comes close.")
        else:
            parts.append(f"It's {puzzle.gap_cp / 100:.1f} pawns better than any other move.")
    if s is not None:
        parts.append(f"The engine scores it {s.words}.")
    return " ".join(parts)


def why_not(line: Line, best: Score | None, best_san: str | None) -> str:
    """Why a move falls short: their answer, what it costs, and the score against the best's.
    With `best_san` None the best move isn't named, so the puzzle can still be tried again."""
    reply = next((p for p in line.plies if p.engine), None)
    s = line.score
    if s is None and reply is None:
        return "Stockfish isn't available here, so there is no line after this move."
    parts = []
    if reply is not None:
        if s is not None and s.mate is not None and s.mate < 0:
            parts.append(f"They answer {reply.text} and mate you in {-s.mate}.")
        else:
            parts.append(f"They answer {reply.text}.")
            parts.append(_material_sentence(line))
    if s is not None:
        against = ""
        if best is not None:
            against = (
                f", against {best.words} after {best_san}"
                if best_san
                else f", while a better move gets {best.words}"
            )
        parts.append(f"The engine scores it {s.words}{against}.")
    return " ".join(parts)


def why_analysis(line: Line) -> str:
    chosen = line.chosen
    if not chosen:
        return ""
    if line.score is None:
        return "Stockfish isn't available here, so there is no score."
    reply = next((p for p in line.plies if p.engine), None)
    text = f"After {chosen[-1].text}: {line.score.words}."
    return text + (f" The engine's move: {reply.text}." if reply else "")


def played_uci(puzzle: Puzzle) -> str | None:
    """The move from the game, or None when its SAN doesn't fit the position."""
    try:
        return chess.Board(puzzle.fen).parse_san(puzzle.played_san).uci()
    except ValueError:
        return None


def best_line(puzzle: Puzzle, analyse: Analyse) -> Line:
    line = build(
        puzzle.fen,
        [puzzle.solution],
        analyse,
        kind="best",
        label=f"Best: {puzzle.solution_san}",
        continuation=puzzle.line.split(),
    )
    line.why = why_best(line, puzzle)
    return line


def attempt(
    puzzle: Puzzle, uci: str, analyse: Analyse, *, best: Line | None = None, reveal: bool = True
) -> Line:
    """The line after a move you tried. `reveal=False` keeps the best move's name out of it."""
    board = chess.Board(puzzle.fen)
    move = legal(board, uci)
    san = board.san(move)
    label = f"Your move: {san} (also good)" if puzzle.is_right(move.uci()) else f"Your try: {san}"
    line = build(puzzle.fen, [move.uci()], analyse, kind="tried", label=label)
    best_score = best.score if best else _score_after(puzzle.fen, puzzle.solution, analyse)
    line.why = why_not(line, best_score, puzzle.solution_san if reveal else None)
    if not puzzle.is_right(move.uci()):
        _reasons(line, puzzle, move.uci(), reveal)
    return line


def _reasons(line: Line, puzzle: Puzzle, uci: str, reveal: bool) -> None:
    reasons = explain(puzzle.fen, uci, puzzle.solution)
    line.faults = reasons.faults
    line.ideas = reasons.ideas if reveal else []


def _score_after(fen: str, uci: str, analyse: Analyse) -> Score | None:
    board = chess.Board(fen)
    solver = board.turn
    board.push(legal(board, uci))
    ev = analyse(board.fen())
    return _score(ev, board, solver) if ev else None


def review(puzzle: Puzzle, tried: Sequence[str], analyse: Analyse) -> list[Line]:
    """The best line, then each different move you tried, then the game move if it's another."""
    best = best_line(puzzle, analyse)
    lines = [best]
    seen = {puzzle.solution}
    for uci in tried:
        move = legal(chess.Board(puzzle.fen), uci).uci()
        if move not in seen:
            seen.add(move)
            lines.append(attempt(puzzle, move, analyse, best=best))
    game = played_uci(puzzle)
    if game is not None and game not in seen:
        line = build(
            puzzle.fen, [game], analyse, kind="game", label=f"In the game: {puzzle.played_san}"
        )
        line.why = why_not(line, best.score, puzzle.solution_san)
        _reasons(line, puzzle, game, reveal=True)
        lines.append(line)
    return lines


def analysis(fen: str, moves: Sequence[str], analyse: Analyse) -> Line:
    """Your own moves from the puzzle position, with the engine's score and its continuation."""
    line = build(fen, moves, analyse, kind="analysis", label="Your analysis")
    line.why = why_analysis(line)
    return line
