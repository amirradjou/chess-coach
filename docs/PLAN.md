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

## v6 — remember opening training (2026-10-31, branch `feat/remember-opening-training`)

- Tables `coach_cards` (id `"<colour>|<family>|<uci path>"`, kind `punish`/`line`, SM-2 fields)
  and `coach_attempts`. `coach/training.py`: `answer` (new card on the first answer, then
  `puzzles/srs.schedule`, now generic over anything with an SM-2 schedule), `deck` (due, then
  new, then later), `summary` (found X of Y punishments, due, line positions practised, due
  paths). A card is `punish` when the move before it is a punishable opponent edge in the tree.
- `POST /api/coach/answer`: the page judges the move (usual sound move, or `/api/coach/check`)
  and sends only the first answer at a position; the server counts one answer per card per UTC
  day, so going back over a line doesn't push a card away. `GET /api/coach/cards`; the punish
  deck and `/api/coach/openings` carry the practice state and the due count.
- Train mode steers their replies towards due positions (`towardDue` in `app.js`); the punish
  deck is refetched after each full pass, so due cards lead again.

## v7 — rating trend against practice (2026-10-31, branch `feat/rating-trend`, on top of #10)

- The plan said Lichess's `/api/user/{name}/rating-history`; on 2026-10-31 it returned `[]` for
  the owner's account (and for some others; it works for e.g. DrNykterstein), so ratings come
  from the stored games instead: `Game.user_elo` is the rating before each game, offline and the
  same for Chess.com and PGN imports.
- `coach/progress.py`: `trend_speeds` (bullet left out, by the owner's rule), `weekly` (last 12
  ISO weeks: games and rating at your last game, change against your last game before the week,
  puzzle attempts and coach answers), `practice_effect` (mean change in weeks with practice vs
  without, only once each side has 4 weeks with games). `Store.coach_attempts`.
- `progress` (CLI) prints the weekly table; `/api/progress?speed=` returns `trend`; the Progress
  tab shows it with a speed selector.
- On the owner's games: blitz moved until 2026-W38, practice starts in W44 and there are no blitz
  or rapid games since, so the comparison needs blitz or rapid games after practice.

## Keeping current: `sync` (2026-10-31, branch `feat/sync`, on top of #14)

- `chess-coach sync`: for each (source, user) in `Store.accounts()` from Lichess or Chess.com,
  fetch everything since that account's newest stored game (`max_games=None`), store it, analyse
  every unanalysed game newest first (`_analyze_games`, shared with `analyze`), then
  `puzzles.make` (`--puzzles`, default 200 candidates) and `make_opening_fixes`; prints the new
  games by speed (results only outside bullet) and what's due (puzzles and opening cards).
- Fetches are all or nothing per account: both sites send newest first, so storing part of a
  failed stream would leave a gap that the next sync (which starts from the newest game) skips.
  A cap on the fetch would do the same, hence none.
