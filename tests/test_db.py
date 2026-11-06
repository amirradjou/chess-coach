from datetime import UTC, datetime

from chess_coach.db import Store
from chess_coach.models import Color, Game, Judgement, MoveFact, Phase, Speed


def make_game(game_id: str = "pgn:1", **overrides: object) -> Game:
    fields: dict[str, object] = dict(
        id=game_id,
        source="pgn",
        source_id=game_id.split(":", 1)[1],
        user="me",
        user_color=Color.WHITE,
        white="me",
        black="them",
        result="1-0",
        pgn="1. e4 e5 *",
        played_at=datetime(2026, 9, 1, 12, 0, tzinfo=UTC),
        speed=Speed.BLITZ,
    )
    fields.update(overrides)
    return Game(**fields)  # type: ignore[arg-type]


def make_move(game_id: str, ply: int, **overrides: object) -> MoveFact:
    fields: dict[str, object] = dict(
        game_id=game_id,
        ply=ply,
        move_number=(ply + 1) // 2,
        color=Color.WHITE if ply % 2 else Color.BLACK,
        is_user=ply % 2 == 1,
        san="e4",
        uci="e2e4",
        fen_before="rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
        eval_before=30,
        eval_after=25,
        best_move="e2e4",
        best_san="e4",
        cp_loss=5,
        wp_before=52.0,
        wp_after=51.5,
        wp_loss=0.5,
        judgement=Judgement.BEST,
        phase=Phase.OPENING,
        depth=10,
        motifs=["development"],
    )
    fields.update(overrides)
    return MoveFact(**fields)  # type: ignore[arg-type]


def test_upsert_game_is_idempotent() -> None:
    with Store(":memory:") as store:
        assert store.upsert_game(make_game()) is True
        assert store.upsert_game(make_game()) is False
        assert store.count_games() == 1


def test_game_round_trips_all_fields() -> None:
    game = make_game(
        white_elo=1500,
        black_elo=1480,
        time_control="300+0",
        rated=True,
        eco="C50",
        opening="Italian Game",
        termination="Normal",
        url="https://lichess.org/abc",
    )
    with Store(":memory:") as store:
        store.upsert_game(game)
        loaded = store.get_game("pgn:1")
    assert loaded == game


def test_user_result_follows_user_color() -> None:
    assert make_game(result="1-0", user_color=Color.WHITE).user_result == "win"
    assert make_game(result="1-0", user_color=Color.BLACK).user_result == "loss"
    assert make_game(result="1/2-1/2").user_result == "draw"
    assert make_game(result="*").user_result == "unknown"


def test_replace_moves_marks_game_analyzed_and_round_trips() -> None:
    with Store(":memory:") as store:
        store.upsert_game(make_game())
        moves = [make_move("pgn:1", 1), make_move("pgn:1", 2, clock_after=59.5, mate_after=-3)]
        store.replace_moves("pgn:1", moves, depth=10)
        assert list(store.iter_moves("pgn:1")) == moves
        assert store.get_game("pgn:1").analyzed_depth == 10  # type: ignore[union-attr]
        assert list(store.iter_games(unanalyzed_below_depth=10)) == []
        assert len(list(store.iter_games(unanalyzed_below_depth=14))) == 1
        # user-only filter keeps only the coached side's moves
        assert [m.ply for m in store.iter_moves("pgn:1", user_only=True)] == [1]


def test_replace_moves_is_atomic_replacement() -> None:
    with Store(":memory:") as store:
        store.upsert_game(make_game())
        store.replace_moves("pgn:1", [make_move("pgn:1", p) for p in range(1, 5)], depth=8)
        store.replace_moves("pgn:1", [make_move("pgn:1", 1)], depth=12)
        assert store.count_moves() == 1


def test_iter_games_filters_by_since_and_source() -> None:
    with Store(":memory:") as store:
        store.upsert_game(make_game("pgn:old", played_at=datetime(2026, 1, 1, tzinfo=UTC)))
        store.upsert_game(make_game("lichess:new", source="lichess"))
        assert [g.id for g in store.iter_games(since=datetime(2026, 6, 1, tzinfo=UTC))] == [
            "lichess:new"
        ]
        assert [g.id for g in store.iter_games(source="pgn")] == ["pgn:old"]


