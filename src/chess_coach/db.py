"""SQLite store for games and per-move facts. One file, WAL mode, schema created on open."""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime
from pathlib import Path

from chess_coach.models import (
    CoachCard,
    Color,
    Game,
    Judgement,
    MoveFact,
    Phase,
    Puzzle,
    Review,
    Speed,
)

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

CREATE TABLE IF NOT EXISTS puzzles (
    id           TEXT PRIMARY KEY,
    game_id      TEXT NOT NULL REFERENCES games (id) ON DELETE CASCADE,
    ply          INTEGER NOT NULL,
    fen          TEXT NOT NULL,
    solution     TEXT NOT NULL,
    solution_san TEXT NOT NULL,
    played_san   TEXT NOT NULL,
    line         TEXT NOT NULL DEFAULT '',
    gap_cp       INTEGER NOT NULL,
    depth        INTEGER NOT NULL,
    tags         TEXT NOT NULL DEFAULT '[]',
    created_at   TEXT NOT NULL,
    accept       TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS puzzle_checks (
    move_id    TEXT PRIMARY KEY,  -- "<game_id>:<ply>"; a candidate checked once is not re-checked
    accepted   INTEGER NOT NULL,
    checked_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reviews (
    puzzle_id TEXT PRIMARY KEY REFERENCES puzzles (id) ON DELETE CASCADE,
    due       TEXT NOT NULL,
    interval  INTEGER NOT NULL,
    ease      REAL NOT NULL,
    reps      INTEGER NOT NULL,
    lapses    INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS attempts (
    puzzle_id TEXT NOT NULL REFERENCES puzzles (id) ON DELETE CASCADE,
    at        TEXT NOT NULL,
    answer    TEXT NOT NULL,
    correct   INTEGER NOT NULL
);

-- Opening training (Learn openings): each position you answer is a card with an SM-2 schedule.
CREATE TABLE IF NOT EXISTS coach_cards (
    id       TEXT PRIMARY KEY,  -- "<colour>|<family>|<path>"
    family   TEXT NOT NULL,
    color    TEXT NOT NULL,     -- yours
    path     TEXT NOT NULL,     -- UCI moves from the start, comma-separated
    kind     TEXT NOT NULL,     -- "punish" (right after their mistake) or "line"
    due      TEXT NOT NULL,
    interval INTEGER NOT NULL,
    ease     REAL NOT NULL,
    reps     INTEGER NOT NULL,
    lapses   INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS coach_attempts (
    card_id TEXT NOT NULL REFERENCES coach_cards (id) ON DELETE CASCADE,
    at      TEXT NOT NULL,
    answer  TEXT NOT NULL,  -- UCI, or "?" when you asked to see the move
    correct INTEGER NOT NULL
);
"""


_HEADER = re.compile(r'^\[(\w+) "(.*)"\]\s*$', re.MULTILINE)


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
        columns = {r[1] for r in self.conn.execute("PRAGMA table_info(puzzles)")}
        if "accept" not in columns:  # databases from before opening-fix drills
            self.conn.execute("ALTER TABLE puzzles ADD COLUMN accept TEXT NOT NULL DEFAULT '[]'")
        self._fix_lichess_ids()

    def _fix_lichess_ids(self) -> None:
        """Lichess games imported while the importer missed Site "lichess.org/game/<id>" got a
        hash of the PGN for an id and a dead link. Give them their Lichess id back everywhere."""
        rows = self.conn.execute(
            "SELECT id, pgn FROM games WHERE source = 'lichess' AND length(source_id) <> 8"
        ).fetchall()
        if not rows:
            return
        from chess_coach.sources.pgn import detect_source

        taken = {r[0] for r in self.conn.execute("SELECT id FROM games")}
        renames = []
        for old, pgn in rows:
            detected = detect_source(dict(_HEADER.findall(pgn)))
            if detected and detected[0] == "lichess" and f"lichess:{detected[1]}" not in taken:
                renames.append((old, detected[1]))
        self.conn.commit()
        with self.conn:
            # Moves, puzzles and their history point at the game id; check them at commit
            self.conn.execute("BEGIN")
            self.conn.execute("PRAGMA defer_foreign_keys = ON")
            for old, source_id in renames:
                new = f"lichess:{source_id}"
                self.conn.execute(
                    "UPDATE games SET id = ?, source_id = ?, url = ? WHERE id = ?",
                    (new, source_id, f"https://lichess.org/{source_id}", old),
                )
                self.conn.execute("UPDATE moves SET game_id = ? WHERE game_id = ?", (new, old))
                # Puzzle and candidate ids are "<game id>:<ply>"
                for table, column in (
                    ("puzzles", "id"),
                    ("reviews", "puzzle_id"),
                    ("attempts", "puzzle_id"),
                    ("puzzle_checks", "move_id"),
                ):
                    self.conn.execute(
                        f"UPDATE {table} SET {column} = ? || substr({column}, ?) "
                        f"WHERE substr({column}, 1, ?) = ?",
                        (new, len(old) + 1, len(old) + 1, f"{old}:"),
                    )
                self.conn.execute("UPDATE puzzles SET game_id = ? WHERE game_id = ?", (new, old))

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

    def accounts(self) -> list[tuple[str, str, datetime | None]]:
        """(source, user, when their newest stored game was played) for every account stored."""
        rows = self.conn.execute(
            "SELECT source, user, MAX(played_at) FROM games GROUP BY source, user "
            "ORDER BY source, user"
        )
        return [(source, user, _from_iso(newest)) for source, user, newest in rows]

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
        self,
        game_id: str | None = None,
        *,
        user_only: bool = False,
        max_ply: int | None = None,
    ) -> Iterator[MoveFact]:
        clauses: list[str] = []
        params: list[object] = []
        if game_id is not None:
            clauses.append("game_id = ?")
            params.append(game_id)
        if user_only:
            clauses.append("is_user = 1")
        if max_ply is not None:
            clauses.append("ply <= ?")
            params.append(max_ply)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.conn.execute(f"SELECT * FROM moves {where} ORDER BY game_id, ply", params)
        for row in rows:
            yield _row_to_move(row)

    def replies_to_errors(self) -> dict[tuple[str, int], MoveFact]:
        """The opponent's move after each of the user's inaccuracies or worse, by (game id, the
        user's ply). Its best move is what the mistake allowed."""
        rows = self.conn.execute(
            "SELECT r.* FROM moves m JOIN moves r ON r.game_id = m.game_id AND r.ply = m.ply + 1 "
            "WHERE m.is_user = 1 AND m.judgement IN ('inaccuracy', 'mistake', 'blunder')"
        )
        return {(row["game_id"], row["ply"] - 1): _row_to_move(row) for row in rows}

    def count_moves(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM moves").fetchone()[0]

    # -- puzzles -----------------------------------------------------------------

    def checked_moves(self) -> set[str]:
        return {r[0] for r in self.conn.execute("SELECT move_id FROM puzzle_checks")}

    def record_check(self, move_id: str, accepted: bool) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO puzzle_checks (move_id, accepted, checked_at) "
                "VALUES (?, ?, ?)",
                (move_id, int(accepted), datetime.now(UTC).isoformat()),
            )

    def add_puzzle(self, p: Puzzle) -> None:
        """Store a puzzle and schedule it for today."""
        with self.conn:
            self.conn.execute(
                """
                INSERT OR REPLACE INTO puzzles (
                    id, game_id, ply, fen, solution, solution_san, played_san, line, gap_cp,
                    depth, tags, created_at, accept
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    p.id,
                    p.game_id,
                    p.ply,
                    p.fen,
                    p.solution,
                    p.solution_san,
                    p.played_san,
                    p.line,
                    p.gap_cp,
                    p.depth,
                    json.dumps(p.tags),
                    datetime.now(UTC).isoformat(),
                    json.dumps(p.accept),
                ),
            )
            self.conn.execute(
                "INSERT OR IGNORE INTO reviews (puzzle_id, due, interval, ease, reps, lapses) "
                "VALUES (?, ?, 0, 2.5, 0, 0)",
                (p.id, datetime.now(UTC).date().isoformat()),
            )

    def set_puzzle_tags(self, puzzle_id: str, tags: list[str]) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE puzzles SET tags = ? WHERE id = ?", (json.dumps(tags), puzzle_id)
            )

    def delete_puzzle(self, puzzle_id: str) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM puzzles WHERE id = ?", (puzzle_id,))

    def puzzles(self, tag: str | None = None) -> list[Puzzle]:
        rows = self.conn.execute("SELECT * FROM puzzles ORDER BY created_at, id")
        out = [_row_to_puzzle(r) for r in rows]
        return [p for p in out if tag is None or tag in p.tags]

    def due(self, today: str, tag: str | None = None, limit: int | None = None) -> list[Puzzle]:
        """Puzzles due on or before `today`, most overdue first."""
        rows = self.conn.execute(
            "SELECT p.* FROM puzzles p JOIN reviews r ON r.puzzle_id = p.id "
            "WHERE r.due <= ? ORDER BY r.due, p.id",
            (today,),
        )
        out = [p for p in (_row_to_puzzle(r) for r in rows) if tag is None or tag in p.tags]
        return out[:limit] if limit is not None else out

    def review(self, puzzle_id: str) -> Review | None:
        row = self.conn.execute(
            "SELECT * FROM reviews WHERE puzzle_id = ?", (puzzle_id,)
        ).fetchone()
        if row is None:
            return None
        return Review(
            row["puzzle_id"], row["due"], row["interval"], row["ease"], row["reps"], row["lapses"]
        )

    def save_attempt(self, review: Review, answer: str, correct: bool, at: datetime) -> None:
        """Record an attempt and the puzzle's new schedule together."""
        with self.conn:
            self.conn.execute(
                "INSERT INTO attempts (puzzle_id, at, answer, correct) VALUES (?, ?, ?, ?)",
                (review.puzzle_id, at.isoformat(), answer, int(correct)),
            )
            self.conn.execute(
                "UPDATE reviews SET due = ?, interval = ?, ease = ?, reps = ?, lapses = ? "
                "WHERE puzzle_id = ?",
                (
                    review.due,
                    review.interval,
                    review.ease,
                    review.reps,
                    review.lapses,
                    review.puzzle_id,
                ),
            )

    def attempts(self) -> list[tuple[str, str, bool]]:
        """(puzzle_id, ISO time, correct) for every attempt, oldest first."""
        rows = self.conn.execute("SELECT puzzle_id, at, correct FROM attempts ORDER BY at")
        return [(r[0], r[1], bool(r[2])) for r in rows]

    # -- opening training ------------------------------------------------------

    def coach_cards(self, family: str | None = None, color: str | None = None) -> list[CoachCard]:
        """Your opening cards, all or one opening's, soonest due first."""
        rows = self.conn.execute(
            "SELECT * FROM coach_cards "
            "WHERE (? IS NULL OR family = ?) AND (? IS NULL OR color = ?) ORDER BY due, id",
            (family, family, color, color),
        )
        return [_row_to_card(r) for r in rows]

    def coach_card(self, id: str) -> CoachCard | None:
        row = self.conn.execute("SELECT * FROM coach_cards WHERE id = ?", (id,)).fetchone()
        return _row_to_card(row) if row else None

    def save_coach_attempt(self, card: CoachCard, answer: str, correct: bool, at: datetime) -> None:
        """Record an answer and the card's new schedule together (the card is created the first
        time)."""
        with self.conn:
            self.conn.execute(
                "INSERT INTO coach_cards "
                "(id, family, color, path, kind, due, interval, ease, reps, lapses) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (id) DO UPDATE SET kind = excluded.kind, due = excluded.due, "
                "interval = excluded.interval, ease = excluded.ease, reps = excluded.reps, "
                "lapses = excluded.lapses",
                (
                    card.id,
                    card.family,
                    card.color,
                    ",".join(card.path),
                    card.kind,
                    card.due,
                    card.interval,
                    card.ease,
                    card.reps,
                    card.lapses,
                ),
            )
            self.conn.execute(
                "INSERT INTO coach_attempts (card_id, at, answer, correct) VALUES (?, ?, ?, ?)",
                (card.id, at.isoformat(), answer, int(correct)),
            )

    def coach_attempts(self) -> list[tuple[str, str, bool]]:
        """(card id, ISO time, correct) for every opening training answer, oldest first."""
        rows = self.conn.execute("SELECT card_id, at, correct FROM coach_attempts ORDER BY at")
        return [(r[0], r[1], bool(r[2])) for r in rows]

    def coach_answered(self, card_id: str, day: str) -> bool:
        """Whether you already answered this card on `day` (an ISO date, UTC like the times)."""
        row = self.conn.execute(
            "SELECT 1 FROM coach_attempts WHERE card_id = ? AND substr(at, 1, 10) = ? LIMIT 1",
            (card_id, day),
        ).fetchone()
        return row is not None

    def coach_last_answers(self, family: str, color: str) -> dict[str, bool]:
        """Card id -> whether your latest answer there was right, for one opening."""
        rows = self.conn.execute(
            "SELECT a.card_id, a.correct, MAX(a.at) FROM coach_attempts a "
            "JOIN coach_cards c ON c.id = a.card_id WHERE c.family = ? AND c.color = ? "
            "GROUP BY a.card_id",
            (family, color),
        )
        return {r[0]: bool(r[1]) for r in rows}


def _row_to_card(row: sqlite3.Row) -> CoachCard:
    return CoachCard(
        family=row["family"],
        color=row["color"],
        path=[u for u in row["path"].split(",") if u],
        kind=row["kind"],
        due=row["due"],
        interval=row["interval"],
        ease=row["ease"],
        reps=row["reps"],
        lapses=row["lapses"],
    )


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


def _row_to_puzzle(row: sqlite3.Row) -> Puzzle:
    return Puzzle(
        id=row["id"],
        game_id=row["game_id"],
        ply=row["ply"],
        fen=row["fen"],
        solution=row["solution"],
        solution_san=row["solution_san"],
        played_san=row["played_san"],
        line=row["line"],
        gap_cp=row["gap_cp"],
        depth=row["depth"],
        tags=json.loads(row["tags"]),
        accept=json.loads(row["accept"]),
    )