- Found while testing it on the owner's games: **every Lichess game imported since Lichess
  changed its Site header to `lichess.org/game/<id>`** (between the 2026-09-17 and 2026-10-29
  imports) **had a PGN hash for an id and a dead link** (all 2,099 of the owner's). The importer
  takes both forms now, and `Store._fix_lichess_ids` renames hashed games on open (games, moves,
  puzzles, reviews, attempts, puzzle_checks; one transaction, foreign keys checked at commit).
  Checked on a copy of the real database: about a second, every count unchanged, no foreign key
  problems, and a second sync then found 0 new games (no duplicates). Old links
  `lichess.org/<id>` redirect to `/game/<id>` and keep `#ply`.
- Chess.com links now open at the move too (`Game.move_url`, see item 5 below).
- On a copy of the owner's database (2026-10-31): 8 new bullet games since 10-29, analysed,
  4 moves checked, 2 new puzzles.

## What was their threat? Ignored threats vs unsafe moves (2026-10-31, branch `feat/threat-check`)

- `engine/threats.py`: before each counted (`features.counted`), non-rushed (`features.rushed`)
  mistake or blunder, **the user's and the opponents'** (the opponents are the peer baseline),
  the mover passes and Stockfish (depth 12) gives the opponent's best line on that board
  (`motifs.pass_turn`: rebuilt from the FEN, so no null move is ever sent to the engine; the
  `chess.engine` logger is silenced in the commands anyway), and a second search gives their best
  answer to the move played. A line **wins** when it mates or wins 2+ points of material, counted
  along the engine's line for at least 4 plies and on while captures and checks continue or a
  capture comes next (so pending recaptures count; at most 12 plies). It is a **threat** when
  their line on the null-move board wins and the free move is worth 200 cp to them (the mover's
  evaluation before plus theirs after passing); it is **new** when they were not already 200 cp
  better off before their last move (the stored evaluation of the previous ply's position, which
  has them to move like the null-move board) and it wasn't already there behind a check they had
  to answer (the same piece, move and target before their move). In check, or when their last
  move captured and the mover can take back on that square, no label. `ignored_threat`: a new
  threat the move didn't meet: a mate threat after which they still mate, or their answer is the
  threat itself and still wins material, or another capture on its square that still wins 2+,
  counted from before the move (what the move took is paid back first). `unsafe_move`: no threat
  at all, and their answer mates, or is 200 cp better for them than passing would have been and
  wins 2+ points, counted from before the move. Everything else: no label.
- Table `threats` keyed by (game id, ply): label, threat UCI and SAN (for an unsafe move, the
  winning reply it allowed), gain (the material its line wins, in cp; empty for a mate), mate,
  depth; `Store.threats()` joins the opponent's previous move at read time. Nothing is written
  into `moves.motifs`; `replace_moves` drops a game's checks with its old analysis, and
  `_fix_lichess_ids` renames them.
- `coach/threats.py`: `check` (newest games first, a FEN cache per run, stored game by game, so
  it resumes) and `report`: each label's share of your checked mistakes and blunders and of your
  opponents', Wilson 95% intervals (`mining.wilson`, clamped to 0-100%) and Newcombe's interval
  for the difference (`mining.difference`). "Same as your opponents" when that interval includes
  zero; a step is only called yours to work on when its low end is above zero, and with no gap
  the summary says only that (never "not a weakness").
- Chess terms from code (`principles.their_threat`, `ignored_threat`, `unsafe_move`): "Their a5
  attacked your pawn on b6, and your Ne7 left it there.", "Nothing was threatened, but your Nc4
  allows Bxc4, taking your knight on c4.", mates, checks and pieces hit by a quiet threat.
- Surfaces: `chess-coach threats [--depth 12 --threads N --limit N]` (summary table and examples;
  needs no engine when nothing is left to check, and says what is left when it stopped early),
  `sync` checks every mistake not checked yet with the same progress bar (the first time, the
  whole backlog), the digest's "Their threats and your blunder check" section, and the "What you
  get wrong" tab (a small section; every example board whose move is labelled gets the threat as
  an orange arrow and the sentence first). Practice: a puzzle whose source move is an ignored
  threat first asks "What do they threaten?" (`POST /api/drill/threat`, not recorded; right if it
  is the threat, any move to the same square, or another mate when it mates; skippable), then
  the normal puzzle with the threat drawn in orange.
