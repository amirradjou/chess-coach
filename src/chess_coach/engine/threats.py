"""What was their threat? Two ways a mistake happens, told apart with a null-move search.

Before a counted mistake or blunder the mover passes (a null move) and the engine finds the
opponent's best line on that board: what they threaten. A second search after the move played
finds their best answer to it.

A line *wins* when it mates, or when the material it wins is at least THREAT_MATERIAL, counted
along the engine's line until the captures and checks stop (so pending recaptures count). The
evaluations can't say that on their own: a free move also lets them dodge whatever the mover had
pending, so with a shot of the mover's on the board passing costs a lot even when they threaten
nothing, and a move that only gives back the mover's own edge leaves them better off than passing.

- Their threat: their line on the null-move board wins, and the free move is worth THREAT_CP to
  them (the mover's evaluation before plus theirs after passing). It is new when they were not
  already THREAT_CP better off before their last move (the stored evaluation of that position,
  which has them to move like the null-move board), and it wasn't already there behind a check
  they had to answer first (the same piece, move and target).
- `ignored_threat`: a new threat the move didn't meet. A mate threat is met when they no longer
  mate; any other is not met when their best answer is the threat itself and still wins material,
  or another capture on its square that still wins THREAT_MATERIAL, counted from before the move
  (so whatever the move took first is paid back).
- `unsafe_move`: there was no threat at all, and the move allowed a winning reply: their answer
  mates, or it leaves them THREAT_CP better off than passing would have and its line wins
  THREAT_MATERIAL, counted from before the move.

Positions in check (no null move) and positions where their last move captured and the mover can
take back on that square (passing would "lose" material already being traded) get no label, and
anything else gets none either.
"""

from __future__ import annotations

from collections.abc import Callable

import chess

from chess_coach.engine.motifs import after_move, material, pass_turn
from chess_coach.engine.stockfish import LineEval
from chess_coach.models import MoveFact, ThreatFact

DEPTH = 12
THREAT_CP = 200  # what a free move (a threat) or the move played (unsafe) is worth to them
THREAT_MATERIAL = 2  # points a winning line wins (a pawn is 1)
MATERIAL_PLIES = 4  # how far along the line material is counted at least...
SETTLE_PLIES = 12  # ...going on while the captures and checks continue
IGNORED, UNSAFE = "ignored_threat", "unsafe_move"
LABELS = (IGNORED, UNSAFE)

Search = Callable[[chess.Board], LineEval]  # the engine's best line for the side to move


def won(board: chess.Board, pv: list[chess.Move]) -> int:
    """Points of material the side to move wins along `pv`: the first MATERIAL_PLIES moves, then
    on while the last move captured or checked or the next one captures, up to SETTLE_PLIES."""
    me = board.turn
    scratch = board.copy(stack=False)
    forcing = False
    for played, move in enumerate(pv[:SETTLE_PLIES]):
        if move not in scratch.legal_moves:
            break
        if played >= MATERIAL_PLIES and not forcing and not scratch.is_capture(move):
            break
        forcing = scratch.is_capture(move) or scratch.gives_check(move)
        scratch.push(move)
    return material(scratch, me) - material(board, me)


def wins(board: chess.Board, line: LineEval) -> bool:
    """Whether `line`, the best line of the side to move on `board`, mates or wins material."""
    if line.mate is not None and line.mate > 0:
        return True
    return line.move is not None and won(board, line.pv) >= THREAT_MATERIAL


def recapture_pending(board: chess.Board, previous: MoveFact | None) -> bool:
    """Their last move captured on a square the side to move can take back on."""
    if previous is None:
        return False
    before = chess.Board(previous.fen_before)
    last = chess.Move.from_uci(previous.uci)
    if not before.is_capture(last):
        return False
    return any(board.is_capture(m) and m.to_square == last.to_square for m in board.legal_moves)


def is_threat(m: MoveFact, line: LineEval, theirs: chess.Board) -> bool:
    """Whether `line`, their best line on `theirs` (the board before `m` with the mover passing),
    is a threat: it mates, or it wins material and the free move is worth THREAT_CP to them."""
    if line.move is None:
        return False
    if line.mate is not None and line.mate > 0:
        return True
    return m.eval_before + line.cp >= THREAT_CP and wins(theirs, line)


def behind_check(previous: MoveFact, theirs: chess.Board, threat: chess.Move) -> bool:
    """Their last move answered a check and the threat was there before it: the same piece could
    already make the same move against the same target, they just had to answer the check
    first. (The evaluation before their move, in check, can't show it.)"""
    before = chess.Board(previous.fen_before)
    return (
        before.is_check()
        and before.piece_at(threat.from_square) == theirs.piece_at(threat.from_square)
        and before.piece_at(threat.to_square) == theirs.piece_at(threat.to_square)
        and before.is_pseudo_legal(threat)
    )


