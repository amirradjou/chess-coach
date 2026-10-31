from datetime import UTC, date, datetime
from pathlib import Path

import chess
from typer.testing import CliRunner

from chess_coach.cli import app
from chess_coach.coach.digest import build, render
from chess_coach.coach.progress import periods, solve_rates
from chess_coach.coach.study import openings
from chess_coach.db import Store
from chess_coach.engine.stockfish import LineEval
from chess_coach.models import Color, Game, Judgement, MoveFact, Phase, Review, Speed
from chess_coach.puzzles.make import make, verify
from chess_coach.puzzles.srs import schedule

START = chess.STARTING_FEN
E4, D4, A3 = (chess.Move.from_uci(u) for u in ("e2e4", "d2d4", "a2a3"))
# White to move with 1.e4 and 1.a3 legal: the start, after 1.Nc3 Nc6, after 1.Nf3 Nf6.
POSITIONS = [
    START,
    "r1bqkbnr/pppppppp/2n5/8/8/2N5/PPPPPPPP/R1BQKBNR w KQkq - 2 2",
    "rnbqkb1r/pppppppp/5n2/8/8/5N2/PPPPPPPP/RNBQKB1R w KQkq - 2 2",
]


class FakeEngine:
    def __init__(self, lines: list[LineEval]):
        self.lines = lines
        self.calls = 0

    def evaluate(self, board, *, depth, multipv=1):
        self.calls += 1
        return self.lines


def game(i: int, *, month: int = 10, result: str = "0-1", opening: str = "Modern Defense") -> Game:
    return Game(
        id=f"lichess:g{i}",
        source="lichess",
        source_id=f"g{i}",
        user="me",
        user_color=Color.WHITE,
        white="me",
        black="them",
        result=result,
        pgn="1. e4 e5 *",
        url=f"https://lichess.org/g{i}",
        played_at=datetime(2026, month, 1 + i % 25, tzinfo=UTC),
        speed=Speed.BLITZ,
        opening=opening,
        analyzed_depth=12,
    )


def blunder(game_id: str, ply: int = 1, phase: Phase = Phase.OPENING) -> MoveFact:
    return MoveFact(
        game_id=game_id,
        ply=ply,
        move_number=1,
        color=Color.WHITE,
        is_user=True,
        san="a3",
        uci="a2a3",
        fen_before=START,
        eval_before=30,
        eval_after=-250,
        best_move="e2e4",
        best_san="e4",
        cp_loss=280,
        wp_before=52.0,
        wp_after=27.0,
        wp_loss=25.0,
        judgement=Judgement.BLUNDER,
        phase=phase,
        depth=12,
        motifs=["missed_tactic"],
    )


# -- SM-2 -----------------------------------------------------------------------------------


def test_schedule_grows_on_solves_and_resets_on_a_miss():
    r = Review("p", "2026-10-29")
    today = date(2026, 10, 29)
    r1 = schedule(r, True, today)
    r2 = schedule(r1, True, today)
    r3 = schedule(r2, True, today)
    assert [r1.interval, r2.interval, r3.interval] == [1, 6, round(6 * r3.ease)]
    assert r1.due == "2026-10-30"
    miss = schedule(r3, False, today)
    assert (miss.interval, miss.reps, miss.lapses, miss.due) == (1, 0, 1, "2026-10-30")
    assert miss.ease < r3.ease and miss.ease >= 1.3


# -- verification ---------------------------------------------------------------------------


def test_a_clear_best_move_makes_a_puzzle():
    engine = FakeEngine(
        [LineEval(300, None, E4, [E4, chess.Move.from_uci("e7e5")]), LineEval(40, None, D4)]
    )
    p = verify(engine, blunder("lichess:g1"))
    assert p is not None
    assert (p.solution, p.solution_san, p.played_san, p.gap_cp) == ("e2e4", "e4", "a3", 260)
    assert p.line == "e7e5" and "missed_tactic" in p.tags and "opening" in p.tags


def test_close_alternatives_or_the_played_move_are_not_puzzles():
    close = FakeEngine([LineEval(300, None, E4), LineEval(200, None, D4)])
    assert verify(close, blunder("lichess:g1")) is None
    played = FakeEngine([LineEval(300, None, A3), LineEval(0, None, D4)])
    assert verify(played, blunder("lichess:g1")) is None


def test_the_only_mate_is_a_puzzle_whatever_the_gap():
    engine = FakeEngine([LineEval(99_990, 2, E4), LineEval(99_000, None, D4)])
    p = verify(engine, blunder("lichess:g1"))
    assert p is not None and "mate" in p.tags


# -- store, make and drill ------------------------------------------------------------------


def _store_with_blunders(path: Path, n: int = 3, same_position: bool = False) -> None:
    with Store(path) as store:
        for i in range(n):
            g = game(i)
            store.upsert_game(g)
            m = blunder(g.id)
            m.fen_before = START if same_position else POSITIONS[i]
            store.replace_moves(g.id, [m], depth=12)


