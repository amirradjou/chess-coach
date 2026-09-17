# Build plan and how to continue

Working notes for whoever picks this up next (human or Claude session). Keep it current.

## Where things stand (2026-09-17)

Branch `v0-import-and-analysis` (PR open against `main`; merge with a merge commit, not squash,
to keep the step-by-step history). Everything below is committed and green (`uv run pytest`,
ruff, pre-commit, CI).

Done in v0:
- `import lichess|chesscom|pgn` → SQLite (`data/chess-coach.db`), ids stable across re-imports,
  user's colour recorded per game, speed classified with Lichess's rule.
- `analyze` → one Stockfish call per position, per-move facts (eval before/after from the
  mover's side, best move, cp loss, win-probability loss, Lichess-style judgement, phase,
  clock, PV). Resumable per game and per depth.
- `stats` → per-game ACPL / inaccuracy / mistake / blunder table.

Local-only state (gitignored, may be missing on another machine):
- `.stockfish/stockfish` — Stockfish 19, from `scripts/get-stockfish.sh`.
- `data/demo.db` — 84 rated blitz games of the public Lichess user `pipinpo` (~1530 blitz),
  fully analyzed at depth 12. Rebuild with:
  `uv run chess-coach --db data/demo.db import lichess pipinpo --max 80 --rated && uv run chess-coach --db data/demo.db analyze --depth 12 --threads 4`
- `data/chess-coach.db` — the three fixture games, analyzed at depth 12.

## Remaining v0 work (finish before merging the PR)

1. **Motif tagging** (`src/chess_coach/engine/motifs.py`, fill `MoveFact.motifs`), all computed
   in code from the board + the engine facts, never by the model:
   - `hanging_piece` — the move left a piece the best reply captures for free (attacked,
     undefended or higher value than its cheapest attacker).
   - `missed_mate` / `allowed_mate` — `mate_before > 0` but move isn't mating; `mate_after < 0`.
   - `back_rank` — the mate in the line lands on the mover's back rank with pawns blocking.
   - `fork` — best move (missed) or best reply attacks two undefended/higher-value pieces.
   - `missed_tactic` — opponent's previous move was a blunder (their `wp_loss` ≥ 20) and the
     user didn't cash in (`cp_loss` ≥ 100 on this move).
   - `time_trouble` — `clock_after` < 10% of the initial time or < 30s.
   - `endgame_conversion` — endgame, `eval_before` ≥ +200, `eval_after` ≤ +50.
   - `queen_trade_behind` — captured/allowed a queen trade while behind in development
     (fewer developed minor pieces than the opponent) — this one is game-level, compute from
     the board at the ply.
   Test each with a hand-written FEN in `tests/engine/test_motifs.py`; the fixture game
   `chesscom:123456789` ends with the user resigning in a mate-in-one position (23...Rd1#), which
   is a ready-made `missed_mate` case.
2. **Opening classification** for plain PGN / Chess.com games: vendor the CC0
   lichess-org/chess-openings TSVs (a.tsv…e.tsv, ~300 KB) into `src/chess_coach/data/openings.tsv`
   and classify by longest matching move prefix; keep Lichess's own header when present.
   Add `opening_family` (name before the colon) for aggregation.
3. Update `README.md` status + CLAUDE.md gotchas; mark PR ready.

## v1 — pattern mining + digest (next branch: `v1-patterns-and-digest`)

- `patterns/features.py`: per-move feature table for the user's moves — phase, speed, colour,
  opening family, move bucket (1–10/11–20/21–30/31+), clock bucket, motif tags, result;
  game-level derived features (castled by move 10?, queen trade ply + development deficit).
- `patterns/mining.py`: for each feature value and pair, compare the user's error rate
  (mistake+blunder per move) and mean cp loss with the user's own baseline; keep slices with
  n ≥ 30 moves (or ≥ 8 games for result slices), lift ≥ 1.5, Wilson lower bound above baseline.
  Output `Pattern(kind, template_text, stats, evidence=[(game_id, ply, san, cp_loss)…])`.
  Deterministic text first — the digest must work with `LLM_PROVIDER=none`.
- `coach/llm.py`: providers `claude` (anthropic SDK; load the `claude-api` skill before writing
  it), `ollama` (local HTTP, `qwen2.5:3b` is pulled), `none`.
- `coach/digest.py`: facts → prompt → narrative; verify every cited game id / move number exists
  in the evidence; drop or flag anything unverifiable. `digest` CLI command writes markdown.

## v2 — puzzles + spaced repetition (`v2-puzzles-and-drills`)

- Candidates: user moves with `wp_loss` ≥ 20. Re-verify with multipv=3 at depth ≥ 18: ship only
  if best beats second-best by ≥ 150 cp or is the only mate. Store `puzzles` + `reviews`
  (SM-2). `drill` CLI: ASCII board, answer in SAN/UCI, schedule update. Tags from motifs.

## v3 — weekly digest, opening study line, progress tracking

## Conventions that matter here

- Feature branch per milestone, many small conventional commits, PR for the owner to merge.
- No AI attribution in commits (global rule; the guard hook refuses it).
- Engine tests are marked `engine` and skip without Stockfish; `-m network` runs live API tests.
- HQ registry: `~/Projects/hq/projects.yaml` + `roadmaps/chess-coach.md` mirror this file.
