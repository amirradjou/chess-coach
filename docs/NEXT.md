# Next steps (written 2026-11-02)

Where the work after `sync` (#15) stands, and what comes next. docs/PLAN.md stays the record of
what was built and why; this file is the to-do list until these branches are merged.

## Branches, in merge order

Each one builds on the one before it, so the PRs go in this order (merge commits, not squash).
No PRs are open yet.

| # | Branch | What it holds | State |
|---|--------|---------------|-------|
| 1 | `feat/repeat-mistakes` | Repeated mistakes (`repeats`, digest, web, sync alert); the opening outcome by the engine eval after both sides' 10th moves; puzzle dedupe (`puzzles dedupe`, no copy stored at make time) | Built, reviewed twice, fixed, pushed |
| 2 | `feat/punish-rate` | When they blunder: your punish rate against your opponents', split into partial misses (your reply gives back 10-20 points) and clear misses (20+), per kind, trend, openings | Built, reviewed twice, fixed, pushed |
| 3 | `feat/threat-check` | What was their threat: `ignored_threat` / `unsafe_move` from a null-move search, `threats` command, "What do they threaten?" in Practice | Built, reviewed, fixed, pushed |
| 4 | `feat/next-level` | So far only the public reference ladder (`scripts/level_reference.py`, `src/chess_coach/data/levels.json`) and this file | Card not built |

Still to do before the PRs: stack them. Merge `feat/repeat-mistakes` into `feat/punish-rate`, then
`feat/punish-rate` into `feat/threat-check`, then `feat/threat-check` into `feat/next-level`
(`git merge --no-ff`, conflicts named in the merge message). Expect conflicts in
`patterns/mining.py` (all three clamp the Wilson bound: keep one), `puzzles/make.py`, `db.py`,
`coach/principles.py` (`allows(yours=True)` and `allows(captures=True)`; threats moved board
helpers into `engine/motifs.py`), `cli.py` (new commands, the end of `sync`), `coach/digest.py`,
`web/app.py`, `web/static/*`, README, PLAN and the tests. If `git log` already shows
`chore: merge feat/repeat-mistakes into feat/punish-rate`, that step is done. After each merge,
run the full suite, every new command on a copy of the database, and every tab in a browser at
1280 px and 375 px.

## What the research found (two rounds, 2026-10-31 and 2026-11-01)

Against the opponents in his own games the owner is level almost everywhere. Rating matching
nearly guarantees that, so round 2 compared him with public Lichess players at his rating and
300 and 600 points higher, measured the same way (Stockfish 19, depth 12, the bullet and
short-on-time rules applied to everyone; games without a Lichess analysis, because analysed
games hold more errors).

- **What separates his level from 300 points higher is accuracy early in the game.** In bullet
  his moves 1-5 are as accurate as players rated about 1800-2100 (1.1% mistakes or blunders).
  His moves 6-10 are at his own level (6.35%; 1400-1600 players 6.1%, 1700-1900 4.8%,
  2000-2200 3.4%). In blitz moves 11-20 separate levels most. The error rate in moves 6-10 falls
  at every step of the ladder.
- **His bullet punishing gap is taking only part of a gift**, not missing it: partial misses
  14.7% vs 8.2% for his opponents (+6.5, 95% interval +2.8 to +10.3), clear misses level
  (29.3% vs 28.1%). Partial misses don't separate rating levels, so this is a personal habit.
- **The opening eval after move 10** has to be read after ply 20 (both sides' 10th moves). Read
  after his own 10th move it always includes his last slip and never the reply: the
  Blackmar-Diemer went from "median -95, 50% at -1 or worse" to "+12, 27%".
- **Not the problem:** his openings (87% of his opening mistakes come in positions he had never
  reached), time use (he slows down on critical moves more than his opponents and every band,
  with the same error rate there), converting won positions (level).
- **Practice is the real limit:** 5 puzzle attempts ever, all 544 puzzles due at once.

Rejected with data (don't redo without new evidence): a table of what humans play in his
positions, time and criticality features, conversion and punishing as "next level" targets,
practice on the phone, a reminder timer, a gentler SM-2 penalty for misses (wait for practice
data).

## To build next

### A. `feat/practice-today`, on top of `feat/threat-check`

Bugs first, each its own commit with a test that fails today:

1. Re-adding a puzzle wipes its practice history: `Store.add_puzzle` uses `INSERT OR REPLACE`,
   which deletes the row and its attempts and resets the review. Use
   `ON CONFLICT(id) DO NOTHING`.
2. Opening-fix drills reuse game-move ids (`<game>:<ply>`), so a fix and a game puzzle can
   overwrite each other. Give fixes their own ids (`fix:<game>:<ply>`), with a one-off migration
   that renames them in puzzles, reviews and attempts, like `_fix_lichess_ids`.
3. Practice serves puzzles the bullet rule leaves out: 3 of the 5 practised puzzles are bullet
   moves 12, 13 and 26 (kept by `puzzles prune` because they have attempts). Never serve a
   puzzle `make.keep` rejects; keep its history.
4. The practice day is the UTC date at about 11 places. Use one helper with a 4 am local
   rollover.

Then the session:

- New puzzles are no longer due the day they are made. Practice reads "N due again · M new".
- A "Today" session of 10 items: due reviews and due opening cards first (most overdue first),
  then at most 5 new ones. New items come first from positions he reached in 2+ games, then
  from games of the last 90 days, then the rest. There are never two new items from one game,
  and never two from one game back to back. Session state comes from today's answers, with no
  new table.
- Web: a first, default Today tab ("4 of 10"), and a done panel showing tomorrow's counts, days
  practised of the last 7, and accuracy on reviews vs first tries.
- CLI: `chess-coach today` opens it (or `--cli` in the terminal; `--status` prints one line).
  sync's last line becomes "Today: 10 to practise (about 5 min): 5 due again, 5 new".
- The database comes from `--db`, then `CHESS_COACH_DB`, then `~/.config/chess-coach/config.toml`.
  The default `data/chess-coach.db` holds only the 3 test games. Stockfish is also found from
  the repo root, not just the working directory. Other database files are only ever opened
  read-only, because `Store` migrates whatever it opens.
- Remove the stale `LLM_PROVIDER=claude` / `ANTHROPIC_API_KEY` lines from `.env.example`.

### B. The next-level card, on `feat/next-level`

- `coach/levels.py` counts his counted, non-rushed moves with 20+ points lost in the same
  segments as `levels.json`: bullet moves 1-5 and 6-10, blitz moves 1-5, 6-10 and 11-20, over
  the last 12 months.
  - Wilson intervals with n divided by the stored design effect.
  - His opponents' rate in the same games.
  - Blitz rows weighted to his time-control mix (`by_time_control`).
  - Where he sits on the ladder ("you play moves 6-10 like players around 1400-1500").
  - "Not enough games to place you" when his interval is wider than a 300-point step, and a
    stale note for blitz (last game 2026-09-20).
- Use a plain move-number split. A "book exit" split is meaningless here, because the opening
  table's named lines end around move 2.
- Shown in a Progress card, `chess-coach levels`, and one digest line. The wording comes from
  code and never claims more than the interval allows.
- Tests for the segmentation, the ladder lookup at its edges, the "not measurable" message, and
  the JSON schema.
- Rebuild the reference only when the engine changes. A rebuild means running the script again
  (about 45 minutes), and his stored evals then need re-analysing too.

### C. Later, once Today has been used for 2-3 weeks

- Whether practice carries over into games: the share of drilled positions he then plays right,
  drilled vs not yet drilled.
- A reminder with counts only, which he installs himself if he wants it.
- Revisit the SM-2 miss penalty, and drills from errors that never become puzzles.

## Open questions for the owner

- Does he play his daily bullet on the phone or on the laptop? That decides whether practice
  on the phone matters.
- Are 10 items a day and a 4 am day change right?
- Will he play blitz or rapid again? The blitz rows and the conversion work only matter if so.
- `digest --narrate openrouter` sends the cited moves to openrouter.ai. Does that fit "my games
  stay on this machine"?