def test_make_checks_each_candidate_once(tmp_path: Path):
    db = tmp_path / "c.db"
    _store_with_blunders(db)
    engine = FakeEngine([LineEval(300, None, E4), LineEval(0, None, D4)])
    with Store(db) as store:
        assert (make(store, engine, limit=10).made, engine.calls) == (3, 3)
        assert make(store, engine, limit=10).checked == 0  # nothing new to check
        assert len(store.due(datetime.now(UTC).date().isoformat())) == 3


def test_a_slip_repeated_from_the_same_position_is_one_puzzle(tmp_path: Path):
    db = tmp_path / "c.db"
    _store_with_blunders(db, same_position=True)
    with Store(tmp_path / "c.db") as store:
        for i, tag in enumerate(("hanging_piece", "pawn_grab")):
            store.conn.execute(
                "UPDATE moves SET motifs = ? WHERE game_id = ?", (f'["{tag}"]', f"lichess:g{i}")
            )
        store.conn.commit()
    engine = FakeEngine([LineEval(300, None, E4), LineEval(0, None, D4)])
    with Store(db) as store:
        result = make(store, engine, limit=10)
        assert (result.checked, result.made) == (3, 1)
        (p,) = store.puzzles()
        assert p.id == "lichess:g2:1"  # the newest game is checked first
        assert p.tags == ["missed_tactic", "opening", "pawn_grab", "hanging_piece"]
        assert make(store, engine, limit=10).checked == 0


def test_drill_records_attempts_and_reschedules(tmp_path: Path):
    db = tmp_path / "c.db"
    _store_with_blunders(db, 2)
    with Store(db) as store:
        make(store, FakeEngine([LineEval(300, None, E4), LineEval(0, None, D4)]), limit=10)
    runner = CliRunner()
    # First puzzle: an illegal move, then the right one in SAN. Second: give up.
    result = runner.invoke(app, ["--db", str(db), "drill"], input="Ke2\ne4\n?\n")
    assert result.exit_code == 0, result.output
    assert "Correct." in result.output and "The move was e4." in result.output
    assert "Solved 1 of 2." in result.output
    with Store(db) as store:
        attempts = store.attempts()
        assert [a[2] for a in attempts] == [True, False]
        reviews = [store.review(p.id) for p in store.puzzles()]
        assert sorted(r.lapses for r in reviews) == [0, 1]
        assert store.due(datetime.now(UTC).date().isoformat()) == []
    again = runner.invoke(app, ["--db", str(db), "drill"])
    assert "Nothing due" in again.output


def test_drill_takes_uci(tmp_path: Path):
    db = tmp_path / "c.db"
    _store_with_blunders(db, 1)
    with Store(db) as store:
        make(store, FakeEngine([LineEval(300, None, E4), LineEval(0, None, D4)]), limit=10)
    result = CliRunner().invoke(app, ["--db", str(db), "drill"], input="e2e4\n")
    assert "Correct." in result.output


# -- progress, openings, weekly digest ------------------------------------------------------


def test_periods_and_solve_rates():
    games = [game(1, month=9, result="1-0"), game(2, month=10), game(3, month=10, result="1/2-1/2")]
    moves = [blunder("lichess:g1"), blunder("lichess:g2")]
    moves[0].judgement = Judgement.GOOD
    rows = periods(games, moves)
    assert [(r.label, r.games, r.moves, r.errors) for r in rows] == [
        ("2026-09", 1, 1, 0),
        ("2026-10", 2, 1, 1),
    ]
    assert rows[0].score == 1.0 and rows[1].score == 0.25
    from chess_coach.models import Puzzle

    p = Puzzle("x", "g", 1, START, "e2e4", "e4", "a3", "", 200, 18, ["mate", "opening"])
    overall, tags = solve_rates([p], [("x", "t1", True), ("x", "t2", False)])
    assert (overall.attempts, overall.solved) == (2, 1)
    assert {(t.name, t.attempts, t.solved) for t in tags} == {("mate", 2, 1), ("opening", 2, 1)}


def test_openings_find_the_repeated_slip():
    games = [game(i) for i in range(6)]
    moves = [blunder(g.id) for g in games[:4]]
    stats = openings(games, moves, min_games=5)
    assert len(stats) == 1
    o = stats[0]
    assert (o.family, o.color, o.games, o.losses) == ("Modern Defense", "white", 6, 6)
    assert o.book is not None and o.book.startswith("1. ")  # the table's line for the name
    assert [(s.move, s.best, s.games) for s in o.slips] == [("1.a3", "e4", 4)]


def test_digest_since_compares_with_before():
    old = [game(i, month=9) for i in range(3)]
    new = [game(i + 10, month=10) for i in range(3)]
    moves = [blunder(g.id) for g in old]
    for g in new:
        m = blunder(g.id)
        m.judgement = Judgement.GOOD
        moves.append(m)
    text = render(build(old + new, moves, since=datetime(2026, 10, 1, tzinfo=UTC)))
    assert text.startswith("# Chess digest: me, since 2026-10-01")
    assert "3 analysed games" in text
    assert "better than before 2026-10-01: 100.0% of moves" in text
