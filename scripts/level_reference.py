"""Reference error rates by rating band, for the "next level" card.

What it measures
    How often public Lichess players make a mistake or a blunder early in a game, per rating
    band, so the coach can say where the owner stands and what players 100-300 points higher do
    better. Bands are windows [c-100, c+100] for c = 1000, 1100, ..., 2300, with both players
    inside the window (neighbouring windows overlap). Per band:
      bullet 60+0              moves 1-5 and 6-10, and clock10: the mean share of the base time
                               left after each player's 10th move;
      blitz 300+3 and 180+2    moves 1-5, 6-10 and 11-20.
    A move's number is (ply + 1) // 2; both colours count, and White's first move is in 1-5.
    Each segment stores [errors, moves] (a mistake or blunder: 20+ points of win probability
    lost) and [inaccuracies, moves] (an inaccuracy or worse: 10+ points), with the design effect
    of game clustering for each (errors cluster in games, so a plain binomial interval is too
    narrow). Blitz bands also keep the rows per time control (by_time_control), so a reader can
    weight them to the owner's own mix.

The yardstick (the one the owner's own games were measured with)
    chess_coach's pipeline, unchanged: engine.analysis.analyze_game with
    engine.stockfish.Engine(threads=1, hash_mb=64) at depth 12. It evaluates the start position,
    then the position after each ply (up to ply 20 in bullet and 40 in blitz), and judges every
    move with engine.evaluation: Lichess's win-probability model, mates clamped to +-10000, and
    the engine's own best move or a forced move loses nothing.

The rules (the owner's, applied to everyone)
    patterns.features.counted: in bullet only moves 1-10 count.
    patterns.features.rushed: moves made short on time don't count, for either player (bullet:
    under 15 s left after the move; otherwise under 10% of the base time).
    Results are never used.

The sample
    A prefix of a Lichess monthly dump (CC0), streamed through `curl -r 0-<bytes> | zstd -dc`
    and never stored; the last, partial game is dropped. A game is kept when its time control is
    one of the above, both players are inside the band, its moves carry no [%eval] (games that
    someone asked Lichess to analyse have more errors than the rest), its Termination is not
    "Abandoned", and neither player is named in --exclude-user (the owner). Per speed and band
    it takes the N games with the smallest blake2b-64 hash of the game id: a uniform sample
    that anyone can rebuild from the same slice. A game that falls in two overlapping bands is
    analysed once and counts in both.

    The blitz time controls are the owner's: the ones that cover at least 80% of his blitz games
    (on 2026-10-31: 300+3 191 and 180+2 86 of 324 games, 85%). 60+0 is 99% of his bullet.

Run it from the repository (needs curl, zstd and Stockfish; about 45 minutes with 5 processes:
5 to stream 2 GB, 40 of engine work at about 260 positions a second):
    uv run python scripts/level_reference.py --bytes 2000000000 --processes 5 \\
        --exclude-user <the owner's Lichess name> --out src/chess_coach/data/levels.json
Engine results are cached per game (JSON lines in --cache), so a stopped run resumes where it
stopped. Games are analysed in hash order, so a partial cache is still a uniform sample.
--benchmark N analyses N games, prints the speed and the projected time, and stops.
"""

from __future__ import annotations

import argparse
import hashlib
import heapq
import json
import logging
import math
import multiprocessing
import re
import subprocess
import sys
import threading
import time
from collections import Counter
from datetime import date
from pathlib import Path

import chess.engine

from chess_coach.config import find_stockfish
from chess_coach.engine.analysis import analyze_game
from chess_coach.engine.evaluation import BLUNDER_WP_LOSS, INACCURACY_WP_LOSS, MISTAKE_WP_LOSS
from chess_coach.engine.stockfish import Engine
from chess_coach.models import Color, Game, Judgement, MoveFact, Phase
from chess_coach.patterns.features import counted, rushed
from chess_coach.sources.pgn import classify_speed, parse_time_control

