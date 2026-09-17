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
Lichess / Chess.com APIs ──► game store (PGN + metadata)
        │
        ▼
Stockfish analysis (eval swings, best lines, motifs) ──► per-move facts
        │
        ▼
pattern mining (openings, phases, time controls, motif tags, eval-drop clusters)
        │
        ▼
Claude: explain patterns in plain language, citing specific games/moves  ──► digest
        │
        ▼
puzzle generator (your positions, engine-verified solutions) + spaced repetition
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

## Planned stack

Go (or Python) service; Stockfish via UCI; SQLite for games, facts and puzzles; a small
web UI for digests and puzzle drills; Lichess/Chess.com API clients.

## Milestones

- **v0** — import games, run engine analysis, store per-move facts.
- **v1** — pattern mining + first plain-language digest citing games.
- **v2** — puzzle generation and spaced repetition drills.
- **v3** — weekly digests, opening study suggestions, progress tracking.

## Status

Idea stage — nothing runs yet.
