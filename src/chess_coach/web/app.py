"""A small local web app over the same database: what you get wrong (with boards), practice,
and your openings. Run it with `chess-coach serve`; it binds to localhost and needs no account or
key."""

from __future__ import annotations

import threading
from collections import Counter
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
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
from chess_coach.coach.principles import Reasons, explain, opening_idea
from chess_coach.coach.progress import (
    periods,
    practice_effect,
    solve_rates,
    trend_speeds,
    weekly,
)
from chess_coach.coach.repertoire import Favorite, Fix, favorites
from chess_coach.db import Store
from chess_coach.engine.stockfish import Engine, EngineNotFoundError, LineEval
from chess_coach.models import Puzzle, card_id
from chess_coach.openings import opening_table
from chess_coach.patterns.features import move_rows, result_of
from chess_coach.patterns.mining import Evidence
from chess_coach.puzzles import review
from chess_coach.puzzles.srs import schedule

PLAYED = "#d1495b"  # your move
BETTER = "#2a9d8f"  # the engine's move
TRIED = "#3d5a80"  # your answer in practice
ARROWS = {"best": BETTER, "played": PLAYED, "tried": TRIED}
OPENING_NOTE_MOVES = 15  # puzzles this early get a note on what their opening is about
COACH_MARGIN_CP = 30  # in opening training, a move this close to the engine's best counts
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


def _example(e: Evidence) -> dict:
    return {
        "svg": board_svg(e.fen, [(e.uci, PLAYED), (e.best_uci, BETTER)]) if e.fen else "",
        "move": e.move,
        "best": e.best_san,
        "loss": round(e.wp_loss),
        "url": e.url,
        "reasons": explain(e.fen, e.uci, e.best_uci).all()[:3] if e.fen and e.uci else [],
    }


def _habit(h: Habit) -> dict:
    return {
        "title": h.title,
        "detail": h.detail,
        "lesson": h.lesson,
        "mistakes": h.mistakes,
        "games": h.games,
        "practice": h.practice,
        "examples": [_example(e) for e in h.examples],
    }


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


def _favorite(f: Favorite) -> dict:
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


class Explore(BaseModel):
    fen: str
    moves: list[str]


