"""Command-line entry point. Subcommands are added milestone by milestone."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Annotated

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


if __name__ == "__main__":
    app()


@app.command()
def analyze(
    ctx: typer.Context,
    depth: Annotated[int | None, typer.Option(help="Search depth per position.")] = None,
    limit: Annotated[int | None, typer.Option(help="Stop after this many games.")] = None,
    threads: Annotated[int | None, typer.Option(help="Engine threads.")] = None,
) -> None:
    """Run Stockfish over every game not yet analyzed at this depth and store per-move facts."""
    from chess_coach.engine.analysis import analyze_game
    from chess_coach.engine.stockfish import Engine, EngineNotFoundError

    settings: Settings = ctx.obj
    depth = depth or settings.analysis_depth
    with Store(settings.db_path) as store:
        pending = list(store.iter_games(unanalyzed_below_depth=depth))
        if limit is not None:
            pending = pending[:limit]
        if not pending:
            console.print(f"Nothing to do: every game is analyzed at depth {depth} or deeper.")
            return
        try:
            engine = Engine(
                settings.stockfish_path,
                threads=threads or settings.engine_threads,
                hash_mb=settings.engine_hash_mb,
            )
        except EngineNotFoundError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(code=1) from exc
        console.print(f"Using {engine.name} at depth {depth} for {len(pending)} games")
        progress = Progress(
            TextColumn("{task.description}"),
            BarColumn(),
            TextColumn("{task.completed}/{task.total}"),
            TimeRemainingColumn(),
            console=console,
        )
        with engine, progress:
            games_task = progress.add_task("games", total=len(pending))
            plies_task = progress.add_task("plies", total=1)
            for game in pending:
                progress.update(plies_task, completed=0, total=1, description=game.id)

                def on_ply(done: int, total: int) -> None:
                    progress.update(plies_task, completed=done, total=total)

                facts = analyze_game(game, engine, depth=depth, on_ply=on_ply)
                store.replace_moves(game.id, facts, depth=depth)
                progress.advance(games_task)
        console.print(
            f"[green]Analyzed {len(pending)} games[/green] -> [dim]{settings.db_path}[/dim]"
        )


@app.command()
def stats(ctx: typer.Context) -> None:
    """Per-game accuracy summary for the coached user: cp loss and mistake counts by phase."""
    settings: Settings = ctx.obj
    with Store(settings.db_path) as store:
        games_by_id = {g.id: g for g in store.iter_games() if g.analyzed_depth}
        moves = list(store.iter_moves(user_only=True))
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
