"""Turn PGN text into `Game` records. Shared by the Lichess, Chess.com and file importers."""

from __future__ import annotations

import hashlib
import io
import re
from collections.abc import Iterator
from datetime import UTC, datetime

import chess.pgn

from chess_coach.models import Color, Game, Speed

_LICHESS_URL = re.compile(r"https?://lichess\.org/([A-Za-z0-9]{8})")
_CHESSCOM_URL = re.compile(r"https?://(?:www\.)?chess\.com/(?:game/)?(?:live|daily)/(\d+)")
_EVENT_SPEEDS = {
    "ultrabullet": Speed.ULTRABULLET,
    "bullet": Speed.BULLET,
    "blitz": Speed.BLITZ,
    "rapid": Speed.RAPID,
    "classical": Speed.CLASSICAL,
    "correspondence": Speed.CORRESPONDENCE,
}


def parse_time_control(value: str | None) -> tuple[int, int] | None:
    """Return (initial_seconds, increment_seconds) or None for unlimited / daily / unknown."""
    if not value or value in {"-", "?"}:
        return None
    if "/" in value:  # chess.com daily: "1/86400" = one move per day
        return None
    main, _, inc = value.partition("+")
    try:
        return int(main), int(inc or 0)
    except ValueError:
        return None


def classify_speed(time_control: str | None, event: str | None = None) -> Speed:
    """Lichess's classes: estimated duration = initial + 40 * increment."""
    if event:
        lowered = event.lower()
        for word, speed in _EVENT_SPEEDS.items():
            if word in lowered:
                return speed
    if time_control and "/" in time_control:
        return Speed.CORRESPONDENCE
    parsed = parse_time_control(time_control)
    if parsed is None:
        return Speed.UNKNOWN
    initial, increment = parsed
    estimated = initial + 40 * increment
    if estimated < 30:
        return Speed.ULTRABULLET
    if estimated < 180:
        return Speed.BULLET
    if estimated < 480:
        return Speed.BLITZ
    if estimated < 1500:
        return Speed.RAPID
    return Speed.CLASSICAL


def parse_played_at(headers: chess.pgn.Headers) -> datetime | None:
    date = headers.get("UTCDate") or headers.get("Date")
    time = headers.get("UTCTime") or headers.get("StartTime") or "00:00:00"
    if not date or "?" in date:
        return None
    try:
        return datetime.strptime(f"{date} {time}", "%Y.%m.%d %H:%M:%S").replace(tzinfo=UTC)
    except ValueError:
        return None


def parse_elo(value: str | None) -> int | None:
    try:
        return int(value) if value else None
    except ValueError:
        return None


def detect_source(headers: chess.pgn.Headers) -> tuple[str, str] | None:
    """(source, source_id) from Site/Link headers when the game came from a known server."""
    for key in ("Site", "Link"):
        value = headers.get(key, "")
        if m := _LICHESS_URL.search(value):
            return "lichess", m.group(1)
        if m := _CHESSCOM_URL.search(value):
            return "chesscom", m.group(1)
    return None


def user_color(headers: chess.pgn.Headers, user: str) -> Color | None:
    wanted = user.casefold()
    if headers.get("White", "").casefold() == wanted:
        return Color.WHITE
    if headers.get("Black", "").casefold() == wanted:
        return Color.BLACK
    return None


def game_from_pgn(
    pgn_text: str,
    user: str,
    *,
    source: str | None = None,
    source_id: str | None = None,
    rated: bool | None = None,
) -> Game | None:
    """Build a `Game` from one PGN. Returns None when `user` is not one of the players."""
    parsed = chess.pgn.read_game(io.StringIO(pgn_text))
    if parsed is None:
        return None
    headers = parsed.headers
    color = user_color(headers, user)
    if color is None:
        return None

    detected = detect_source(headers)
    if source is None or source_id is None:
        if detected:
            source, source_id = detected
        else:
            source = source or "pgn"
            source_id = source_id or hashlib.sha1(pgn_text.strip().encode()).hexdigest()[:12]

    url = None
    if source == "lichess":
        url = f"https://lichess.org/{source_id}"
    elif source == "chesscom":
        url = headers.get("Link") or f"https://www.chess.com/game/live/{source_id}"

    event = headers.get("Event")
    if rated is None and event:
        lowered = event.lower()
        rated = True if "rated" in lowered else (False if "casual" in lowered else None)

    time_control = headers.get("TimeControl")
    return Game(
        id=f"{source}:{source_id}",
        source=source,
        source_id=source_id,
        user=user,
        user_color=color,
        white=headers.get("White", "?"),
        black=headers.get("Black", "?"),
        result=headers.get("Result", "*"),
        pgn=pgn_text.strip() + "\n",
        url=url,
        white_elo=parse_elo(headers.get("WhiteElo")),
        black_elo=parse_elo(headers.get("BlackElo")),
        played_at=parse_played_at(headers),
        time_control=time_control,
        speed=classify_speed(time_control, event),
        rated=rated,
        eco=headers.get("ECO") or None,
        opening=headers.get("Opening") or None,
        termination=headers.get("Termination") or None,
    )


def split_pgn(text: str) -> Iterator[str]:
    """Split a multi-game PGN string into one string per game (headers + movetext)."""
    handle = io.StringIO(text)
    while True:
        offset = handle.tell()
        game = chess.pgn.read_game(handle)
        if game is None:
            return
        end = handle.tell()
        handle.seek(offset)
        chunk = handle.read(end - offset).strip()
        handle.seek(end)
        if chunk:
            yield chunk


def games_from_pgn_text(text: str, user: str) -> Iterator[Game]:
    for chunk in split_pgn(text):
        game = game_from_pgn(chunk, user)
        if game is not None:
            yield game