- Decisions, from the spec and its reviews: the loose rule (gain >= 150 cp on the null-move
  board) labelled most errors as threats. Prototyping on 300 + 300 sampled errors: a gain-only
  rule still found a "threat" in 69%, often a quiet free tempo (castling, a retreat) that only
  looked strong because the opponent got two moves in a row. The spec's (c) asked for "one extra
  null search on the previous ply's position, from the same side's perspective": that position
  already has them to move, so its stored depth-12 evaluation is the search. Unsafe moves are
  measured against passing, not by centipawn loss, so a quiet move that misses your own win is
  not "unsafe" (the sample game's 15.Nd2, which missed axb6, gets no label). One engine thread:
  two runs give identical rows.
- The review (2026-11-01) found the first version counting the mover's own edge as their threat:
  the free move also lets them dodge a shot the mover had pending, so missed shots and escaping
  pieces became "Their g5 attacked your pawn on h6" (a move that missed mate in one and hung a
  rook), and moves that only gave back the mover's lasting edge became "unsafe" with a quiet king
  move as the reply. Capture or check plus a 200 cp evaluation gain is therefore no longer
  enough: every label needs a line that wins material (or mates), and the evaluations only
  confirm it. Also from the review: a threat behind a check isn't theirs (the review counted 26
  labels like "Their Kb6 threatened Bxe6" for an attack your own check left), a threat whose
  answer no longer wins (a trade now, or the move took as much first) is met, and the answer is
  searched rather than taken from the next ply, whose stored line is only six plies. Considered
  and left out: counting one-pawn captures and sacrifices like Bxf7+ as threats by their
  evaluation gain (it labels more, but about a third of those read wrong: "attacked your knight
  on c3" for an even trade).
- On a copy of the owner's database (2,099 games, depth 12, 1 thread, 60 s for 2,526 moves; a
  second run gives the same rows): 1,269 of the user's counted mistakes and blunders and 1,257
  of the opponents'. Ignored their threat: 158 (12.5%, 10.7-14.4%) vs 115 (9.1%, 7.7-10.9%),
  +3.3 points (+0.9 to +5.7): more often than your opponents, so it is shown as a step to work
  on. Unsafe move: 136 (10.7%, 9.1-12.5%) vs 135 (10.7%, 9.1-12.6%), +0.0 points (-2.4 to
  +2.4): same. Unsafe moves are 46% of the user's labelled errors (136 of 294). Unlabelled: in
  check 38 / 34, recapture pending 111 / 113, a threat that was old or met 254 / 259, nothing
  threatened and no winning reply allowed 572 / 601. 29 puzzles get the threat question; 19 of
  the 36 example boards in "What you get wrong" carry an orange threat. The ignored-threat gap is
  borderline: depth 14 gives +3.0 (+0.5 to +5.4), and two 2-thread runs at depth 12 gave +1.5
  (-0.9 to +3.9, "same") and +2.5 (+0.1 to +4.9), so engine noise alone can flip the verdict.

## Remaining (in order)

1. ~~Fresh puzzles from the full analysis~~: done 2026-10-31. `puzzles prune` removed 83
   (backup: `data/amirradjou-2026-10-31-before-prune.db`), then `puzzles make --limit 1300
   --threads 8 --openings` checked every remaining candidate: all 1,264 under the bullet rule are
   checked, 542 puzzles in all (48 opening-fix drills). New games only need
   `puzzles make` after `analyze`.
2. ~~Remember opening training~~: done in v6.
3. ~~Rating trend against practice~~: done in v7 (from the games, not the API: see above).
4. ~~New screenshots~~: five new ones in #13 (`docs/screenshots`: mistakes, practice with the
   lines, your openings, Learn openings, the weekly trend), taken from #10-#12 merged locally.
5. Smaller (branch `chore/polish`, 2026-10-31):
   - ~~Reasons for the opponent's mistakes in the opening tree~~: done. `principles.allows`
     says what the punishment does to them ("It lets e5 hit their knight on f6": check, mate,
     pieces hit) and `explain(..., theirs=True)` gives the faults in their voice, no ideas.
   - ~~Chess.com ply links~~: done 2026-10-31 (`Game.move_url`, one place for all four links).
     Checked on a real live game (183193101523): `?move=5` selects 3.Na3 and `?move=14` shows
     7...Nd5, the position after that ply, like Lichess's `#ply`. Daily games (`/game/daily/`)
     still open at the start: no public daily game was found to check them on.
   - ~~Phone width and favicon~~: done. The Learn openings select grew to 718 px on a 375 px
     screen (now capped); every tab checked at 375 px; inline SVG favicon.
   - ~~httpx deprecation in the tests~~: done, `httpx2` (pydantic/httpx2) as a dev dependency.
   - ~~Tests comparing UTC due dates with the local date~~: fixed (they failed 20:00-24:00 EDT).
   - ~~A hand-graded accuracy set for the model-written digest~~: the harness is done
     (`coach/evaluate.py`, `eval narrate` / `eval score`, branch `feat/digest-accuracy`); the
     grading itself is the owner's: a sheet from the local qwen2.5:7b is in `data/narrate-eval/`
     (local only), and the accuracy goes into the README once graded.

## Conventions that matter here

- Feature branch per milestone, many small conventional commits, merged with a merge commit.
- Engine tests are marked `engine` and skip without Stockfish; `-m network` runs live API tests.
- Your own games live in `data/` (gitignored) and never leave the machine.
