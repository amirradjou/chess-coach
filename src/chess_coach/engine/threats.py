"""What was their threat? Two ways a mistake happens, told apart with one null-move search.

Before a counted mistake or blunder the mover passes (a null move) and the engine finds the
opponent's best move on that board: what they threaten. It is a threat only when

- it mates, or wins THREAT_CP and is a capture, a check, or a quiet move whose line wins at least
  THREAT_MATERIAL points of material (a quiet move that only uses a free tempo is no threat);
- it is new: they were not already THREAT_CP better off before their last move (the stored
  evaluation of that position, which has them to move like the null-move board).

Positions in check (no null move) and positions where their last move captured and the mover can
take back on that square (passing would "lose" material already being traded) get no label.
Otherwise `ignored_threat`: there was a new threat and the move didn't meet it (their best reply
is the threat, or a capture on its square that still wins THREAT_CP, or a mate after a mate
threat); `unsafe_move`: there was no threat at all and the move left them THREAT_CP better off
than passing would have (or let them mate). Anything else gets no label.
"""

from __future__ import annotations

from collections.abc import Callable

import chess

from chess_coach.engine.motifs import PIECE_VALUES
from chess_coach.engine.stockfish import LineEval
from chess_coach.models import MoveFact, ThreatFact

DEPTH = 12
THREAT_CP = 200  # what a threat, or the reply an unsafe move allows, has to win
THREAT_MATERIAL = 2  # points a quiet threat's line has to win (a pawn is 1)
MATERIAL_PLIES = 4  # how far along the line material is counted...
SETTLE_PLIES = 8  # ...going on while the captures and checks continue
IGNORED, UNSAFE = "ignored_threat", "unsafe_move"
LABELS = (IGNORED, UNSAFE)

Search = Callable[[chess.Board], LineEval]  # the engine's best line for the side to move


def passed(board: chess.Board) -> chess.Board:
    """The same position with the other side to move. Rebuilt from the FEN so the engine is
    never sent a null move in the history (UCI has none; python-chess warns and sends the FEN)."""
    turn = board.copy(stack=False)
    turn.push(chess.Move.null())
    return chess.Board(turn.fen())


def _after(board: chess.Board, move: chess.Move) -> chess.Board:
    after = board.copy(stack=False)
    after.push(move)
    return after


def material(board: chess.Board, color: chess.Color) -> int:
    """`color`'s material minus the other side's, in points (pawn 1 ... queen 9)."""
    return sum(
        PIECE_VALUES[p.piece_type] * (1 if p.color == color else -1)
        for p in board.piece_map().values()
    )


def won(board: chess.Board, pv: list[chess.Move]) -> int:
    """Points of material the side to move wins along the start of `pv`."""
    me = board.turn
    scratch = board.copy(stack=False)
    played = 0
    forcing = False
    for move in pv[:SETTLE_PLIES]:
        if played >= MATERIAL_PLIES and not forcing:
            break
        if move not in scratch.legal_moves:
            break
        forcing = scratch.is_capture(move) or scratch.gives_check(move)
        scratch.push(move)
        played += 1
    return material(scratch, me) - material(board, me)


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
    is a threat."""
    if line.move is None:
        return False
    if line.mate is not None and line.mate > 0:
        return True
    if m.eval_before + line.cp < THREAT_CP:
        return False
    forcing = theirs.is_capture(line.move) or theirs.gives_check(line.move)
    return forcing or won(theirs, line.pv) >= THREAT_MATERIAL


def is_new(line: LineEval, previous: MoveFact | None) -> bool:
    """Their last move made the threat: before it (them to move) they were THREAT_CP worse off."""
    return previous is None or line.cp - previous.eval_before >= THREAT_CP


def met(m: MoveFact, threat: LineEval, reply: chess.Move | None, after: chess.Board) -> bool:
    """Whether the move dealt with the threat. It didn't when their best reply is the threat, a
    capture on the threat's square that still wins THREAT_CP, or (after a mate threat) a mate."""
    if reply is None:
        return True
    if reply == threat.move:
        return False
    if (
        threat.move is not None
        and after.is_capture(reply)
        and reply.to_square == threat.move.to_square
        and m.eval_before - m.eval_after >= THREAT_CP
    ):
        return False
    mated = m.mate_after is not None and m.mate_after < 0
    return not (threat.mate is not None and threat.mate > 0 and mated)


def classify(
    m: MoveFact, previous: MoveFact | None, line: LineEval, reply: chess.Move | None, depth: int
) -> ThreatFact:
    """Label one move from the engine's facts: `line` is the opponent's best line had the mover
    passed, `reply` their best move after the move played (None when unknown)."""
    row = ThreatFact(m.game_id, m.ply, depth=depth)
    board = chess.Board(m.fen_before)
    theirs = passed(board)
    after = _after(board, chess.Move.from_uci(m.uci))
    if is_threat(m, line, theirs):
        assert line.move is not None
        row.threat, row.threat_san = line.move.uci(), theirs.san(line.move)
        row.gain, row.mate = m.eval_before + line.cp, line.mate
        if is_new(line, previous) and not met(m, line, reply, after):
            row.label = IGNORED
        return row
    mated = m.mate_after is not None and m.mate_after < 0
    gain = -m.eval_after - line.cp  # their best move now, against their best had it passed
    if mated or gain >= THREAT_CP:
        row.label, row.gain = UNSAFE, gain
        row.mate = -m.mate_after if mated and m.mate_after is not None else None
        if reply is not None and reply in after.legal_moves:
            row.threat, row.threat_san = reply.uci(), after.san(reply)
    return row


def look(
    m: MoveFact,
    previous: MoveFact | None,
    following: MoveFact | None,
    search: Search,
    depth: int = DEPTH,
) -> ThreatFact:
    """The threat check of one move: one null-move search (and one more after a game's last
    move, whose reply isn't stored). In check or with a recapture pending: no label."""
    board = chess.Board(m.fen_before)
    if board.is_check() or recapture_pending(board, previous):
        return ThreatFact(m.game_id, m.ply, depth=depth)
    line = search(passed(board))
    if following is not None:
        reply = chess.Move.from_uci(following.best_move)
    else:
        reply = search(_after(board, chess.Move.from_uci(m.uci))).move
    return classify(m, previous, line, reply, depth)


def same_threat(theirs: chess.Board, guess: chess.Move, threat: chess.Move) -> bool:
    """A lenient check of a guess at their threat on `theirs` (their move, you passing): the threat
    itself, a move to the same square, or another mate when the threat mates."""
    if guess == threat or guess.to_square == threat.to_square:
        return True
    return _after(theirs, threat).is_checkmate() and _after(theirs, guess).is_checkmate()
