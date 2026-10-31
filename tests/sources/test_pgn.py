from datetime import UTC, datetime

from chess_coach.models import Color, Speed
from chess_coach.sources.pgn import (
    classify_speed,
    game_from_pgn,
    games_from_pgn_text,
    parse_time_control,
    split_pgn,
)


def test_split_pgn_yields_each_game(sample_pgn_text: str) -> None:
    chunks = list(split_pgn(sample_pgn_text))
    assert len(chunks) == 4
    assert chunks[0].startswith('[Event "Rated Blitz game"]')
    assert chunks[0].rstrip().endswith("1-0")
    assert chunks[-1].startswith('[Event "Not my game"]')


def test_lichess_export_is_parsed(sample_pgn_text: str) -> None:
    games = list(games_from_pgn_text(sample_pgn_text, "coachme"))
    lichess = games[0]
    assert lichess.id == "lichess:AbCdEfGh"
    assert lichess.source == "lichess"
    assert lichess.url == "https://lichess.org/AbCdEfGh"
    assert lichess.user == "coachme"
    assert lichess.user_color is Color.WHITE
    assert lichess.user_result == "win"
    assert (lichess.white_elo, lichess.black_elo) == (1512, 1498)
    assert lichess.played_at == datetime(2026, 9, 1, 18, 4, 11, tzinfo=UTC)
    assert lichess.time_control == "300+0"
    assert lichess.speed is Speed.BLITZ
    assert lichess.rated is True
    assert lichess.eco == "C58"
    assert lichess.opening == "Italian Game: Two Knights Defense, Fried Liver Attack"
    assert lichess.termination == "Normal"


def test_chesscom_export_is_parsed_case_insensitively(sample_pgn_text: str) -> None:
    games = list(games_from_pgn_text(sample_pgn_text, "coachme"))
    chesscom = games[1]
    assert chesscom.id == "chesscom:123456789"
    assert chesscom.url == "https://www.chess.com/game/live/123456789"
    assert chesscom.user_color is Color.BLACK
    assert chesscom.user_result == "loss"
    assert chesscom.speed is Speed.RAPID
    assert chesscom.rated is None  # chess.com PGN headers do not say
    # Chess.com gives only an ECOUrl; the name comes from the vendored table by position
    assert chesscom.eco is not None and chesscom.eco.startswith("D")  # table's, not the header's
    assert chesscom.opening is not None and chesscom.opening.startswith("Queen's Gambit Declined")
    assert chesscom.opening_family == "Queen's Gambit Declined"


def test_move_links_open_the_game_at_the_move(sample_pgn_text: str) -> None:
    lichess, chesscom = list(games_from_pgn_text(sample_pgn_text, "coachme"))[:2]
    assert lichess.move_url(14) == f"{lichess.url}#14"
    assert chesscom.move_url(14) == "https://www.chess.com/game/live/123456789?move=14"
    # No checked move parameter for daily games: they open at the start
    chesscom.url = "https://www.chess.com/game/daily/987654321"
    assert chesscom.move_url(14) == chesscom.url
    chesscom.url = None
    assert chesscom.move_url(14) is None


def test_plain_pgn_gets_a_content_hash_id(sample_pgn_text: str) -> None:
    games = list(games_from_pgn_text(sample_pgn_text, "coachme"))
    plain = games[2]
    assert plain.source == "pgn"
    assert plain.id.startswith("pgn:") and len(plain.id) == len("pgn:") + 12
    assert plain.url is None
    assert plain.speed is Speed.UNKNOWN
    assert plain.user_result == "draw"
    assert plain.eco is not None and plain.eco.startswith("C6")
    assert plain.opening_family == "Ruy Lopez"
    # same content -> same id
    assert game_from_pgn(plain.pgn, "coachme").id == plain.id  # type: ignore[union-attr]


def test_games_where_user_did_not_play_are_skipped(sample_pgn_text: str) -> None:
    assert len(list(games_from_pgn_text(sample_pgn_text, "coachme"))) == 3
    assert len(list(games_from_pgn_text(sample_pgn_text, "nobody"))) == 0


def test_time_control_parsing() -> None:
    assert parse_time_control("300+3") == (300, 3)
    assert parse_time_control("600") == (600, 0)
    assert parse_time_control("1/86400") is None
    assert parse_time_control("-") is None
    assert parse_time_control(None) is None
    assert parse_time_control("garbage") is None


def test_speed_classification_follows_lichess_thresholds() -> None:
    assert classify_speed("15+0") is Speed.ULTRABULLET
    assert classify_speed("60+0") is Speed.BULLET
    assert classify_speed("120+1") is Speed.BULLET  # 120 + 40 = 160 < 180
    assert classify_speed("180+0") is Speed.BLITZ
    assert classify_speed("300+3") is Speed.BLITZ
    assert classify_speed("600") is Speed.RAPID
    assert classify_speed("900+10") is Speed.RAPID  # 1300 < 1500
    assert classify_speed("1800+0") is Speed.CLASSICAL
    assert classify_speed("1/86400") is Speed.CORRESPONDENCE
    assert classify_speed(None) is Speed.UNKNOWN
    # the Lichess event name wins over arithmetic
    assert classify_speed("300+0", "Rated Bullet game") is Speed.BULLET


def test_lichess_game_urls_in_both_forms_give_the_game_id() -> None:
    from chess_coach.sources.pgn import detect_source

    assert detect_source({"Site": "https://lichess.org/game/co5iFldS"}) == ("lichess", "co5iFldS")
    assert detect_source({"Site": "https://lichess.org/AbCdEfGh"}) == ("lichess", "AbCdEfGh")
    # Not a game: the first 8 letters of a longer path are not an id
    assert detect_source({"Site": "https://lichess.org/broadcast/some-event/round-1"}) is None
