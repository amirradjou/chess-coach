"""A small local web app over the same database: what you get wrong (with boards), practice,
and your openings. Run it with `chess-coach serve`; it binds to localhost and needs no account or
key."""

from __future__ import annotations

import threading
from collections import Counter
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from importlib import resources
from pathlib import Path
from typing import Annotated

import chess
import chess.engine
import chess.svg
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel

from chess_coach.coach import training
from chess_coach.coach import tree as optree
from chess_coach.coach.digest import build
from chess_coach.coach.habits import (
    Habit,
    from_motifs,
    from_patterns,
    from_pressure,
    missed_kinds,
)
from chess_coach.coach.principles import Reasons, allows, explain, opening_idea, their_threat
from chess_coach.coach.progress import (
    periods,
    practice_effect,
    solve_rates,
    trend_speeds,
    weekly,
)
from chess_coach.coach.punish import (
    GIFT_WP_LOSS,
    KINDS,
    OUTCOMES,
    Compare,
    Gift,
    Rate,
    Split,
    headline,
    label,
    part_note,
    report,
    worst_title,
)
from chess_coach.coach.repeats import Repeat, repeats
from chess_coach.coach.repertoire import (
    LEVEL_PLY,
    AfterTen,
    Favorite,
    Fix,
    after_ten,
    evals_after_ten,
    favorites,
)
from chess_coach.coach.threats import Report as ThreatReport
from chess_coach.coach.threats import Share, Step
from chess_coach.coach.threats import reason as threat_reason
from chess_coach.db import Store
from chess_coach.engine.motifs import pass_turn
from chess_coach.engine.stockfish import Engine, EngineNotFoundError, LineEval
from chess_coach.engine.threats import IGNORED, same_threat
from chess_coach.models import CoachCard, Puzzle, ThreatFact, card_id
from chess_coach.openings import opening_families, opening_table
from chess_coach.patterns.features import ERRORS, move_rows, result_of
from chess_coach.patterns.mining import Evidence
from chess_coach.puzzles import review
from chess_coach.puzzles.srs import schedule
from chess_coach.puzzles.today import (
    Facts,
    accuracy,
    days_practised,
    practice_queue,
    read_facts,
    session,
    upcoming,
)
from chess_coach.util import practice_day

PLAYED = "#d1495b"  # your move
BETTER = "#2a9d8f"  # the engine's move
TRIED = "#3d5a80"  # your answer in practice
THREAT = "#e07a1f"  # their threat, or the reply your move allowed
ARROWS = {"best": BETTER, "played": PLAYED, "tried": TRIED, "threat": THREAT}
OPENING_NOTE_MOVES = 15  # puzzles this early get a note on what their opening is about
EXPLORE_DEPTH = 16  # quick enough to answer while you click through a line
CACHE_POSITIONS = 10_000


def board_svg(
    fen: str,
    arrows: list[tuple[str, str]] = (),
    size: int = 320,
    *,
    orientation: chess.Color | None = None,
    lastmove: str | None = None,
) -> str:
    """The position (from the side to move unless told), with (uci, colour) arrows."""
    board = chess.Board(fen)
    drawn = []
    for uci, color in arrows:
        if uci and len(uci) >= 4:
            move = chess.Move.from_uci(uci)
            drawn.append(chess.svg.Arrow(move.from_square, move.to_square, color=color))
    return chess.svg.board(
        board,
        orientation=board.turn if orientation is None else orientation,
        arrows=drawn,
        lastmove=chess.Move.from_uci(lastmove) if lastmove else None,
        size=size,
    )


class Analyst:
    """One engine for the app, started on first use, with its answers cached by FEN. Without
    Stockfish it answers None and the pages show lines without the engine."""

    def __init__(self, start: Callable[[], Engine]) -> None:
        self._start = start
        self._engine: Engine | None = None
        self._missing = False
        self._lock = threading.Lock()
        self._cache: dict[str, LineEval] = {}

    def __call__(self, fen: str) -> LineEval | None:
        if fen in self._cache:
            return self._cache[fen]
        with self._lock:
            if self._engine is None and not self._missing:
                try:
                    self._engine = self._start()
                except EngineNotFoundError:
                    self._missing = True
            if self._engine is None:
                return None
            try:
                ev = self._engine.evaluate(chess.Board(fen), depth=EXPLORE_DEPTH)[0]
            except (chess.engine.EngineError, chess.engine.EngineTerminatedError):
                self._close()  # started again on the next call
                return None
        if len(self._cache) >= CACHE_POSITIONS:
            self._cache.clear()
        self._cache[fen] = ev
        return ev

    def _close(self) -> None:
        engine, self._engine = self._engine, None
        if engine is not None and hasattr(engine, "close"):
            engine.close()

    def close(self) -> None:
        with self._lock:
            self._close()


