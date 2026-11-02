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

## When they blunder: punish rate against your opponents (2026-11-01, branch `feat/punish-rate`)

- `coach/punish.py`: a gift is any move losing 20+ points of win probability (`Store.gifts`, a
  self-join that pairs it with the next move, either side); the reply missed the punishment when
  it carries `missed_tactic`, which the engine already sets for exactly that (an inaccuracy or
  worse right after a 20+ move), so nothing is re-derived. That is partial and clear misses
  together: the group headlines split them (next section). The mirror ("Luck") is the same over
  your gifts and your opponents' replies, and is the yardstick: players at your level with the
  same kind of chance. `features.counted` and `features.rushed` are applied to the replying move
  on both sides (in bullet, opponents' replies after move 10 are out too).
- Groups: bullet (moves 1-10) and everything slower together ("Blitz and rapid"; rapid alone is
  too small; classical, correspondence or unknown-speed games count there too, and then the label
  names them: `punish.group_names`). Each rate has a Wilson interval (`mining.wilson_upper` joins
  `wilson_lower`), the gap a Newcombe hybrid-score interval (`mining.newcombe`); the sentence says
  worse or better only when that interval leaves out zero, otherwise no clear difference, or too
  few to tell (next section). A side with no chances has no rate, gap or interval ('–', `null` in
  the API), never 0 of 0 read as 0%.
- Kind of punishment from the reply's `best_san` (`features.punish_kind`: capture, check, quiet;
  castling is quiet). A kind's lesson (`punish.LESSONS`) fires only when you miss that kind more
  than your opponents do with the interval clear of zero; your quiet rate against your own
  capture rate is never compared (everyone misses quiet punishments more).
- Trend: stretches of games holding about 150 of your chances each, counted back from the newest
  game; a stretch closes at the end of the game that brings it to 150 or more, and the oldest
  holds what is left. Both rates in a stretch come from the same games. No month column (2026
  months have 4-50 chances each).
- Evidence: openings only inside the groups where you're worse, only with 10+ chances on each
  side (`OPENING_CHANCES`), and only those whose own gap clears zero at 95% across all the
  openings looked at (Bonferroni, `_clear_of_chance`), ranked by excess misses (yours minus your
  n times their rate), each with examples of the part (partial or clear) behind most of its gap.
  When no opening qualifies, the examples follow the gap of the worse groups the same way (none
  when the partial examples already show it), or with no worse group they are your costliest
  misses (next section). Boards show their blunder as the
  last move, your reply in red and the punishment in green, with `Game.move_url` at the reply's
  ply and `principles.allows(..., captures=True)`. That also names a capture of a piece that
  was hanging or attacked by a cheaper piece ("It lets Nxh6 take their bishop on h6, which was
  hanging ..."), with a new attack it makes, even after a blunder that gave check
  (`principles.en_prise` is `hanging()` without its in-check guard). Only When they blunder passes
  `captures`: the opening tree joins `allows` with `explain`, which already says the piece was
  left hanging, so the tree's reasons are the same as on main.
- Puzzle sub-tags: a puzzle with `missed_tactic` gets `punish_capture|punish_check|punish_quiet`
  from its solution SAN (`features.punish_tag`); new ones in `make.verify`, existing ones by a
  tiny Store migration on open (`Store._tag_punishments`, idempotent: it only touches puzzles
  with `missed_tactic` and no `punish_` tag yet), not by `puzzles prune`. No new deck: the
  Practice tag filter (and `drill --tag`) offers them.
- Surfaces: `chess-coach punish [--since] [--examples]`, a *When they blunder* section in the
  digest (only when the CLI passes `gifts`; the narrated summary and the eval harness don't get
  it), and `GET /api/punish` (cached like the overview) rendered on What you get wrong, loaded
  after the rest of the page, with practice buttons per sub-tag and a note that Learn openings'
  punish deck covers only the first 8 moves. No engine anywhere.
- On a copy of the owner's database (2,099 Lichess games, depth 12; 8,493 answered moves losing
  20+, 2,398 of them with a counted, unhurried reply), about 0.2 s. Missed here is partial and
  clear together; the split is in the next section:
  - Bullet, moves 1-10: you missed 44.0% (248 of 563), they missed 36.3% (212 of 584): +7.7
    points, 95% interval +2.1 to +13.4, the one real gap (most of it partial misses: +6.5 of the
    +7.7 points; clear misses +1.2, -4.0 to +6.5, not shown to differ).
  - Blitz and rapid: 36.0% (221 of 614) against 35.6% (227 of 637): +0.4, -4.9 to +5.7, no
    clear difference.
  - By kind: capture 33.1% (599) vs 29.1% (670), check 45.6% (68) vs 39.7% (73), quiet 47.1%
    (510) vs 45.0% (478); no interval clears zero, so no kind lesson.
  - Trend (8 stretches, 2018-10 to 2026-10): your rate 34.7-45.3%, theirs 30.6-40.6%; no
    stretch's gap clears zero.
  - Openings, as first built (each opening's own 95% interval): Danish Gambit Accepted 23/42 vs
    12/42 (+26.2, 95% interval +5.1 to +44.3), Queen's Pawn Game 23/38 vs 12/37 (+28.1, +5.5 to
    +47.0), Blackmar-Diemer Gambit 30/48 vs 26/62 (+20.6, +1.8 to +37.4); the Sicilian Defense
    (22/44 vs 10/32, +18.8, -3.6 to +38.2) was not listed. They were not "where the bullet gap
    comes from": of his 36.7 excess partial misses in bullet they hold 11.0, 3.9 and 1.6, and the
    Queen's Pawn Game and the Blackmar-Diemer stand out mostly on clear misses. Since the third
    review none is listed (13 openings with 10+ chances a side, z = 2.89; see "After the review
    of the split"). The costliest misses are fianchetto reflexes, 2.Bh6 2...Bg7?? (2...Nxh6 wins
    the bishop): clear misses, which show no clear difference from his opponents'.
  - `--since 7d`: bullet 8 of 13 against 8 of 19 (+19.4, -14.4 to +47.5), too few to tell, so no
    openings: just the three costliest misses.
  - Puzzles: the migration tagged all 228 `missed_tactic` puzzles: 112 punish_capture, 95
    punish_quiet, 21 punish_check.
