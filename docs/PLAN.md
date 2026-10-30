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
- `data/demo.db` — about 80 rated blitz games of a public ~1500 Lichess player, analyzed at
  depth 12. Rebuild with any username:
  `uv run chess-coach --db data/demo.db import lichess <username> --max 80 --rated && uv run chess-coach --db data/demo.db analyze --depth 12 --threads 4`
- `data/chess-coach.db` — the three fixture games, analyzed at depth 12.

Motif tags as built (thresholds at the top of `engine/motifs.py`; README has the plain-language
table): `hanging_piece`/`hanging_pawn`, `allowed_fork`/`missed_fork`, `allowed_mate`/`missed_mate`
(up to mate in 6), `back_rank`, `missed_tactic` (opponent's previous `wp_loss` ≥ 20), `pawn_grab`,
`time_trouble` (< 20 s or < 10% of the initial time), `endgame_conversion` (≥ +200 to < +100),
all only on inaccuracies or worse; `queen_trade_behind` (development deficit ≥ 2) on any move.
On `data/demo.db` 243 of 5,258 moves carry a tag.

## v1 — pattern mining + digest (branch `v1-patterns-and-digest`)

**Done (2026-10-29), no model:** `patterns/features.py` (move rows: phase, colour, speed,
opening family, move bucket, clock bucket; game rows: colour, speed, opening, castling, queen
trade behind), `patterns/mining.py` (singles and pairs; n ≥ 30 moves / 8 games; lift ≥ 1.5 for
moves, 1.25 for losses because a loss rate starts near 50%; Wilson lower bound above baseline;
pairs must be 1.2× each half; values on ≥ 90% of rows never pair; ranked by excess errors;
worst-move evidence with Lichess ply links), `coach/digest.py` (template text, motif table
with a practice line each) and `chess-coach digest`. Motif tags are reported as counts, not
mined as slices: they are only set on errors, so their error rate is trivially high.

**Left in v1:** the optional narrative below (`coach/llm.py`), verified against the evidence.


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

## v2 — puzzles + spaced repetition (done 2026-10-29, branch `v2-puzzles-and-progress`)

- Candidates: user moves with `wp_loss` ≥ 20, newest games first, each checked once
  (`puzzle_checks`). Re-verified with multipv=3 at depth 18: kept only if the best move is the only
  mate or beats the second-best by ≥ 150 cp, and differs from the move played. Tables `puzzles`,
  `reviews` (SM-2: quality 4 for a solve, 1 for a miss) and `attempts`. `drill`: Unicode board from
  the side to move, SAN or UCI, `?` gives up, three tries for an illegal entry. Tags = motifs +
  phase (+ `mate`).

## v3 — weekly digest, opening study, progress (done 2026-10-29)

- `digest --since` (weekly digest vs the games before), `progress` (month/week: games, score,
  error rate, ACPL; puzzle solve rate overall and per tag), `openings` (worst-scoring families by
  colour, book line from the vendored table, opening errors repeated in 2+ games).
- v1's optional narrative: `coach/narrate.py`, OpenAI-compatible chat (presets ollama, openrouter);
  sentences citing moves outside the evidence are dropped. No paid key by default.

## v4 — the web coach (done 2026-10-29, branch `v4-web-coach`)

- `coach/habits.py`: piece and move-kind features (`piece`, `kind` in the move table), missed
  captures/checks (share of errors whose best move was one, vs how often it is the best move at
  all; ≥ 1.3×), motifs, mined situations, each with a lesson chosen by the most specific feature.
- `coach/repertoire.py`: favourite families per colour, usual line (longest prefix most games
  share), book line, and fixes: same position + same move in 2+ games, average loss ≥ 50 cp, never
  a named book move (`is_book`); deduplicated by move. `puzzles/make.py: make_opening_fixes` drills
  them at depth 18, multipv 4, accepting moves within 30 cp (`puzzles.accept`).
- Short-on-time rule (`features.rushed`): under 15 s in bullet or under 10% of the clock; left out
  of move tables, progress and puzzle candidates unless the motif repeats (15 moves, 8 games;
  `mining.repeated_under_pressure`), which gets its own section. `time_trouble` is not listed among
  the main motifs. `puzzles prune` removes untried rushed puzzles. Superseded for bullet by the
  bullet rule below.
- `web/app.py` (FastAPI) + `web/static/` (plain JS, no libraries): boards are python-chess SVGs
  with arrows; click-to-move maps clicks through the SVG viewBox (margin 15, square 45).

## v5 — learn from mistakes and openings (done 2026-10-29, PRs #6 and #7)

- `puzzles/review.py`: a wrong answer shows their best reply (engine, depth 16) and what it costs
  (material over the line, score against the best's, the best move not named) and allows retries;
  only the first answer is recorded (`Answer.record`). After solving or revealing: the best line
  (stored PV), each try and the game move as lines; `POST /api/explore` answers any move.
- `web/app.py: Analyst`: one Stockfish per app, started on first use, answers cached by FEN;
  without Stockfish the lines stop after the chosen moves. Boards come from `GET /api/board`.
- `coach/principles.py`: chess reasons from the board alone (hanging piece, castling given up,
  king cover, early queen, development, centre pawns, doubled/isolated/passed pawns; and what the
  better move does: mate, check/capture, attacks a piece, castles, develops, centre, endgame king
  and passed pawn) and `OPENING_IDEAS`, a hand-written note per opening family. Owner's rule:
  explanations come in chess terms from code, not only from a model.
- `coach/tree.py` + the "Learn openings" tab: a tree per (opening family, colour) from the stored
  facts, first 16 plies; opponent moves losing 10+ points of win probability carry the punishment
  (the engine's answer, only when it leaves you +0.5) and how you answered. Modes: Explore, Train
  your moves (your usual sound move counts, else within 30 cp at depth 16; the engine takes over
  where your games stop), Punish their mistakes (cards for mistakes met 2+ times).

## Bullet is not taken seriously (2026-10-29, branch `feat/bullet-first-ten-moves`)

Owner's rule: in bullet (Lichess's category: 1+0, 2+1, ...) only the user's first 10 moves count,
and bullet results never do; after the opening the clock decides those games.
`features.counted(m, game)` and `features.result_of(game)` are the rule; every reader goes
through them: `move_rows` (patterns, habits, digest), `game_rows` (loss rates), `progress`
(error rate; the score is over games whose result counts, `None` when there are none),
`study.openings` (only families with `min_games` counted results are ranked by score),
`repertoire` (score, opening error rate, fixes), the opening tree's scores (with `scored`, the
number of games a score is over) and puzzle candidates (`make.keep`). `puzzles prune` drops
untried puzzles that no longer count. The short-on-time rule still applies to everything else.

On the owner's games: 1,723 of 2,091 are bullet; puzzle candidates fall from 4,186 to 1,264, and
`puzzles prune` would remove 83 of the 128 puzzles made so far (untried ones from bullet after
move 10).

## Remaining (in order)

1. **Fresh puzzles from the full analysis.** All 2,091 of the owner's games are analysed; a first
   `puzzles make --limit 150 --openings` run made 128 puzzles. After the bullet rule and
   `puzzles prune`, 1,218 candidates are left unchecked (588 bullet openings, 545 blitz, 85
   rapid), about half of which become puzzles. Run
   `uv run chess-coach --db data/amirradjou.db puzzles make --limit 150 --threads 8` in batches.
2. **Remember opening training.** The coach keeps no record: punish cards and trained lines
   aren't scheduled. Store each coach attempt (position, move, right or not) and schedule cards
   with SM-2 like puzzles (`puzzles/srs.py`); show "found 3 of 5 punishments" per opening and
   put due cards first.
3. **Rating trend against practice** (the brief's metric): Lichess rating history API
   (`/api/user/{name}/rating-history`) per speed, next to puzzle attempts and coach practice
   per week in the Progress tab, so you can see whether practice moves the rating.
4. **New screenshots** for the README (puzzle review lines, Learn openings) from the owner's
   games; the current two predate v5.
5. Smaller:
   - Reasons for the opponent's mistakes in the opening tree ("their 4...Nf6 lets e5 hit the
     knight"): `principles.explain` speaks to the mover ("your"); add a "their" voice.
   - Chess.com ply links (`mining._ply_url`, `repertoire._url` only anchor Lichess URLs).
   - Check the pages at phone width (the board column and line cards), add a favicon (404 now).
   - Tests: `fastapi.testclient` warns that httpx support is going away; follow Starlette's
     replacement when it lands.
   - A hand-graded accuracy set for the model-written digest (the brief's other metric).

## Conventions that matter here

- Feature branch per milestone, many small conventional commits, merged with a merge commit.
- Engine tests are marked `engine` and skip without Stockfish; `-m network` runs live API tests.
- Your own games live in `data/` (gitignored) and never leave the machine.
