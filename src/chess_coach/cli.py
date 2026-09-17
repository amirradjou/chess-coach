"""Command-line entry point. Subcommands are added milestone by milestone."""

import typer

from chess_coach import __version__

app = typer.Typer(no_args_is_help=True)


@app.callback()
def main() -> None:
    """A coach that reads your games and trains you on your own mistakes."""


@app.command()
def version() -> None:
    """Print the installed version."""
    typer.echo(f"chess-coach {__version__}")


if __name__ == "__main__":
    app()