- After two reviews (2026-11-01), fixed: openings had been listed on a raw excess (1-of-1
  samples, and from all games when no group was worse) under "where you miss more than your
  opponents" (the per-opening interval that replaced it still let a 1 of 1 against 0 of 3
  through: see "After the review of the split" below); `allows`' new capture clause had
  repeated explain's hanging-piece reason on the opening tree (82 of 940 punishable opponent
  mistakes there); a side with 0 chances read as 0%
  (a "-100 points" gap); the capture of a checking piece got no reason (3 of the 469 counted
  misses); the CLI said the engine's move "punished it" and every trend row held 150; any
  non-bullet speed was labelled "Blitz and rapid"; and no test covered `digest`'s wiring.

### Partial and clear misses (2026-11-01, after research round 2)

Research round 2 (re-checked by both skeptics at depth 12 and 18) found that "missed" mixes two
habits: on the owner's bullet games most of the gap is in replies that give back 10-20 points
(+6.5 of the +7.7 points: he takes only part of the gift); replies that give back 20+ show no
clear difference from his opponents' (+1.2, -4.0 to +6.5: not shown to differ, not shown to be
zero). So each group now shows the two apart, and the headline says which one differs.

- `Gift.outcome` from the reply's `wp_loss`: kept (< 10), partial (10 to < 20), clear (>= 20);
  the bars are `evaluation.INACCURACY_WP_LOSS` and `MISTAKE_WP_LOSS`. `Split` holds one group's
  partial and clear `Compare`s and the two together (`missed`: `compare` takes the predicate;
  `missed` is the default), each
  with Wilson intervals, a Newcombe interval for the gap and the same verdict rule (worse or
  better only when the interval leaves out zero).