class CoachMove(BaseModel):
    fen: str
    uci: str
    best: str | None = None  # the move stored for this position, used when there's no engine


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

    def cached(name: str, key: tuple[int, int], make) -> dict:
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

        def make() -> dict:
            d = build(games, moves)
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
                "habits": [_habit(h) for h in missed_kinds(rows) + from_motifs(d.motifs)],
                "situations": [_habit(h) for h in from_patterns(d.move_patterns)],
                "pressure": [_habit(h) for h in from_pressure(d.pressure)],
                "rushed_moves": d.rushed_moves,
            }

        return cached("overview", key, make)

    @app.get("/api/openings")
    def openings() -> dict:
        with Store(db_path) as store:
            games, moves, key = data(store)
        return cached(
            "openings",
            key,
            lambda: {c: [_favorite(f) for f in fs] for c, fs in favorites(games, moves).items()},
        )

    @app.get("/api/drill/next")
    def drill_next(tag: str | None = None) -> dict:
        today = datetime.now(UTC).date().isoformat()
        with Store(db_path) as store:
            due = store.due(today, tag, 1)
            total_due = len(store.due(today, tag))
            if not due:
                return {"done": True, "puzzles": len(store.puzzles(tag))}
            p = due[0]
            game = store.get_game(p.game_id)
        board = chess.Board(p.fen)
        family = p.tags[1] if "opening-fix" in p.tags else (game.opening_family if game else None)
        early = board.fullmove_number <= OPENING_NOTE_MOVES
        return {
            "done": False,
            "id": p.id,
            "fen": p.fen,
            "svg": board_svg(p.fen, size=400),
            "side": "white" if board.turn == chess.WHITE else "black",
            "played": p.played_san,
            "tags": p.tags,
            "due": total_due,
            "opening_fix": "opening-fix" in p.tags,
            "opening": family,
            "opening_idea": opening_idea(family) if early else None,
            "game": (game.url + f"#{p.ply}")
            if game and game.url and "lichess" in game.url
            else (game.url if game else None),
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
                after = schedule(schedule_now, correct, now.date())
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

    def opening_tree(family: str, color: str) -> tuple[optree.Node, dict[str, str]]:
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
            moves = [m for m in store.iter_moves(max_ply=optree.TREE_PLIES) if m.game_id in ids]
        value = (optree.build(games, moves), {g.id: result_of(g) for g in games})
        cache[name] = (key, value)
        return value

    def score_of(fact, sign: int) -> str:
        mate = None if fact.mate_before is None else sign * fact.mate_before
        return review.Score(sign * fact.eval_before, mate).text

    def node_json(root: optree.Node, results: dict[str, str], path: list[str], color: str):
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
            }
            if edge.punishable:
                f = edge.child.best
                item["punish"] = {"uci": f.best_move, "san": f.best_san, "score": score_of(f, 1)}
                item["answers"] = [
                    {"san": e.san, "games": len(e.facts), "right": e.uci == f.best_move}
                    for e in sorted(edge.child.edges.values(), key=lambda e: -len(e.facts))
                ]
            if edge.by_user and edge.verdict not in ("best", "good") and node.best is not None:
                item["reasons"] = explain(node.fen, edge.uci, node.best.best_move).all()[:3]
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
            "best": best,
            "moves": moves,
        }

    @app.get("/api/coach/openings")
    def coach_openings() -> dict:
        today = datetime.now(UTC).date().isoformat()
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
        root, results = opening_tree(family, color)
        try:
            return node_json(root, results, [u for u in path.split(",") if u], color)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    def coach_deck(family: str, color: str):
        """This opening's punish deck in practice order, its cards, and today's date."""
        root, _ = opening_tree(family, color)
        today = datetime.now(UTC).date().isoformat()
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
        root, _ = opening_tree(a.family, a.color)
        parent = optree.walk(root, path[:-1]) if path else None
        edge = parent.edges.get(path[-1]) if parent else None
        kind = training.PUNISH if edge is not None and edge.punishable else training.LINE
        now = datetime.now(UTC)
        cid = card_id(a.color, a.family, path)
        with Store(db_path) as store:
            before = store.coach_card(cid)
            # One answer a day counts, so going back over a line doesn't push a card away.
            recorded = not store.coach_answered(cid, now.date().isoformat())
            if recorded:
                card = training.answer(before, a.family, a.color, path, kind, a.correct, now.date())
                store.save_coach_attempt(card, a.uci, a.correct, now)
            else:
                card = before
        return {
            "recorded": recorded,
            "kind": kind,
            "next_due": card.due if card else None,
            **coach_summary(a.family, a.color),
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
        after = board.copy(stack=False)
        after.push(move)
        ev, reply = analyst(board.fen()), analyst(after.fen())
        loss = score = None
        if ev is not None and ev.move is not None and reply is not None:
            best = ev.move
            loss = max(0, ev.cp + reply.cp)  # the reply is scored for the other side
            ok = move == best or loss <= COACH_MARGIN_CP
            score = review.Score(-reply.cp, None if reply.mate is None else -reply.mate).text
        else:
            best = stored
            ok = best is None or move == best
        reasons = explain(c.fen, move.uci(), best.uci()) if best and not ok else Reasons()
        return {
            "ok": ok,
            "uci": move.uci(),
            "san": board.san(move),
            "best": {"uci": best.uci(), "san": board.san(best)} if best else None,
            "loss": None if loss is None else round(loss / 100, 1),
            "score": score,
            "faults": reasons.faults,
            "ideas": reasons.ideas,
        }

    @app.get("/api/drill/tags")
    def drill_tags() -> list[dict]:
        today = datetime.now(UTC).date().isoformat()
        with Store(db_path) as store:
            puzzles = store.puzzles()
            due = {p.id for p in store.due(today)}
        counts: dict[str, list[int]] = {}
        for p in puzzles:
            for t in p.tags:
                c = counts.setdefault(t, [0, 0])
                c[0] += 1
                c[1] += p.id in due
        return [
            {"tag": t, "puzzles": n, "due": d}
            for t, (n, d) in sorted(counts.items(), key=lambda kv: -kv[1][0])
        ]

    @app.get("/api/progress")
    def progress(by: str = "month", speed: str | None = None) -> dict:
        with Store(db_path) as store:
            games, moves, _ = data(store)
            attempts, answers = store.attempts(), store.coach_attempts()
            overall, tags = solve_rates(store.puzzles(), attempts)
        speeds = trend_speeds(games)
        chosen = speed if speed in speeds else (speeds[0] if speeds else None)
        today = datetime.now(UTC).date()
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
