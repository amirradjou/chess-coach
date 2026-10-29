# Build plan and how to continue

Working notes for whoever picks this up next (human or Claude session). Keep it current.

## Where things stand (2026-10-29)

v0 is done: branch `v0-import-and-analysis`, PR #1, merged with a merge commit (not squash) to
keep the step-by-step history. Everything below is green (`uv run pytest`, ruff, pre-commit, CI).

Done in v0:
- `import lichess|chesscom|pgn` → SQLite (`data/chess-coach.db`), ids stable across re-imports,
  user's colour recorded per game, speed classified with Lichess's rule, opening from Lichess's
  header or the longest prefix in the vendored lichess-org table (`openings.py`, `family_of`).
- `analyze` → one Stockfish call per position, per-move facts (eval before/after from the
  mover's side, best move, cp loss, win-probability loss, Lichess-style judgement, phase,
  clock, PV, motif tags from `engine/motifs.py`). Resumable per game and per depth.
- `stats` → per-game ACPL / inaccuracy / mistake / blunder table, then motif counts (moves and
  games) over the user's moves.

Local-only state (gitignored, may be missing on another machine):
- `.stockfish/stockfish` — Stockfish 19, from `scripts/get-stockfish.sh`.
- `data/demo.db` — 84 rated blitz games of the public Lichess user `pipinpo` (~1530 blitz),
  fully analyzed at depth 12. Rebuild with:
  `uv run chess-coach --db data/demo.db import lichess pipinpo --max 80 --rated && uv run chess-coach --db data/demo.db analyze --depth 12 --threads 4`
- `data/chess-coach.db` — the three fixture games, analyzed at depth 12.

Motif tags as built (thresholds at the top of `engine/motifs.py`; README has the plain-language
table): `hanging_piece`/`hanging_pawn`, `allowed_fork`/`missed_fork`, `allowed_mate`/`missed_mate`
(up to mate in 6), `back_rank`, `missed_tactic` (opponent's previous `wp_loss` ≥ 20), `pawn_grab`,
`time_trouble` (< 20 s or < 10% of the initial time), `endgame_conversion` (≥ +200 to < +100),
all only on inaccuracies or worse; `queen_trade_behind` (development deficit ≥ 2) on any move.
On `data/demo.db` 243 of 5,258 moves carry a tag.

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
  it), `ollama` (local HTTP, `qwen2.5:3b` is pulled), `none`. The owner's rule for anything
  live: never the owner's paid key — a visitor's own key (used for the session, never stored or
  logged) or a free-tier model, and `none` must still give a useful digest.
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