- Checked first, on the copy: `missed_tactic` on the reply is exactly `wp_loss >= 10`, on all
  8,493 answered gifts (3,494 tagged, every one 10+; 4,999 untagged, every one under 10) and on
  the 2,398 counted ones (908 = 235 partial + 673 clear). The stored judgement agrees: partial =
  inaccuracy (235), clear = mistake (248) or blunder (425). No counted reply sits exactly on 10.0
  or 20.0; of all 8,493 answered gifts one does, lichess:dsJhaWUI ply 38 (bullet move 19, so not
  counted) at exactly 20.0, a mistake, sorted as clear like its judgement. It holds by
  construction too (`motifs.py` tags `missed_tactic` when the move before lost 20+ and this one
  10+), so `missed` is partial plus clear. The parts below that use it can still stress another
  part than a group's headline (an opening can stand out on clear misses inside a group whose gap
  is partial), so examples come from the part behind most of the gap they stand for.
- `headline(Split)`, a gap first (worse before better, partial before clear) and a gap only
  when its interval clears zero. A level row keeps its numbers: "Clear misses: no clear
  difference, 29.3% vs 28.1% (+1.2 points, 95% interval -4.0 to +6.5)", or "too few to tell"
  while the interval still reaches 10 points either way (`UNSURE_GAP`); never "the same". Both
  parts level: the two together are named when their interval clears zero ("When they blunder
  you miss it more often than your opponents, partial and clear misses together: ... Apart,
  neither is clear."), else "there is no clear difference between you and your opponents" or
  "there are too few chances yet to tell you apart"; never "you do as well". Partial worse, clear
  not: "When they blunder you often take only part of it: your reply gave back 10-20 points after
  14.7% of their blunders (your opponents: 8.2%), +6.5 points (95% interval +2.8 to +10.3). Clear
  misses: no clear difference, 29.3% vs 28.1% (+1.2 points, 95% interval -4.0 to +6.5)." Clear
  gaps read "you miss it outright more (less) often".
- `LESSONS["partial"]` ("After their slip, compare checks, captures and threats before your
  usual move, and take the one that wins most.") is `Report.partial_lesson`, only when some
  group's partial gap is worse (`partial_focus`); `Report.lessons` stays the kinds'. Examples:
  `Report.partials`, typical partial misses in those groups (`typical`: ones whose blunder has a
  reason in chess terms first, then those nearest the middle of the band, 15 points; the
  costliest sit just under 20, where another engine depth may call them clear), in the existing
  format (their blunder, your reply, the engine's move, `principles.allows(captures=True)`, the
  link). Losses in examples show one decimal, cut rather than rounded (`Gift.given_back`):
  rounding printed 19.96-19.99 as 20.0, the clear bar.
- Which definition each part uses:
  - partial / clear (`outcome`): the group headlines and their rows, the partial lesson, the
    partial examples, and every example of a gap (`main_part`: the part with the bigger excess
    over the opponents' rate, partial on a tie);
  - missed, partial and clear together (`missed_tactic`): each group's third row, the headline
    when neither part has a gap, the kinds table (and its lessons), the trend, the openings list
    and its gate (a group where you miss more, then 10+ chances a side and a gap clear of zero
    across the openings looked at), and the costliest misses shown when no group is worse. Their
    titles say so ("Missed (partial or clear)", "partial and clear together"; the other example
    headings name their part, `worst_title`);
  - puzzle tags (`punish_capture|check|quiet`): every `missed_tactic` puzzle, and puzzles start
    at 20 points lost (`make.MIN_WP_LOSS`), so all are clear misses: 228 of 228 on the copy. No
    drill trains partial misses yet; the CLI's drill line says so, and the card's practice line
    says the puzzles come from moves losing 20+.
- Surfaces: the CLI prints both headlines and one table with a partial, a clear and a together
  row per group, then the lesson and the examples; the digest puts the three rows under each
  group's line, and lists each opening with its examples (no costliest misses next to them, as
  in the CLI and on the card); `/api/punish` groups are `{label, headline, partial, clear,
  missed}` (each a compare with `name`), plus `partial_focus`, `partial_lesson` and `partials`;
  families carry `part` and `part_note`, and `worst_part` and `worst_title` say what `worst`
  holds; the web card shows each group with its headline and a three-row table, then the lesson
  and the boards.
- On a fresh copy of the owner's database (same 2,398 counted chances):
  - Bullet, moves 1-10: partial 14.7% (83 of 563) against 8.2% (48 of 584), +6.5, 95% interval
    +2.8 to +10.3, worse; clear 29.3% (165 of 563) against 28.1% (164 of 584), +1.2, -4.0 to
    +6.5, no clear difference; together 44.0% (248 of 563) against 36.3% (212 of 584), +7.7,
    +2.1 to +13.4.
  - Blitz and rapid: partial 8.8% (54 of 614) against 7.8% (50 of 637), +0.9, -2.1 to +4.1;
    clear 27.2% (167 of 614) against 27.8% (177 of 637), -0.6, -5.5 to +4.4; no clear difference
    on either (together +0.4, -4.9 to +5.7).
  - Partial examples (bullet), typical since the third review: 9...Nxf3 after 9.Ngf3 (9...Nxd3
    hits the bishop on f4; -14.8), 10.Qe1 after 9...Bd6 (10.g4 hits the bishop on h5; -15.4),
    6...a6 after 6.c5 (6...c6 hits the knight on b5; -15.5). Before: 19.61, 19.57 and 19.38, the
    edge of the band, the third with no reason.
  - No opening is listed, and no other examples: the partial examples show the bullet gap.
- How firm the partial gap is (research round 2): game-clustered +6.5 [+2.7, +10.0] over 681
  games; at depth 18 (6,567 positions re-scored, game-clustered) +6.6 [+3.3, +10.0], with clear
  +1.7 [-3.7, +7.1]. Public players' partial rates don't separate rating levels (bands A / B /
  C: 9.8 / 10.3 / 9.7%), so it is a personal habit, not a level gap, and the wording stays away
  from levels.
- Caveats (here, not in the UI):
  - In the 268 bullet games where both sides gave a counted gift, the partial gap is +2.6, 95%
    interval -1.8 to +7.1 (39 of 351 against 31 of 365): inside the same games it is not clear.
  - 11 of his 83 bullet partial misses come from the Danish Gambit Accepted: 11 of his 42
    chances there, against his opponents' 0 of 42 (clear: 12 of 42 each). With the other two
    Danish families it is 13 of 83 (Danish Gambit 2 of 7 against 0 of 3; Declined 0 of 1
    against 0 of 5).
  - The gap sits in his newer games: older half +3.3 [-1.9, +8.5], newer half +9.8 [+4.5,
    +15.2]. A second depth-12 run with other engine settings shrinks it to +2.9 [-0.6, +6.6];
    that run agrees with depth 18 less often (90.7% of reply classes against 94.8% for the
    stored evals), but the 10-20 band does depend on the engine's settings.

### After the review of the split (2026-11-01)

Fixed, each with a test that fails on the code before it (numbers from a copy of the database):
- Both parts level, together worse (`--since 90d`, bullet: partial +8.0, -0.0 to +17.8; clear
  +11.4, -3.2 to +25.8; together 31 of 67 against 22 of 82, +19.4, +4.0 to +33.9): the headline
  said "you do as well as your opponents" while the same section listed the openings where he
  misses more. It now names the together gap, and the gate uses the same compare
  (`Split.missed`).
- A level row read "the same as theirs" with no interval, even on 3 of 13 against 1 of 19 (the
  weekly digest: +17.8, -6.6 to +45.3). Now "no clear difference" or "too few to tell", with the
  interval.
- Every board under "where you miss more" was a clear miss (9 of 9), including both of the
  Danish Gambit Accepted's, whose gap is all partial. Examples now come from the part behind
  most of the gap they stand for, and the digest lists the openings with their examples instead
  of the costliest misses.
- The partial examples were the edge of the band (19.61, 19.57, 19.38, the last with no reason):
  now typical ones with a reason.
- Rounding printed 19.96-19.99 as 20.0 (there is an answered reply at 19.99, lichess:ur6JHTSf
  ply 24, not counted): now cut to one decimal; the card printed 81.0 as "81".
- The openings gate listed 1 of 1 against 0 of 3 (`--since 90d`, the English Opening) and tested
  about 40 openings at 95% each (16 with 3 or fewer of his chances), so about one listing was
  expected by chance. Now 10+ chances a side and a Bonferroni interval across the openings
  looked at; on all his games none of the 13 looked at clears it (z = 2.89), and with `--since
  90d` the Pirc Defense, the only one looked at, stays.
- Wording: "the whole gap is in partial misses" is most of it (+6.5 of +7.7); "no reply sits
  exactly on 10.0 or 20.0" holds for the counted replies, not for all answered ones.

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
