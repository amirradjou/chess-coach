# chess-coach

A coach that reads your games, finds the patterns behind your losses, explains them in plain
language, and trains you on exactly those spots. The engine (Stockfish) establishes every fact;
the language model only narrates facts and must cite game ids and move numbers.

## Stack
- Language/runtime: Python 3.12 (`python-chess` for PGN/UCI/board logic, `typer` CLI, stdlib `sqlite3`)
- Package manager: uv
- Tests: `uv run pytest`
- Lint/format: `uv run ruff check . && uv run ruff format .`

## Commands
| Task | Command |
|------|---------|
| Install deps | `uv sync` |
| Get an engine | `scripts/get-stockfish.sh` (downloads the official Linux binary into `.stockfish/`) |
| Run | `uv run chess-coach --help` |
| Test | `uv run pytest` (engine tests skip without Stockfish; `-m network` opts into live API tests) |
| Lint + format | `uv run ruff check . && uv run ruff format .` |

## Layout
- `src/chess_coach/` — application code
  - `sources/` — game importers (Lichess, Chess.com, PGN files)
  - `engine/` — Stockfish wrapper, per-move analysis, motif tagging
  - `patterns/` — feature extraction and pattern mining across games
  - `coach/` — LLM providers, digest generation, citation checking
  - `puzzles/` — puzzle generation and spaced repetition
- `tests/` — tests mirror `src/` layout; `tests/data/` holds fixture PGNs
- `scripts/` — helper scripts (engine download)

## Conventions
- See global preferences in `~/.claude/CLAUDE.md` (conventional commits, feature branches, etc.).
- Run tests and lint before declaring work done.
- The engine is the source of truth: code never asks the model to judge a move, and every
  narrative claim must be traceable to a stored per-move fact.

## Gotchas / decisions
- Stockfish is resolved from `STOCKFISH_PATH`, then `./.stockfish/stockfish`, then `stockfish` on PATH.
- Evaluations are stored in centipawns from the *mover's* point of view; mates are clamped to ±10000.
- Move classification follows Lichess's win-probability thresholds (inaccuracy/mistake/blunder at
  10/20/30 percentage points of win-probability lost), so numbers line up with what users see on Lichess.
