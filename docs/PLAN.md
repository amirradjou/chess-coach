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
  worse or better only when that interval leaves out zero, otherwise "about the same as your
  opponents". A side with no chances has no rate, gap or interval ('–', `null` in the API),
  never 0 of 0 read as 0%.
- Kind of punishment from the reply's `best_san` (`features.punish_kind`: capture, check, quiet;
  castling is quiet). A kind's lesson (`punish.LESSONS`) fires only when you miss that kind more
  than your opponents do with the interval clear of zero; your quiet rate against your own
  capture rate is never compared (everyone misses quiet punishments more).
- Trend: stretches of games holding about 150 of your chances each, counted back from the newest
  game; a stretch closes at the end of the game that brings it to 150 or more, and the oldest
  holds what is left. Both rates in a stretch come from the same games. No month column (2026
  months have 4-50 chances each).
- Evidence: openings only inside the groups where you're worse, and only those whose own gap
  clears zero too, ranked by excess misses (yours minus your n times their rate), with your
  costliest misses there. When no opening qualifies (or no group is worse), your costliest misses
  are the evidence instead: from the worse groups, or from all. Boards show their blunder as the
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
    points, 95% interval +2.1 to +13.4, the one real gap (all of it partial misses).
  - Blitz and rapid: 36.0% (221 of 614) against 35.6% (227 of 637): +0.4, -4.9 to +5.7, level.
  - By kind: capture 33.1% (599) vs 29.1% (670), check 45.6% (68) vs 39.7% (73), quiet 47.1%
    (510) vs 45.0% (478); no interval clears zero, so no kind lesson.
  - Trend (8 stretches, 2018-10 to 2026-10): your rate 34.7-45.3%, theirs 30.6-40.6%; no
    stretch's gap clears zero.
  - Where the bullet gap comes from: Danish Gambit Accepted 23/42 vs 12/42 (+26.2, 95% interval
    +5.1 to +44.3), Queen's Pawn Game 23/38 vs 12/37 (+28.1, +5.5 to +47.0), Blackmar-Diemer
    Gambit 30/48 vs 26/62 (+20.6, +1.8 to +37.4). The Sicilian Defense (22/44 vs 10/32, +18.8,
    -3.6 to +38.2) is not listed: its interval includes zero. The costliest misses are fianchetto
    reflexes: 2.Bh6 2...Bg7?? (2...Nxh6 wins the bishop).
  - `--since 7d`: bullet 8 of 13 against 8 of 19 (+19.4, -14.4 to +47.5), about the same, so no
    openings: just the three costliest misses.
  - Puzzles: the migration tagged all 228 `missed_tactic` puzzles: 112 punish_capture, 95
    punish_quiet, 21 punish_check.
- After two reviews (2026-11-01), fixed: openings had been listed on a raw excess (1-of-1
  samples, and from all games when no group was worse) under "where you miss more than your
  opponents"; `allows`' new capture clause had repeated explain's hanging-piece reason on the
  opening tree (82 of 940 punishable opponent mistakes there); a side with 0 chances read as 0%
  (a "-100 points" gap); the capture of a checking piece got no reason (3 of the 469 counted
  misses); the CLI said the engine's move "punished it" and every trend row held 150; any
  non-bullet speed was labelled "Blitz and rapid"; and no test covered `digest`'s wiring.

### Partial and clear misses (2026-11-01, after research round 2)

Research round 2 (re-checked by both skeptics at depth 12 and 18) found that "missed" mixes two
habits: on the owner's bullet games the whole gap is in replies that give back 10-20 points
(he takes only part of the gift); replies that give back 20+ are level with his opponents'. So
each group now shows the two apart, and the headline says which one differs.

- `Gift.outcome` from the reply's `wp_loss`: kept (< 10), partial (10 to < 20), clear (>= 20);
  the bars are `evaluation.INACCURACY_WP_LOSS` and `MISTAKE_WP_LOSS`. `Split` holds one group's
  partial and clear `Compare`s (`compare` takes the predicate; `missed` is the default), each
  with Wilson intervals, a Newcombe interval for the gap and the same verdict rule (worse or
  better only when the interval leaves out zero).
