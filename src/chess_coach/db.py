"""SQLite store for games and per-move facts. One file, WAL mode, schema created on open."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime
from pathlib import Path

from chess_coach.models import Color, Game, Judgement, MoveFact, Phase, Speed

SCHEMA = """
CREATE TABLE IF NOT EXISTS games (
    id             TEXT PRIMARY KEY,
    source         TEXT NOT NULL,
    source_id      TEXT NOT NULL,
    user           TEXT NOT NULL,
    user_color     TEXT NOT NULL,
    white          TEXT NOT NULL,
    black          TEXT NOT NULL,
    result         TEXT NOT NULL,
    pgn            TEXT NOT NULL,
    url            TEXT,
    white_elo      INTEGER,
    black_elo      INTEGER,
    played_at      TEXT,
    time_control   TEXT,
    speed          TEXT NOT NULL,
    rated          INTEGER,
    eco            TEXT,
    opening        TEXT,
    termination    TEXT,
    analyzed_depth INTEGER,
    imported_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS games_played_at ON games (played_at);

CREATE TABLE IF NOT EXISTS moves (
    game_id     TEXT NOT NULL REFERENCES games (id) ON DELETE CASCADE,
    ply         INTEGER NOT NULL,
    move_number INTEGER NOT NULL,
    color       TEXT NOT NULL,
    is_user     INTEGER NOT NULL,
    san         TEXT NOT NULL,
    uci         TEXT NOT NULL,
    fen_before  TEXT NOT NULL,
    eval_before INTEGER NOT NULL,
    eval_after  INTEGER NOT NULL,
    best_move   TEXT NOT NULL,
    best_san    TEXT NOT NULL,
    cp_loss     INTEGER NOT NULL,
    wp_before   REAL NOT NULL,
    wp_after    REAL NOT NULL,
    wp_loss     REAL NOT NULL,
    judgement   TEXT NOT NULL,
    phase       TEXT NOT NULL,
    depth       INTEGER NOT NULL,
    mate_before INTEGER,
    mate_after  INTEGER,
    pv          TEXT NOT NULL DEFAULT '',
    clock_after REAL,
    motifs      TEXT NOT NULL DEFAULT '[]',
    PRIMARY KEY (game_id, ply)
);
CREATE INDEX IF NOT EXISTS moves_user_judgement ON moves (is_user, judgement);
"""


def _iso(dt: datetime | None) -> str | None:
    return dt.astimezone(UTC).isoformat() if dt else None


def _from_iso(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


class Store:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- games -----------------------------------------------------------------

    def upsert_game(self, game: Game) -> bool:
        """Insert a game; return False if it was already stored (existing rows are kept)."""
        cur = self.conn.execute(
            """
            INSERT OR IGNORE INTO games (
                id, source, source_id, user, user_color, white, black, result, pgn, url,
                white_elo, black_elo, played_at, time_control, speed, rated, eco, opening,
                termination, analyzed_depth, imported_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                game.id,
                game.source,
                game.source_id,
                game.user,
                game.user_color.value,
                game.white,
                game.black,
                game.result,
                game.pgn,
                game.url,
                game.white_elo,
                game.black_elo,
                _iso(game.played_at),
                game.time_control,
                game.speed.value,
                None if game.rated is None else int(game.rated),
                game.eco,
                game.opening,
                game.termination,
                game.analyzed_depth,
                datetime.now(UTC).isoformat(),
            ),
        )
        self.conn.commit()
        return cur.rowcount == 1

    def get_game(self, game_id: str) -> Game | None:
        row = self.conn.execute("SELECT * FROM games WHERE id = ?", (game_id,)).fetchone()
        return _row_to_game(row) if row else None

    def iter_games(
        self,
        *,
        unanalyzed_below_depth: int | None = None,
        since: datetime | None = None,
        source: str | None = None,
    ) -> Iterator[Game]:
        clauses: list[str] = []
        params: list[object] = []
        if unanalyzed_below_depth is not None:
            clauses.append("(analyzed_depth IS NULL OR analyzed_depth < ?)")
            params.append(unanalyzed_below_depth)
        if since is not None:
            clauses.append("played_at >= ?")
            params.append(_iso(since))
        if source is not None:
            clauses.append("source = ?")
            params.append(source)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.conn.execute(f"SELECT * FROM games {where} ORDER BY played_at", params)
        for row in rows:
            yield _row_to_game(row)

    def count_games(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM games").fetchone()[0]

    # -- moves -----------------------------------------------------------------

    def replace_moves(self, game_id: str, moves: Iterable[MoveFact], depth: int) -> None:
        """Store a full analysis of one game atomically and mark the game analyzed."""
        with self.conn:
            self.conn.execute("DELETE FROM moves WHERE game_id = ?", (game_id,))
            self.conn.executemany(
                """
                INSERT INTO moves (
                    game_id, ply, move_number, color, is_user, san, uci, fen_before,
                    eval_before, eval_after, best_move, best_san, cp_loss, wp_before, wp_after,
                    wp_loss, judgement, phase, depth, mate_before, mate_after, pv, clock_after,
                    motifs
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        m.game_id,
                        m.ply,
                        m.move_number,
                        m.color.value,
                        int(m.is_user),
                        m.san,
                        m.uci,
                        m.fen_before,
                        m.eval_before,
                        m.eval_after,
                        m.best_move,
                        m.best_san,
                        m.cp_loss,
                        m.wp_before,
                        m.wp_after,
                        m.wp_loss,
                        m.judgement.value,
                        m.phase.value,
                        m.depth,
                        m.mate_before,
                        m.mate_after,
                        m.pv,
                        m.clock_after,
                        json.dumps(m.motifs),
                    )
                    for m in moves
                ],
            )
            self.conn.execute("UPDATE games SET analyzed_depth = ? WHERE id = ?", (depth, game_id))

    def iter_moves(
        self, game_id: str | None = None, *, user_only: bool = False
    ) -> Iterator[MoveFact]:
        clauses: list[str] = []
        params: list[object] = []
        if game_id is not None:
            clauses.append("game_id = ?")
            params.append(game_id)
        if user_only:
            clauses.append("is_user = 1")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.conn.execute(f"SELECT * FROM moves {where} ORDER BY game_id, ply", params)
        for row in rows:
            yield _row_to_move(row)

    def count_moves(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM moves").fetchone()[0]


def _row_to_game(row: sqlite3.Row) -> Game:
    return Game(
        id=row["id"],
        source=row["source"],
        source_id=row["source_id"],
        user=row["user"],
        user_color=Color(row["user_color"]),
        white=row["white"],
        black=row["black"],
        result=row["result"],
        pgn=row["pgn"],
        url=row["url"],
        white_elo=row["white_elo"],
        black_elo=row["black_elo"],
        played_at=_from_iso(row["played_at"]),
        time_control=row["time_control"],
        speed=Speed(row["speed"]),
        rated=None if row["rated"] is None else bool(row["rated"]),
        eco=row["eco"],
        opening=row["opening"],
        termination=row["termination"],
        analyzed_depth=row["analyzed_depth"],
    )


def _row_to_move(row: sqlite3.Row) -> MoveFact:
    return MoveFact(
        game_id=row["game_id"],
        ply=row["ply"],
        move_number=row["move_number"],
        color=Color(row["color"]),
        is_user=bool(row["is_user"]),
        san=row["san"],
        uci=row["uci"],
        fen_before=row["fen_before"],
        eval_before=row["eval_before"],
        eval_after=row["eval_after"],
        best_move=row["best_move"],
        best_san=row["best_san"],
        cp_loss=row["cp_loss"],
        wp_before=row["wp_before"],
        wp_after=row["wp_after"],
        wp_loss=row["wp_loss"],
        judgement=Judgement(row["judgement"]),
        phase=Phase(row["phase"]),
        depth=row["depth"],
        mate_before=row["mate_before"],
        mate_after=row["mate_after"],
        pv=row["pv"],
        clock_after=row["clock_after"],
        motifs=json.loads(row["motifs"]),
    )