def _example(e: Evidence, threat: ThreatFact | None = None) -> dict:
    """A position before your move: your move in red, the engine's in green and, when the move
    is an ignored threat or an unsafe move, their threat (or the reply it allowed) in orange."""
    why = threat_reason(e.fen, e.uci, threat) if threat is not None else None
    arrows = [(e.uci, PLAYED), (e.best_uci, BETTER)]
    if why and threat is not None:
        arrows.append((threat.threat, THREAT))
    reasons = explain(e.fen, e.uci, e.best_uci).all() if e.fen and e.uci else []
    return {
        "svg": board_svg(e.fen, arrows) if e.fen else "",
        "move": e.move,
        "best": e.best_san,
        "loss": round(e.wp_loss),
        "url": e.url,
        "threat": threat.threat_san if why and threat is not None else None,
        "reasons": [*([why] if why else []), *reasons][:3],
    }


def _habit(h: Habit, threats: dict[tuple[str, int], ThreatFact]) -> dict:
    return {
        "title": h.title,
        "detail": h.detail,
        "lesson": h.lesson,
        "mistakes": h.mistakes,
        "games": h.games,
        "practice": h.practice,
        "examples": [_example(e, threats.get((e.game_id, e.ply))) for e in h.examples],
    }


def _rate(r: Rate) -> dict:
    return {
        "missed": r.missed,
        "n": r.n,
        "rate": round(r.rate * 100, 1),
        "low": round(r.low * 100, 1),
        "high": round(r.high * 100, 1),
    }


def _compare(c: Compare) -> dict:
    """gap and interval are None when a side had no chances: 0 of 0 is no rate to compare."""
    interval = c.interval
    return {
        "label": c.label,
        "yours": _rate(c.yours),
        "theirs": _rate(c.theirs),
        "gap": None if c.gap is None else round(c.gap * 100, 1),
        "interval": None if interval is None else [round(x * 100, 1) for x in interval],
        "verdict": c.verdict,
    }


def _split(s: Split) -> dict:
    """A group's headline and its rows: partial misses (10-20 points), clear ones (20+) and the
    two together (what the kinds, the trend and the openings count)."""
    return {
        "label": s.label,
        "headline": headline(s),
        "partial": {**_compare(s.partial), "name": OUTCOMES["partial"]},
        "clear": {**_compare(s.clear), "name": OUTCOMES["clear"]},
        "missed": {**_compare(s.missed), "name": OUTCOMES["missed"]},
    }


def _miss(g: Gift) -> dict:
    """One of your missed punishments: their blunder as the last move, your reply in red and the
    punishment in green."""
    r = g.reply
    arrows = [(r.uci, PLAYED), (r.best_move, BETTER)]
    return {
        "svg": board_svg(r.fen_before, arrows, lastmove=g.blunder.uci) if r.fen_before else "",
        "theirs": label(g.blunder),
        "move": label(r),
        "best": label(r, r.best_san),
        "loss": g.given_back,  # one decimal, cut: a partial miss never reads as 20
        "kind": g.kind,
        "url": g.url,
        "reasons": g.reasons(),
    }


def _share(x: Share) -> dict:
    low, high = x.interval
    return {"hits": x.hits, "n": x.n, "rate": _pct(x.rate), "low": _pct(low), "high": _pct(high)}


def _step(s: Step) -> dict:
    low, high = s.diff
    return {
        "label": s.label,
        "title": s.title,
        "what": s.what,
        "tip": s.tip,
        "you": _share(s.you),
        "them": _share(s.them),
        "diff": {
            "points": round(100 * (s.you.rate - s.them.rate), 1),
            "low": round(100 * low, 1),
            "high": round(100 * high, 1),
        },
        "verdict": s.verdict,
        "yours": s.yours,
        "examples": [_example(e.evidence, e.threat) for e in s.examples],
    }


def _threats(r: ThreatReport | None) -> dict | None:
    if r is None or not r.you:
        return None
    steps = [_step(s) for s in r.steps]
    return {"you": r.you, "them": r.them, "unchecked": r.unchecked, "steps": steps}


def _fix(f: Fix) -> dict:
    return {
        "svg": board_svg(f.fen, [(f.played_uci, PLAYED), (f.best_uci, BETTER)]),
        "move": f.move,
        "best": f.best_san,
        "times": f.times,
        "loss_cp": round(f.avg_cp_loss),
        "urls": [url for _, _, url in f.games if url],
        "reasons": explain(f.fen, f.played_uci, f.best_uci).all()[:3],
    }


def _repeat(r: Repeat) -> dict:
    return {
        "svg": board_svg(r.fen, [(r.uci, PLAYED), (r.best_uci, BETTER)]),
        "move": r.move,
        "better": r.better,
        "family": r.family,
        "games": r.games,
        "last": r.last.date().isoformat() if r.last else None,
        "punish": r.punish,
        "punished": r.punished,
        "replies": r.replies,
        "reasons": r.reasons,
        "urls": r.urls,
        "puzzle": r.puzzle is not None,
    }


def _eval(cp: int) -> str:
    """'-0.5' from your side, and 0.0 rather than -0.0."""
    return review.Score(cp).text if abs(cp) >= 5 else "0.0"