def test_lichess_games_stored_under_a_hash_get_their_lichess_id_back(tmp_path) -> None:
    from chess_coach.models import Puzzle

    db = tmp_path / "coach.db"
    old, new = "lichess:1de7c128dd0d", "lichess:co5iFldS"
    pgn = '[Site "https://lichess.org/game/co5iFldS"]\n[GameId "co5iFldS"]\n\n1. e4 e5 *'
    with Store(db) as store:
        store.upsert_game(
            make_game(old, source="lichess", url="https://lichess.org/1de7c128dd0d", pgn=pgn)
        )
        store.upsert_game(make_game("lichess:AbCdEfGh", source="lichess"))
        store.replace_moves(old, [make_move(old, 1), make_move(old, 2)], depth=12)
        puzzle = Puzzle(f"{old}:1", old, 1, "fen", "e2e4", "e4", "d4", "", 200, 18)
        store.add_puzzle(puzzle)
        store.record_check(f"{old}:1", True)
        review = store.review(puzzle.id)
        assert review is not None
        store.save_attempt(review, "d2d4", False, datetime(2026, 10, 31, tzinfo=UTC))
        # As the importer stored them before it knew "lichess.org/game/<id>"
        store.conn.execute("UPDATE games SET source_id = '1de7c128dd0d' WHERE id = ?", (old,))
        store.conn.commit()

    with Store(db) as store:  # opening the store fixes them
        game = store.get_game(new)
        assert game is not None and store.get_game(old) is None
        assert (game.source_id, game.url) == ("co5iFldS", "https://lichess.org/co5iFldS")
        assert store.get_game("lichess:AbCdEfGh") is not None  # already right, untouched
        assert [m.game_id for m in store.iter_moves()] == [new, new]
        assert [(p.id, p.game_id) for p in store.puzzles()] == [(f"{new}:1", new)]
        assert store.review(f"{new}:1") is not None
        assert store.attempts()[0][0] == f"{new}:1"
        assert store.checked_moves() == {f"{new}:1"}
        assert store.conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_gifts_pair_each_costly_move_with_the_next_one() -> None:
    with Store(":memory:") as store:
        store.upsert_game(make_game())
        losses = {1: 0.5, 2: 30.0, 3: 25.0, 4: 1.0, 5: 19.9, 6: 2.0, 7: 40.0}  # 7 is the last move
        store.replace_moves(
            "pgn:1", [make_move("pgn:1", p, wp_loss=w) for p, w in losses.items()], depth=12
        )
        pairs = store.gifts(20.0)
    # 2 and 3 are both costly (3 is the reply to 2 and a gift of its own); 7 has no reply
    assert [(g.ply, r.ply) for g, r in pairs] == [(2, 3), (3, 4)]
    assert [(g.is_user, r.is_user) for g, r in pairs] == [(False, True), (True, False)]


def test_old_missed_punishment_puzzles_get_their_punish_tag_on_open(tmp_path) -> None:
    from chess_coach.models import Puzzle

    db = tmp_path / "coach.db"
    with Store(db) as store:
        store.upsert_game(make_game())
        for ply, san, tags in (
            (1, "Nxe5", ["missed_tactic", "opening"]),
            (3, "Bb5+", ["missed_tactic"]),
            (5, "O-O", ["missed_tactic"]),  # castling punishes quietly
            (7, "Qxd8", ["hanging_piece"]),  # not a missed punishment: left alone
        ):
            store.add_puzzle(
                Puzzle(f"pgn:1:{ply}", "pgn:1", ply, "fen", "e2e4", san, "a3", "", 200, 18, tags)
            )

    with Store(db) as store:
        tags = {p.ply: p.tags for p in store.puzzles()}
        assert tags == {
            1: ["missed_tactic", "opening", "punish_capture"],
            3: ["missed_tactic", "punish_check"],
            5: ["missed_tactic", "punish_quiet"],
            7: ["hanging_piece"],
        }
    with Store(db) as store:  # once is enough
        assert {p.ply: p.tags for p in store.puzzles()} == tags
