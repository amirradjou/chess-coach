from dataclasses import replace

import chess

from chess_coach.engine.motifs import (
    MoveContext,
    development,
    forks,
    hangs_piece,
    mates_on_back_rank,
    tag_motifs,
)
from chess_coach.models import Color, Judgement, MoveFact, Phase


def make_ctx(
    fen: str,
    san: str,
    *,
    best: str | None = None,
    reply: str | None = None,
    reply_line: list[str] | None = None,
    best_line: list[str] | None = None,
    wp_loss: float = 25.0,
    eval_before: int = 0,
    eval_after: int = 0,
    mate_before: int | None = None,
    mate_after: int | None = None,
    phase: Phase = Phase.MIDDLEGAME,
    clock_after: float | None = None,
    initial_time: int | None = None,
    previous_wp_loss: float | None = None,
) -> MoveContext:
    board = chess.Board(fen)
    move = board.parse_san(san)
    after = board.copy()
    after.push(move)
    best_move = board.parse_san(best) if best else None
    reply_move = after.parse_san(reply) if reply else None

    def line(start: chess.Board, sans: list[str] | None, fallback: chess.Move | None) -> list:
        if sans is None:
            return [fallback] if fallback else []
        scratch = start.copy()
        moves = []
        for text in sans:
            mv = scratch.parse_san(text)
            moves.append(mv)
            scratch.push(mv)
        return moves

    fact = MoveFact(
        game_id="g",
        ply=1,
        move_number=board.fullmove_number,
        color=Color.WHITE if board.turn else Color.BLACK,
        is_user=True,
        san=san,
        uci=move.uci(),
        fen_before=fen,
        eval_before=eval_before,
        eval_after=eval_after,
        best_move=best_move.uci() if best_move else move.uci(),
        best_san=best or san,
        cp_loss=max(0, eval_before - eval_after),
        wp_before=50.0,
        wp_after=50.0 - wp_loss,
        wp_loss=wp_loss,
        judgement=Judgement.MISTAKE if wp_loss >= 10 else Judgement.GOOD,
        phase=phase,
        depth=1,
        mate_before=mate_before,
        mate_after=mate_after,
        clock_after=clock_after,
    )
    previous = None
    if previous_wp_loss is not None:
        previous = replace(fact, wp_loss=previous_wp_loss, ply=0)
    return MoveContext(
        fact=fact,
        board_before=board,
        move=move,
        board_after=after,
        best_move=best_move,
        best_pv=line(board, best_line, best_move),
        best_reply=reply_move,
        reply_pv=line(after, reply_line, reply_move),
        previous=previous,
        initial_time=initial_time,
    )


ITALIAN = "r1bqkbnr/pppp1ppp/2n5/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 2 3"


def test_hanging_piece_when_reply_takes_for_free() -> None:
    ctx = make_ctx(ITALIAN, "Ng5", best="Bc4", reply="Qxg5")
    assert "hanging_piece" in tag_motifs(ctx)
    # a defended piece taken by an equal or higher value attacker is not "hanging"
    defended = chess.Board("4k3/8/2p5/3n4/8/8/8/R3K3 w - - 0 1")
    assert hangs_piece(defended, defended.parse_san("Ra5"), chess.BLACK) is None
    assert hangs_piece(defended, defended.parse_san("Rd1"), chess.BLACK) is None
    # ...but a pawn capturing a defended knight is
    pawn_attack = chess.Board("4k3/8/2p5/3n4/4P3/8/8/4K3 w - - 0 1")
    assert hangs_piece(pawn_attack, pawn_attack.parse_san("exd5"), chess.BLACK) == "hanging_piece"


def test_hanging_pawn_is_its_own_tag() -> None:
    ctx = make_ctx(
        "rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2", "f4", reply="exf4"
    )
    assert "hanging_pawn" in tag_motifs(ctx)