def _after_ten(a: AfterTen | None) -> dict | None:
    if a is None:
        return None
    (bad_low, bad_high), (error_low, error_high) = a.bad_interval, a.error_interval
    return {
        "family": a.family,
        "games": a.games,
        "median": _eval(a.median_cp),
        "bad": _pct(a.bad_rate),
        "bad_low": _pct(bad_low),
        "bad_high": _pct(bad_high),
        "error_rate": round(a.error_rate * 100, 1),
        "error_low": round(error_low * 100, 1),
        "error_high": round(error_high * 100, 1),
        "since_book": _eval(a.since_book),
    }


def _at_ten(found: tuple[int, int] | None) -> dict | None:
    return None if found is None else {"games": found[0], "median": _eval(found[1])}


def _favorite(f: Favorite, after: AfterTen | None = None) -> dict:
    return {
        "family": f.family,
        "color": f.color,
        "games": f.games,
        "wins": f.wins,
        "draws": f.draws,
        "losses": f.losses,
        "score": _pct(f.score),
        "names": [{"name": n, "games": c} for n, c in f.names],
        "line": f.line,
        "book": f.book,
        "idea": opening_idea(f.family),
        "opening_error_rate": round(f.opening_error_rate * 100, 1),
        "fixes": [_fix(x) for x in f.fixes],
        "after_ten": _after_ten(after),
    }


def _line(line: review.Line) -> dict:
    return {
        "kind": line.kind,
        "label": line.label,
        "start": line.start,
        "plies": [
            {"san": p.san, "uci": p.uci, "fen": p.fen, "number": p.number, "engine": p.engine}
            for p in line.plies
        ],
        "score": line.score.text if line.score else None,
        "why": line.why,
        "faults": line.faults,
        "ideas": line.ideas,
    }


class Answer(BaseModel):
    id: str
    uci: str  # "?" gives up
    record: bool = True  # only the first answer to a puzzle counts; tries after it pass False


class ThreatGuess(BaseModel):
    id: str
    uci: str  # a move of theirs from the puzzle position, as if you passed


class Explore(BaseModel):
    fen: str
    moves: list[str]


class CoachMove(BaseModel):
    fen: str
    uci: str
    best: str | None = None  # the move stored for this position, used when there's no engine


class CardAnswer(BaseModel):
    id: str  # a due opening card
    uci: str  # your move, or "?" when you asked to see it


class CoachAnswer(BaseModel):
    family: str
    color: str
    path: list[str]  # UCI moves from the start to the position you answered
    uci: str  # your move, or "?" when you asked to see it
    correct: bool  # judged by the page: your usual sound move, or /api/coach/check


def _pct(value: float | None) -> int | None:
    return None if value is None else round(value * 100)


def _number(board: chess.Board) -> str:
    return f"{board.fullmove_number}{'.' if board.turn == chess.WHITE else '...'}"


def _drill_opening(p: Puzzle, tag: str | None, game_family: str | None) -> str | None:
    """The opening a drill is shown under: an opening fix's own opening (its tag after
    "opening-fix"), any other puzzle's game's. A fix two openings share (Modern and Pirc 3...Bg7)
    has both names: drilled as one of them (the tag asked for), it is that one's."""
    if "opening-fix" not in p.tags:
        return game_family
    if tag in p.tags[1:] and tag in opening_families():  # not a theme such as "opening"
        return tag
    return p.tags[1] if len(p.tags) > 1 else None


def _last_number(board: chess.Board) -> str:
    """The number of the move that led to `board`: "12..." when White is to move, else "13."."""
    if board.turn == chess.WHITE:
        return f"{board.fullmove_number - 1}..."
    return f"{board.fullmove_number}."


