"""Command-line entry point. Subcommands are added milestone by milestone."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable
from pathlib import Path
from typing import Annotated

import httpx
import typer
from rich.console import Console
from rich.progress import BarColumn, Progress, TextColumn, TimeRemainingColumn
from rich.table import Table

from chess_coach import __version__
from chess_coach.config import ConfigError, Settings, better_database, config_file
from chess_coach.db import Store
from chess_coach.models import Game, Judgement
from chess_coach.util import parse_since, practice_day

UCI_MOVE = re.compile(r"[a-h][1-8][a-h][1-8][qrbn]?")  # not SAN such as e8=Q or R1a3

app = typer.Typer(no_args_is_help=True)
import_app = typer.Typer(no_args_is_help=True, help="Pull games into the local database.")
app.add_typer(import_app, name="import")
puzzles_app = typer.Typer(no_args_is_help=True, help="Puzzles from your own mistakes.")
app.add_typer(puzzles_app, name="puzzles")
eval_app = typer.Typer(no_args_is_help=True, help="Measure the coach: are the summaries right?")
app.add_typer(eval_app, name="eval")
console = Console()

SinceOption = Annotated[
    str | None, typer.Option("--since", help="Only games after this: 7d, 2w, 3m, or 2026-09-01.")
]
MaxOption = Annotated[int, typer.Option("--max", help="Maximum number of games to fetch.")]
RatedOption = Annotated[bool, typer.Option("--rated", help="Rated games only.")]


@app.callback()
def main(
    ctx: typer.Context,
    db: Annotated[
        Path | None,
        typer.Option(
            "--db",
            help="SQLite file. Default: CHESS_COACH_DB, then db in "
            "~/.config/chess-coach/config.toml, then data/chess-coach.db.",
        ),
    ] = None,
) -> None:
    """A coach that reads your games and trains you on your own mistakes."""
    try:
        # With --db the config file isn't read at all, so a broken one can't get in the way.
        ctx.obj = Settings(db_path=db) if db is not None else Settings()
    except ConfigError as exc:
        console.print(str(exc), style="red", markup=False, highlight=False)
        raise typer.Exit(code=1) from exc


@app.command()
def version() -> None:
    """Print the installed version."""
    typer.echo(f"chess-coach {__version__}")


def _store_games(settings: Settings, games: Iterable[Game]) -> tuple[int, int]:
    added = skipped = 0
    with Store(settings.db_path) as store:
        for game in games:
            if store.upsert_game(game):
                added += 1
            else:
                skipped += 1
    return added, skipped


def _report_import(added: int, skipped: int, settings: Settings) -> None:
    console.print(
        f"[green]{added}[/green] new games stored, {skipped} already present "
        f"-> [dim]{settings.db_path}[/dim]"
    )


@import_app.command("lichess")
def import_lichess(
    ctx: typer.Context,
    username: str,
    max_games: MaxOption = 100,
    since: SinceOption = None,
    rated: RatedOption = False,
) -> None:
    """Import games from Lichess (set LICHESS_TOKEN for a higher rate limit)."""
    from chess_coach.sources import lichess

    settings: Settings = ctx.obj
    with console.status(f"Fetching up to {max_games} games for {username} from Lichess..."):
        games = lichess.fetch_games(
            username,
            max_games=max_games,
            since=parse_since(since),
            rated_only=rated,
            token=settings.lichess_token,
        )
        added, skipped = _store_games(settings, games)
    _report_import(added, skipped, settings)


@import_app.command("chesscom")
def import_chesscom(
    ctx: typer.Context,
    username: str,
    max_games: MaxOption = 100,
    since: SinceOption = None,
    rated: RatedOption = False,
) -> None:
    """Import games from Chess.com."""
    from chess_coach.sources import chesscom

    settings: Settings = ctx.obj
    with console.status(f"Fetching up to {max_games} games for {username} from Chess.com..."):
        games = chesscom.fetch_games(
            username, max_games=max_games, since=parse_since(since), rated_only=rated
        )
        added, skipped = _store_games(settings, games)
    _report_import(added, skipped, settings)


@import_app.command("pgn")
def import_pgn(
    ctx: typer.Context,
    path: Annotated[Path, typer.Argument(exists=True, dir_okay=False, readable=True)],
    user: Annotated[str, typer.Option("--user", help="Your name as it appears in the PGN.")],
) -> None:
    """Import games from a PGN file. Games where --user did not play are skipped."""
    from chess_coach.sources.pgn import games_from_pgn_text

    settings: Settings = ctx.obj
    games = games_from_pgn_text(path.read_text(encoding="utf-8", errors="replace"), user)
    added, skipped = _store_games(settings, games)
    _report_import(added, skipped, settings)


@app.command()
def games(ctx: typer.Context, limit: int = 30) -> None:
    """List stored games, newest first."""
    settings: Settings = ctx.obj
    with Store(settings.db_path) as store:
        rows = list(store.iter_games())
    rows.sort(key=lambda g: (g.played_at is None, g.played_at), reverse=True)
    table = Table(title=f"{len(rows)} games in {settings.db_path}")
    table.add_column("id", no_wrap=True)
    for column in ("date", "colour", "opponent", "result", "speed", "opening", "analyzed"):
        table.add_column(column)
    for game in rows[:limit]:
        opponent = game.black if game.user_color == "white" else game.white
        table.add_row(
            game.id,
            game.played_at.strftime("%Y-%m-%d") if game.played_at else "?",
            game.user_color.value,
            opponent,
            game.user_result,
            game.speed.value,
            (game.opening or game.eco or "?")[:40],
            f"d{game.analyzed_depth}" if game.analyzed_depth else "-",
        )
    console.print(table)


@app.command()
def analyze(
    ctx: typer.Context,
    depth: Annotated[int | None, typer.Option(help="Search depth per position.")] = None,
    limit: Annotated[int | None, typer.Option(help="Stop after this many games.")] = None,
    threads: Annotated[int | None, typer.Option(help="Engine threads.")] = None,
    force: Annotated[
        bool, typer.Option("--force", help="Re-analyze games that are already analyzed.")
    ] = False,
    newest: Annotated[
        bool, typer.Option("--newest", help="Start with the most recent games (default: oldest).")
    ] = False,
) -> None:
    """Run Stockfish over every game not yet analyzed at this depth and store per-move facts."""
    settings: Settings = ctx.obj
    depth = depth or settings.analysis_depth
    with Store(settings.db_path) as store:
        pending = list(store.iter_games(unanalyzed_below_depth=None if force else depth))
        if newest:
            pending.reverse()
        if limit is not None:
            pending = pending[:limit]
        if not pending:
            console.print(f"Nothing to do: every game is analyzed at depth {depth} or deeper.")
            return
        with _engine(settings, threads) as engine:
            _analyze_games(store, engine, pending, depth)
        console.print(
            f"[green]Analyzed {len(pending)} games[/green] -> [dim]{settings.db_path}[/dim]"
        )


def _analyze_games(store: Store, engine, pending: list[Game], depth: int) -> None:
    from chess_coach.engine.analysis import analyze_game

    console.print(f"Using {engine.name} at depth {depth} for {len(pending)} games")
    progress = Progress(
        TextColumn("{task.description}"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total}"),
        TimeRemainingColumn(),
        console=console,
    )
    with progress:
        games_task = progress.add_task("games", total=len(pending))
        plies_task = progress.add_task("plies", total=1)
        for game in pending:
            progress.update(plies_task, completed=0, total=1, description=game.id)

            def on_ply(done: int, total: int) -> None:
                progress.update(plies_task, completed=done, total=total)

            facts = analyze_game(game, engine, depth=depth, on_ply=on_ply)
            store.replace_moves(game.id, facts, depth=depth)
            progress.advance(games_task)


SOURCES = {"lichess": "Lichess", "chesscom": "Chess.com"}


@app.command()
def sync(
    ctx: typer.Context,
    threads: Annotated[int | None, typer.Option(help="Engine threads.")] = None,
    puzzles: Annotated[
        int, typer.Option(help="How many new costly moves to check for puzzles.")
    ] = 200,
) -> None:
    """Bring the coach up to date in one step: new games from the accounts you've imported,
    analysed, puzzles from them, and their threats before every mistake not checked yet."""

    from chess_coach.engine.threats import DEPTH as THREAT_DEPTH
    from chess_coach.puzzles.make import make, make_opening_fixes
    from chess_coach.sources import chesscom, lichess

    settings: Settings = ctx.obj
    with Store(settings.db_path) as store:
        accounts = [a for a in store.accounts() if a[0] in SOURCES]
        if not accounts:
            console.print(
                "Nothing to sync from: import your games once first, "
                "e.g. `chess-coach import lichess <username>`."
            )
            raise typer.Exit(code=1)
        new: list[Game] = []
        for source, user, newest in accounts:
            where = SOURCES[source]
            with console.status(f"Fetching new games for {user} from {where}..."):
                try:
                    # All or nothing: both sites send newest first, so storing part of the
                    # stream would leave a gap that the next sync (from the newest game) skips.
                    if source == "lichess":
                        fetched = list(
                            lichess.fetch_games(
                                user, max_games=None, since=newest, token=settings.lichess_token
                            )
                        )
                    else:
                        fetched = list(chesscom.fetch_games(user, max_games=None, since=newest))
                except httpx.HTTPError as exc:
                    console.print(f"[red]{where} failed for {user}, nothing stored:[/red] {exc}")
                    continue
            added = [g for g in reversed(fetched) if store.upsert_game(g)]
            console.print(f"{user} on {where}: [green]{len(added)}[/green] new games")
            new += added
        if new:
            console.print(_new_games_line(new))

        depth = settings.analysis_depth
        pending = list(store.iter_games(unanalyzed_below_depth=depth))
        pending.reverse()  # newest first, so an interrupted sync has analysed the recent ones
        with _engine(settings, threads) as engine:
            if pending:
                _analyze_games(store, engine, pending, depth)
            with console.status("Making puzzles from your new mistakes..."):
                made = make(store, engine, limit=puzzles)
                fixes = make_opening_fixes(store, engine)
            checked = 0
            if todo := _threats_todo(store):  # the first sync also checks the older backlog
                console.print(f"Looking for their threats before {todo} mistakes and blunders")
                checked = _check_threats(store, engine, todo, depth=THREAT_DEPTH)
        total = len(store.puzzles())
        alerts = _repeat_alerts(store, new) if new else []
        status = _today_status(store)
    console.print(
        f"Analysed {len(pending)} games. Checked {made.checked + fixes.checked} moves, made "
        f"[green]{made.made + fixes.made}[/green] puzzles ({total} in all)."
    )
    console.print(
        f"Looked for their threats before {checked} mistakes and blunders not checked before "
        "(yours and your opponents'): `chess-coach threats` shows how your mistakes happen."
    )
    for line in alerts:
        console.print(f"[yellow]{line}[/yellow]")
    console.print(status, highlight=False)


def _repeat_alerts(store: Store, new: list[Game]) -> list[str]:
    """'You played 3...Bg7 again (12th time); it has a puzzle.' for each mistake you repeat from
    the same position (coach.repeats) that the new games played once more."""
    from chess_coach.coach.repeats import again, alert, repeats

    found = repeats(
        list(store.iter_games()),
        list(store.iter_moves(user_only=True)),
        store.replies_to_errors(),
        store.puzzles(),
    )
    return [alert(r) for r in again(found, {g.id for g in new})]


def _new_games_line(games: list[Game]) -> str:
    """'23 new games: 18 bullet; 5 blitz (won 3, lost 2)'. Bullet results don't count."""
    from chess_coach.patterns.features import is_bullet, result_of

    speeds = Counter(g.speed.value for g in games)
    parts = []
    for speed, n in speeds.most_common():
        group = [g for g in games if g.speed.value == speed]
        if is_bullet(group[0]):
            parts.append(f"{n} {speed}")
            continue
        results = Counter(result_of(g) for g in group)
        scored = ", ".join(
            f"{word} {results[key]}"
            for key, word in (("win", "won"), ("draw", "drew"), ("loss", "lost"))
            if results[key]
        )
        parts.append(f"{n} {speed} ({scored})" if scored else f"{n} {speed}")
    return f"{len(games)} new games: " + "; ".join(parts)