- Checked first, on the copy: `missed_tactic` on the reply is exactly `wp_loss >= 10`, on all
  8,493 answered gifts (3,494 tagged, every one 10+; 4,999 untagged, every one under 10) and on
  the 2,398 counted ones (908 = 235 partial + 673 clear). The stored judgement agrees: partial =
  inaccuracy (235), clear = mistake (248) or blunder (425). No reply sits exactly on 10.0 or
  20.0. It holds by construction too (`motifs.py` tags `missed_tactic` when the move before lost
  20+ and this one 10+), so `missed` is partial plus clear and the parts below that use it don't
  contradict the split.
- `headline(Split)`, a gap first (worse before better, partial before clear) and a gap only
  when its interval clears zero; otherwise "Partial misses are the same as theirs: X vs Y" and
  "Clear misses are the same as theirs: X vs Y" (both same: "When they blunder you do as well as
  your opponents."). Partial worse, clear not: "When they blunder you often take only part of
  it: your reply gave back 10-20 points after 14.7% of their blunders (your opponents: 8.2%),
  +6.5 points (95% interval +2.8 to +10.3). Clear misses are the same as theirs: 29.3% vs
  28.1%." Clear gaps read "you miss it outright more (less) often".
- `LESSONS["partial"]` ("After their slip, compare checks, captures and threats before your
  usual move, and take the one that wins most.") is `Report.partial_lesson`, only when some
  group's partial gap is worse (`partial_focus`); `Report.lessons` stays the kinds'. Examples:
  `Report.partials`, your partial misses in those groups that gave back the most (`costliest`),
  in the existing format (their blunder, your reply, the engine's move,
  `principles.allows(captures=True)`, the link). Losses in examples now show one decimal
  (digest, API): rounded, 19.6 read "-20%", the clear bar.
- Which definition each part uses:
  - partial / clear (`outcome`): the group headlines and their two rows, the partial lesson and
    the partial examples;
  - missed, partial and clear together (`missed_tactic`): the kinds table (and its lessons),
    the trend, the openings list and its gate (a group where you miss more, its interval above
    zero, as before), and the costliest misses. Their titles say so ("Missed (partial or
    clear)", "partial and clear together");
  - puzzle tags (`punish_capture|check|quiet`): every `missed_tactic` puzzle, and puzzles start
    at 20 points lost (`make.MIN_WP_LOSS`), so all are clear misses: 228 of 228 on the copy. No
    drill trains partial misses yet; the CLI's drill line says so, and the card's practice line
    says the puzzles come from moves losing 20+.
- Surfaces: the CLI prints both headlines and one table with a partial and a clear row per
  group, then the lesson and the examples; the digest puts both rows under each group's line;
  `/api/punish` groups are `{label, headline, partial, clear}` (each a compare with `name`), plus
  `partial_focus`, `partial_lesson` and `partials`; the web card shows each group with its
  headline and a two-row table, then the lesson and the boards.
- On a fresh copy of the owner's database (same 2,398 counted chances):
  - Bullet, moves 1-10: partial 14.7% (83 of 563) against 8.2% (48 of 584), +6.5, 95% interval
    +2.8 to +10.3, worse; clear 29.3% (165 of 563) against 28.1% (164 of 584), +1.2, -4.0 to
    +6.5, the same.
  - Blitz and rapid: partial 8.8% (54 of 614) against 7.8% (50 of 637), +0.9, -2.1 to +4.1;
    clear 27.2% (167 of 614) against 27.8% (177 of 637), -0.6, -5.5 to +4.4; level on both.
  - Partial examples (bullet): 9.Qd5 after 8...Qf6 (9.Qh3 hits the queen; -19.6), 5...d5 after
    5.O-O (5...Qxg5 takes a hanging knight; -19.6), 10...Re8 after 10.Nf3 (10...h6; -19.4).
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
