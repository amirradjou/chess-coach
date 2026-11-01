"""Command-line entry point. Subcommands are added milestone by milestone."""

from __future__ import annotations

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
from chess_coach.config import Settings
from chess_coach.db import Store
from chess_coach.models import Game, Judgement
from chess_coach.util import parse_since

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
        Path | None, typer.Option("--db", help="SQLite file (default: data/chess-coach.db).")
    ] = None,
) -> None:
    """A coach that reads your games and trains you on your own mistakes."""
    settings = Settings()
    if db is not None:
        settings.db_path = db
    ctx.obj = settings


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
    analysed, puzzles from them, and their threats before the new mistakes."""
    import logging
    from datetime import UTC, datetime

    from chess_coach.coach.threats import check as check_threats
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
            with console.status("Looking for their threats before the new mistakes..."):
                logging.getLogger("chess.engine").setLevel(logging.ERROR)
                checked = check_threats(store, engine)
        total = len(store.puzzles())
        today = datetime.now(UTC).date().isoformat()
        due = len(store.due(today))
        cards = sum(c.due <= today for c in store.coach_cards())
    console.print(
        f"Analysed {len(pending)} games. Checked {made.checked + fixes.checked} moves, made "
        f"[green]{made.made + fixes.made}[/green] puzzles ({total} in all)."
    )
    console.print(
        f"Looked for their threats before {checked} new mistakes and blunders (yours and your "
        "opponents'): `chess-coach threats` shows how your mistakes happen."
    )
    console.print(
        f"Due today: {due} puzzles (`chess-coach drill`) and {cards} opening positions "
        "(Learn openings in `chess-coach serve`)."
    )


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
    from chess_coach.patterns.features import ERRORS

    settings: Settings = ctx.obj
    with Store(settings.db_path) as store:
        games = list(store.iter_games())
        moves = list(store.iter_moves(user_only=True))
        threats = store.threats()
        errors = list(store.iter_moves(judgements=ERRORS)) if threats else []
    built = build(
        games,
        moves,
        min_moves=min_moves,
        min_games=min_games,
        since=parse_since(since),
        threats=threats,
        errors=errors,
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
def threats(
    ctx: typer.Context,
    depth: Annotated[int, typer.Option(help="Search depth per position.")] = 12,
    threads: Annotated[int | None, typer.Option(help="Engine threads.")] = None,
    limit: Annotated[int | None, typer.Option(help="Stop after this many moves.")] = None,
) -> None:
    """What was their threat? Before every counted mistake and blunder, yours and your
    opponents', the mover passes and the engine finds what the other side threatened. Mistakes
    split into ignored threats and unsafe moves, compared with your opponents'. Resumable."""
    import logging

    from chess_coach.coach.threats import check, pending
    from chess_coach.patterns.features import ERRORS

    settings: Settings = ctx.obj
    with Store(settings.db_path) as store:
        todo = len(
            pending(store.iter_games(), store.iter_moves(judgements=ERRORS), store.threats())
        )
        todo = min(todo, limit) if limit is not None else todo
        if todo:
            logging.getLogger("chess.engine").setLevel(logging.ERROR)
            with _engine(settings, threads) as engine:
                console.print(f"Using {engine.name} at depth {depth} for {todo} moves")
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

                    check(store, engine, depth=depth, limit=limit, on_check=on_check)
        _threat_summary(store)


def _threat_summary(store: Store) -> None:
    from chess_coach.coach.threats import gap, percent, report
    from chess_coach.patterns.features import ERRORS

    r = report(list(store.iter_games()), store.iter_moves(judgements=ERRORS), store.threats())
    if not r.you:
        console.print("No mistakes checked yet. Run `chess-coach analyze` first.")
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
        console.print(
            "Neither is more common in your mistakes than in your opponents': this is how "
            "mistakes happen at your level, not a weakness of yours."
        )
    for s in r.steps:
        for e in s.examples[:2]:
            where = f" [dim]{e.evidence.url}[/dim]" if e.evidence.url else ""
            console.print(f"  {s.title}, {e.evidence.move}: {e.why}{where}")
    if r.unchecked:
        console.print(f"[yellow]{r.unchecked} mistakes and blunders not checked yet.[/yellow]")


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
    """Practise the puzzles that are due. Answer in SAN (Nf3) or UCI (g1f3); ? shows the answer."""
    from datetime import UTC, datetime

    import chess

    from chess_coach.puzzles.srs import schedule

    settings: Settings = ctx.obj
    now = datetime.now(UTC)
    with Store(settings.db_path) as store:
        due = store.due(now.date().isoformat(), tag, count)
        if not due:
            console.print(
                "Nothing due. Make more with `chess-coach puzzles make`, or come back tomorrow."
            )
            return
        solved = 0
        for i, p in enumerate(due, 1):
            board = chess.Board(p.fen)
            side = "White" if board.turn == chess.WHITE else "Black"
            game = store.get_game(p.game_id)
            console.print(
                f"\n[bold]Puzzle {i} of {len(due)}[/bold]  ({side} to move; {', '.join(p.tags)})"
            )
            console.print(_board_text(p.fen))
            console.print(f"In the game you played [red]{p.played_san}[/red]. Find the best move.")
            answer = ""
            correct = False
            for _ in range(3):
                answer = typer.prompt("Your move").strip()
                if answer == "?":
                    break
                try:
                    move = (
                        chess.Move.from_uci(answer)
                        if len(answer) in (4, 5) and answer[1].isdigit()
                        else board.parse_san(answer)
                    )
                except ValueError:
                    console.print(
                        "That isn't a legal move here; try again (or ? to see the answer)."
                    )
                    continue
                if move not in board.legal_moves:
                    console.print(
                        "That isn't a legal move here; try again (or ? to see the answer)."
                    )
                    continue
                correct = p.is_right(move.uci())
                break
            line = _san_line(board, p.solution, p.line)
            if correct:
                solved += 1
                console.print(f"[green]Correct.[/green] {line}")
            else:
                console.print(f"[yellow]The move was {p.solution_san}.[/yellow] {line}")
            review = store.review(p.id)
            if review is not None:
                after = schedule(review, correct, now.date())
                store.save_attempt(after, answer, correct, now)
                console.print(f"[dim]Next time: {after.due}.[/dim]")
            if game is not None and game.url:
                console.print(f"[dim]The game: {game.move_url(p.ply)}[/dim]")
        console.print(f"\nSolved {solved} of {len(due)}.")


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
    from datetime import UTC, datetime

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
    weeks = weekly(games, trend, attempts, answers, today=datetime.now(UTC).date())
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
    """The openings you score worst in, the book line, and the slips you repeat in them."""
    from chess_coach.coach.study import openings as opening_stats

    settings: Settings = ctx.obj
    with Store(settings.db_path) as store:
        stats = opening_stats(
            list(store.iter_games()), list(store.iter_moves(user_only=True)), min_games
        )
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