@app.command()
def stats(ctx: typer.Context) -> None:
    """Per-game accuracy summary for the coached user: cp loss and mistake counts by phase.
    In bullet only the first 10 moves count."""
    from chess_coach.patterns.features import counted

    settings: Settings = ctx.obj
    with Store(settings.db_path) as store:
        games_by_id = {g.id: g for g in store.iter_games() if g.analyzed_depth}
        moves = [
            m
            for m in store.iter_moves(user_only=True)
            if m.game_id in games_by_id and counted(m, games_by_id[m.game_id])
        ]
    if not games_by_id:
        console.print("No analyzed games yet. Run `chess-coach analyze` first.")
        return
    table = Table(title=f"{len(games_by_id)} analyzed games ({settings.db_path})")
    table.add_column("game", no_wrap=True)
    table.add_column("result")
    table.add_column("acpl", justify="right")
    table.add_column("inacc", justify="right")
    table.add_column("mist", justify="right")
    table.add_column("blund", justify="right")
    table.add_column("worst move")
    for game_id, game in games_by_id.items():
        own = [m for m in moves if m.game_id == game_id]
        if not own:
            continue
        counts = {j: sum(1 for m in own if m.judgement is j) for j in Judgement}
        worst = max(own, key=lambda m: m.wp_loss)
        table.add_row(
            game_id,
            game.user_result,
            f"{sum(m.cp_loss for m in own) / len(own):.0f}",
            str(counts[Judgement.INACCURACY]),
            str(counts[Judgement.MISTAKE]),
            str(counts[Judgement.BLUNDER]),
            f"{worst.move_number}{'.' if worst.color == 'white' else '...'}{worst.san} "
            f"(-{worst.wp_loss:.0f}%, best {worst.best_san})",
        )
    console.print(table)

    own_moves = [m for m in moves if m.game_id in games_by_id]
    motifs = Counter(tag for m in own_moves for tag in m.motifs)
    if motifs:
        summary = Table(title=f"Motifs on your {len(own_moves)} moves")
        summary.add_column("motif")
        summary.add_column("moves", justify="right")
        summary.add_column("games", justify="right")
        for tag, count in motifs.most_common():
            games_with = len({m.game_id for m in own_moves if tag in m.motifs})
            summary.add_row(tag, str(count), str(games_with))
        console.print(summary)