def test_allowed_fork_and_missed_fork() -> None:
    allowed = make_ctx("4k3/8/8/8/1n6/8/7P/Q3K3 w - - 0 1", "h3", best="Kd1", reply="Nc2+")
    tags = tag_motifs(allowed)
    assert "allowed_fork" in tags and "hanging_piece" not in tags
    missed = make_ctx("r3k3/8/8/1N6/8/8/8/4K3 w - - 0 1", "Ke2", best="Nc7+")
    assert "missed_fork" in tag_motifs(missed)
    board = chess.Board("r3k3/2N5/8/8/8/8/8/4K3 b - - 0 1")
    assert forks(board, chess.C7, chess.BLACK)


def test_allowed_mate_on_the_back_rank() -> None:
    ctx = make_ctx(
        "4r1k1/5ppp/8/8/8/8/5PPP/3R2K1 w - - 0 1",
        "Rd2",
        best="Kf1",
        reply="Re1#",
        mate_after=-1,
        wp_loss=90.0,
    )
    tags = tag_motifs(ctx)
    assert "allowed_mate" in tags and "back_rank" in tags


def test_missed_mate_on_the_back_rank() -> None:
    ctx = make_ctx(
        "4r1k1/5ppp/8/8/8/8/3R1PPP/6K1 b - - 0 1",
        "h6",
        best="Re1#",
        mate_before=1,
        wp_loss=50.0,
    )
    tags = tag_motifs(ctx)
    assert "missed_mate" in tags and "back_rank" in tags
    # a distant mate the engine sees is not a human "missed mate"
    far = make_ctx("4r1k1/5ppp/8/8/8/8/3R1PPP/6K1 b - - 0 1", "h6", best="Re1#", mate_before=12)
    assert "missed_mate" not in tag_motifs(far)


def test_mates_on_back_rank_requires_rook_or_queen_along_the_rank() -> None:
    board = chess.Board("6k1/5ppp/8/8/8/8/8/4R1K1 w - - 0 1")
    assert mates_on_back_rank(board, [board.parse_san("Re8#")])
    smothered = chess.Board("6rk/6pp/8/6N1/8/8/8/6K1 w - - 0 1")
    assert not mates_on_back_rank(smothered, [smothered.parse_san("Nf7#")])


def test_context_motifs() -> None:
    base = "r1bqkbnr/pppp1ppp/2n5/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 2 3"
    assert "missed_tactic" in tag_motifs(make_ctx(base, "a3", previous_wp_loss=35.0))
    assert "missed_tactic" not in tag_motifs(make_ctx(base, "a3", previous_wp_loss=5.0))
    assert "time_trouble" in tag_motifs(make_ctx(base, "a3", clock_after=12.0))
    assert "time_trouble" not in tag_motifs(
        make_ctx(base, "a3", clock_after=25.0, initial_time=180)
    )
    assert "time_trouble" in tag_motifs(make_ctx(base, "a3", clock_after=25.0, initial_time=600))
    endgame = make_ctx(
        "8/8/4k3/8/8/4K3/4P3/8 w - - 0 1",
        "Kd3",
        best="Kd4",
        phase=Phase.ENDGAME,
        eval_before=350,
        eval_after=40,
    )
    assert "endgame_conversion" in tag_motifs(endgame)


def test_pawn_grab_only_when_the_capture_was_a_mistake() -> None:
    fen = "rnbqkbnr/ppp2ppp/8/3pp3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq d6 0 3"
    assert "pawn_grab" in tag_motifs(make_ctx(fen, "exd5", best="Nf3", wp_loss=15.0))
    assert tag_motifs(make_ctx(fen, "exd5", best="Nf3", wp_loss=2.0)) == []


def test_queen_trade_behind_in_development_is_a_trait() -> None:
    fen = "rnbqkbnr/ppp2ppp/8/3Q4/2B1P3/2N2N2/PPPP1PPP/R1B1K2R b KQkq - 0 6"
    board = chess.Board(fen)
    assert development(board, chess.WHITE) == 3 and development(board, chess.BLACK) == 0
    assert tag_motifs(make_ctx(fen, "Qxd5", wp_loss=0.0)) == ["queen_trade_behind"]
    # the same trade when developed enough is not flagged
    even = "r1bqk1nr/ppp2ppp/2n5/2bQ4/2B1P3/2N2N2/PPPP1PPP/R1B1K2R b KQkq - 0 6"
    assert tag_motifs(make_ctx(even, "Qxd5", wp_loss=0.0)) == []