def create_app(db_path: Path, engine: Callable[[], Engine] = Engine) -> FastAPI:
    analyst = Analyst(engine)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        analyst.close()

    app = FastAPI(title="chess-coach", docs_url=None, redoc_url=None, lifespan=lifespan)
    cache: dict[str, tuple[tuple[int, int], object]] = {}

    def puzzle_or_404(store: Store, puzzle_id: str) -> Puzzle:
        puzzle = next((p for p in store.puzzles() if p.id == puzzle_id), None)
        if puzzle is None:
            raise HTTPException(404, "no such puzzle")
        return puzzle

    def data(store: Store):
        games = list(store.iter_games())
        moves = list(store.iter_moves(user_only=True))
        return games, moves, (sum(1 for g in games if g.analyzed_depth), len(moves))

    def cached(name: str, key: tuple[int, ...], make):
        if name in cache and cache[name][0] == key:
            return cache[name][1]
        value = make()
        cache[name] = (key, value)
        return value

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return resources.files("chess_coach.web").joinpath("static/index.html").read_text()

    @app.get("/static/{name}")
    def static(name: str) -> Response:
        types = {".js": "text/javascript", ".css": "text/css"}
        suffix = Path(name).suffix
        if suffix not in types or "/" in name:
            raise HTTPException(404)
        path = resources.files("chess_coach.web").joinpath(f"static/{name}")
        if not path.is_file():
            raise HTTPException(404)
        return Response(path.read_text(), media_type=types[suffix])

    @app.get("/api/overview")
    def overview() -> dict:
        with Store(db_path) as store:
            games, moves, key = data(store)
            checks = store.count_threats()

        def make() -> dict:
            with Store(db_path) as store:
                threats = store.threats()
                errors = list(store.iter_moves(judgements=ERRORS)) if threats else []
            d = build(games, moves, threats=threats, errors=errors)
            analysed = [g for g in games if g.analyzed_depth]
            rows = move_rows(analysed, moves)
            return {
                "user": d.user,
                "games": d.games,
                "moves": d.moves,
                "first": d.first,
                "last": d.last,
                "error_rate": round(d.error_rate * 100, 1),
                "acpl": round(d.acpl),
                "losses": d.losses,
                "decided": d.decided,
                "bullet_games": d.bullet_games,
                "habits": [_habit(h, threats) for h in missed_kinds(rows) + from_motifs(d.motifs)],
                "situations": [_habit(h, threats) for h in from_patterns(d.move_patterns)],
                "pressure": [_habit(h, threats) for h in from_pressure(d.pressure)],
                "rushed_moves": d.rushed_moves,
                "threats": _threats(d.threats),
            }

        return cached("overview", (*key, checks), make)

    @app.get("/api/punish")
    def punish() -> dict:
        """When they blunder: your punish rate against your opponents' (coach/punish.py)."""
        with Store(db_path) as store:
            games = [g for g in store.iter_games() if g.analyzed_depth]
            key = (len(games), store.count_moves())

        def make() -> dict:
            with Store(db_path) as store:
                r = report(games, store.gifts(GIFT_WP_LOSS))
            return {
                "gifts": r.gifts,
                "groups": [_split(s) for s in r.groups],
                # Where you take only part of their blunders more often than your opponents.
                "partial_focus": r.partial_focus,
                "partial_lesson": r.partial_lesson,
                "partials": [_miss(g) for g in r.partials],
                # From here on a miss is partial or clear together.
                "kinds": [{**_compare(c), "name": KINDS[c.label]} for c in r.kinds],
                "lessons": r.lessons,
                "trend": [_compare(c) for c in r.trend],
                "focus": r.focus,
                # Each with the part behind most of its gap, which its examples come from.
                "families": [
                    {
                        **_compare(f.compare),
                        "part": {**_compare(f.part), "name": OUTCOMES[f.part.label]},
                        "part_note": part_note(f.part),
                        "examples": [_miss(g) for g in f.examples],
                    }
                    for f in r.families
                ],
                # Shown when no opening stands out (maybe none: worst_title says what they are).
                "worst": [_miss(g) for g in r.worst],
                "worst_part": r.worst_part,
                "worst_title": worst_title(r),
            }

        return cached("punish", key, make)

    @app.get("/api/repeats")
    def repeat_offenders() -> list[dict]:
        """The mistakes you repeat from the same position (coach.repeats), most games first."""
        with Store(db_path) as store:
            games, moves, key = data(store)
            puzzles = store.puzzles()
            return cached(
                "repeats",
                (*key, len(puzzles)),
                lambda: [
                    _repeat(r) for r in repeats(games, moves, store.replies_to_errors(), puzzles)
                ],
            )

    @app.get("/api/openings")
    def openings() -> dict:
        with Store(db_path) as store:
            games, moves, key = data(store)

        def make() -> dict:
            with Store(db_path) as store:  # both sides' moves: ply 20 is Black's move
                early = list(store.iter_moves(max_ply=LEVEL_PLY))
            ranked = after_ten(games, early)
            by_key = {(a.family, a.color): a for rows in ranked.values() for a in rows}
            out: dict = {
                c: [_favorite(f, by_key.get((f.family, f.color))) for f in fs]
                for c, fs in favorites(games, moves).items()
            }
            out["after_ten"] = {c: [_after_ten(a) for a in rows] for c, rows in ranked.items()}
            return out

        return cached("openings", key, make)

    def practice_facts(store: Store) -> Facts:
        key = (store.count_games(), store.count_moves(), len(store.puzzles()))
        return cached("practice", key, lambda: read_facts(store))

    @app.get("/api/drill/next")
    def drill_next(tag: str | None = None) -> dict:
        with Store(db_path) as store:
            again, new = practice_queue(store, practice_facts(store), practice_day(), tag)
            if not again and not new:
                left = practice_facts(store).left_out
                return {"done": True, "puzzles": sum(p.id not in left for p in store.puzzles(tag))}
            return {
                "again": len(again),
                "new": len(new),
                **drill_item(store, [*again, *new][0], tag),
            }

    def drill_item(store: Store, p: Puzzle, tag: str | None = None) -> dict:
        """A puzzle as the page shows it."""
        game = store.get_game(p.game_id)
        threat = None if "opening-fix" in p.tags else store.threat(p.game_id, p.ply)
        board = chess.Board(p.fen)
        ask = None
        if threat is not None and threat.label == IGNORED and threat.last_uci:
            # First question: what does their last move threaten? (see /api/drill/threat)
            ask = {"last": f"{_last_number(board)}{threat.last_san}", "last_uci": threat.last_uci}
        family = _drill_opening(p, tag, game.opening_family if game else None)
        early = board.fullmove_number <= OPENING_NOTE_MOVES
        return {
            "done": False,
            "id": p.id,
            "fen": p.fen,
            "svg": board_svg(p.fen, size=400),
            "side": "white" if board.turn == chess.WHITE else "black",
            "played": p.played_san,
            "tags": p.tags,
            "opening_fix": "opening-fix" in p.tags,
            "opening": family,
            "opening_idea": opening_idea(family) if early else None,
            "game": game.move_url(p.ply) if game else None,
            "ask_threat": ask,
        }

    @app.post("/api/drill/threat")
    def drill_threat(g: ThreatGuess) -> dict:
        """Practice's first question on a puzzle from an ignored threat: what do they threaten?
        Graded leniently (engine.threats.same_threat) and not recorded: the puzzle is."""
        with Store(db_path) as store:
            puzzle = puzzle_or_404(store, g.id)
            t = store.threat(puzzle.game_id, puzzle.ply)
        if t is None or t.label != IGNORED or "opening-fix" in puzzle.tags:
            raise HTTPException(404, "no threat question for this puzzle")
        theirs = pass_turn(chess.Board(puzzle.fen))
        try:
            guess = review.legal(theirs, g.uci)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        threat = chess.Move.from_uci(t.threat)
        why = their_threat(
            puzzle.fen, t.threat, last_san=t.last_san, last_uci=t.last_uci, mate=t.mate
        )
        return {
            "right": same_threat(theirs, guess, threat),
            "guess": theirs.san(guess),
            "threat": {"uci": t.threat, "san": theirs.san(threat)},
            "why": f"{why}." if why else None,
        }

    @app.post("/api/drill/answer")
    def drill_answer(a: Answer) -> dict:
        now = datetime.now(UTC)
        with Store(db_path) as store:
            puzzle = puzzle_or_404(store, a.id)
            schedule_now = store.review(a.id)
            if schedule_now is None:
                raise HTTPException(404, "no such puzzle")
            board = chess.Board(puzzle.fen)
            tried = None
            if a.uci != "?":
                try:
                    tried = review.legal(board, a.uci)
                except ValueError as exc:
                    raise HTTPException(400, str(exc)) from exc
            correct = tried is not None and puzzle.is_right(tried.uci())
            due = schedule_now.due
            if a.record:
                after = schedule(schedule_now, correct, practice_day(now))
                store.save_attempt(after, a.uci, correct, now)
                due = after.due
        out = {
            "correct": correct,
            "answer": board.san(tried) if tried is not None else None,
            "uci": tried.uci() if tried is not None else None,
            "solution": puzzle.solution_san,
            "also": [board.san(chess.Move.from_uci(u)) for u in puzzle.accept],
            "recorded": a.record,
            "next_due": due,
        }
        if tried is not None and not correct:
            # Why it fails, without naming the answer, so the puzzle can be tried again.
            out["tried"] = _line(review.attempt(puzzle, tried.uci(), analyst, reveal=False))
        return out

    @app.get("/api/drill/review")
    def drill_review(id: str, tried: Annotated[list[str] | None, Query()] = None) -> dict:
        with Store(db_path) as store:
            puzzle = puzzle_or_404(store, id)
        try:
            lines = review.review(puzzle, tried or [], analyst)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"lines": [_line(x) for x in lines]}

    @app.post("/api/explore")
    def explore(e: Explore) -> dict:
        if len(e.moves) > 200:
            raise HTTPException(400, "too many moves")
        try:
            return _line(review.analysis(e.fen, e.moves, analyst))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/api/board")
    def board_image(
        fen: str,
        orientation: str = "white",
        last: str | None = None,
        arrow: str | None = None,
        color: str = "best",
    ) -> Response:
        if color not in ARROWS or orientation not in ("white", "black"):
            raise HTTPException(400, "unknown colour or orientation")
        try:
            svg = board_svg(
                fen,
                [(arrow, ARROWS[color])] if arrow else [],
                size=400,
                orientation=chess.WHITE if orientation == "white" else chess.BLACK,
                lastmove=last,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return Response(svg, media_type="image/svg+xml")

    def opening_tree(family: str, color: str) -> tuple[optree.Node, dict[str, str], dict[str, int]]:
        """The tree, each game's result as it counts, and each game's eval after both sides'
        10th moves (past the tree's last ply, which `tree.build` stops at)."""
        with Store(db_path) as store:
            games = [
                g
                for g in store.iter_games()
                if g.analyzed_depth and g.opening_family == family and g.user_color.value == color
            ]
            key = (len(games), store.count_moves())
            name = f"tree:{color}:{family}"
            if name in cache and cache[name][0] == key:
                return cache[name][1]  # type: ignore[return-value]
            ids = {g.id for g in games}
            # both sides' moves to ply 20: the tree keeps the first 16 plies
            moves = [m for m in store.iter_moves(max_ply=LEVEL_PLY) if m.game_id in ids]
        value = (
            optree.build(games, moves),
            {g.id: result_of(g) for g in games},
            evals_after_ten(games, moves),
        )
        cache[name] = (key, value)
        return value

    def score_of(fact, sign: int) -> str:
        mate = None if fact.mate_before is None else sign * fact.mate_before
        return review.Score(sign * fact.eval_before, mate).text

    def node_json(
        root: optree.Node,
        results: dict[str, str],
        path: list[str],
        color: str,
        evals: dict[str, int],
    ):
        board = chess.Board()
        plies = []
        for uci in path:
            move = review.legal(board, uci)
            plies.append({"uci": move.uci(), "san": board.san(move), "number": _number(board)})
            board.push(move)
        mine = board.turn == (chess.WHITE if color == "white" else chess.BLACK)
        node = optree.walk(root, [p["uci"] for p in plies])
        opening = opening_table().get(board.epd())
        best = None
        if node is not None and node.best is not None:
            f = node.best
            best = {"uci": f.best_move, "san": f.best_san, "score": score_of(f, 1 if mine else -1)}
        elif (ev := analyst(board.fen())) is not None and ev.move is not None:
            sign = 1 if mine else -1
            mate = None if ev.mate is None else sign * ev.mate
            best = {
                "uci": ev.move.uci(),
                "san": board.san(ev.move),
                "score": review.Score(sign * ev.cp, mate).text,
            }
        moves = []
        edges = sorted(node.edges.values(), key=lambda e: -len(e.facts)) if node else []
        for edge in edges:
            item: dict = {
                "uci": edge.uci,
                "san": edge.san,
                "number": _number(board),
                "by": "you" if edge.by_user else "them",
                "games": len(edge.facts),
                "score": _pct(optree.score(edge.child.games, results)),
                "scored": optree.scored(edge.child.games, results),
                "verdict": edge.verdict,
                "loss": round(edge.cp_loss),
                "after_ten": _at_ten(optree.at_move_ten(edge.child.games, evals)),
            }
            if edge.punishable:
                f = edge.child.best
                item["punish"] = {"uci": f.best_move, "san": f.best_san, "score": score_of(f, 1)}
                item["answers"] = [
                    {"san": e.san, "games": len(e.facts), "right": e.uci == f.best_move}
                    for e in sorted(edge.child.edges.values(), key=lambda e: -len(e.facts))
                ]
            if edge.verdict not in ("best", "good") and node.best is not None:
                # Yours: what's wrong and what the better move does. Theirs: what it lets you do,
                # then what's wrong with it.
                why = explain(node.fen, edge.uci, node.best.best_move, theirs=not edge.by_user)
                punish = edge.child.best if edge.punishable else None
                let = allows(edge.child.fen, punish.best_move) if punish else []
                item["reasons"] = [*let, *why.all()][:3]
            moves.append(item)
        # The move to learn here: your most-played sound move, so training keeps to your
        # opening; the engine's move only where you have none.
        sound = [m for m in moves if m["by"] == "you" and m["verdict"] in ("best", "good")]
        target = {"uci": sound[0]["uci"], "san": sound[0]["san"]} if sound else best
        return {
            "fen": board.fen(),
            "turn": "you" if mine else "them",
            "target": target if mine else None,
            "path": plies,
            "name": opening.name if opening else None,
            "games": len(node.games) if node else 0,
            "score": _pct(optree.score(node.games, results)) if node else None,
            "scored": optree.scored(node.games, results) if node else 0,
            "after_ten": _at_ten(optree.at_move_ten(node.games, evals)) if node else None,
            "best": best,
            "moves": moves,
        }

    @app.get("/api/coach/openings")
    def coach_openings() -> dict:
        today = practice_day().isoformat()
        with Store(db_path) as store:
            games = list(store.iter_games())
            due = Counter((c.family, c.color) for c in store.coach_cards() if c.due <= today)
        out: dict[str, list[dict]] = {"white": [], "black": []}
        for o in optree.openings(games):
            out[o.color].append(
                {
                    "family": o.family,
                    "games": o.games,
                    "score": _pct(o.score),
                    "scored": o.scored,
                    "idea": opening_idea(o.family),
                    "due": due[(o.family, o.color)],
                }
            )
        return out

    @app.get("/api/coach/node")
    def coach_node(family: str, color: str, path: str = "") -> dict:
        if color not in ("white", "black"):
            raise HTTPException(400, "colour is white or black")
        root, results, evals = opening_tree(family, color)
        try:
            return node_json(root, results, [u for u in path.split(",") if u], color, evals)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    def coach_deck(family: str, color: str):
        """This opening's punish deck in practice order, its cards, and today's date."""
        root, _, _ = opening_tree(family, color)
        today = practice_day().isoformat()
        with Store(db_path) as store:
            cards = store.coach_cards(family, color)
            last = store.coach_last_answers(family, color)
        mistakes = optree.mistakes(root)
        deck = training.deck(mistakes, family, color, {c.id: c for c in cards}, last, today)
        return deck, cards, today

    def coach_summary(family: str, color: str) -> dict:
        deck, cards, today = coach_deck(family, color)
        s = training.summary(deck, cards, today)
        return {
            "found": s.found,
            "total": s.total,
            "due": s.due,
            "lines": s.lines,
            "due_paths": s.due_paths,
        }

    @app.get("/api/coach/punish")
    def coach_punish(family: str, color: str) -> list[dict]:
        """The opponent mistakes you meet in this opening, each with its punishment: due ones
        first, then new ones, then the rest."""
        deck, _, _ = coach_deck(family, color)
        out = []
        for d in deck:
            edge = d.edge
            board = chess.Board(edge.facts[0].fen_before)
            f = edge.child.best
            out.append(
                {
                    "path": d.path,
                    "number": _number(board),
                    "san": edge.san,
                    "games": len(edge.facts),
                    "verdict": edge.verdict,
                    "punish": {"uci": f.best_move, "san": f.best_san, "score": score_of(f, 1)},
                    "answers": [
                        {"san": e.san, "games": len(e.facts), "right": e.uci == f.best_move}
                        for e in sorted(edge.child.edges.values(), key=lambda e: -len(e.facts))
                    ],
                    "state": d.state,
                    "due": d.due,
                    "found": d.found,
                }
            )
        return out

    @app.get("/api/coach/cards")
    def coach_cards(family: str, color: str) -> dict:
        """Your practice in this opening: punishments found, cards due, and where they are."""
        return coach_summary(family, color)

    @app.post("/api/coach/answer")
    def coach_answer(a: CoachAnswer) -> dict:
        """Record your first answer at a position in opening training and schedule it again."""
        if a.color not in ("white", "black"):
            raise HTTPException(400, "colour is white or black")
        board = chess.Board()
        try:
            for uci in a.path:
                board.push(review.legal(board, uci))
            if a.uci != "?":
                review.legal(board, a.uci)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if board.turn != (chess.WHITE if a.color == "white" else chess.BLACK):
            raise HTTPException(400, "it isn't your move there")
        path = [m.uci() for m in board.move_stack]
        recorded, kind, card = save_card_answer(a.family, a.color, path, a.uci, a.correct)
        return {
            "recorded": recorded,
            "kind": kind,
            "next_due": card.due if card else None,
            **coach_summary(a.family, a.color),
        }

    def save_card_answer(
        family: str, color: str, path: list[str], uci: str, correct: bool
    ) -> tuple[bool, str, CoachCard | None]:
        """Record an answer and schedule the card again. One answer a day counts, so going back
        over a line doesn't push a card away. Returns (recorded, kind, the card)."""
        root, _, _ = opening_tree(family, color)
        kind = training.kind_at(root, path)
        now = datetime.now(UTC)
        today = practice_day(now)
        cid = card_id(color, family, path)
        with Store(db_path) as store:
            before = store.coach_card(cid)
            if store.coach_answered(cid, today.isoformat()):
                return False, kind, before
            card = training.answer(before, family, color, path, kind, correct, today)
            store.save_coach_attempt(card, uci, correct, now)
        return True, kind, card

    def check_json(board: chess.Board, move: chess.Move, stored: chess.Move | None) -> dict:
        """What the page says about a move in opening training, in chess terms."""
        c = training.check_move(board, move, analyst, stored)
        fen = board.fen()
        reasons = explain(fen, move.uci(), c.best.uci()) if c.best and not c.ok else Reasons()
        score = None
        if c.reply is not None:
            mate = None if c.reply.mate is None else -c.reply.mate
            score = review.Score(-c.reply.cp, mate).text
        return {
            "ok": c.ok,
            "uci": move.uci(),
            "san": board.san(move),
            "best": {"uci": c.best.uci(), "san": board.san(c.best)} if c.best else None,
            "loss": None if c.loss is None else round(c.loss / 100, 1),
            "score": score,
            "faults": reasons.faults,
            "ideas": reasons.ideas,
        }

    @app.post("/api/coach/check")
    def coach_check(c: CoachMove) -> dict:
        """Is this move good enough here? Within COACH_MARGIN_CP of the engine's best counts."""
        try:
            board = chess.Board(c.fen)
            move = review.legal(board, c.uci)
            stored = review.legal(board, c.best) if c.best else None
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return check_json(board, move, stored)

    def card_item(card: CoachCard) -> dict:
        """A due opening card as Today shows it. It doesn't name the move to find."""
        moves = [chess.Move.from_uci(u) for u in card.path]
        board = chess.Board()
        line = board.variation_san(moves) if moves else ""
        for m in moves:
            board.push(m)
        return {
            "kind": "card",
            "id": card.id,
            "fen": board.fen(),
            "side": card.color,
            "punish": card.kind == training.PUNISH,
            "opening": card.family,
            "opening_idea": opening_idea(card.family),
            "line": line,
            "last": card.path[-1] if card.path else None,
        }

    @app.post("/api/today/card")
    def today_card(a: CardAnswer) -> dict:
        """Answer a due opening card: judged as in Learn openings, with the move to learn."""
        with Store(db_path) as store:
            card = store.coach_card(a.id)
        if card is None:
            raise HTTPException(404, "no such card")
        board = chess.Board()
        for uci in card.path:
            board.push(chess.Move.from_uci(uci))
        try:
            move = None if a.uci == "?" else review.legal(board, a.uci)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        root, _, _ = opening_tree(card.family, card.color)
        judged = training.judge(card.kind, optree.walk(root, card.path), board, move, analyst)
        _, _, saved = save_card_answer(card.family, card.color, card.path, a.uci, judged.correct)
        t = judged.target
        check = judged.check
        reasons = (
            explain(board.fen(), move.uci(), check.best.uci())
            if move and check and check.best and not check.ok
            else Reasons()
        )
        return {
            "correct": judged.correct,
            "answer": board.san(move) if move else None,
            "target": {"uci": t.uci(), "san": board.san(t)} if t else None,
            "loss": None if check is None or check.loss is None else round(check.loss / 100, 1),
            "faults": reasons.faults,
            "ideas": reasons.ideas,
            "next_due": saved.due if saved else None,
        }

    @app.get("/api/today/next")
    def today_next() -> dict:
        """What to practise next in today's session; when it's over, how the practice is going."""
        day = practice_day()
        with Store(db_path) as store:
            facts = practice_facts(store)
            now = session(store, facts, day)
            progress = {
                "done": now.done,
                "total": now.total,
                "again": now.again,
                "new": now.new,
                "minutes": now.minutes,
            }
            if now.items:
                item = now.items[0]
                if item.card is not None:
                    shown = card_item(item.card)
                else:
                    shown = {"kind": "puzzle", **drill_item(store, item.puzzle)}
                return {"done": False, "progress": progress, **shown}
            tomorrow = upcoming(store, facts, day + timedelta(days=1))
            attempts, answers = store.attempts(), store.coach_attempts()
            puzzles = sum(p.id not in facts.left_out for p in store.puzzles())
        reviews, first = accuracy(attempts, answers)
        return {
            "done": True,
            "progress": progress,
            "puzzles": puzzles,
            "tomorrow": {"again": tomorrow.again, "new": tomorrow.new},
            "days": {"practised": days_practised(attempts, answers, day), "of": 7},
            "reviews": {"right": reviews.right, "tried": reviews.tried},
            "first": {"right": first.right, "tried": first.tried},
        }

    @app.get("/api/drill/tags")
    def drill_tags() -> list[dict]:
        with Store(db_path) as store:
            facts = practice_facts(store)
            puzzles = [p for p in store.puzzles() if p.id not in facts.left_out]
            again, new = practice_queue(store, facts, practice_day())
        due, fresh = {p.id for p in again}, {p.id for p in new}
        counts: dict[str, list[int]] = {}
        for p in puzzles:
            for t in p.tags:
                c = counts.setdefault(t, [0, 0, 0])
                c[0] += 1
                c[1] += p.id in due
                c[2] += p.id in fresh
        return [
            {"tag": t, "puzzles": n, "due": d, "new": f}
            for t, (n, d, f) in sorted(counts.items(), key=lambda kv: -kv[1][0])
        ]

    @app.get("/api/progress")
    def progress(by: str = "month", speed: str | None = None) -> dict:
        with Store(db_path) as store:
            games, moves, _ = data(store)
            attempts, answers = store.attempts(), store.coach_attempts()
            overall, tags = solve_rates(store.puzzles(), attempts)
        speeds = trend_speeds(games)
        chosen = speed if speed in speeds else (speeds[0] if speeds else None)
        today = practice_day()
        weeks = weekly(games, chosen, attempts, answers, today=today) if chosen else []
        effect = practice_effect(weeks)
        return {
            "trend": {
                "speeds": speeds,
                "speed": chosen,
                "weeks": [
                    {
                        "label": w.label,
                        "games": w.games,
                        "rating": w.rating,
                        "change": w.change,
                        "puzzles": w.puzzles,
                        "solved": w.solved,
                        "answers": w.answers,
                        "right": w.right,
                    }
                    for w in weeks
                ],
                "effect": None
                if effect is None
                else {
                    "practised": round(effect[0], 1),
                    "practised_weeks": effect[1],
                    "idle": round(effect[2], 1),
                    "idle_weeks": effect[3],
                },
            },
            "periods": [
                {
                    "label": p.label,
                    "games": p.games,
                    "score": _pct(p.score),
                    "error_rate": round(p.error_rate * 100, 1),
                    "acpl": round(p.acpl),
                }
                for p in periods(games, moves, by)
            ],
            "puzzles": {"attempts": overall.attempts, "solved": overall.solved},
            "tags": [
                {"tag": t.name, "attempts": t.attempts, "rate": round(t.rate * 100)} for t in tags
            ],
        }

    return app