def is_new(line: LineEval, previous: MoveFact | None, theirs: chess.Board) -> bool:
    """Their last move made the threat `line` (on `theirs`): before it (them to move) they were
    THREAT_CP worse off, and it wasn't only waiting behind a check they had to answer."""
    if previous is None:
        return True
    assert line.move is not None
    old = line.cp - previous.eval_before < THREAT_CP or behind_check(previous, theirs, line.move)
    return not old


def answered(board: chess.Board, after: chess.Board, answer: LineEval) -> int:
    """Points of material `answer`, their best line after the move (from `board` to `after`),
    wins counted from before the move: whatever the move took is paid back first."""
    them = after.turn
    return material(after, them) - material(board, them) + won(after, answer.pv)


def met(line: LineEval, answer: LineEval, board: chess.Board, after: chess.Board) -> bool:
    """Whether the move (from `board` to `after`) dealt with the threat `line`, given `answer`,
    their best line after it. A mate threat is met when they no longer mate. Any other isn't
    when their answer is the threat itself and still wins material, or another capture on its
    square that still wins THREAT_MATERIAL."""
    if line.move is None or answer.move is None:
        return True
    mates = answer.mate is not None and answer.mate > 0
    if line.mate is not None and line.mate > 0:
        return not mates
    reply, threat = answer.move, line.move
    if reply == threat:
        return not (mates or answered(board, after, answer) > 0)
    if after.is_capture(reply) and reply.to_square == threat.to_square:
        return not (mates or answered(board, after, answer) >= THREAT_MATERIAL)
    return True


def allows(line: LineEval, answer: LineEval, board: chess.Board, after: chess.Board) -> bool:
    """Whether the move (from `board` to `after`), with nothing threatened, allowed a winning
    reply: their answer mates, or leaves them THREAT_CP better off than passing (`line`) would
    have and wins THREAT_MATERIAL."""
    if answer.move is None:
        return False
    if answer.mate is not None and answer.mate > 0:
        return True
    gain = answer.cp - line.cp
    return gain >= THREAT_CP and answered(board, after, answer) >= THREAT_MATERIAL


def classify(
    m: MoveFact, previous: MoveFact | None, line: LineEval, answer: LineEval, depth: int
) -> ThreatFact:
    """Label one move from the engine's facts: `line` is the opponent's best line had the mover
    passed, `answer` their best line after the move played."""
    row = ThreatFact(m.game_id, m.ply, depth=depth)
    board = chess.Board(m.fen_before)
    theirs = pass_turn(board)
    after = after_move(board, chess.Move.from_uci(m.uci))
    if is_threat(m, line, theirs):
        assert line.move is not None
        row.threat, row.threat_san = line.move.uci(), theirs.san(line.move)
        row.mate = line.mate
        row.gain = None if line.mate else 100 * won(theirs, line.pv)
        if is_new(line, previous, theirs) and not met(line, answer, board, after):
            row.label = IGNORED
        return row
    if wins(theirs, line):
        return row  # material they could win, but the engine doesn't make it worth THREAT_CP
    if allows(line, answer, board, after):
        assert answer.move is not None
        row.label = UNSAFE
        row.threat, row.threat_san = answer.move.uci(), after.san(answer.move)
        row.mate = answer.mate
        row.gain = None if answer.mate else 100 * answered(board, after, answer)
    return row


def look(m: MoveFact, previous: MoveFact | None, search: Search, depth: int = DEPTH) -> ThreatFact:
    """The threat check of one move: two searches, their best line had the mover passed and their
    best answer to the move played. In check or with a recapture pending: no label, no search."""
    board = chess.Board(m.fen_before)
    if board.is_check() or recapture_pending(board, previous):
        return ThreatFact(m.game_id, m.ply, depth=depth)
    line = search(pass_turn(board))
    answer = search(after_move(board, chess.Move.from_uci(m.uci)))
    return classify(m, previous, line, answer, depth)


def same_threat(theirs: chess.Board, guess: chess.Move, threat: chess.Move) -> bool:
    """A lenient check of a guess at their threat on `theirs` (their move, you passing): the threat
    itself, a move to the same square, or another mate when the threat mates."""
    if guess == threat or guess.to_square == threat.to_square:
        return True
    return after_move(theirs, threat).is_checkmate() and after_move(theirs, guess).is_checkmate()
