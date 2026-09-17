"""Lichess game export: https://lichess.org/api#tag/Games/operation/apiGamesUser"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime

import httpx

from chess_coach.models import Game
from chess_coach.sources.pgn import game_from_pgn, iter_pgn_chunks

API = "https://lichess.org"
USER_AGENT = "chess-coach (https://github.com/amirradjou/chess-coach)"


def make_client(token: str | None = None) -> httpx.Client:
    headers = {"User-Agent": USER_AGENT, "Accept": "application/x-chess-pgn"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return httpx.Client(base_url=API, headers=headers, timeout=httpx.Timeout(30.0, read=120.0))


def fetch_games(
    username: str,
    *,
    max_games: int | None = 100,
    since: datetime | None = None,
    rated_only: bool = False,
    token: str | None = None,
    client: httpx.Client | None = None,
) -> Iterator[Game]:
    """Stream the user's games newest-first as `Game` records. Variants are skipped."""
    params: dict[str, str | int] = {
        "clocks": "true",
        "opening": "true",
        "evals": "false",
        "tags": "true",
        "moves": "true",
    }
    if max_games is not None:
        params["max"] = max_games
    if since is not None:
        params["since"] = int(since.timestamp() * 1000)
    if rated_only:
        params["rated"] = "true"

    own_client = client is None
    client = client or make_client(token)
    try:
        with client.stream("GET", f"/api/games/user/{username}", params=params) as response:
            response.raise_for_status()
            for chunk in iter_pgn_chunks(response.iter_lines()):
                if '[Variant "' in chunk and '[Variant "Standard"]' not in chunk:
                    continue
                game = game_from_pgn(chunk, username, source="lichess", source_id=None)
                if game is not None:
                    yield game
    finally:
        if own_client:
            client.close()