DUMP_URL = "https://database.lichess.org/standard/lichess_db_standard_rated_2026-09.pgn.zst"
DEPTH = 12
ENGINE_THREADS = 1
ENGINE_HASH_MB = 64
PLIES = {"bullet": 20, "blitz": 40}
TIME_CONTROLS = {"bullet": ("60+0",), "blitz": ("300+3", "180+2")}
SEGMENTS = {
    "bullet": (("1-5", 1, 5), ("6-10", 6, 10)),
    "blitz": (("1-5", 1, 5), ("6-10", 6, 10), ("11-20", 11, 20)),
}
FIRST_CENTRE, LAST_CENTRE, HALF_WIDTH = 1000, 2300, 100
CLOCK_MOVE = 10  # clock10 reads the clock after each player's 10th move (bullet)

SEP = b'\n\n[Event "'
TAG_RE = re.compile(rb'^\[(\w+) "([^"\n]*)"\]$', re.M)
TOKEN_RE = re.compile(rb"\{([^}]*)\}|([^\s{}]+)")
CLOCK_RE = re.compile(rb"\[%clk ([0-9:.]+)\]")
RESULTS = {b"1-0", b"0-1", b"1/2-1/2", b"*"}
CHUNK = 16 << 20

log = logging.getLogger("level_reference")


def blake64(game_id: str) -> int:
    """The sampling key: the first 8 bytes of blake2b(game id), as an unsigned integer."""
    return int.from_bytes(hashlib.blake2b(game_id.encode(), digest_size=8).digest(), "big")


def centres(step: int) -> list[int]:
    return list(range(FIRST_CENTRE, LAST_CENTRE + 1, step))


# --- reading the dump ----------------------------------------------------------------------


def moves_of(movetext: bytes, limit: int) -> tuple[list[str], list[str | None]]:
    """The first `limit` plies: SAN and the [%clk] text after each move (None when missing)."""
    san: list[str] = []
    clocks: list[str | None] = []
    for m in TOKEN_RE.finditer(movetext):
        comment, token = m.group(1), m.group(2)
        if comment is not None:
            if san and (c := CLOCK_RE.search(comment)):
                clocks[-1] = c.group(1).decode()
            continue
        if token.endswith(b".") or token.startswith(b"$"):
            continue  # move numbers ("12.", "12...") and NAGs
        if token in RESULTS or len(san) == limit:
            break
        san.append(token.rstrip(b"?!").decode())
        clocks.append(None)
    return san, clocks


