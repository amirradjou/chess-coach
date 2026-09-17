from datetime import UTC, datetime

import httpx
import pytest

from chess_coach.sources.lichess import fetch_games, make_client
from chess_coach.sources.pgn import iter_pgn_chunks, split_pgn


def test_iter_pgn_chunks_matches_split_pgn(sample_pgn_text: str) -> None:
    streamed = list(iter_pgn_chunks(sample_pgn_text.splitlines(keepends=True)))
    assert streamed == list(split_pgn(sample_pgn_text))


def test_fetch_games_streams_pgn_and_sends_expected_params(sample_pgn_text: str) -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["params"] = dict(request.url.params)
        seen["auth"] = request.headers.get("Authorization")
        seen["accept"] = request.headers.get("Accept")
        return httpx.Response(200, text=sample_pgn_text)

    client = make_client(token="lip_secret")
    client._transport = httpx.MockTransport(handler)
    games = list(
        fetch_games(
            "coachme",
            max_games=50,
            since=datetime(2026, 9, 1, tzinfo=UTC),
            rated_only=True,
            client=client,
        )
    )
    assert seen["path"] == "/api/games/user/coachme"
    assert seen["params"] == {
        "clocks": "true",
        "opening": "true",
        "evals": "false",
        "tags": "true",
        "moves": "true",
        "max": "50",
        "since": str(int(datetime(2026, 9, 1, tzinfo=UTC).timestamp() * 1000)),
        "rated": "true",
    }
    assert seen["auth"] == "Bearer lip_secret"
    assert seen["accept"] == "application/x-chess-pgn"
    # only games where coachme played; the plain-PGN game is tagged as lichess by the caller
    assert [g.id for g in games][:1] == ["lichess:AbCdEfGh"]
    assert all(g.source == "lichess" for g in games if g.id == "lichess:AbCdEfGh")
    assert games[0].opening == "Italian Game: Two Knights Defense, Fried Liver Attack"


def test_fetch_games_skips_variants() -> None:
    pgn = (
        '[Event "Rated Crazyhouse game"]\n[Site "https://lichess.org/zzzzzzzz"]\n'
        '[White "coachme"]\n[Black "x"]\n[Result "1-0"]\n[Variant "Crazyhouse"]\n\n1. e4 1-0\n\n'
        '[Event "Rated Blitz game"]\n[Site "https://lichess.org/yyyyyyyy"]\n'
        '[White "coachme"]\n[Black "x"]\n[Result "1-0"]\n[Variant "Standard"]\n\n1. e4 1-0\n'
    )
    client = make_client()
    client._transport = httpx.MockTransport(lambda _: httpx.Response(200, text=pgn))
    assert [g.id for g in fetch_games("coachme", client=client)] == ["lichess:yyyyyyyy"]


def test_fetch_games_caps_at_max_games(sample_pgn_text: str) -> None:
    client = make_client()
    client._transport = httpx.MockTransport(lambda _: httpx.Response(200, text=sample_pgn_text))
    assert len(list(fetch_games("coachme", max_games=1, client=client))) == 1


def test_fetch_games_raises_on_http_error() -> None:
    client = make_client()
    client._transport = httpx.MockTransport(lambda _: httpx.Response(429, text="slow down"))
    with pytest.raises(httpx.HTTPStatusError):
        list(fetch_games("coachme", client=client))


@pytest.mark.network
def test_live_lichess_export_parses() -> None:
    games = list(fetch_games("DrNykterstein", max_games=2))
    assert len(games) == 2
    assert all(g.source == "lichess" and g.pgn for g in games)
