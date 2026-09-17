import json
from datetime import UTC, datetime

import httpx
import pytest

from chess_coach.models import Color, Speed
from chess_coach.sources.chesscom import fetch_games, make_client
from chess_coach.sources.pgn import split_pgn


def _archive_payload(pgn: str, *, url: str, end_time: int, rated: bool = True) -> dict:
    return {
        "url": url,
        "pgn": pgn,
        "time_control": "600",
        "end_time": end_time,
        "rated": rated,
        "rules": "chess",
        "time_class": "rapid",
        "white": {"username": "SomeRival", "rating": 1530, "result": "win"},
        "black": {"username": "CoachMe", "rating": 1505, "result": "resigned"},
    }


@pytest.fixture
def chesscom_client(sample_pgn_text: str) -> tuple[httpx.Client, list[str]]:
    chesscom_pgn = list(split_pgn(sample_pgn_text))[1]
    requested: list[str] = []
    august = datetime(2026, 8, 15, tzinfo=UTC)
    september = datetime(2026, 9, 2, 20, 31, tzinfo=UTC)

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(request.url.path)
        assert "chess-coach" in request.headers["User-Agent"]
        if request.url.path == "/pub/player/coachme/games/archives":
            return httpx.Response(
                200,
                json={
                    "archives": [
                        "https://api.chess.com/pub/player/coachme/games/2026/08",
                        "https://api.chess.com/pub/player/coachme/games/2026/09",
                    ]
                },
            )
        if request.url.path.endswith("/2026/09"):
            games = [
                _archive_payload(
                    chesscom_pgn,
                    url="https://www.chess.com/game/live/123456789",
                    end_time=int(september.timestamp()),
                ),
                {"rules": "chess960", "pgn": chesscom_pgn, "url": "x/1"},
                {"rules": "chess", "url": "x/2"},  # no pgn
            ]
            return httpx.Response(200, json={"games": games})
        if request.url.path.endswith("/2026/08"):
            older = chesscom_pgn.replace("123456789", "111111111")
            games = [
                _archive_payload(
                    older,
                    url="https://www.chess.com/game/live/111111111",
                    end_time=int(august.timestamp()),
                    rated=False,
                )
            ]
            return httpx.Response(200, json={"games": games})
        return httpx.Response(404, text=json.dumps({"message": "not found"}))

    client = make_client()
    client._transport = httpx.MockTransport(handler)
    return client, requested


def test_fetch_games_walks_archives_newest_first(chesscom_client) -> None:
    client, requested = chesscom_client
    games = list(fetch_games("CoachMe", client=client))
    assert [g.id for g in games] == ["chesscom:123456789", "chesscom:111111111"]
    assert requested[0] == "/pub/player/coachme/games/archives"
    assert requested[1].endswith("/2026/09")
    newest = games[0]
    assert newest.user_color is Color.BLACK
    assert newest.rated is True
    assert newest.speed is Speed.RAPID
    assert games[1].rated is False


def test_fetch_games_honours_max_and_rated_filters(chesscom_client) -> None:
    client, _ = chesscom_client
    assert len(list(fetch_games("CoachMe", max_games=1, client=client))) == 1
    assert [g.id for g in fetch_games("CoachMe", rated_only=True, client=client)] == [
        "chesscom:123456789"
    ]


def test_fetch_games_stops_at_since(chesscom_client) -> None:
    client, requested = chesscom_client
    games = list(fetch_games("CoachMe", since=datetime(2026, 9, 1, tzinfo=UTC), client=client))
    assert [g.id for g in games] == ["chesscom:123456789"]
    assert not any(path.endswith("/2026/08") for path in requested)


@pytest.mark.network
def test_live_chesscom_archives_parse() -> None:
    games = list(fetch_games("hikaru", max_games=2))
    assert len(games) == 2
    assert all(g.source == "chesscom" for g in games)