@app.command()
def digest(
    ctx: typer.Context,
    out: Annotated[
        Path | None, typer.Option("--out", "-o", help="Write the Markdown here instead of stdout.")
    ] = None,
    top: Annotated[int, typer.Option(help="How many patterns per section.")] = 5,
    min_moves: Annotated[int, typer.Option(help="Smallest move slice worth reporting.")] = 30,
    min_games: Annotated[int, typer.Option(help="Smallest game slice worth reporting.")] = 8,
    since: SinceOption = None,
    narrate: Annotated[
        str | None,
        typer.Option(help="Add a plain-language summary by a model: ollama (local) or openrouter."),
    ] = None,
    model: Annotated[str | None, typer.Option(help="Model for --narrate.")] = None,
) -> None:
    """Patterns across your analysed games: where you go wrong, with the moves behind each claim.

    With --since (e.g. 7d for a weekly digest) only those games count, compared with the ones
    before."""
    from chess_coach.coach.digest import build, render
    from chess_coach.coach.levels import MAX_PLY, headline
    from chess_coach.coach.levels import report as levels_report
    from chess_coach.coach.punish import GIFT_WP_LOSS
    from chess_coach.patterns.features import ERRORS

    settings: Settings = ctx.obj
    with Store(settings.db_path) as store:
        games = list(store.iter_games())
        moves = list(store.iter_moves(user_only=True))
        gifts = store.gifts(GIFT_WP_LOSS)
        replies, puzzles = store.replies_to_errors(), store.puzzles()
        threats = store.threats()
        errors = list(store.iter_moves(judgements=ERRORS)) if threats else []
        early = list(store.iter_moves(max_ply=MAX_PLY))
    built = build(
        games,
        moves,
        min_moves=min_moves,
        min_games=min_games,
        since=parse_since(since),
        gifts=gifts,
        replies=replies,
        puzzles=puzzles,
        threats=threats,
        errors=errors,
        next_level=headline(levels_report(games, early, practice_day())),
    )
    text = render(built, top=top)
    if narrate:
        from chess_coach.coach.narrate import model_for
        from chess_coach.coach.narrate import narrate as write_summary

        try:
            with console.status(f"Asking {narrate} for a summary..."):
                text += "\n" + write_summary(built, model_for(narrate, model))
        except (ValueError, RuntimeError, httpx.HTTPError) as exc:
            console.print(f"[yellow]No summary: {exc}[/yellow]")
    if out is None:
        typer.echo(text, nl=False)
    else:
        out.write_text(text, encoding="utf-8")
        console.print(f"Wrote the digest -> [dim]{out}[/dim]")


@app.command()
def punish(
    ctx: typer.Context,
    since: SinceOption = None,
    examples: Annotated[int, typer.Option(help="Missed punishments to show per opening.")] = 2,
) -> None:
    """When they blunder: what your reply gave back, against what your opponents' replies gave
    back after your blunders, with partial misses (10-20 points) and clear ones (20+) apart. In
    bullet only moves 1-10 count; replies short on time don't."""
    from chess_coach.coach.punish import (
        GIFT_WP_LOSS,
        KINDS,
        OUTCOMES,
        TREND_GIFTS,
        headline,
        part_note,
        report,
        worst_title,
    )

    settings: Settings = ctx.obj
    with Store(settings.db_path) as store:
        games = [g for g in store.iter_games(since=parse_since(since)) if g.analyzed_depth]
        r = report(games, store.gifts(GIFT_WP_LOSS), examples=examples)
    if not r.gifts:
        console.print("No blunders to compare yet. Run `chess-coach analyze` first.")
        return
    console.print(
        "[bold]When they blunder[/bold] (a move losing 20+ points of win probability), your reply "
        "keeps the gift (gives back under 10 points), takes only part of it (10-20: a partial "
        "miss) or misses it (20 or more: a clear miss). Yours against your opponents' replies to "
        "your blunders."
    )
    for s in r.groups:
        console.print(f"\n[bold]{s.label}[/bold]: {headline(s)}")
    rows = [
        (f"{s.label}: {OUTCOMES[c.label]}", c)
        for s in r.groups
        for c in (s.partial, s.clear, s.missed)
    ]
    console.print(_rates_table("What your reply gave back", "group", rows, ("you", "they")))
    if r.partial_lesson:
        console.print(f"[green]What to learn:[/green] {r.partial_lesson}")
        console.print(
            f"\n[bold]Where you took only part of it ({', '.join(r.partial_focus)})[/bold]"
        )
        for g in r.partials:
            console.print(_gift_line(g))
    title = "Missed (partial or clear), by the move that punishes"
    console.print(_rates_table(title, "punishing move", [(KINDS[c.label], c) for c in r.kinds]))
    for lesson in r.lessons:
        console.print(f"[green]What to learn:[/green] {lesson}")
    if not r.lessons:
        console.print("No kind of punishing move stands out against your opponents.")
    title = (
        f"Missed over time (about {TREND_GIFTS} of their blunders you could punish per row; the "
        "oldest may hold fewer)"
    )
    console.print(_rates_table(title, "games played", [(c.label, c) for c in r.trend]))
    # Openings only where you clearly miss more (inside a group where you do), each with examples
    # of the part behind most of its gap; otherwise the examples in r.worst (worst_title says
    # what they are), if any.
    if r.families:
        title = (
            f"Openings where you miss more than your opponents, partial and clear together "
            f"({', '.join(r.focus)})"
        )
        rows = [(f.compare.label, f.compare) for f in r.families]
        console.print(_rates_table(title, "opening", rows))
        for f in r.families:
            console.print(f"[bold]{f.compare.label}[/bold]: {part_note(f.part)}.")
            for g in f.examples:
                console.print(_gift_line(g))
    elif r.worst:
        console.print(f"\n[bold]{worst_title(r)}[/bold]")
        for g in r.worst:
            console.print(_gift_line(g))
    console.print(
        "Drill the punishments you missed outright (puzzles are made from moves losing 20+ "
        "points, so partial misses have none): `chess-coach drill --tag punish_capture` (or "
        "punish_check, punish_quiet; missed_tactic for all of them)."
    )