class Selector:
    """Keeps, per (speed, band centre), the `quota` eligible games with the smallest hash."""

    def __init__(
        self,
        time_controls: dict[str, tuple[str, ...]],
        band_centres: list[int],
        quota: dict[str, int],
        exclude: set[str],
    ) -> None:
        self.speed_of = {tc.encode(): s for s, tcs in time_controls.items() for tc in tcs}
        self.centres = band_centres
        self.quota = quota
        self.exclude = {name.casefold() for name in exclude}
        self.heaps: dict[tuple[str, int], list] = {
            (s, c): [] for s in time_controls for c in band_centres
        }
        self.pool: Counter = Counter()  # eligible games per (speed, centre)
        self.counts: Counter = Counter()  # why games were left out, per speed
        self.utc: list[str] = []  # first and last eligible game, "YYYY.MM.DD HH:MM:SS"

    def game(self, g: bytes) -> None:
        """One game; `g` starts right after '[Event "' and ends with its movetext."""
        self.counts["scanned"] += 1
        end = g.find(b"\n\n")
        if end < 0:
            return
        header = g[:end]
        i = header.find(b'\n[TimeControl "')
        if i < 0:
            return
        speed = self.speed_of.get(header[i + 15 : header.find(b'"', i + 15)])
        if speed is None:
            return
        tags = dict(TAG_RE.findall(header))
        we, be = tags.get(b"WhiteElo", b""), tags.get(b"BlackElo", b"")
        if not (we.isdigit() and be.isdigit()):
            return
        lo = max(int(we), int(be)) - HALF_WIDTH
        hi = min(int(we), int(be)) + HALF_WIDTH
        cells = [c for c in self.centres if lo <= c <= hi]
        if not cells:
            return
        self.counts[f"{speed}|in a band"] += 1
        if tags.get(b"Termination") == b"Abandoned":
            self.counts[f"{speed}|abandoned"] += 1
            return
        if b"[%eval" in g[end:]:
            self.counts[f"{speed}|analysed ([%eval])"] += 1
            return
        names = {tags.get(b"White", b"").decode(), tags.get(b"Black", b"").decode()}
        if self.exclude and {n.casefold() for n in names} & self.exclude:
            self.counts[f"{speed}|excluded player"] += 1
            return
        self.counts[f"{speed}|eligible"] += 1
        stamp = f"{tags.get(b'UTCDate', b'').decode()} {tags.get(b'UTCTime', b'').decode()}"
        if not self.utc:
            self.utc = [stamp, stamp]
        else:
            self.utc = [min(self.utc[0], stamp), max(self.utc[1], stamp)]
        gid = tags.get(b"Site", b"").rsplit(b"/", 1)[-1].decode()
        h = blake64(gid)
        for c in cells:
            self.pool[(speed, c)] += 1
            heap = self.heaps[(speed, c)]
            if len(heap) < self.quota[speed]:
                heapq.heappush(heap, (-h, gid, g))
            elif h < -heap[0][0]:
                heapq.heapreplace(heap, (-h, gid, g))

    def records(self) -> tuple[dict[str, dict], dict[tuple[str, int], list[str]]]:
        """(game id -> record, (speed, centre) -> game ids, smallest hash first)."""
        recs: dict[str, dict] = {}
        cells: dict[tuple[str, int], list[str]] = {}
        for (speed, c), heap in self.heaps.items():
            chosen = sorted(heap, key=lambda e: (-e[0], e[1]))
            cells[(speed, c)] = [gid for _, gid, _ in chosen]
            for neg, gid, g in chosen:
                if gid not in recs:
                    recs[gid] = record_of(g, gid, -neg, speed)
        return recs, cells


def record_of(g: bytes, gid: str, h: int, speed: str) -> dict:
    end = g.find(b"\n\n")
    tags = {k.decode(): v.decode("utf-8", "replace") for k, v in TAG_RE.findall(g[:end])}
    san, clocks = moves_of(g[end + 2 :], PLIES[speed])
    return {
        "id": gid,
        "h": h,
        "speed": speed,
        "tc": tags["TimeControl"],
        "event": g[: g.find(b'"')].decode("utf-8", "replace"),
        "white": tags.get("White", "?"),
        "black": tags.get("Black", "?"),
        "welo": int(tags["WhiteElo"]),
        "belo": int(tags["BlackElo"]),
        "termination": tags.get("Termination"),
        "bot": "BOT" in (tags.get("WhiteTitle"), tags.get("BlackTitle")),
        "utc": f"{tags.get('UTCDate', '')} {tags.get('UTCTime', '')}",
        "san": san,
        "clk": clocks,
    }


def _feed(url: str, nbytes: int, sink, state: dict) -> None:
    """Copy bytes 0..nbytes-1 of `url` into `sink` with curl, resuming after a dropped
    connection, so the stream is exactly the requested prefix or the run fails."""
    got, failures = 0, 0
    try:
        while got < nbytes:
            curl = subprocess.Popen(
                ["curl", "-s", "-f", "-r", f"{got}-{nbytes - 1}", url], stdout=subprocess.PIPE
            )
            assert curl.stdout is not None
            while chunk := curl.stdout.read(1 << 20):
                sink.write(chunk)
                got += len(chunk)
                state["compressed"] = got
            if curl.wait() == 0:
                break  # the whole range arrived (or the file ended before it)
            failures += 1
            if failures > 5:
                raise OSError(f"curl failed {failures} times; got {got} of {nbytes} bytes")
            time.sleep(5 * failures)
    except BaseException as e:  # noqa: BLE001 - the main thread reports it
        state["error"] = e
    finally:
        sink.close()


