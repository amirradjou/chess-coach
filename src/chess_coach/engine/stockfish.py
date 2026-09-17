"""Thin, synchronous wrapper over a UCI engine via python-chess."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import chess
import chess.engine

from chess_coach.config import find_stockfish
from chess_coach.engine.evaluation import MATE_CP, mate_to_cp


class EngineNotFoundError(RuntimeError):
    pass


@dataclass(slots=True)
class LineEval:
    """One principal variation, scored from the side to move's point of view."""

    cp: int
    mate: int | None
    move: chess.Move | None
    pv: list[chess.Move] = field(default_factory=list)


def terminal_eval(board: chess.Board) -> LineEval | None:
    """Score for positions the engine should not be asked about (mate, stalemate, dead draws)."""
    if board.is_checkmate():
        return LineEval(cp=-MATE_CP, mate=0, move=None)
    if board.is_stalemate() or board.is_insufficient_material():
        return LineEval(cp=0, mate=None, move=None)
    return None


class Engine:
    def __init__(
        self, path: str | Path | None = None, *, threads: int = 1, hash_mb: int = 128
    ) -> None:
        resolved = find_stockfish(path)
        if resolved is None:
            raise EngineNotFoundError(
                "No Stockfish binary found. Run scripts/get-stockfish.sh or set STOCKFISH_PATH."
            )
        self.path = resolved
        self._engine = chess.engine.SimpleEngine.popen_uci(str(resolved))
        self._engine.configure({"Threads": threads, "Hash": hash_mb})

    @property
    def name(self) -> str:
        return self._engine.id.get("name", "unknown engine")

    def close(self) -> None:
        self._engine.quit()

    def __enter__(self) -> Engine:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def evaluate(self, board: chess.Board, *, depth: int, multipv: int = 1) -> list[LineEval]:
        """Best lines for the side to move, strongest first. Terminal positions need no search."""
        terminal = terminal_eval(board)
        if terminal is not None:
            return [terminal]
        infos = self._engine.analyse(board, chess.engine.Limit(depth=depth), multipv=multipv)
        lines: list[LineEval] = []
        for info in infos:
            score = info["score"].pov(board.turn)
            mate = score.mate()
            cp = mate_to_cp(mate) if mate is not None else score.score(mate_score=MATE_CP)
            pv = list(info.get("pv", []))
            lines.append(LineEval(cp=int(cp), mate=mate, move=pv[0] if pv else None, pv=pv))
        return lines
