# chess-coach

A coach that reads *your* games, finds the patterns behind your losses, explains them in
plain language, and trains you on exactly those spots.

## What it is

- Pulls your games from Lichess and Chess.com.
- Runs Stockfish over every game to find mistakes, blunders, and missed tactics.
- Instead of the usual per-game "you blundered on move 23", it looks *across* games and
  explains recurring patterns: "you lose when you castle late against the London",
  "you trade queens when behind in development", "you miss back-rank threats after
  move 30 in blitz".
- Generates puzzles from your own positions targeted at those weaknesses, with spaced
  repetition so the ones you keep failing come back.
- Weekly digest: what changed, what to work on next, one opening line to study.

## Why build it

Because generic chess trainers don't know you, and a coach that says "here's the
mistake you make every Tuesday" is worth more than a hundred random puzzles. Also
because it is a satisfying shape of project: a hard, exact engine does the truth-finding
and the language model does the explaining — and you can check whether the explanation
was right.

## How it works (planned)

```
Lichess / Chess.com APIs, PGN files ──► game store (PGN + metadata)          v0, built
        │
        ▼
Stockfish analysis (eval swings, best lines, motifs) ──► per-move facts     v0, built
        │
        ▼
pattern mining (openings, phases, time controls, motif tags, eval-drop clusters)   v1
        │
        ▼
language model: explain patterns in plain language, citing games/moves ──► digest  v1
        │
        ▼
puzzle generator (your positions, engine-verified solutions) + spaced repetition   v2
```

- **The engine is the source of truth.** The model never judges a move; it explains
  facts the engine established, and every claim cites game IDs and move numbers.
- **Patterns first, explanations second.** Clustering happens in code; the model
  narrates clusters, which keeps hallucinated "patterns" out.
- **Puzzles are verified**: a puzzle only ships if Stockfish confirms a unique, clearly
  better solution.

## What gets measured

- Puzzle solve rate over time per weakness tag.
- Rating trend vs. digest adoption (did the advice change anything?).
- Explanation quality: a small hand-graded set of digests, scored for accuracy against
  engine facts.

## Stack

Python 3.12 (uv), a Typer + Rich command line, python-chess, Stockfish 19 over UCI, SQLite for
games and per-move facts, and the CC0 [lichess-org/chess-openings](https://github.com/lichess-org/chess-openings)
table for opening names. Planned: a small web UI for digests and puzzle drills.

## Milestones

- **v0 (done)** — import games, run engine analysis, store per-move facts with motif tags.
- **v1 (done, without a model)** — pattern mining + a plain-language digest citing games. The
  optional model-written narrative (checked against the evidence) is next.
- **v2** — puzzle generation and spaced repetition drills.
- **v3** — weekly digests, opening study suggestions, progress tracking.

## Status

**v1 done, without a model.** `digest` finds the situations where you go wrong more than usual
and links every claim to the moves behind it; the optional model-written narrative is next.
[docs/PLAN.md](docs/PLAN.md) has the build plan and how to pick the work up.

- `import lichess|chesscom|pgn` stores games in SQLite with your colour, the speed (Lichess's
  rule) and the opening: Lichess's own header when it has one, otherwise the longest matching
  line in the lichess-org opening table. Re-imports never duplicate a game.
- `analyze` asks Stockfish about every position once and stores, per move: the evaluation
  before and after from the mover's side, the best move and line, centipawn and win-probability
  loss, a Lichess-style judgement (inaccuracy, mistake, blunder), the game phase, the clock, and
  motif tags. It resumes where it stopped and re-runs only at a greater depth (or `--force`).
- `stats` prints a per-game accuracy table and how often each motif shows up in your moves.
- `digest` slices your moves by phase, colour, speed, opening, move number and clock (and
  pairs of them), and your games by colour, speed, opening, castling and queen trades. It keeps a
  slice only when it has enough moves (30) or games (8), your mistake rate (or loss rate) there
  is well above your own (1.5×, or 1.25× for losses), and the low end of its 95% interval is
  still above it. Each pattern cites its worst moves with links; then a motif table with one
  thing to practise for each. All counted, no model, so it can't invent a pattern.

Motif tags are computed in code from the board and the engine's facts, never by a model:

| Tag | When |
|---|---|
| `hanging_piece`, `hanging_pawn` | the best reply captures something of yours that was undefended, or worth more than the piece taking it |
| `allowed_fork`, `missed_fork` | the best reply attacks two of your pieces (the king, pieces worth more than the attacker, or undefended ones), or your best move would have done that to the opponent |
| `allowed_mate`, `missed_mate` | your move let a forced mate in, or skipped one (up to mate in 6) |
| `back_rank` | that mate is a rook or queen mate on the king's back rank |
| `missed_tactic` | the opponent's previous move lost 20 points of win probability or more, and you didn't punish it |
| `pawn_grab` | an error that captured a pawn |
| `time_trouble` | under 20 s, or under 10% of the starting time, after the move |
| `endgame_conversion` | a winning endgame (+2 or better) let slip below +1 |
| `queen_trade_behind` | before the endgame you traded queens, or allowed the trade, while two or more steps behind in development (minor pieces off their home squares, king moved) |

All tags except `queen_trade_behind` are only set on inaccuracies or worse.

On 84 rated blitz games of a ~1500 Lichess player, analyzed at depth 12, `stats` ends with:

```
      Motifs on your 2626 moves
┏━━━━━━━━━━━━━━━━━━━━┳━━━━━━━┳━━━━━━━┓
┃ motif              ┃ moves ┃ games ┃
┡━━━━━━━━━━━━━━━━━━━━╇━━━━━━━╇━━━━━━━┩
│ missed_tactic      │    37 │    26 │
│ time_trouble       │    30 │    13 │
│ hanging_piece      │    29 │    23 │
│ pawn_grab          │    26 │    23 │
│ endgame_conversion │    17 │    12 │
│ hanging_pawn       │    12 │    12 │
│ allowed_mate       │    10 │     8 │
│ missed_fork        │     6 │     6 │
│ allowed_fork       │     4 │     4 │
│ missed_mate        │     2 │     2 │
│ back_rank          │     2 │     2 │
└────────────────────┴───────┴───────┘
```

## Getting started

```sh
uv sync                     # install
scripts/get-stockfish.sh    # fetch a Stockfish binary into ./.stockfish (no sudo needed)

uv run chess-coach import lichess <username> --max 100 --rated   # LICHESS_TOKEN raises the rate limit
uv run chess-coach import chesscom <username> --since 3m
uv run chess-coach import pgn games.pgn --user "<your name in the PGN>"
uv run chess-coach games                                         # what is stored
uv run chess-coach analyze --depth 12 --threads 4
uv run chess-coach stats
uv run chess-coach digest -o digest.md                           # patterns across your games
```

`--db <file>` picks another database (default `data/chess-coach.db`).

## Development

```sh
uv run pytest                                   # tests (engine tests skip without Stockfish)
uv run ruff check . && uv run ruff format .     # lint + format
```
