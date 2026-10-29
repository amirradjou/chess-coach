"""Runtime settings, resolved from environment variables with sensible local defaults."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_DB = Path("data/chess-coach.db")
DEFAULT_LOCAL_ENGINE = Path(".stockfish/stockfish")


def find_stockfish(explicit: str | os.PathLike[str] | None = None) -> Path | None:
    """Locate a Stockfish binary: explicit path > STOCKFISH_PATH > ./.stockfish > PATH."""
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    if env := os.environ.get("STOCKFISH_PATH"):
        candidates.append(Path(env))
    candidates.append(DEFAULT_LOCAL_ENGINE)
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    on_path = shutil.which("stockfish")
    return Path(on_path) if on_path else None


@dataclass(slots=True)
class Settings:
    db_path: Path = field(
        default_factory=lambda: Path(os.environ.get("CHESS_COACH_DB", DEFAULT_DB))
    )
    stockfish_path: Path | None = field(default_factory=find_stockfish)
    analysis_depth: int = field(
        default_factory=lambda: int(os.environ.get("CHESS_COACH_DEPTH", "12"))
    )
    engine_threads: int = field(
        default_factory=lambda: int(os.environ.get("CHESS_COACH_THREADS", "1"))
    )
    engine_hash_mb: int = field(
        default_factory=lambda: int(os.environ.get("CHESS_COACH_HASH_MB", "128"))
    )
    lichess_token: str | None = field(default_factory=lambda: os.environ.get("LICHESS_TOKEN"))
