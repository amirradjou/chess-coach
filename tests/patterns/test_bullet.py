"""The owner's rule for bullet: only the first 10 moves count, and bullet results never do."""

from dataclasses import replace

from chess_coach.coach import tree as optree
from chess_coach.coach.digest import build, render
from chess_coach.coach.progress import periods
from chess_coach.coach.repertoire import favorites, fixes
from chess_coach.coach.study import openings
from chess_coach.models import Speed
from chess_coach.patterns.features import counted, game_rows, move_rows, result_of
from chess_coach.puzzles.make import candidates

from .test_coach_web import italian_games, kf1
from .test_patterns import game, move


def bullet(i: int, result: str = "1-0"):
    g = game(i, result=result, speed=Speed.BULLET)
    g.time_control = "60+0"
    return g


def test_bullet_counts_only_its_first_ten_moves_and_never_its_result():
    b, blitz = bullet(1), game(2)
    assert counted(move(b.id, 20), b)  # move 10
    assert not counted(move(b.id, 21), b)  # move 11
    assert counted(move(blitz.id, 81), blitz)
    assert (result_of(b), result_of(blitz)) == ("unknown", "win")


def test_bullet_after_move_ten_is_left_out_of_patterns_and_puzzles():
    b, blitz = bullet(1), game(2)
    moves = [move(b.id, 5, error=True), move(b.id, 41, error=True), move(blitz.id, 41, error=True)]
    rows = move_rows([b, blitz], moves)
    assert [(r.game.id, r.fact.ply) for r in rows] == [(b.id, 5), (blitz.id, 41)]
    assert [r.game.id for r in game_rows([b, blitz], moves)] == [blitz.id]
    found = candidates([b, blitz], moves, set())
    assert [(m.game_id, m.ply) for m in found] == [(blitz.id, 41), (b.id, 5)]


def test_progress_scores_leave_bullet_out():
    b, blitz = bullet(1, result="0-1"), game(2, result="1-0")
    moves = [move(b.id, 5), move(b.id, 41, error=True), move(blitz.id, 41)]
    (row,) = periods([b, blitz], moves)
    assert (row.games, row.decided, row.score, row.moves, row.errors) == (2, 1, 1.0, 2, 0)
    assert periods([b], moves)[0].score is None


def test_openings_count_bullet_games_but_not_their_results():
    games = italian_games()  # won 0, 2, 4; lost 1, 3, 5
    for g in games[3:]:
        g.speed = Speed.BULLET
    fav = favorites(games, [], min_games=5)["white"][0]
    assert (fav.games, fav.wins, fav.losses, fav.score) == (6, 2, 1, 2 / 3)
    (stat,) = openings(games, [], min_games=5)
    assert (stat.games, stat.decided, stat.score) == (6, 3, 2 / 3)
    for g in games:
        g.speed = Speed.BULLET
    assert favorites(games, [], min_games=5)["white"][0].score is None
    assert openings(games, [], min_games=5)[0].score is None


def test_opening_fixes_in_bullet_stop_at_move_ten():
    games = italian_games()
    for g in games:
        g.speed = Speed.BULLET
    assert [f.move for f in fixes(games, [kf1(g.id) for g in games[:3]])] == ["4.Kf1"]
    late = [replace(kf1(g.id), move_number=11) for g in games[:3]]
    assert fixes(games, late) == []


def test_digest_says_what_bullet_leaves_out():
    b1, b2, blitz = bullet(1), bullet(2), game(3, result="0-1")
    moves = [move(b1.id, 1), move(b2.id, 41, error=True), move(blitz.id, 1)]
    text = render(build([b1, b2, blitz], moves))
    assert "of your 2 moves" in text
    assert "lost 1 of 1 games outside bullet" in text
    assert "In your 2 bullet games only the first 10 moves count" in text


def test_openings_rank_only_scores_with_enough_games_outside_bullet():
    italian = italian_games()  # 3 won, 3 lost
    scandinavian = [bullet(10 + i) for i in range(5)] + [game(20, result="0-1")]
    for g in scandinavian:
        g.opening = "Scandinavian Defense"
    ranked = openings(italian + scandinavian, [], min_games=5)
    assert [(o.family, o.decided) for o in ranked] == [
        ("Italian Game", 6),
        ("Scandinavian Defense", 1),  # 0% from one game: too few to rank first
    ]


def test_the_tree_says_how_many_games_a_score_is_over():
    games = italian_games()
    for g in games[3:]:
        g.speed = Speed.BULLET
    results = {g.id: result_of(g) for g in games}
    ids = [g.id for g in games]
    assert (optree.score(ids, results), optree.scored(ids, results)) == (2 / 3, 3)
    (opening,) = optree.openings(games, min_games=5)
    assert (opening.games, opening.scored, opening.score) == (6, 3, 2 / 3)
