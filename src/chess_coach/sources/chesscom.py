"""Chess.com published-data API: https://www.chess.com/news/view/published-data-api"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import httpx

from chess_coach.models import Game
from chess_coach.sources.pgn import game_from_pgn

API = "https://api.chess.com"
# Chess.com rejects requests without a descriptive User-Agent.
USER_AGENT = "chess-coach (https://github.com/amirradjou/chess-coach)"


def make_client() -> httpx.Client:
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    return httpx.Client(base_url=API, headers=headers, timeout=30.0)


def list_archives(username: str, client: httpx.Client) -> list[str]:
    """Monthly archive URLs, oldest first."""
    response = client.get(f"/pub/player/{username.lower()}/games/archives")
    response.raise_for_status()
    return list(response.json().get("archives", []))


def fetch_games(
    username: str,
    *,
    max_games: int | None = 100,
    since: datetime | None = None,
    rated_only: bool = False,
    client: httpx.Client | None = None,
) -> Iterator[Game]:
    """Yield the user's games newest-first by walking monthly archives backwards."""
    own_client = client is None
    client = client or make_client()
    yielded = 0
    try:
        for archive_url in reversed(list_archives(username, client)):
            year, month = (int(part) for part in archive_url.rsplit("/", 2)[-2:])
            if since is not None and (year, month) < (since.year, since.month):
                break
            response = client.get(archive_url)
            response.raise_for_status()
            games = response.json().get("games", [])
            for raw in reversed(games):
                if raw.get("rules") != "chess" or "pgn" not in raw:
                    continue
                if rated_only and not raw.get("rated", False):
                    continue
                end_time = raw.get("end_time")
                if since is not None and end_time is not None:
                    if datetime.fromtimestamp(end_time, tz=UTC) < since:
                        continue
                source_id = str(raw.get("url", "")).rstrip("/").rsplit("/", 1)[-1] or None
                game = game_from_pgn(
                    raw["pgn"],
                    username,
                    source="chesscom",
                    source_id=source_id,
                    rated=raw.get("rated"),
                )
                if game is None:
                    continue
                yield game
                yielded += 1
                if max_games is not None and yielded >= max_games:
                    return
    finally:
        if own_client:
            client.close()