def stream_select(url: str, nbytes: int, selector: Selector) -> dict:
    """Stream the dump prefix through zstd and hand every complete game to `selector`."""
    zstd = subprocess.Popen(
        ["zstd", "-dc"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,  # a cut prefix always ends in a truncated frame
    )
    assert zstd.stdin is not None and zstd.stdout is not None
    state: dict = {"compressed": 0}
    feeder = threading.Thread(target=_feed, args=(url, nbytes, zstd.stdin, state), daemon=True)
    feeder.start()
    t0 = time.time()
    leftover = b"\n\n"  # so the first game also starts after a separator
    decompressed = 0
    report = 1 << 30
    while chunk := zstd.stdout.read(CHUNK):
        decompressed += len(chunk)
        buf = leftover + chunk
        cut = buf.rfind(SEP)
        if cut < 0:
            leftover = buf
            continue
        parts = buf[:cut].split(SEP)
        leftover = buf[cut:]
        for g in parts[1:]:  # parts[0] is what came before the first separator
            selector.game(g)
        if decompressed >= report:
            report += 1 << 30
            log.info(
                "%.2f GB read, %.1f GB of PGN, %d games, %.0f s",
                state["compressed"] / 1e9,
                decompressed / 1e9,
                selector.counts["scanned"],
                time.time() - t0,
            )
    feeder.join()
    zstd.wait()
    if "error" in state:
        raise SystemExit(f"download failed: {state['error']!r}")
    # `leftover` holds the last game, cut off by the end of the prefix: dropped.
    return {
        "compressed_bytes": state["compressed"],
        "pgn_bytes": decompressed,
        "dropped_tail_bytes": len(leftover) - 2,
        "seconds": round(time.time() - t0, 1),
    }


# --- the engine pass -----------------------------------------------------------------------


def pgn_of(rec: dict) -> str:
    parts: list[str] = []
    for i, (san, clock) in enumerate(zip(rec["san"], rec["clk"], strict=True)):
        if i % 2 == 0:
            parts.append(f"{i // 2 + 1}.")
        parts.append(san)
        if clock is not None:
            parts.append(f"{{ [%clk {clock}] }}")
    head = f'[Site "https://lichess.org/{rec["id"]}"]\n[TimeControl "{rec["tc"]}"]\n\n'
    return head + " ".join(parts) + " *\n"


def game_of(rec: dict, *, with_moves: bool = True) -> Game:
    """The app's Game record for a public game (White stands in as the "user")."""
    return Game(
        id=f"lichess:{rec['id']}",
        source="lichess",
        source_id=rec["id"],
        user=rec["white"],
        user_color=Color.WHITE,
        white=rec["white"],
        black=rec["black"],
        result="*",
        pgn=pgn_of(rec) if with_moves else "",
        white_elo=rec["welo"],
        black_elo=rec["belo"],
        time_control=rec["tc"],
        speed=classify_speed(rec["tc"]),
        rated=True,
    )


_engine: Engine | None = None
_engine_path = ""


def _start_engine(path: str) -> None:
    global _engine, _engine_path
    logging.getLogger("chess.engine").setLevel(logging.ERROR)
    _engine_path = path
    _engine = Engine(path, threads=ENGINE_THREADS, hash_mb=ENGINE_HASH_MB)


def score(rec: dict) -> dict:
    """Run analyze_game over the record's plies. Each move becomes
    [eval_before, eval_after, wp_loss, clock_after] (evals in cp from the mover's side)."""
    global _engine
    for attempt in (1, 2):
        try:
            assert _engine is not None
            facts = analyze_game(game_of(rec), _engine, depth=DEPTH)
            break
        except (chess.engine.EngineError, chess.engine.EngineTerminatedError):
            if attempt == 2:
                raise
            try:
                assert _engine is not None
                _engine.close()
            except Exception:  # noqa: BLE001 - the engine is already gone
                pass
            _start_engine(_engine_path)
    return {
        "id": rec["id"],
        "depth": DEPTH,
        "limit": PLIES[rec["speed"]],
        "engine": _engine.name,
        "m": [[f.eval_before, f.eval_after, f.wp_loss, f.clock_after] for f in facts],
    }


def load_scores(path: Path, engine_name: str) -> dict[str, dict]:
    done: dict[str, dict] = {}
    if not path.exists():
        return done
    with path.open(encoding="utf-8") as f:
        for line in f:
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue  # a line cut short by a stopped run
            if r.get("depth") == DEPTH and r.get("engine") == engine_name:
                done[r["id"]] = r
    return done


def run_engine(
    todo: list[dict], out: Path, engine_path: str, processes: int, done: dict[str, dict]
) -> tuple[int, int, float]:
    """Analyse `todo` (in order) in `processes` workers, appending one JSON line per game.
    Returns (games, positions, seconds)."""
    if not todo:
        return 0, 0, 0.0
    positions = sum(len(r["san"]) + 1 for r in todo)
    t0 = time.time()
    n_pos = 0
    ctx = multiprocessing.get_context("spawn")
    with (
        ctx.Pool(processes, initializer=_start_engine, initargs=(engine_path,)) as pool,
        out.open("a", encoding="utf-8") as f,
    ):
        for k, r in enumerate(pool.imap_unordered(score, todo, chunksize=2), start=1):
            f.write(json.dumps(r, separators=(",", ":")) + "\n")
            f.flush()
            done[r["id"]] = r
            n_pos += len(r["m"]) + 1
            if k % 250 == 0 or k == len(todo):
                dt = time.time() - t0
                rate = n_pos / max(dt, 1e-9)
                log.info(
                    "%d/%d games, %d/%d positions, %.0f s, %.0f positions/s, %.1f min left",
                    k,
                    len(todo),
                    n_pos,
                    positions,
                    dt,
                    rate,
                    (positions - n_pos) / max(rate, 1e-9) / 60,
                )
    return len(todo), n_pos, time.time() - t0


# --- counting ------------------------------------------------------------------------------


def judgement_of(wp_loss: float) -> Judgement:
    if wp_loss >= BLUNDER_WP_LOSS:
        return Judgement.BLUNDER
    if wp_loss >= MISTAKE_WP_LOSS:
        return Judgement.MISTAKE
    if wp_loss >= INACCURACY_WP_LOSS:
        return Judgement.INACCURACY
    return Judgement.GOOD


def light_fact(game_id: str, ply: int, row: list) -> MoveFact:
    """A MoveFact carrying only what counted() and rushed() read, plus the stored loss."""
    eval_before, eval_after, wp_loss, clock_after = row
    return MoveFact(
        game_id=game_id,
        ply=ply,
        move_number=(ply + 1) // 2,
        color=Color.WHITE if ply % 2 else Color.BLACK,
        is_user=True,
        san="",
        uci="",
        fen_before="",
        eval_before=eval_before,
        eval_after=eval_after,
        best_move="",
        best_san="",
        cp_loss=0,
        wp_before=0.0,
        wp_after=0.0,
        wp_loss=wp_loss,
        judgement=judgement_of(wp_loss),
        phase=Phase.OPENING,
        depth=DEPTH,
        clock_after=clock_after,
    )


def game_counts(rec: dict, scored: dict) -> tuple[dict[str, list[int]], list[float]]:
    """Per segment [errors, inaccuracies, moves] for one game, and the shares of the base time
    left after each player's 10th move (bullet)."""
    speed = rec["speed"]
    game = game_of(rec, with_moves=False)
    segs = {name: [0, 0, 0] for name, _, _ in SEGMENTS[speed]}
    for ply, row in enumerate(scored["m"], start=1):
        m = light_fact(rec["id"], ply, row)
        if not counted(m, game) or rushed(m, game):
            continue
        for name, first, last in SEGMENTS[speed]:
            if first <= m.move_number <= last:
                s = segs[name]
                s[0] += m.wp_loss >= MISTAKE_WP_LOSS
                s[1] += m.wp_loss >= INACCURACY_WP_LOSS
                s[2] += 1
                break
    shares = []
    if speed == "bullet":
        base = parse_time_control(rec["tc"])
        for ply in (2 * CLOCK_MOVE - 1, 2 * CLOCK_MOVE):
            if base and ply <= len(scored["m"]) and scored["m"][ply - 1][3] is not None:
                shares.append(scored["m"][ply - 1][3] / base[0])
    return segs, shares


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return math.nan, math.nan
    p = k / n
    d = 1 + z * z / n
    mid = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0.0, mid - half), min(1.0, mid + half)