def _gift_line(g) -> str:
    """'Modern Defense: after 2.Bh6 you played 2...Bg7; 2...Nxh6 would have punished it.' (a
    partial miss took some of it: 'would have taken all of it'), then what their blunder allowed
    in chess terms and the link."""
    from chess_coach.coach.punish import label

    ending = "would have taken all of it" if g.outcome == "partial" else "would have punished it"
    text = f"after {label(g.blunder)} you played {label(g.reply)}; "
    text += f"{label(g.reply, g.reply.best_san)} {ending}."
    family = g.game.opening_family
    parts = [
        f"{family}: {text}" if family else text[0].upper() + text[1:],
        *g.reasons(),
        f"[dim]{g.url}[/dim]" if g.url else "",
    ]
    return " ".join(p for p in parts if p)


def _rates_table(
    title: str, first: str, rows, columns: tuple[str, str] = ("you missed", "they missed")
) -> Table:
    """Your rate against your opponents' per (name, Compare) row, each with its 95% interval;
    '–' where a side had no chances, so there is no rate and no gap."""
    table = Table(title=title)
    for column in (first, *columns, "gap, points (95%)"):
        table.add_column(column, justify="left" if column == first else "right")
    for name, c in rows:
        gap = "–"
        if c.gap is not None and c.interval is not None:
            low, high = c.interval
            gap = f"{100 * c.gap:+.1f} ({100 * low:+.1f} to {100 * high:+.1f})"
        table.add_row(
            name,
            *(
                f"{r.rate:.1%} of {r.n} ({r.low:.0%}-{r.high:.0%})" if r.n else "–"
                for r in (c.yours, c.theirs)
            ),
            gap,
        )
    return table


@app.command()
def threats(
    ctx: typer.Context,
    depth: Annotated[int, typer.Option(help="Search depth per position.")] = 12,
    threads: Annotated[int | None, typer.Option(help="Engine threads.")] = None,
    limit: Annotated[int | None, typer.Option(help="Stop after this many moves.")] = None,
) -> None:
    """What was their threat? Before every counted mistake and blunder, yours and your
    opponents', the mover passes and the engine finds what the other side threatened. Mistakes
    split into ignored threats and unsafe moves, compared with your opponents'. Resumable."""
    settings: Settings = ctx.obj
    with Store(settings.db_path) as store:
        todo = _threats_todo(store, limit)
        if todo:
            with _engine(settings, threads) as engine:
                console.print(f"Using {engine.name} at depth {depth} for {todo} moves")
                _check_threats(store, engine, todo, depth=depth, limit=limit)
        _threat_summary(store)


def _threats_todo(store: Store, limit: int | None = None) -> int:
    """How many mistakes and blunders (yours and your opponents') a threat check has left."""
    from chess_coach.coach.threats import pending
    from chess_coach.patterns.features import ERRORS

    todo = len(pending(store.iter_games(), store.iter_moves(judgements=ERRORS), store.threats()))
    return min(todo, limit) if limit is not None else todo


def _check_threats(store: Store, engine, todo: int, *, depth: int, limit: int | None = None) -> int:
    """Look for their threats before the `todo` mistakes and blunders not checked yet, with a
    progress bar; returns how many were checked."""
    import logging

    from chess_coach.coach.threats import check

    logging.getLogger("chess.engine").setLevel(logging.ERROR)
    progress = Progress(
        TextColumn("checking"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total}"),
        TimeRemainingColumn(),
        console=console,
    )
    with progress:
        task = progress.add_task("check", total=todo)

        def on_check(done: int, total: int) -> None:
            progress.update(task, completed=done, total=total)

        return check(store, engine, depth=depth, limit=limit, on_check=on_check)


def _threat_summary(store: Store) -> None:
    from chess_coach.coach.threats import NO_GAP, gap, percent, report
    from chess_coach.patterns.features import ERRORS

    games = list(store.iter_games())
    r = report(games, store.iter_moves(judgements=ERRORS), store.threats())
    if not r.you:
        if r.unchecked:
            console.print(
                f"[yellow]{r.unchecked} mistakes and blunders not checked yet[/yellow] "
                "(yours and your opponents'): run `chess-coach threats` to check them."
            )
        elif not any(g.analyzed_depth for g in games):
            console.print("No analysed games yet. Run `chess-coach analyze` first.")
        else:
            console.print(
                "None of your mistakes and blunders count here: in bullet only the first 10 "
                "moves count, and moves made short on time are left out."
            )
        return
    table = Table(
        title=f"How your mistakes happen: {r.you} of your mistakes and blunders, "
        f"{r.them} of your opponents'",
        caption="Share of the mistakes and blunders, with its 95% interval. In bullet only the "
        "first 10 moves count; moves made short on time are left out.",
    )
    for column in ("what happened", "you", "your opponents", "compared"):
        table.add_column(
            column, justify="left" if column in ("what happened", "compared") else "right"
        )
    for s in r.steps:
        table.add_row(s.title, percent(s.you), percent(s.them), f"{s.verdict} ({gap(s)})")
    console.print(table)
    mine = [s for s in r.steps if s.yours]
    for s in mine:
        console.print(f"[bold]{s.title}[/bold] is a step to work on. Practise: {s.tip}.")
    if not mine:
        console.print(NO_GAP)
    for s in r.steps:
        for e in s.examples[:2]:
            where = f" [dim]{e.evidence.url}[/dim]" if e.evidence.url else ""
            console.print(f"  {s.title}, {e.evidence.move}: {e.why}{where}")
    if r.unchecked:
        console.print(
            f"[yellow]{r.unchecked} mistakes and blunders not checked yet:[/yellow] run "
            "`chess-coach threats` to check them."
        )


def _engine(settings: Settings, threads: int | None):
    from chess_coach.engine.stockfish import Engine, EngineNotFoundError

    try:
        return Engine(
            settings.stockfish_path,
            threads=threads or settings.engine_threads,
            hash_mb=settings.engine_hash_mb,
        )
    except EngineNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc


