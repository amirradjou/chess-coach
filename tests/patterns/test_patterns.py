from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from chess_coach.cli import app
from chess_coach.coach.digest import build, describe, render
from chess_coach.db import Store
from chess_coach.models import Color, Game, Judgement, MoveFact, Phase, Speed
from chess_coach.patterns.features import castling, clock_bucket, game_rows, move_bucket, move_rows
from chess_coach.patterns.mining import mine_games, mine_moves, motif_counts, wilson_lower

CASTLES = "1. e4 e5 2. Nf3 Nc6 3. Bc4 Bc5 4. O-O Nf6 *"
NO_CASTLE = "1. e4 e5 2. Nf3 Nc6 *"


def game(i: int, result: str = "1-0", pgn: str = CASTLES, speed: Speed = Speed.BLITZ) -> Game:
    return Game(
        id=f"lichess:g{i}",
        source="lichess",
        source_id=f"g{i}",
        user="me",
        user_color=Color.WHITE,
        white="me",
        black="them",
        result=result,
        pgn=pgn,
        url=f"https://lichess.org/g{i}",
        played_at=datetime(2026, 10, 1 + i % 20, tzinfo=UTC),
        speed=speed,
        opening="Italian Game: Giuoco Piano",
        analyzed_depth=12,
    )


def move(
    game_id: str,
    ply: int,
    *,
    error: bool = False,
    phase: Phase = Phase.MIDDLEGAME,
    clock: float | None = 200.0,
    motifs: tuple[str, ...] = (),
) -> MoveFact:
    return MoveFact(
        game_id=game_id,
        ply=ply,
        move_number=(ply + 1) // 2,
        color=Color.WHITE,
        is_user=True,
        san="Qh5",
        uci="d1h5",
        fen_before="",
        eval_before=0,
        eval_after=-300 if error else 0,
        best_move="g1f3",
        best_san="Nf3",
        cp_loss=300 if error else 5,
        wp_before=50.0,
        wp_after=20.0 if error else 49.0,
        wp_loss=30.0 + ply / 100 if error else 1.0,
        judgement=Judgement.BLUNDER if error else Judgement.GOOD,
        phase=phase,
        depth=12,
        clock_after=clock,
        motifs=list(motifs),
    )


def planted() -> tuple[list[Game], list[MoveFact]]:
    """10 games: 16 calm middlegame moves each (1 error per game), then 4 endgame moves under 30s
    (2 errors per game). Baseline 30/200 = 15%; the endgame / low-clock slice is 50%."""
    games, moves = [], []
    for g in range(10):
        gm = game(g)
        games.append(gm)
        for ply in range(1, 33, 2):
            moves.append(
                move(gm.id, ply, error=ply == 9, motifs=("hanging_piece",) if ply == 9 else ())
            )
        for k, ply in enumerate(range(33, 41, 2)):
            moves.append(
                move(
                    gm.id,
                    ply,
                    error=k < 2,
                    phase=Phase.ENDGAME,
                    clock=12.0,
                    motifs=("time_trouble",) if k < 2 else (),
                )
            )
    return games, moves


def test_wilson_lower():
    assert wilson_lower(0, 0) == 0.0
    assert wilson_lower(5, 10) == pytest.approx(0.2366, abs=1e-4)
    assert wilson_lower(10, 10) < 1.0


def test_buckets():
    assert [move_bucket(n) for n in (1, 10, 11, 30, 31)] == [
        "1-10",
        "1-10",
        "11-20",
        "21-30",
        "31+",
    ]
    assert [clock_bucket(s) for s in (None, 5, 30, 90, 600)] == [
        None,
        "under 30s",
        "30-60s",
        "1-3 min",
        "over 3 min",
    ]


def test_castling():
    assert castling(game(1)) == "castled by move 10"
    assert castling(game(2, pgn=NO_CASTLE)) == "never castled"


def test_planted_slice_is_found_with_evidence():
    games, moves = planted()
    found = mine_moves(move_rows(games, moves))
    keys = [p.features for p in found]
    assert (("phase", "endgame"),) in keys and (("clock", "under 30s"),) in keys
    top = found[0]
    assert (top.n, top.hits, top.games) == (40, 20, 10)
    assert top.baseline == pytest.approx(0.15)
    # The pair covers exactly the same moves as each half, so it adds nothing and is dropped.
    assert all(len(p.features) == 1 for p in found)
    # Every move is blitz and every game an Italian: values that common never make a pattern.
    assert not any(f[0] in ("speed", "opening") for p in found for f in p.features)
    worst = top.evidence
    assert len(worst) == 3 and worst[0].wp_loss >= worst[1].wp_loss
    assert worst[0].url and worst[0].url.endswith(f"#{worst[0].ply}")


def test_small_slices_are_not_patterns():
    games, moves = planted()
    found = mine_moves(move_rows(games, moves), min_n=50)
    assert found == []


def test_games_lost_without_castling():
    games, moves = [], []
    for i in range(20):
        lost = i < 15  # 15 of 20 lost
        games.append(game(i, result="0-1" if lost else "1-0", pgn=NO_CASTLE))
    for i in range(20, 40):
        lost = i < 25  # 5 of 20 lost
        games.append(game(i, result="0-1" if lost else "1-0"))
    for g in games:
        moves.append(move(g.id, 1))
    found = mine_games(game_rows(games, moves))
    assert [p.features for p in found] == [(("castling", "never castled"),)]
    assert (found[0].hits, found[0].n) == (15, 20)


def test_motif_counts():
    games, moves = planted()
    counts = {m.tag: (m.moves, m.games) for m in motif_counts(move_rows(games, moves))}
    assert counts == {"time_trouble": (20, 10), "hanging_piece": (10, 10)}


def test_describe():
    assert describe((("phase", "endgame"), ("clock", "under 30s"))) == (
        "In the endgame, with under 30 seconds left"
    )
    assert describe((("opening", "Sicilian Defense"),)) == "In the Sicilian Defense"


def test_render():
    games, moves = planted()
    text = render(build(games, moves))
    assert "mistake or blunder on **15.0%** of your 200 moves" in text
    assert "**In the endgame**: 20 mistakes or blunders in 40 moves across 10 games, 50%" in text
    assert "(https://lichess.org/g" in text
    assert "No kind of game stands out yet" in text
    assert "| leaving a piece where it can be taken | 10 | 10 |" in text
    assert "going wrong with little time left" not in text  # discounted: short-on-time rule
    assert render(build([], [])).startswith("# Chess digest\n\nNo analysed games yet")


def test_digest_command(tmp_path: Path):
    db = tmp_path / "coach.db"
    games, moves = planted()
    with Store(db) as store:
        for g in games:
            store.upsert_game(g)
            store.replace_moves(g.id, [m for m in moves if m.game_id == g.id], depth=12)
    out = tmp_path / "digest.md"
    result = CliRunner().invoke(app, ["--db", str(db), "digest", "-o", str(out)])
    assert result.exit_code == 0, result.output
    assert "**In the endgame**" in out.read_text()
    empty = CliRunner().invoke(app, ["--db", str(tmp_path / "empty.db"), "digest"])
    assert "No analysed games yet" in empty.output
