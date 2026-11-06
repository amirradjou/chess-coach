"""Runtime settings, resolved from environment variables with sensible local defaults."""

from __future__ import annotations

import os
import shutil
import sqlite3
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_DB = Path("data/chess-coach.db")
DEFAULT_LOCAL_ENGINE = Path(".stockfish/stockfish")
# Where the code was checked out, so its engine is found from any directory.
REPO_ROOT = Path(__file__).resolve().parents[2]


def config_file() -> Path:
    return Path.home() / ".config" / "chess-coach" / "config.toml"


def configured_db() -> Path | None:
    """The `db` set in the config file. One that can't be read is an error to see, not a reason to
    quietly use another database."""
    path = config_file()
    if not path.is_file():
        return None
    db = tomllib.loads(path.read_text()).get("db")
    return Path(db).expanduser() if db else None


def default_db() -> Path:
    """CHESS_COACH_DB, then the config file, then data/chess-coach.db."""
    if env := os.environ.get("CHESS_COACH_DB"):
        return Path(env)
    return configured_db() or DEFAULT_DB


def puzzle_count(path: Path) -> int | None:
    """How many puzzles a database holds, read-only: opening it with `Store` would migrate it. None
    when it isn't a coach database, or is one from before puzzles."""
    try:
        conn = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    except sqlite3.Error:
        return None
    try:
        return conn.execute("SELECT count(*) FROM puzzles").fetchone()[0]
    except sqlite3.Error:
        return None
    finally:
        conn.close()


def better_database(current: Path) -> tuple[Path, int] | None:
    """Another database beside `current` with puzzles in it, the one with the most (the newest of
    equals), when `current` has none: the games you meant may be in that file."""
    if puzzle_count(current) or not current.parent.is_dir():
        return None
    found = [
        (count, p.stat().st_mtime, p)
        for p in current.parent.glob("*.db")
        if p.resolve() != current.resolve() and (count := puzzle_count(p))
    ]
    if not found:
        return None
    count, _, path = max(found)
    return path, count


def find_stockfish(explicit: str | os.PathLike[str] | None = None) -> Path | None:
    """Locate a Stockfish binary: explicit path > STOCKFISH_PATH > ./.stockfish > the checkout's
    .stockfish > PATH."""
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    if env := os.environ.get("STOCKFISH_PATH"):
        candidates.append(Path(env))
    candidates += [DEFAULT_LOCAL_ENGINE, REPO_ROOT / DEFAULT_LOCAL_ENGINE]
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    on_path = shutil.which("stockfish")
    return Path(on_path) if on_path else None


@dataclass(slots=True)
class Settings:
    db_path: Path = field(default_factory=default_db)
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