@puzzles_app.command("make")
def puzzles_make(
    ctx: typer.Context,
    limit: Annotated[int, typer.Option(help="How many candidate moves to check this run.")] = 50,
    depth: Annotated[int, typer.Option(help="Search depth for the check.")] = 18,
    threads: Annotated[int | None, typer.Option(help="Engine threads.")] = None,
    openings: Annotated[
        bool, typer.Option("--openings", help="Also drill the fixes in your favourite openings.")
    ] = False,
) -> None:
    """Check your costliest moves (newest games first) and keep the ones with one clear answer."""
    from chess_coach.puzzles.make import make, make_opening_fixes

    settings: Settings = ctx.obj
    with Store(settings.db_path) as store, _engine(settings, threads) as engine:
        progress = Progress(
            TextColumn("checking"),
            BarColumn(),
            TextColumn("{task.completed}/{task.total}"),
            console=console,
        )
        with progress:
            task = progress.add_task("check", total=limit)

            def on_check(done: int, total: int) -> None:
                progress.update(task, completed=done, total=total)

            result = make(store, engine, depth=depth, limit=limit, on_check=on_check)
        if openings:
            with console.status("Checking the fixes in your favourite openings..."):
                fixes = make_opening_fixes(store, engine, depth=depth)
            result.checked += fixes.checked
            result.made += fixes.made
        total = len(store.puzzles())
    console.print(
        f"Checked {result.checked} moves, made [green]{result.made}[/green] puzzles "
        f"({total} in all). Practise with `chess-coach drill`."
    )


@puzzles_app.command("prune")
def puzzles_prune(ctx: typer.Context) -> None:
    """Drop puzzles you haven't tried that no longer count: from bullet after move 10, or from
    moves made short on time (unless that mistake keeps repeating)."""
    from chess_coach.puzzles.make import prune

    settings: Settings = ctx.obj
    with Store(settings.db_path) as store:
        removed = prune(store)
        left = len(store.puzzles())
    console.print(f"Removed {len(removed)} puzzles that no longer count; {left} left.")


@puzzles_app.command("dedupe")
def puzzles_dedupe(ctx: typer.Context) -> None:
    """Merge puzzles stored more than once (same position, same answer): keep the copy you've
    tried, otherwise the oldest, with every copy's tags. Tried copies are never deleted."""
    import chess

    from chess_coach.puzzles.make import dedupe

    settings: Settings = ctx.obj
    with Store(settings.db_path) as store:
        merges = dedupe(store)
        left = len(store.puzzles())
    if not merges:
        console.print(f"No puzzle is stored twice ({left} puzzles).")
        return
    for m in merges:
        p = m.kept[0]
        board = chess.Board(p.fen)
        answer = f"{_number(board)}{p.solution_san}"
        if len(m.kept) > 1:
            kept = f"kept all {len(m.kept)} copies you've tried ({', '.join(k.id for k in m.kept)})"
        else:
            kept = f"kept {p.id}" + (" (the one you've tried)" if m.tried else "")
        line = f"{answer} (you played {p.played_san}): {kept}"
        if m.removed:
            line += f", removed {', '.join(m.removed)}"
        if m.added:
            line += f"; it now also has {', '.join(m.added)}"
        console.print(line)
    removed = sum(len(m.removed) for m in merges)
    console.print(
        f"{len(merges)} positions were stored more than once: removed {removed} copies, "
        f"{left} puzzles left."
    )


def _number(board) -> str:
    """'12.' when White is to move, '12...' when Black is."""
    return f"{board.fullmove_number}{'.' if board.turn else '...'}"


@puzzles_app.command("list")
def puzzles_list(
    ctx: typer.Context,
    tag: Annotated[str | None, typer.Option(help="Only puzzles with this tag.")] = None,
) -> None:
    """Your puzzles, with their tags and when each is due."""
    settings: Settings = ctx.obj
    with Store(settings.db_path) as store:
        puzzles = store.puzzles(tag)
        due = {p.id: r.due for p in puzzles if (r := store.review(p.id))}
    table = Table(title=f"{len(puzzles)} puzzles")
    for column in ("puzzle", "you played", "answer", "tags", "due"):
        table.add_column(column)
    for p in puzzles:
        table.add_row(p.id, p.played_san, p.solution_san, ", ".join(p.tags), due.get(p.id, "-"))
    console.print(table)


def _board_text(fen: str) -> str:
    import chess

    board = chess.Board(fen)
    return board.unicode(empty_square="·", orientation=board.turn)


@app.command()
def drill(
    ctx: typer.Context,
    count: Annotated[int, typer.Option("--count", "-n", help="Puzzles this session.")] = 10,
    tag: Annotated[str | None, typer.Option(help="Only puzzles with this tag.")] = None,
) -> None:
    """Practise the puzzles due again, then new ones. Answer in SAN (Nf3) or UCI (g1f3); ? shows
    the answer."""
    from chess_coach.puzzles.today import practice_queue, read_facts

    settings: Settings = ctx.obj
    with Store(settings.db_path) as store:
        again, new = practice_queue(store, read_facts(store), practice_day(), tag)
        due = [*again, *new][:count]
        if not due:
            console.print(
                "Nothing due. Make more with `chess-coach puzzles make`, or come back tomorrow."
            )
            return
        solved = sum(
            _puzzle_turn(store, p, f"Puzzle {i} of {len(due)}") for i, p in enumerate(due, 1)
        )
        console.print(f"\nSolved {solved} of {len(due)}.")


def _ask_move(board) -> tuple[str, object | None]:
    """Your answer, as you typed it, and the move: None when you asked to see it (?) or after three
    tries that weren't legal moves."""
    import chess

    answer = ""
    for _ in range(3):
        answer = typer.prompt("Your move").strip()
        if answer == "?":
            return answer, None
        try:
            move = (
                chess.Move.from_uci(answer)
                if UCI_MOVE.fullmatch(answer)
                else board.parse_san(answer)
            )
        except ValueError:
            console.print("That isn't a legal move here; try again (or ? to see the answer).")
            continue
        if move not in board.legal_moves:
            console.print("That isn't a legal move here; try again (or ? to see the answer).")
            continue
        return answer, move
    return answer, None


def _puzzle_turn(store: Store, p, title: str) -> bool:
    """Ask one puzzle in the terminal and record the answer. Returns whether you found it."""
    from datetime import UTC, datetime

    import chess

    from chess_coach.puzzles.srs import schedule

    board = chess.Board(p.fen)
    side = "White" if board.turn == chess.WHITE else "Black"
    game = store.get_game(p.game_id)
    console.print(f"\n[bold]{title}[/bold]  ({side} to move; {', '.join(p.tags)})")
    console.print(_board_text(p.fen))
    console.print(f"In the game you played [red]{p.played_san}[/red]. Find the best move.")
    answer, move = _ask_move(board)
    correct = move is not None and p.is_right(move.uci())
    line = _san_line(board, p.solution, p.line)
    if correct:
        console.print(f"[green]Correct.[/green] {line}")
    else:
        console.print(f"[yellow]The move was {p.solution_san}.[/yellow] {line}")
    review = store.review(p.id)
    if review is not None:
        now = datetime.now(UTC)
        after = schedule(review, correct, practice_day(now))
        store.save_attempt(after, answer, correct, now)
        console.print(f"[dim]Next time: {after.due}.[/dim]")
    if game is not None and game.url:
        console.print(f"[dim]The game: {game.move_url(p.ply)}[/dim]")
    return correct


