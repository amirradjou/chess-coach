# Next steps (written 2026-11-02, updated 2026-11-05)

Where the work after `sync` (#15) stands, and what comes next. docs/PLAN.md stays the record of
what was built and why; this file is the to-do list until these branches are merged.

## Branches, in merge order

Each one builds on the one before it, and each PR is based on the branch before it, so its diff is
only its own work. Merge in this order, with merge commits, not squash. If a base branch isn't
deleted when its PR merges, retarget the next PR to `main` first.

| PR | Branch | What it holds |
|----|--------|---------------|
| #16 | `feat/repeat-mistakes` | Repeated mistakes, the opening eval after both sides' 10th moves, puzzle dedupe |
| #17 | `feat/punish-rate` | When they blunder: your punish rate against your opponents', partial and clear misses |
| #18 | `feat/threat-check` | What was their threat: ignored threats vs unsafe moves, "What do they threaten?" in Practice |
| #19 | `feat/practice-today` | The practice bugs, and Today: 10 items a day from today's answers |
| #20 | `feat/next-level` | The next-level card, and this file |

The independent check of the merges in #17 and #18 is done (2026-11-05); they are sound. Every
function and module-level statement each side changed was compared with both parents, the test ids,
routes and CLI options are the union of both, and the suite passes at each merge and at both parents
of each. A fresh copy of the database opened twice changes nothing the second time. Every tab was
walked at 1280 px and 375 px.

## After merging

On the real database, in this order:

1. Back it up: `cp data/amirradjou.db data/amirradjou-before-today.db`. The first open adds the
   `threats` table, `punish_*` tags on 228 puzzles, and renames 48 opening fixes.
2. `uv run chess-coach --db data/amirradjou.db puzzles dedupe` (525 puzzles left of 544).
3. `uv run chess-coach --db data/amirradjou.db sync`. The first one checks the backlog of about
   2,500 threats: 126 s on the default 1 thread, tried on a copy with 28 new games (2026-11-05). Keep
   to 1 thread: more than one relabels about 100 rows, and the numbers in the docs are from one.
4. Set `db` in `~/.config/chess-coach/config.toml`, so `chess-coach today` works from anywhere.

## What the research found (two rounds, 2026-10-31 and 2026-11-01)

Against the opponents in their own games the owner is level almost everywhere. Rating matching
nearly guarantees that, so round 2 compared the owner with public Lichess players at the same rating
and 300 and 600 points higher, measured the same way (Stockfish 19, depth 12, the bullet and
short-on-time rules applied to everyone; games without a Lichess analysis, because analysed
games hold more errors).

- **What separates the owner's level from 300 points higher is accuracy early in the game.** In
  bullet, moves 6-10 are at the owner's own level (6.35%; 1400-1600 players 6.1%, 1700-1900 4.8%,
  2000-2200 3.4%). In blitz moves 11-20 separate levels most. The error rate in moves 6-10 falls
  at every step of the ladder.
- **Bullet moves 1-5 can't be placed** (corrected 2026-11-05). Round 2 read the owner's rate as that
  of players rated 1800-2100. But the opponents in the same games, rated about 1470, make 1.3% where
  the ladder expects about 1.9%, so it reads both sides of these games too high.
- **The gap in moves 6-10 is in the owner's White games** (found 2026-11-05). With White: 8.2%,
  opponents 7.9%, public players 300 points higher about 4.4%. With Black: 4.6%, opponents 3.6%,
  public about 5.2%, so no gap. How often both sides go wrong depends on the opening: Danish Gambit
  Accepted 9.6% and 8.8%, French 10.3% and 9.0%, Caro-Kann 3.0% and 4.5%, Modern 4.4% and 3.0%.
  The pooled comparison is still fair; what it means for practice is an open question below.
- **The owner's bullet punishing gap is taking only part of a gift**, not missing it: partial misses
  14.7% vs 8.2% for the opponents (+6.5, 95% interval +2.8 to +10.3), clear misses level
  (29.3% vs 28.1%). Partial misses don't separate rating levels, so this is a personal habit.
- **The opening eval after move 10** has to be read after ply 20 (both sides' 10th moves). Read
  after the owner's own 10th move it always includes the owner's last slip and never the reply: the
  Blackmar-Diemer went from "median -95, 50% at -1 or worse" to "+12, 27%".
- **Not the problem:** knowing more book (87% of the owner's opening mistakes come in positions never
  reached before), time use (the owner slows down on critical moves more than the opponents and every
  band, with the same error rate there), converting won positions (level). Which openings to play
  as White is a different question, and it is open.
- **Practice is the real limit:** 5 puzzle attempts ever, all 544 puzzles due at once.

Rejected with data (don't redo without new evidence): a table of what humans play in the owner's
positions, time and criticality features, conversion and punishing as "next level" targets,
practice on the phone, a reminder timer, a gentler SM-2 penalty for misses (wait for practice
data).

## To build next

A (`feat/practice-today`) and B (the next-level card) are built: see docs/PLAN.md.

### Later, once Today has been used for 2-3 weeks

- Whether practice carries over into games: the share of drilled positions then played right,
  drilled vs not yet drilled.
- A reminder with counts only, for the owner to install if wanted.
- Revisit the SM-2 miss penalty, and drills from errors that never become puzzles.

## Open questions for the owner

- Is the owner's daily bullet played on the phone or on the laptop? That decides whether practice
  on the phone matters.
- The gap in bullet moves 6-10 is in the owner's White games, and those are sharp for both sides
  (see above). Is the answer practice in those positions, or other openings as White? The card can't
  say: it would need colour rows in `levels.json`, a new tally of the scores already in
  `~/.cache/chess-coach-research/levels/`, with nothing to re-score.
- Are 10 items a day and a 4 am day change right? Both are single constants
  (`puzzles/today.py: SESSION`, `util.py: DAY_STARTS_AT_HOUR`).
- The next-level card's 12-month window places bullet moves 6-10 only around 1200-1700: more games
  would narrow it, but older ones may not show how the owner plays now. Is 12 months right?
- Will the owner play blitz or rapid again? The blitz rows and the conversion work only matter if
  so.
- `digest --narrate openrouter` sends the cited moves to openrouter.ai. Does that fit "my games
  stay on this machine"?