def design_effect(pairs: list[tuple[int, int]]) -> float | None:
    """Variance of a rate with games as clusters over the binomial p(1-p)/n: how much more
    uncertain the rate is because moves of one game are alike. Divide n by it for an interval."""
    k = sum(e for e, _ in pairs)
    n = sum(m for _, m in pairs)
    if len(pairs) < 2 or k in (0, n):
        return None
    r = k / n
    clustered = sum((e - r * m) ** 2 for e, m in pairs) / n**2 * len(pairs) / (len(pairs) - 1)
    return round(clustered / (r * (1 - r) / n), 2)


def tally(
    recs: dict[str, dict],
    cells: dict[tuple[str, int], list[str]],
    scores: dict[str, dict],
    pool: dict[str, int],
) -> dict:
    """levels.json's bands, per speed. `pool` counts the eligible games per "speed|centre".
    Speeds with several time controls also get the rows per time control, so a reader can
    weight them to the owner's own mix."""

    def rows_of(counts: dict[str, list[int]], per_game: dict[str, list] | None = None) -> dict:
        rows = {}
        for name, (e, i, n) in counts.items():
            rows[name] = {"errors": [e, n], "inaccuracies": [i, n]}
            if per_game is not None:
                rows[name]["design_effect"] = [
                    design_effect([(g[0], g[2]) for g in per_game[name]]),
                    design_effect([(g[1], g[2]) for g in per_game[name]]),
                ]
        return rows

    out: dict = {}
    for (speed, c), ids in sorted(cells.items()):
        names = [name for name, _, _ in SEGMENTS[speed]]
        rows = {name: [0, 0, 0] for name in names}
        per_game: dict[str, list] = {name: [] for name in names}
        by_tc = {tc: [0, {name: [0, 0, 0] for name in names}] for tc in TIME_CONTROLS[speed]}
        players: set[str] = set()
        elo = 0.0
        shares: list[float] = []
        games = 0
        for gid in ids:
            if gid not in scores:
                continue
            rec = recs[gid]
            games += 1
            players.update((rec["white"], rec["black"]))
            elo += (rec["welo"] + rec["belo"]) / 2
            segs, sh = game_counts(rec, scores[gid])
            shares.extend(sh)
            tc = by_tc[rec["tc"]]
            tc[0] += 1
            for name, v in segs.items():
                per_game[name].append(v)
                for i in range(3):
                    rows[name][i] += v[i]
                    tc[1][name][i] += v[i]
        band = {
            "lo": c - HALF_WIDTH,
            "hi": c + HALF_WIDTH,
            "games": games,
            "eligible": pool.get(f"{speed}|{c}", 0),
            "players": len(players),
            "mean_elo": round(elo / games, 1) if games else None,
            "rows": rows_of(rows, per_game),
        }
        if speed == "bullet":
            band["clock10"] = [round(sum(shares) / len(shares), 4) if shares else None, len(shares)]
        if len(by_tc) > 1:
            band["by_time_control"] = {
                tc: {"games": n, "rows": rows_of(counts)} for tc, (n, counts) in by_tc.items()
            }
        out.setdefault(speed, []).append(band)
    return out