def _card_turn(store: Store, card, title: str, analyse) -> bool:
    """Ask one due opening card in the terminal and record the answer: judged as on the page."""
    from datetime import UTC, datetime

    import chess

    from chess_coach.coach import training
    from chess_coach.coach import tree as optree

    moves = [chess.Move.from_uci(u) for u in card.path]
    board = chess.Board()
    line = board.variation_san(moves) if moves else ""
    for m in moves:
        board.push(m)
    games = [
        g
        for g in store.iter_games()
        if g.analyzed_depth and g.opening_family == card.family and g.user_color.value == card.color
    ]
    ids = {g.id for g in games}
    facts = [m for m in store.iter_moves(max_ply=optree.TREE_PLIES) if m.game_id in ids]
    root = optree.build(games, facts)
    console.print(f"\n[bold]{title}[/bold]  (your {card.family}; {card.color} to move)")
    console.print(_board_text(board.fen()))
    if line:
        console.print(f"[dim]{line}[/dim]")
    kind = training.kind_at(root, card.path)
    console.print(
        "Their last move was a mistake you've met before. Punish it."
        if kind == training.PUNISH
        else "What do you play here?"
    )
    answer, move = _ask_move(board)
    judged = training.judge(kind, optree.walk(root, card.path), board, move, analyse)
    target = board.san(judged.target) if judged.target else None
    if judged.correct:
        console.print(f"[green]{board.san(move)}: good.[/green]")
    if target is None:
        if not judged.correct:
            console.print("[yellow]No move is known for this position.[/yellow]")
    elif not judged.correct or target != board.san(move):
        console.print(f"[yellow]The move to remember: {target}.[/yellow]")
    now = datetime.now(UTC)
    if not store.coach_answered(card.id, practice_day(now).isoformat()):
        after = training.answer(
            card, card.family, card.color, card.path, kind, judged.correct, practice_day(now)
        )
        store.save_coach_attempt(after, answer, judged.correct, now)
        console.print(f"[dim]Next time: {after.due}.[/dim]")
    return judged.correct


def _san_line(board, solution: str, line: str) -> str:
    import chess

    b = board.copy()
    sans = []
    for uci in [solution, *line.split()]:
        move = chess.Move.from_uci(uci)
        if move not in b.legal_moves:
            break
        sans.append(b.san(move))
        b.push(move)
    return f"Line: {' '.join(sans)}" if sans else ""


@app.command()
def progress(
    ctx: typer.Context,
    by: Annotated[str, typer.Option(help="month or week")] = "month",
    speed: Annotated[str | None, typer.Option(help="Only this speed, e.g. blitz.")] = None,
) -> None:
    """Your error rate, centipawn loss and score over time, how your puzzle practice goes, and
    your rating week by week next to the practice you did."""

    from chess_coach.coach.progress import (
        periods,
        practice_effect,
        solve_rates,
        trend_speeds,
        weekly,
    )

    settings: Settings = ctx.obj
    with Store(settings.db_path) as store:
        games = list(store.iter_games())
        rows = periods(games, list(store.iter_moves(user_only=True)), by, speed)
        attempts, answers = store.attempts(), store.coach_attempts()
        overall, tags = solve_rates(store.puzzles(), attempts)
    if not rows:
        console.print("No analysed games yet. Run `chess-coach analyze` first.")
        return
    table = Table(
        title=f"Your games by {by}" + (f" ({speed})" if speed else ""),
        caption="In bullet only your first 10 moves count, and the score leaves bullet out.",
    )
    for column in (by, "games", "score", "mistakes+blunders", "acpl"):
        table.add_column(column, justify="left" if column == by else "right")
    for r in rows:
        score = "-" if r.score is None else f"{r.score:.0%}"
        table.add_row(r.label, str(r.games), score, f"{r.error_rate:.1%}", f"{r.acpl:.0f}")
    console.print(table)
    if overall.attempts:
        t = Table(
            title=f"Puzzles: solved {overall.solved} of {overall.attempts} ({overall.rate:.0%})"
        )
        for column in ("tag", "attempts", "solved"):
            t.add_column(column, justify="left" if column == "tag" else "right")
        for r in tags:
            t.add_row(r.name, str(r.attempts), f"{r.rate:.0%}")
        console.print(t)
    speeds = trend_speeds(games)
    trend = speed if speed in speeds else (speeds[0] if speeds else None)
    if trend is None:
        return
    weeks = weekly(games, trend, attempts, answers, today=practice_day())
    t = Table(
        title=f"Rating and practice by week ({trend})",
        caption="Your rating in your last game each week; bullet is left out.",
    )
    for column in ("week", "games", "rating", "change", "puzzles solved", "openings right"):
        t.add_column(column, justify="left" if column == "week" else "right")
    for w in weeks:
        t.add_row(
            w.label,
            str(w.games or ""),
            "" if w.rating is None else str(w.rating),
            "" if w.change is None else f"{w.change:+d}",
            f"{w.solved} of {w.puzzles}" if w.puzzles else "",
            f"{w.right} of {w.answers}" if w.answers else "",
        )
    console.print(t)
    if effect := practice_effect(weeks):
        console.print(
            f"Weeks you practised: {effect[0]:+.1f} a week on average ({effect[1]} weeks); weeks "
            f"you didn't: {effect[2]:+.1f} ({effect[3]} weeks). A comparison, not proof."
        )


@app.command()
def openings(
    ctx: typer.Context,
    min_games: Annotated[int, typer.Option(help="Smallest number of games per opening.")] = 5,
    top: Annotated[int, typer.Option(help="How many openings to show.")] = 3,
) -> None:
    """The openings you score worst in, the book line, and the slips you repeat in them; then how
    every opening comes out by the engine after both sides' 10th moves (bullet games too)."""
    from chess_coach.coach.repertoire import LEVEL_PLY, after_ten
    from chess_coach.coach.study import openings as opening_stats

    settings: Settings = ctx.obj
    with Store(settings.db_path) as store:
        games, moves = list(store.iter_games()), list(store.iter_moves(user_only=True))
        early = list(store.iter_moves(max_ply=LEVEL_PLY))  # both sides': ply 20 is Black's move
    stats = opening_stats(games, moves, min_games)
    if not stats:
        console.print(f"No opening has {min_games} analysed games yet.")
        return
    for o in stats[:top]:
        if o.score is None or o.decided < min_games:
            scored = f"{o.decided} outside bullet, too few to score (bullet results don't count)"
        else:
            scored = f"scored {o.score:.0%} ({o.wins} won, {o.draws} drawn, {o.losses} lost"
            scored += "; bullet results don't count)" if o.decided < o.games else ")"
        console.print(
            f"\n[bold]{o.family}[/bold] with {o.color.capitalize()}: {o.games} games, {scored}"
        )
        if o.book:
            console.print(f"  Book line ({o.name}): {o.book}")
        for slip in o.slips:
            where = f" e.g. {slip.example_url}" if slip.example_url else ""
            console.print(
                f"  You played {slip.move} in {slip.games} games; the engine prefers "
                f"{slip.best}.{where}"
            )
    _after_ten_tables(after_ten(games, early, min_games), min_games)


