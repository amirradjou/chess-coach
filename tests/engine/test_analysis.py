import chess
import pytest

from chess_coach.engine.analysis import analyze_game
from chess_coach.engine.evaluation import MATE_CP
from chess_coach.engine.stockfish import Engine, terminal_eval
from chess_coach.models import Color, Judgement, Phase
from chess_coach.sources.pgn import games_from_pgn_text

pytestmark = pytest.mark.engine


def test_terminal_positions_need_no_search() -> None:
    mated = chess.Board("rnb1kbnr/pppp1ppp/8/4p3/6Pq/5P2/PPPPP2P/RNBQKBNR w KQkq - 1 3")
    assert terminal_eval(mated).cp == -MATE_CP  # type: ignore[union-attr]
    stalemate = chess.Board("7k/5Q2/6K1/8/8/8/8/8 b - - 0 1")
    assert terminal_eval(stalemate).cp == 0  # type: ignore[union-attr]
    assert terminal_eval(chess.Board()) is None


def test_engine_reports_mate_and_best_move(engine: Engine) -> None:
    # Scholar's mate in one: Qxf7#
    board = chess.Board("r1bqkbnr/pppp1ppp/2n5/4p2Q/2B1P3/8/PPPP1PPP/RNB1K1NR w KQkq - 4 4")
    line = engine.evaluate(board, depth=6)[0]
    assert line.mate == 1
    assert line.move == chess.Move.from_uci("h5f7")
    assert line.cp == MATE_CP - 1


def test_engine_multipv_returns_lines_strongest_first(engine: Engine) -> None:
    lines = engine.evaluate(chess.Board(), depth=6, multipv=3)
    assert len(lines) == 3
    assert lines[0].cp >= lines[1].cp >= lines[2].cp
    assert all(line.move is not None for line in lines)


def test_analyze_game_produces_one_fact_per_ply(sample_pgn_text: str, engine: Engine) -> None:
    game = next(games_from_pgn_text(sample_pgn_text, "coachme"))  # the Fried Liver game
    facts = analyze_game(game, engine, depth=8)
    assert len(facts) == 45
    first, last = facts[0], facts[-1]
    assert (first.ply, first.move_number, first.color, first.san) == (1, 1, Color.WHITE, "e4")
    assert first.is_user is True and facts[1].is_user is False
    assert first.phase is Phase.OPENING
    assert first.clock_after == 300.0
    assert last.san == "Qf6+" and last.color is Color.WHITE
    assert all(f.depth == 8 for f in facts)
    assert all(0 <= f.wp_before <= 100 and 0 <= f.wp_after <= 100 for f in facts)
    # cp_loss is never negative and a best move never loses anything
    assert all(f.cp_loss >= 0 for f in facts)
    assert all(f.wp_loss == 0.0 and f.cp_loss == 0 for f in facts if f.judgement is Judgement.BEST)
    # 7...Ke6 is a well-known fried-liver blunder-magnet; the engine sees the king walk
    kxf7 = next(f for f in facts if f.san == "Kxf7")
    assert kxf7.eval_after < kxf7.eval_before + 50  # taking is forced-ish, not a big loss
    # white's queen chase in the fixture ends with a crushing position
    assert last.eval_after > 500


def test_analyze_game_flags_the_blunder_from_the_movers_side(
    sample_pgn_text: str, engine: Engine
) -> None:
    games = list(games_from_pgn_text(sample_pgn_text, "coachme"))
    qgd = games[1]  # user is black; 17...Qxe6?? hands over the queen for a rook
    facts = analyze_game(qgd, engine, depth=8)
    blunder = next(f for f in facts if f.san == "Qxe6")
    assert blunder.is_user is True and blunder.color is Color.BLACK
    assert blunder.judgement in {Judgement.MISTAKE, Judgement.BLUNDER}
    assert blunder.best_san == "fxe6"
    assert blunder.cp_loss > 250
    assert blunder.pv.startswith("17...fxe6")
    assert blunder.clock_after == pytest.approx(358.2)
    # the opponent's 22.Qxc6?? threw away a won game, and 23.dxe5 walked into Rd1#
    qxc6 = next(f for f in facts if f.san == "Qxc6")
    assert qxc6.is_user is False and qxc6.judgement is Judgement.BLUNDER
    assert qxc6.wp_loss > 60
    final = facts[-1]
    assert final.san == "dxe5" and final.mate_after == -1
    assert final.wp_after == 0.0
    assert "allowed_mate" in final.motifs and "back_rank" in final.motifs
    # 17...Qxe6 hangs the queen to Rxe6; 22.Qxc6?? grabs a pawn and hangs the queen to bxc6;
    # 22...Rxe5 failed to punish it
    assert "hanging_piece" in blunder.motifs
    assert {"hanging_piece", "pawn_grab"} <= set(qxc6.motifs)
    rxe5 = next(f for f in facts if f.san == "Rxe5")
    assert "missed_tactic" in rxe5.motifs
    # progress callback sees every ply
    seen: list[int] = []
    analyze_game(qgd, engine, depth=4, on_ply=lambda i, n: seen.append(i))
    assert seen == list(range(1, 46))
