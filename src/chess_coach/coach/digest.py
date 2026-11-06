"""The digest: mined patterns as a page you can read in two minutes, each claim linked to its moves.

Deterministic: the text comes from templates over counted facts, so it needs no model and cannot
invent a pattern.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime

from chess_coach.coach.punish import (
    KINDS,
    Compare,
    Gift,
    Report,
    headline,
    label,
    part_note,
    report,
    worst_title,
)
from chess_coach.coach.repeats import Repeat, repeats
from chess_coach.coach.threats import NO_GAP, gap, percent
from chess_coach.coach.threats import Report as ThreatReport
from chess_coach.coach.threats import report as threat_report
from chess_coach.models import Game, MoveFact, Puzzle, ThreatFact
from chess_coach.patterns.features import (
    BULLET_MOVES,
    game_rows,
    is_bullet,
    move_rows,
    rushed_rows,
)
from chess_coach.patterns.mining import (
    Evidence,
    MotifCount,
    Pattern,
    baseline,
    mine_games,
    mine_moves,
    motif_counts,
    repeated_under_pressure,
)

_FIXED: dict[str, dict[str, str]] = {
    "phase": {
        "opening": "in the opening",
        "middlegame": "in the middlegame",
        "endgame": "in the endgame",
    },
    "colour": {"white": "with White", "black": "with Black"},
    "clock": {
        "under 30s": "with under 30 seconds left",
        "30-60s": "with 30 to 60 seconds left",
        "1-3 min": "with 1 to 3 minutes left",
        "over 3 min": "with more than 3 minutes left",
    },
    "castling": {
        "never castled": "when you don't castle",
        "castled after move 10": "when you castle after move 10",
        "castled by move 10": "when you castle by move 10",
    },
    "queens": {
        "traded queens behind in development": "when you trade queens while behind in development"
    },
    "piece": {
        "pawn": "with pawn moves",
        "knight": "with your knights",
        "bishop": "with your bishops",
        "rook": "with your rooks",
        "queen": "with your queen",
        "king": "with your king",
    },
    "kind": {
        "capture": "when you capture",
        "check": "when you give check",
        "castle": "when you castle",
        "quiet": "with quiet moves",
    },
}

MOTIFS: dict[str, tuple[str, str]] = {
    # tag: (what it is, one thing to practise)
    "missed_tactic": (
        "not punishing the opponent's mistake",
        "after their move, ask what it just left undefended",
    ),
    "hanging_piece": (
        "leaving a piece where it can be taken",
        "before each move, check every capture they will have",
    ),
    "hanging_pawn": (
        "leaving a pawn where it can be taken",
        "before each move, check every capture they will have",
    ),
    "time_trouble": (
        "going wrong with little time left",
        "spend less in the opening so the endgame isn't a scramble",
    ),
    "pawn_grab": (
        "taking a pawn that costs more than it wins",
        "before a capture, look at their best reply",
    ),
    "endgame_conversion": (
        "letting a winning endgame slip",
        "practise converting won endgames slowly and safely",
    ),
    "allowed_mate": ("allowing a forced mate", "check every check they will have before you move"),
    "missed_mate": ("missing a forced mate", "look at checks first when the king is exposed"),
    "back_rank": ("back-rank mates", "make luft or keep a rook home when the back rank is weak"),
    "allowed_fork": ("allowing a fork", "watch the squares their knights and queen can reach"),
    "missed_fork": ("missing a fork", "look for one move that attacks two things"),
    "queen_trade_behind": (
        "trading queens while behind in development",
        "develop before simplifying",
    ),
}


def describe(features: Sequence[tuple[str, str]]) -> str:
    parts = []
    for name, value in features:
        if name in _FIXED and value in _FIXED[name]:
            parts.append(_FIXED[name][value])
        elif name == "speed":
            parts.append(f"in {value}")
        elif name == "moves":
            parts.append(f"on moves {value}")
        elif name == "opening":
            parts.append(f"in the {value}")
        else:
            parts.append(f"{name} {value}")
    text = ", ".join(parts)
    return text[0].upper() + text[1:] if text else text


def _link(e: Evidence, label: str) -> str:
    return f"[{label}]({e.url})" if e.url else label


def _move(e: Evidence) -> str:
    return _link(e, f"{e.move} (best {e.best_san}, -{e.wp_loss:.0f}%)")


@dataclass(slots=True)
class Digest:
    user: str
    games: int
    moves: int
    first: str
    last: str
    error_rate: float
    acpl: float
    losses: int
    decided: int
    move_patterns: list[Pattern]
    game_patterns: list[Pattern]
    motifs: list[MotifCount]
    min_moves: int
    min_games: int
    min_lift: float
    game_lift: float
    # With --since: the same rates over the games before that date, for comparison.
    since: str | None = None
    before_error_rate: float | None = None
    before_acpl: float | None = None
    # In bullet only the first moves count and results never do (features.counted, result_of).
    bullet_games: int = 0
    # Moves played short on time are left out above; these are their mistakes that keep repeating.
    rushed_moves: int = 0
    pressure: list[MotifCount] = field(default_factory=list)
    # When they blunder: your punish rate against your opponents' (with Store.gifts given).
    punish: Report | None = None
    # The same mistake from the same position in 3+ games (with --since: played again since).
    repeats: list[Repeat] = field(default_factory=list)
    # Ignored threats and unsafe moves, yours against your opponents' (once `threats` has run).
    threats: ThreatReport | None = None
    # One line: your gap to public players 300 points above you (coach.levels.headline).
    next_level: str | None = None


def build(
    games: Sequence[Game],
    moves: Sequence[MoveFact],
    *,
    min_moves: int = 30,
    min_games: int = 8,
    min_lift: float = 1.5,
    game_lift: float = 1.25,
    since: datetime | None = None,
    gifts: Iterable[tuple[MoveFact, MoveFact]] | None = None,
    replies: Mapping[tuple[str, int], MoveFact] | None = None,
    puzzles: Sequence[Puzzle] = (),
    threats: Mapping[tuple[str, int], ThreatFact] | None = None,
    errors: Sequence[MoveFact] = (),
    next_level: str | None = None,
) -> Digest:
    """The digest of the analysed games in `games`. `moves` are the user's; `gifts`
    (Store.gifts, both players' moves) adds When they blunder; `replies`
    (Store.replies_to_errors) and `puzzles` complete the repeated mistakes with their punishment
    and their puzzle; with `threats` (Store.threats) and `errors` (both sides' mistakes and
    blunders) it also says how your mistakes happen."""
    analysed = [g for g in games if g.analyzed_depth]
    # Counted over every game: with --since, the ones played again in this digest's games
    again = repeats(analysed, moves, replies or {}, puzzles)
    before: list = []
    if since is not None:
        before = [g for g in analysed if g.played_at and g.played_at < since]
        analysed = [g for g in analysed if g.played_at and g.played_at >= since]
    brows = move_rows(before, moves)
    mrows = move_rows(analysed, moves)
    rrows = rushed_rows(analysed, moves)
    grows = game_rows(analysed, moves)
    dates = sorted(g.played_at for g in analysed if g.played_at)
    return Digest(
        user=analysed[0].user if analysed else "",
        games=len(analysed),
        moves=len(mrows),
        first=dates[0].strftime("%Y-%m-%d") if dates else "?",
        last=dates[-1].strftime("%Y-%m-%d") if dates else "?",
        error_rate=baseline(mrows),
        acpl=sum(r.fact.cp_loss for r in mrows) / len(mrows) if mrows else 0.0,
        losses=sum(r.lost for r in grows),
        decided=len(grows),
        move_patterns=mine_moves(mrows, min_moves, min_lift),
        game_patterns=mine_games(grows, min_games, game_lift),
        # Time trouble is what the short-on-time rule discounts; it has its own section below.
        motifs=[m for m in motif_counts(mrows) if m.tag != "time_trouble"],
        min_moves=min_moves,
        min_games=min_games,
        min_lift=min_lift,
        game_lift=game_lift,
        since=since.strftime("%Y-%m-%d") if since else None,
        before_error_rate=baseline(brows) if brows else None,
        before_acpl=sum(r.fact.cp_loss for r in brows) / len(brows) if brows else None,
        bullet_games=sum(is_bullet(g) for g in analysed),
        rushed_moves=len(rrows),
        pressure=repeated_under_pressure(rrows),
        punish=report(analysed, gifts) if gifts is not None else None,
        repeats=[r for r in again if since is None or (r.last and r.last >= since)],
        threats=threat_report(analysed, errors, threats) if threats else None,
        next_level=next_level,
    )


def _moves_line(i: int, p: Pattern) -> str:
    worst = ", ".join(_move(e) for e in p.evidence)
    return (
        f"{i}. **{describe(p.features)}**: {p.hits} mistakes or blunders in {p.n} moves "
        f"across {p.games} games, {p.rate:.0%} against your usual {p.baseline:.0%} "
        f"({p.lift:.1f}×). Worst: {worst}."
    )


def _games_line(i: int, p: Pattern) -> str:
    games = ", ".join(_link(e, e.game_id) for e in p.evidence)
    return (
        f"{i}. **{describe(p.features)}**: lost {p.hits} of {p.n} ({p.rate:.0%} against your "
        f"usual {p.baseline:.0%}). For example: {games}."
    )


def _miss(g: Gift) -> str:
    """'[13.Bd3 after 12...Qd7 (best 13.Nxe5, -45.2%)](link): It lets Nxe5 take ...'. One
    decimal, cut rather than rounded (Gift.given_back), so a partial miss never reads as 20."""
    r = g.reply
    text = (
        f"{label(r)} after {label(g.blunder)} (best {label(r, r.best_san)}, -{g.given_back:.1f}%)"
    )
    linked = f"[{text}]({g.url})" if g.url else text
    reasons = g.reasons()
    return f"{linked}: {reasons[0]}" if reasons else linked


def _row(c: Compare) -> str:
    """'you 14.7% (83 of 563), them 8.2% (48 of 584): +6.5 points (95% interval +2.8 to +10.3)'"""
    text = (
        f"you {c.yours.rate:.1%} ({c.yours.missed} of {c.yours.n}), them {c.theirs.rate:.1%} "
        f"({c.theirs.missed} of {c.theirs.n})"
    )
    if c.gap is None or c.interval is None:  # a side with no chances: no gap
        return text
    low, high = c.interval
    zero = ", which includes zero" if c.verdict == "same" else ""
    interval = f"95% interval {100 * low:+.1f} to {100 * high:+.1f}{zero}"
    return f"{text}: {100 * c.gap:+.1f} points ({interval})"


def _punish_lines(r: Report) -> list[str]:
    out = [
        "## When they blunder",
        "",
        "A blunder here is a move that loses 20+ points of win probability. Your reply keeps the "
        "gift when it gives back under 10 points, takes only part of it at 10-20 (a partial "
        "miss) and misses it at 20 or more (a clear miss). Your opponents' replies to your "
        "blunders are the yardstick: players at your level with the same kind of chance.",
        "",
    ]
    for s in r.groups:
        if s.chances:
            out += [
                f"- **{s.label}**: {headline(s)}",
                f"  - Partial (gave back 10-20 points): {_row(s.partial)}.",
                f"  - Clear (gave back 20 or more): {_row(s.clear)}.",
                f"  - Partial and clear together (gave back 10 or more): {_row(s.missed)}.",
            ]
    if r.partial_lesson:  # only where your partial gap clears zero
        out += ["", f"What to learn: {r.partial_lesson}"]
        if r.partials:
            where = " and ".join(r.partial_focus).lower()
            lines = [f"- {_miss(g)}" for g in r.partials]
            out += ["", f"Where you took only part of it in {where}:", "", *lines]
    # A kind of punishment one side never had to find can't be compared, so it is left out.
    kinds = "; ".join(
        f"{KINDS[c.label]}, you {c.yours.rate:.0%} of {c.yours.n} and them {c.theirs.rate:.0%} "
        f"of {c.theirs.n}"
        for c in r.kinds
        if c.gap is not None
    )
    if kinds:
        verdict = " ".join(r.lessons) or "No kind stands out against your opponents."
        out += ["", f"Missed (partial or clear), by the move that punishes: {kinds}. {verdict}"]
    focus = " and ".join(r.focus).lower()  # the groups where you miss more, if any
    if r.families:  # only inside those groups, each with its own gap clear of zero
        out += [
            "",
            f"In {focus}, counting partial and clear misses together, you miss more than your "
            "opponents most in these openings (the examples are of the part behind most of each "
            "gap):",
            "",
        ]
        for f in r.families:
            c = f.compare
            out.append(
                f"- **{c.label}**: you missed {c.yours.missed} of {c.yours.n}, they missed "
                f"{c.theirs.missed} of {c.theirs.n}; {part_note(f.part)}."
            )
            out += [f"  - {_miss(g)}" for g in f.examples]
    elif r.worst:  # otherwise examples of the gap, or your costliest misses
        out += ["", f"{worst_title(r)}:", "", *(f"- {_miss(g)}" for g in r.worst)]
    out.append("")
    return out


def _repeat_line(i: int, r: Repeat) -> str:
    where = f" ({r.family})" if r.family else ""
    last = f", last on {r.last:%Y-%m-%d}" if r.last else ""
    parts = [f"{i}. **{r.move}**{where} in {r.games} games{last}; better {r.better}."]
    if r.punish:
        parts.append(
            f"Their best answer is {r.punish}, and they found it {r.punished} of {r.replies} times."
        )
    parts += r.reasons
    games = ", ".join(f"[{n}]({url})" for n, url in enumerate(r.urls, 1))
    if games:
        parts.append(f"Games: {games}.")
    parts.append("It has a puzzle." if r.puzzle else "No puzzle for it yet.")
    return " ".join(parts)


def _threats(r: ThreatReport) -> list[str]:
    left = f" ({r.unchecked} not checked yet: run `chess-coach threats`)" if r.unchecked else ""
    out = [
        "## Their threats and your blunder check",
        "",
        "Before each counted mistake or blunder the engine let the mover pass, to see what the "
        f"other side threatened. Checked: {r.you} of your mistakes and blunders and {r.them} of "
        f"your opponents'{left}.",
        "",
        "| What happened | You | Your opponents | Compared |",
        "|---|---:|---:|---|",
    ]
    for s in r.steps:
        out.append(
            f"| **{s.title}**: {s.what} | {percent(s.you)} | {percent(s.them)} | "
            f"{s.verdict} ({gap(s)}) |"
        )
    out.append("")
    mine = [s for s in r.steps if s.yours]
    for s in mine:
        out.append(
            f"**{s.title}** is a step to work on: it is in more of your mistakes than of your "
            f"opponents'. Practise: {s.tip}."
        )
    if not mine:
        out.append(NO_GAP)
    examples = [(s, e) for s in r.steps for e in s.examples]
    if examples:
        out += ["", "For example:", ""]
        out += [f"- {s.title}, {_link(e.evidence, e.evidence.move)}: {e.why}" for s, e in examples]
    return [*out, ""]


def render(d: Digest, top: int = 5) -> str:
    if not d.moves:
        return "# Chess digest\n\nNo analysed games yet. Run `chess-coach analyze` first.\n"
    summary = (
        f"{d.games} analysed games, {d.first} to {d.last}. You made a mistake or blunder on "
        f"**{d.error_rate:.1%}** of your {d.moves} moves (average centipawn loss {d.acpl:.0f})"
    )
    if d.decided:
        outside = " outside bullet" if d.bullet_games else ""
        summary += (
            f", and lost {d.losses} of {d.decided} games{outside} ({d.losses / d.decided:.0%})"
        )
    summary += "."
    if d.bullet_games:
        summary += (
            f" In your {d.bullet_games} bullet games only the first {BULLET_MOVES} moves count: "
            "after that the clock decides, so later moves and results are left out."
        )
    if d.before_error_rate is not None and d.before_acpl is not None:
        trend = "better than" if d.error_rate < d.before_error_rate else "worse than"
        if abs(d.error_rate - d.before_error_rate) < 0.002:
            trend = "about the same as"
        summary += (
            f" That is {trend} before {d.since}: {d.before_error_rate:.1%} of moves "
            f"(average centipawn loss {d.before_acpl:.0f})."
        )
    if d.rushed_moves:
        summary += (
            f" {d.rushed_moves} moves played short on time (under 15 s in bullet, or under a "
            "tenth of the clock) are left out, unless the same mistake keeps repeating."
        )
    title = f"# Chess digest: {d.user or 'you'}" + (f", since {d.since}" if d.since else "")
    out = [title, "", summary, ""]

    out += ["## Where your moves go wrong", ""]
    if d.move_patterns:
        out += [_moves_line(i, p) for i, p in enumerate(d.move_patterns[:top], 1)]
    else:
        out.append(
            f"Nothing clears the bar yet (at least {d.min_moves} moves and {d.min_lift}× your "
            "usual rate). More analysed games will help."
        )
    out.append("")

    out += ["## Games you lose more often", ""]
    if d.game_patterns:
        out += [_games_line(i, p) for i, p in enumerate(d.game_patterns[:top], 1)]
    else:
        out.append(
            f"No kind of game stands out yet (at least {d.min_games} games and {d.game_lift}× your "
            "usual loss rate)."
        )
    out.append("")

    if d.motifs:
        out += [
            "## What goes wrong most",
            "",
            "| What happens | Moves | Games | Worst example | Practise |",
            "|---|---:|---:|---|---|",
        ]
        for m in d.motifs[: top + 3]:
            what, tip = MOTIFS.get(m.tag, (m.tag.replace("_", " "), ""))
            example = _move(m.evidence[0]) if m.evidence else ""
            out.append(f"| {what} | {m.moves} | {m.games} | {example} | {tip} |")
        out.append("")

    if d.next_level:
        out += ["## The next level", "", d.next_level, ""]
    if d.threats is not None and d.threats.you:
        out += _threats(d.threats)
    if d.punish is not None and d.punish.gifts:
        out += _punish_lines(d.punish)
    if d.repeats:
        when = f", played again since {d.since}" if d.since else ""
        out += [
            "## The same mistake in the same position",
            "",
            "Your inaccuracies or worse from the very same position in 3 or more games"
            f"{when} (named book moves left out):",
            "",
        ]
        out += [_repeat_line(i, r) for i, r in enumerate(d.repeats[:top], 1)]
        out.append("")

    if d.pressure:
        out += [
            "## Short on time, but it keeps happening",
            "",
            "| What happens | Moves | Games | Worst example |",
            "|---|---:|---:|---|",
        ]
        for m in d.pressure:
            what, _ = MOTIFS.get(m.tag, (m.tag.replace("_", " "), ""))
            example = _move(m.evidence[0]) if m.evidence else ""
            out.append(f"| {what} | {m.moves} | {m.games} | {example} |")
        out.append("")

    out += [
        "## How to read this",
        "",
        "Every line is counted from Stockfish's verdict on your own moves. A situation is listed "
        f"only when it has at least {d.min_moves} moves ({d.min_games} games for results), your "
        f"rate there is at least {d.min_lift}× your usual one ({d.game_lift}× for results), and "
        "the low end of its 95% interval is still above your usual rate. They are ranked by how "
        "many extra mistakes they cost you. Links open the game at the move on Lichess.",
    ]
    if d.punish is not None and d.punish.gifts:
        out += [
            "",
            "When they blunder is the exception: it also counts your opponents' replies to your "
            "blunders, as the yardstick for yours. There you are only called worse or better than "
            "your opponents (for partial or clear misses in a group of games, for a kind of "
            "punishing move, or in an opening) when the 95% interval of the gap leaves out zero; "
            "otherwise there is no clear difference, or too few chances to tell. Openings are "
            "only looked at in a group where you miss more, partial and clear together, and with "
            "10+ chances on each side, and an opening's interval allows for how many were looked "
            "at. Partial misses are only shown where your partial gap clears zero. Examples stand "
            "for a gap, so they are partial or clear misses, whichever is behind most of it.",
        ]
    return "\n".join(out).rstrip() + "\n"