# --- main ----------------------------------------------------------------------------------


def selection_key(args: argparse.Namespace) -> str:
    spec = {
        "url": args.dump_url,
        "bytes": args.bytes,
        "time_controls": TIME_CONTROLS,
        "centres": centres(args.band_step),
        "half_width": HALF_WIDTH,
        "plies": PLIES,
        "exclude": sorted(n.casefold() for n in args.exclude_user),
    }
    return hashlib.blake2b(json.dumps(spec, sort_keys=True).encode(), digest_size=6).hexdigest()


def select(args: argparse.Namespace) -> dict:
    """The selection for these arguments, from the cache when an earlier run made it."""
    path = args.cache / f"selection-{selection_key(args)}.json"
    quota = {"bullet": args.games_bullet, "blitz": args.games_blitz}
    if path.exists():
        sel = json.loads(path.read_text(encoding="utf-8"))
        if all(sel["quota"][s] >= quota[s] for s in quota):
            log.info("selection from %s", path)
            return sel
    selector = Selector(TIME_CONTROLS, centres(args.band_step), quota, set(args.exclude_user))
    log.info("streaming %d bytes of %s", args.bytes, args.dump_url)
    stream = stream_select(args.dump_url, args.bytes, selector)
    recs, cells = selector.records()
    sel = {
        "url": args.dump_url,
        "bytes": args.bytes,
        "quota": quota,
        "stream": stream,
        "counts": dict(selector.counts),
        "utc": selector.utc,
        "pool": {f"{s}|{c}": n for (s, c), n in sorted(selector.pool.items())},
        "cells": {f"{s}|{c}": ids for (s, c), ids in cells.items()},
        "games": recs,
    }
    path.write_text(json.dumps(sel, separators=(",", ":")), encoding="utf-8")
    log.info(
        "selection: %d games scanned, %d chosen: %s", selector.counts["scanned"], len(recs), path
    )
    return sel


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--dump-url", default=DUMP_URL, help="a Lichess monthly dump (.pgn.zst)")
    ap.add_argument(
        "--bytes",
        type=int,
        default=2_000_000_000,
        help="how many bytes of the dump to stream (from the start)",
    )
    ap.add_argument("--games-bullet", type=int, default=1200, help="games per bullet band")
    ap.add_argument("--games-blitz", type=int, default=1000, help="games per blitz band")
    ap.add_argument("--processes", type=int, default=5, help="engine processes, 1 thread each")
    ap.add_argument(
        "--out",
        type=Path,
        default=Path("src/chess_coach/data/levels.json"),
        help="where the reference goes",
    )
    ap.add_argument(
        "--cache",
        type=Path,
        default=Path.home() / ".cache" / "chess-coach" / "levels",
        help="selection and per-game engine results (resumable)",
    )
    ap.add_argument(
        "--exclude-user",
        action="append",
        default=[],
        metavar="NAME",
        help="leave out games of this player (repeatable): the owner",
    )
    ap.add_argument("--band-step", type=int, default=100, help="distance between band centres")
    ap.add_argument("--stockfish", help="engine binary (default: the app's usual lookup)")
    ap.add_argument(
        "--benchmark",
        type=int,
        metavar="N",
        help="analyse N games, print the speed and the projected time, and stop",
    )
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    args.cache.mkdir(parents=True, exist_ok=True)

    engine_path = find_stockfish(args.stockfish)
    if engine_path is None:
        raise SystemExit("No Stockfish found: run scripts/get-stockfish.sh or pass --stockfish.")
    with Engine(engine_path, threads=ENGINE_THREADS, hash_mb=ENGINE_HASH_MB) as probe:
        engine_name = probe.name

    sel = select(args)
    quota = {"bullet": args.games_bullet, "blitz": args.games_blitz}
    cells: dict[tuple[str, int], list[str]] = {}
    for key, ids in sel["cells"].items():
        speed, centre = key.split("|")
        cells[(speed, int(centre))] = ids[: quota[speed]]
    recs = sel["games"]
    wanted = {gid for ids in cells.values() for gid in ids}
    scores_path = args.cache / "scores.jsonl"
    scores = load_scores(scores_path, engine_name)
    # a cached result counts only if it covers the plies this run wants for that speed
    scores = {
        g: r for g, r in scores.items() if g in recs and r.get("limit") == PLIES[recs[g]["speed"]]
    }
    todo = sorted((recs[g] for g in wanted if g not in scores), key=lambda r: (r["h"], r["id"]))
    total_pos = sum(len(recs[g]["san"]) + 1 for g in wanted)
    left_pos = sum(len(r["san"]) + 1 for r in todo)
    log.info(
        "%d games in %d cells (%d positions); %d games (%d positions) still to analyse",
        len(wanted),
        len(cells),
        total_pos,
        len(todo),
        left_pos,
    )
    if args.benchmark:
        batch = todo[: args.benchmark]
        games, pos, secs = run_engine(batch, scores_path, str(engine_path), args.processes, scores)
        rate = pos / max(secs, 1e-9)
        rest = left_pos - pos
        print(
            f"benchmark: {games} games, {pos} positions in {secs:.0f} s = {rate:.1f} positions/s "
            f"with {args.processes} processes; the remaining {rest} positions would take "
            f"{rest / rate / 60:.0f} min"
        )
        return
    run_engine(todo, scores_path, str(engine_path), args.processes, scores)

    speeds = tally(recs, cells, scores, sel["pool"])
    result = {
        "source": f"{sel['url']} (Lichess open database, CC0)",
        "slice_bytes": sel["stream"]["compressed_bytes"],
        "slice": {
            "games_scanned": sel["counts"].get("scanned", 0),
            "first_game_utc": sel["utc"][0] if sel["utc"] else None,
            "last_game_utc": sel["utc"][1] if sel["utc"] else None,
            "filters": {
                speed: {
                    k.split("|", 1)[1]: v
                    for k, v in sorted(sel["counts"].items())
                    if k.startswith(f"{speed}|")
                }
                for speed in TIME_CONTROLS
            },
        },
        "built": date.today().isoformat(),
        "yardstick": {
            "engine": f"{engine_name}, depth {DEPTH}, {ENGINE_THREADS} thread, hash "
            f"{ENGINE_HASH_MB} MB, through chess_coach.engine.analysis.analyze_game (start "
            "position, then the position after each ply), the pipeline `analyze` uses",
            "plies": PLIES,
            "error": f"wp_loss >= {MISTAKE_WP_LOSS:g} (mistake or blunder; Lichess's "
            "win-probability model, mates clamped to +-10000, the engine's best move and forced "
            "moves lose nothing)",
            "inaccuracy": f"wp_loss >= {INACCURACY_WP_LOSS:g} (inaccuracy or worse)",
            "sample": "per speed and band, the games with the smallest blake2b-64 hash of the "
            "game id among games with both players in the band, no [%eval] in the moves "
            '(not analysed on Lichess) and Termination not "Abandoned"',
        },
        "rules": {
            "counted": "bullet: only moves 1-10 count (patterns.features.counted)",
            "rushed": "moves made short on time are left out, for both players: under 15 s "
            "left in bullet, otherwise under 10% of the base time (patterns.features.rushed)",
            "moves": "move number = (ply + 1) // 2; both colours; White's first move included",
            "results": "never used",
            "clock10": "bullet: mean share of the base time left after each player's 10th "
            "move, every player-game that reached it, [mean, n]",
            "design_effect": "per row, [errors, inaccuracies]: the variance of the rate with "
            "games as clusters over the binomial p(1-p)/n; divide n by it before a Wilson "
            "interval, since moves of one game are alike",
        },
        "segments": {s: [name for name, _, _ in segs] for s, segs in SEGMENTS.items()},
        "speeds": {
            speed: {"time_controls": list(TIME_CONTROLS[speed]), "bands": bands}
            for speed, bands in speeds.items()
        },
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=1) + "\n", encoding="utf-8")
    print_table(speeds)
    log.info("wrote %s (%d bytes)", args.out, args.out.stat().st_size)


def print_table(speeds: dict) -> None:
    for speed, bands in speeds.items():
        print(f"\n{speed}: mistakes or blunders per counted move, % [95% Wilson interval]")
        names = [name for name, _, _ in SEGMENTS[speed]]
        print("band        games " + "".join(f"{name:>24}" for name in names))
        for b in bands:
            cols = []
            for name in names:
                e, n = b["rows"][name]["errors"]
                lo, hi = wilson(e, n)
                cols.append(f"{100 * e / n:5.2f} [{100 * lo:4.2f}, {100 * hi:4.2f}]" if n else "-")
            print(f"{b['lo']}-{b['hi']}  {b['games']:5d} " + "".join(f"{c:>24}" for c in cols))


if __name__ == "__main__":
    sys.exit(main())