def _pawns(cp: int) -> str:
    """+1.2, -0.5, and 0.0 rather than -0.0."""
    return f"{cp / 100:+.1f}" if abs(cp) >= 5 else "0.0"


def _after_ten_tables(ranked: dict, min_games: int) -> None:
    """Openings by the engine's eval after both sides' 10th moves: results don't come into it, so
    bullet games count. Ranked by the mistake rate's interval and what you lost since book."""
    if not any(ranked.values()):
        console.print(
            f"\nNo opening has {min_games} games that got to both sides' 10th moves yet, so none "
            "is judged by the engine there."
        )
        return
    console.print(
        "\n[bold]Engine eval after both sides' 10th moves (not results)[/bold], so bullet games "
        f"count too; openings with {min_games}+ games that got that far. Worst first: by your "
        "mistake rate on moves 1-10 (the low end of its 95% interval), then by what you lost "
        "since book, so a gambit's pawn is the table's, not yours. Since book counts from the "
        "last position the opening table names, or from just before it when a White move reached "
        "it (that move then counts too), so both sides make the same number of moves."
    )
    for color, rows in ranked.items():
        if not rows:
            continue
        table = Table(title=f"With {color.capitalize()}")
        for column in ("opening", "games", "eval (median)", "at -1 or worse", "mistakes 1-10"):
            table.add_column(column, justify="left" if column == "opening" else "right")
        table.add_column("since book", justify="right")
        for a in rows:
            (bad_low, bad_high), (err_low, err_high) = a.bad_interval, a.error_interval
            table.add_row(
                a.family,
                str(a.games),
                _pawns(a.median_cp),
                f"{a.bad_rate:.0%} ({bad_low:.0%}-{bad_high:.0%})",
                f"{a.error_rate:.1%} ({err_low:.1%}-{err_high:.1%})",
                _pawns(a.since_book),
            )
        console.print(table)


@app.command()
def repeats(
    ctx: typer.Context,
    min_games: Annotated[int, typer.Option(help="Smallest number of games it happened in.")] = 3,
    top: Annotated[int, typer.Option(help="How many to show.")] = 10,
) -> None:
    """The same mistake from the same position in several games: what it allows, the better move,
    and whether a puzzle drills it."""
    from chess_coach.coach.repeats import repeats as find

    settings: Settings = ctx.obj
    with Store(settings.db_path) as store:
        found = find(
            list(store.iter_games()),
            list(store.iter_moves(user_only=True)),
            store.replies_to_errors(),
            store.puzzles(),
            min_games,
        )
    if not found:
        console.print(f"No mistake of yours repeats from the same position in {min_games}+ games.")
        return
    console.print(
        f"{len(found)} mistakes you repeat from the same position, in {min_games} or more games "
        "(inaccuracies or worse; in bullet only your first 10 moves count, and moves made short "
        "on time don't)."
    )
    for r in found[:top]:
        where = f" ({r.family})" if r.family else ""
        last = f", last on {r.last:%Y-%m-%d}" if r.last else ""
        console.print(f"\n[bold]{r.move}[/bold]{where} in {r.games} games{last}.")
        console.print(f"  Better: {r.better}.")
        if r.punish:
            console.print(
                f"  Their best answer: {r.punish}, found {r.punished} of {r.replies} times."
            )
        for reason in r.reasons:
            console.print(f"  {reason}")
        console.print(f"  It has a puzzle ({r.puzzle})." if r.puzzle else "  No puzzle for it yet.")
        for url in r.urls[:3]:
            console.print(f"  [dim]{url}[/dim]")


def _percent(rate) -> str:
    return f"{rate.rate:.1%} ({rate.low:.1%}-{rate.high:.1%})" if rate.moves else "-"


@app.command()
def levels(ctx: typer.Context) -> None:
    """How often you go wrong early in a game, against public Lichess players by rating, from your
    last 12 months. Says so when your games can't place you."""
    from chess_coach.coach.levels import MAX_PLY, headline
    from chess_coach.coach.levels import report as levels_report

    settings: Settings = ctx.obj
    with Store(settings.db_path) as store:
        all_games = list(store.iter_games())
        early = list(store.iter_moves(max_ply=MAX_PLY))
    rep = levels_report(all_games, early, practice_day())
    if not rep.speeds:
        console.print("No analysed bullet or blitz games yet: run `chess-coach analyze`.")
        return
    if line := headline(rep):
        console.print(line, highlight=False)
    for s in rep.speeds:
        rated = f", your median rating {s.rating}" if s.rating else ""
        title = f"{s.speed.capitalize()}: {s.games} games at {' or '.join(s.controls)}{rated}"
        table = Table(title=title, title_justify="left")
        for column in ("moves", "you", "your opponents", "300 points above you"):
            table.add_column(column, justify="left" if column == "moves" else "right")
        for r in s.rows:
            above = "-" if r.above is None else f"{r.above:.1%}"
            table.add_row(r.segment, _percent(r.you), _percent(r.them), above)
        console.print(table)
        for r in s.rows:
            console.print(r.sentence, highlight=False)
        if note := s.left_out_note:
            console.print(f"[dim]{note}[/dim]", highlight=False)
        if note := s.stale_note:
            console.print(f"[dim]{note}[/dim]", highlight=False)
    console.print(
        f"\n[dim]From your last 12 months. Mistakes and blunders (20+ points of win probability), "
        "with 95% intervals. The "
        f"public games are unanalysed Lichess games, scored the same way (built {rep.built}). "
        "`digest` has this in one line.[/dim]",
        highlight=False,
    )


def _today_status(store: Store) -> str:
    from datetime import timedelta

    from chess_coach.puzzles.today import read_facts, session, status_line, upcoming

    facts = read_facts(store)
    day = practice_day()
    return status_line(session(store, facts, day), upcoming(store, facts, day + timedelta(days=1)))


