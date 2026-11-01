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

## Repeated mistakes, the opening after move 10, puzzle dedupe (2026-10-31, branch `feat/repeat-mistakes`)

Built:
- **Puzzle dedupe.** `make_opening_fixes` read the checked keys once and never added the one it
  had just checked, so a fix two families share (Modern and Pirc, 3...Bg7 after 1.e4 d6 2.Bc4 g6
  3.Qf3) was verified and stored twice: fixed. `make` and `make_opening_fixes` now keep an
  in-memory index of the stored puzzles by (fen, solution) (`puzzles.make.Stored`): a copy isn't
  stored, its tags go to the puzzle already there, and a fix already checked for another opening
  tags that drill with this opening too. `puzzles dedupe` (its own command; `prune` is about the
  counting rules) merges older copies: keeps the copy with attempts, else the oldest, unions the
  tags, deletes the rest; when 2+ copies have attempts they all stay and it says so.
  "opening-fix" is a kind of drill (prompt, accepted moves), not a theme, so it is never carried
  over to an ordinary puzzle; its opening's tag is.
- **Repeats** (`coach/repeats.py`): the user's counted errors (inaccuracy or worse; bullet only to
  move 10; nothing played short on time) grouped by (fen_before, move), kept when played in 3+
  games. Each has the better move, their best answer (the next ply's stored best move, from
  `Store.replies_to_errors`) and how often they found it, reasons from `principles.allows`
  (`yours=True`: "hit your knight") and `explain`, links (`Game.move_url`), and whether a puzzle
  drills the position (a puzzle there whose answer isn't the move). Shown by `chess-coach
  repeats`, a digest section (with `--since`: the ones played again since, still counted over all
  games), `/api/repeats` and a section on "What you get wrong", and `sync` prints "You played
  3...Bg7 again (12th time); it has a puzzle." (or the better move) for each repeat a new game
  is part of.
- **The opening after move 10** (`repertoire.after_ten`), per family and colour, by the engine
  only, so bullet counts: the median eval after your 10th move (clamped at +-10 like Lichess's
  ACPL; median because blowouts skew a mean), the share of games at -1 or worse and the mistake
  rate (mistakes and blunders, counted moves 1-10) with Wilson intervals (`mining.wilson_upper`
  is new), and "since book": eval at move 10 minus the eval at the last position up to move 10
  that the opening table names (transpositions back into a named line count; after move 4 when
  none matched). Ranked by the low end of the mistake-rate interval, then since-book; never by a
  mean. Shown in `openings` (a table per colour), the Your openings tab (a table, and a line on
  each card) and Learn openings (each node and move: "engine eval after your 10th move: -0.9
  (median of 79 games; not results)") through a {game id: eval after move 10} map read with its
  own query, so `tree.build` keeps its 16-ply filter. `is_book` is now cached.

Decisions:
- Named book moves are left out of repeats, as `repertoire.fixes` does. Without that the top
  "repeat" is 2.d4 in the Blackmar-Diemer (123 games): a choice of gambit, which the owner put
  out of scope, not a slip.
- The sync alert fires for every repeat (3+ games) a new game is part of, including the game that
  makes it the third.
- The book position is the *last* one named (the spec's wording), not where the game first left
  the table: in the Modern and Pirc, games often leave the table on White's second move and come
  back to a named line two moves later.

On the owner's games (a copy, 2,099 Lichess games at depth 12):
- `puzzles dedupe`: 13 positions were stored more than once; removed 17 copies, 544 -> 527
  puzzles, the 5 attempts untouched (no group had a tried copy). The Modern/Pirc 3...Nf6 drill now
  has both tags; 6...Qxd1+ (an own-mistake puzzle) took the Pirc fix's family tag. Make-time
  check on another copy with those 17 copies and their checks removed: `puzzles make --openings`
  re-checked 21 positions at depth 18 and made 0, leaving the same 527.
- `repeats`: 30 repeats over 167 games, 19 already drilled by a puzzle (37 over 324 with book
  moves). Top: 3.c3 in the Center Game, 3.c3 in the Sicilian and 5.Nf3 in the Danish Gambit
  Accepted (19 games each), then 3...Bg7 in the Modern (11 games, last 2026-09-22; 4.Qxf7+ found
  9 of 11 times; has a puzzle). The 3.c3 Center Game one has no puzzle.
- After move 10: 1,693 bullet games get there (mean -0.43, 31% at -1 or worse); 23 families with
  White and 14 with Black have 5+ such games. Worst with White: the Blackmar-Diemer, 79 games,
  median -0.9, 49% (39-60%) at -1 or worse, mistakes on 7.8% (6.1-9.8%) of moves 1-10, but only
  -0.2 since book: the deficit is mostly the gambit's. With Black the Modern (417 games, -0.4,
  33% at -1 or worse, 3.3%), Queen's Pawn Game (175, -0.5 median vs -0.8 mean) and Pirc (177,
  -0.3) are all *up* since book (+0.3, +0.1, +0.2): the engine dislikes Black's book positions;
  the user doesn't lose ground after them.
- `/api/repeats` takes 1.1 s and `/api/openings` 1.6 s the first time (both cached after).
- `sync` was not run for real (no network in this work); its alert is tested with a faked fetch.

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