def _today_in_the_terminal(store: Store, settings: Settings) -> None:
    from chess_coach.engine.stockfish import Engine
    from chess_coach.puzzles.today import read_facts, session
    from chess_coach.web.app import Analyst

    facts = read_facts(store)
    analyse = Analyst(
        lambda: Engine(
            settings.stockfish_path,
            threads=settings.engine_threads,
            hash_mb=settings.engine_hash_mb,
        )
    )
    try:
        while (now := session(store, facts, practice_day())).items:
            item = now.items[0]
            title = f"{now.done + 1} of {now.total}"
            if item.card is not None:
                _card_turn(store, item.card, title, analyse)
            else:
                _puzzle_turn(store, item.puzzle, title)
    finally:
        analyse.close()
    console.print(f"\n{_today_status(store)}")


@app.command("today")
def today_command(
    ctx: typer.Context,
    in_terminal: Annotated[bool, typer.Option("--cli", help="Practise in the terminal.")] = False,
    status: Annotated[bool, typer.Option("--status", help="Print one line: what's left.")] = False,
    port: Annotated[int, typer.Option(help="Port on localhost.")] = 8765,
) -> None:
    """Today's practice, a session you can finish: opens it in the browser (or --cli)."""
    import socket
    import webbrowser

    settings: Settings = ctx.obj
    # Before Store opens it: an empty default database shouldn't hide the one you meant.
    if not status and (other := better_database(settings.db_path)) is not None:
        path, count = other
        console.print(
            f"[yellow]{settings.db_path} has no puzzles, but {path} has {count}.[/yellow] Use it "
            f'with --db, or put db = "{path.resolve()}" in {config_file()}.',
            highlight=False,
        )
    with Store(settings.db_path) as store:
        if status:
            console.print(_today_status(store), highlight=False)
            return
        if in_terminal:
            _today_in_the_terminal(store, settings)
            return
    with socket.socket() as probe:
        probe.settimeout(0.2)
        running = probe.connect_ex(("127.0.0.1", port)) == 0
    if running:
        url = f"http://127.0.0.1:{port}"
        console.print(f"Already running: opening [bold]{url}[/bold].")
        webbrowser.open(url)
        return
    serve(ctx, port=port, host="127.0.0.1", open_browser=True)


@app.command()
def serve(
    ctx: typer.Context,
    port: Annotated[int, typer.Option(help="Port on localhost.")] = 8765,
    host: Annotated[str, typer.Option(help="Interface to bind (keep it local).")] = "127.0.0.1",
    open_browser: Annotated[bool, typer.Option("--open", help="Open it in the browser.")] = False,
) -> None:
    """The coach in your browser: your mistakes on boards, practice, your openings, progress."""
    import webbrowser

    import uvicorn

    from chess_coach.engine.stockfish import Engine
    from chess_coach.web.app import create_app

    settings: Settings = ctx.obj
    url = f"http://{host}:{port}"
    if host not in ("127.0.0.1", "localhost", "::1"):
        console.print(
            f"[yellow]Warning: {host} exposes your games and lets anyone on the network record "
            "puzzle attempts; there is no login. Prefer the default 127.0.0.1.[/yellow]"
        )
    console.print(f"Chess coach on [bold]{url}[/bold] ({settings.db_path}). Ctrl+C to stop.")
    if open_browser:
        webbrowser.open(url)

    def engine() -> Engine:
        return Engine(
            settings.stockfish_path,
            threads=settings.engine_threads,
            hash_mb=settings.engine_hash_mb,
        )

    uvicorn.run(create_app(settings.db_path, engine), host=host, port=port, log_level="warning")


@eval_app.command("narrate")
def eval_narrate(
    ctx: typer.Context,
    provider: Annotated[
        str, typer.Option(help="Who writes the summaries: ollama (local) or openrouter.")
    ] = "ollama",
    model: Annotated[str | None, typer.Option(help="The model (ollama: qwen2.5:7b).")] = None,
    out: Annotated[Path, typer.Option(help="Folder for the sheet and the facts.")] = Path(
        "data/narrate-eval"
    ),
) -> None:
    """Have the model summarise several digests (all games, then the last year, 6, 3 and 1
    month) and write every sentence to a sheet to grade by hand, next to the facts it was given."""
    from datetime import UTC, datetime

    from chess_coach.coach.evaluate import build_sheet, write_facts, write_sheet
    from chess_coach.coach.narrate import complete, model_for

    settings: Settings = ctx.obj
    with Store(settings.db_path) as store:
        games = list(store.iter_games())
        moves = list(store.iter_moves(user_only=True))
    now = datetime.now(UTC)
    try:
        m = model_for(provider, model)
        with console.status(f"Asking {m.model} for five summaries..."):
            rows, given = build_sheet(games, moves, lambda prompt: complete(m, prompt), now=now)
    except (ValueError, RuntimeError, httpx.HTTPError) as exc:
        console.print(f"[red]No summaries: {exc}[/red]")
        raise typer.Exit(code=1) from exc
    if not rows:
        console.print("No digest has anything to explain yet. Run `chess-coach analyze` first.")
        return
    out.mkdir(parents=True, exist_ok=True)
    name = f"{now:%Y-%m-%d-%H%M}-{m.model.replace(':', '-').replace('/', '-')}"
    sheet, facts_md = out / f"{name}.csv", out / f"{name}-facts.md"  # "qwen2.5" has a dot
    write_sheet(sheet, rows)
    write_facts(facts_md, given)
    kept = sum(r.check == "kept" for r in rows)
    console.print(
        f"{len(rows)} sentences from {len(given)} summaries, {kept} kept by the automatic check.\n"
        f"Sheet: [dim]{sheet}[/dim]\nFacts: [dim]{facts_md}[/dim]\n"
        "In the grade column of each kept sentence put y (the facts support it), n (they don't) "
        f"or ? (can't tell), then run `chess-coach eval score {sheet}`."
    )


@eval_app.command("score")
def eval_score(
    sheet: Annotated[Path, typer.Argument(help="A graded sheet from `eval narrate`.")],
) -> None:
    """How accurate the summaries are: of the sentences the automatic check kept, the share the
    facts support."""
    from chess_coach.coach.evaluate import read_sheet, score

    overall, samples = score(read_sheet(sheet))
    table = Table(title=f"Summary accuracy ({sheet.name})")
    for column in ("digest", "right", "wrong", "unsure", "ungraded", "dropped", "accuracy"):
        table.add_column(column, justify="left" if column == "digest" else "right")
    for s in [*samples, overall]:
        accuracy = "-" if s.accuracy is None else f"{s.accuracy:.0%}"
        table.add_row(
            s.sample,
            *(str(n) for n in (s.right, s.wrong, s.unsure, s.ungraded, s.dropped)),
            accuracy,
        )
    console.print(table)
    if overall.ungraded:
        console.print(f"[yellow]{overall.ungraded} kept sentences have no grade yet.[/yellow]")


if __name__ == "__main__":
    app()
