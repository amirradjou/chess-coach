"""When they blunder: what your replies to your opponents' blunders gave back, next to what
theirs gave back after yours.

A gift is a move that loses at least 20 points of win probability; the reply is the other
player's next move. What the reply gave back sorts it (`Gift.outcome`): under 10 points it kept
the gift, 10 to under 20 it took only part of it (a partial miss), 20 or more it missed it (a
clear miss). Partial and clear misses are compared apart because they are different habits: on
the owner's bullet games the whole gap is in partial misses, and clear ones are level. Your
opponents' replies to your own gifts are the yardstick: players at your level with the same kind
of chance (Lichess Insights calls the two rates Opportunism and Luck). A gap only counts when its
95% interval (Newcombe) leaves out zero.

`Gift.missed` is partial and clear together: the engine's `missed_tactic` on the reply (an
inaccuracy or worse right after a gift: engine/motifs.py), the same as a reply losing 10+ points.
The kinds of punishing move, the trend and the openings use it, so nothing is re-derived there.

The owner's rules apply to the replying move, on both sides alike: in bullet only moves 1-10
count (features.counted) and replies made short on time don't (features.rushed). Bullet is
reported apart from blitz and rapid (rapid alone is too small); classical, correspondence and
unknown-speed games count with blitz and rapid, and then the group's label names them.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field

from chess_coach.coach.principles import allows
from chess_coach.engine.evaluation import INACCURACY_WP_LOSS, MISTAKE_WP_LOSS
from chess_coach.models import Color, Game, MoveFact, Speed
from chess_coach.patterns.features import BULLET_MOVES, counted, is_bullet, punish_kind, rushed
from chess_coach.patterns.mining import newcombe, wilson_lower, wilson_upper

GIFT_WP_LOSS = 20.0  # the same bar as missed_tactic (engine/motifs.py)
# What the reply gave back: under 10 points it kept the gift, from 20 it missed it outright.
PARTIAL_WP_LOSS = INACCURACY_WP_LOSS  # 10, where missed_tactic starts
CLEAR_WP_LOSS = MISTAKE_WP_LOSS  # 20
OUTCOMES = {"partial": "partial (10-20 points)", "clear": "clear (20+ points)"}
TREND_GIFTS = 150  # their blunders you could punish, per stretch of the trend: about ±8 points
GROUPS = {"bullet": f"Bullet, moves 1-{BULLET_MOVES}", "slower": "Blitz and rapid"}
# Other speeds that count with blitz and rapid, named in the label when there are any.
OTHER_SPEEDS = {
    Speed.CLASSICAL: "classical",
    Speed.CORRESPONDENCE: "correspondence",
    Speed.UNKNOWN: "games of unknown speed",
}
KINDS = {"capture": "a capture", "check": "a check", "quiet": "a quiet move"}
# Only shown when you do it clearly more often than your opponents: take only part of their
# blunders (partial), or miss a kind of punishment (everyone misses quiet punishments more than
# captures, so that alone is no lesson).
LESSONS = {
    "partial": (
        "After their slip, compare checks, captures and threats before your usual move, and take "
        "the one that wins most."
    ),
    "capture": (
        "When they blunder, look first at what their move left undefended: count attackers and "
        "defenders, and take what is free."
    ),
    "check": (
        "When they blunder, look at every check: one that forks, wins material or drives the "
        "king into the open is often the punishment."
    ),
    "quiet": (
        "When they blunder, look for quiet punishing moves too, not only captures and checks: a "
        "move that attacks two things, traps a piece or threatens mate."
    ),
}


def label(m: MoveFact, san: str | None = None) -> str:
    """'12...Nf6': the move with its number, or another move (`san`) played at that point."""
    return f"{m.move_number}{'.' if m.color is Color.WHITE else '...'}{san or m.san}"


@dataclass(frozen=True, slots=True)
class Gift:
    """A blunder and the other player's reply to it."""

    game: Game
    blunder: MoveFact
    reply: MoveFact

    @property
    def yours(self) -> bool:
        """Yours to punish: your opponent blundered and you replied."""
        return self.reply.is_user

    @property
    def missed(self) -> bool:
        """Partial or clear: the engine tagged the reply missed_tactic (it lost 10+ points)."""
        return "missed_tactic" in self.reply.motifs

    @property
    def outcome(self) -> str:
        """What the reply gave back of the gift: kept (under 10 points), partial (10 to under
        20: it took only part of it) or clear (20 or more: it missed it)."""
        loss = self.reply.wp_loss
        return "kept" if loss < PARTIAL_WP_LOSS else "partial" if loss < CLEAR_WP_LOSS else "clear"

    @property
    def kind(self) -> str:
        """capture, check or quiet: the engine's punishing move (the reply's best move)."""
        return punish_kind(self.reply.best_san)

    @property
    def group(self) -> str:
        return "bullet" if is_bullet(self.game) else "slower"

    @property
    def url(self) -> str | None:
        return self.game.move_url(self.reply.ply)

    def reasons(self) -> list[str]:
        """What the blunder allowed, in chess terms (principles.allows), including taking a piece
        it left hanging: the commonest punishment."""
        if not self.reply.fen_before or not self.reply.best_move:
            return []
        return allows(self.reply.fen_before, self.reply.best_move, captures=True)


def group_names(found: Iterable[Gift]) -> dict[str, str]:
    """GROUPS, with the slower group's label naming any classical, correspondence or
    unknown-speed games among `found`: 'Blitz, rapid and classical'."""
    speeds = {g.game.speed for g in found}
    words = ["blitz", "rapid", *(name for speed, name in OTHER_SPEEDS.items() if speed in speeds)]
    if len(words) == 2:
        return GROUPS
    return {**GROUPS, "slower": f"{', '.join(words[:-1])} and {words[-1]}".capitalize()}


def gifts(games: Iterable[Game], pairs: Iterable[tuple[MoveFact, MoveFact]]) -> list[Gift]:
    """The gifts in `games` whose reply counts: coached at all (in bullet, moves 1-10) and not
    made short on time. `pairs` are (move, next move) as Store.gifts returns them."""
    by_id = {g.id: g for g in games}
    out = []
    for blunder, reply in pairs:
        game = by_id.get(reply.game_id)
        if game is None or blunder.wp_loss < GIFT_WP_LOSS:
            continue
        if counted(reply, game) and not rushed(reply, game):
            out.append(Gift(game, blunder, reply))
    return out


@dataclass(frozen=True, slots=True)
class Rate:
    missed: int
    n: int

    @property
    def rate(self) -> float:
        return self.missed / self.n if self.n else 0.0

    @property
    def low(self) -> float:
        return wilson_lower(self.missed, self.n)

    @property
    def high(self) -> float:
        return wilson_upper(self.missed, self.n)


@dataclass(frozen=True, slots=True)
class Compare:
    """Over one slice: how often you missed the punishment, and how often your opponents did."""

    label: str
    yours: Rate  # your replies to their blunders
    theirs: Rate  # their replies to yours

    @property
    def gap(self) -> float | None:
        """Your rate minus theirs; None when either side had no chances (0 of 0 is no rate)."""
        if not (self.yours.n and self.theirs.n):
            return None
        return self.yours.rate - self.theirs.rate

    @property
    def interval(self) -> tuple[float, float] | None:
        """The gap's 95% interval (Newcombe); None with the gap."""
        if not (self.yours.n and self.theirs.n):
            return None
        return newcombe(self.yours.missed, self.yours.n, self.theirs.missed, self.theirs.n)

    @property
    def verdict(self) -> str:
        """worse, better or same: a gap only counts when its 95% interval leaves out zero."""
        if (interval := self.interval) is None:
            return "same"
        low, high = interval
        return "worse" if low > 0 else "better" if high < 0 else "same"

    @property
    def excess(self) -> float:
        """How many more of their blunders you missed than your opponents' rate predicts."""
        return self.yours.missed - self.yours.n * self.theirs.rate


def compare(
    name: str, found: Iterable[Gift], hit: Callable[[Gift], bool] = lambda g: g.missed
) -> Compare:
    """How often `hit` holds for your replies and for theirs; by default missed (partial or
    clear)."""
    yours, theirs = [0, 0], [0, 0]
    for g in found:
        side = yours if g.yours else theirs
        side[0] += hit(g)
        side[1] += 1
    return Compare(name, Rate(*yours), Rate(*theirs))


@dataclass(frozen=True, slots=True)
class Split:
    """One group's chances by what the reply gave back: how often it took only part of the gift
    (partial) and how often it missed it (clear), yours against your opponents'. The labels of
    `partial` and `clear` are OUTCOMES keys."""

    label: str
    partial: Compare
    clear: Compare

    @property
    def chances(self) -> int:
        """Gifts in the group, both sides'."""
        return self.partial.yours.n + self.partial.theirs.n


def split(name: str, found: Iterable[Gift]) -> Split:
    found = list(found)
    return Split(
        name,
        compare("partial", found, lambda g: g.outcome == "partial"),
        compare("clear", found, lambda g: g.outcome == "clear"),
    )


_ORDER = {"worse": 0, "better": 1, "same": 2}


def headline(s: Split) -> str:
    """The group in plain words, a gap first (worse before better, partial before clear). A gap
    is only named when its 95% interval leaves out zero; otherwise the rates are the same."""
    p, c = s.partial, s.clear
    if p.gap is None or c.gap is None:
        return "Not enough blunders on both sides to compare yet."
    if p.verdict == c.verdict == "same":
        return f"When they blunder you do as well as your opponents. {_partial(p)} {_clear(c)}"
    if _ORDER[c.verdict] < _ORDER[p.verdict]:
        return f"{_clear(c, lead=True)} {_partial(p)}"
    return f"{_partial(p, lead=True)} {_clear(c, also=p.verdict == c.verdict)}"


def _partial(p: Compare, lead: bool = False) -> str:
    if p.verdict == "same":
        return f"Partial misses are the same as theirs: {p.yours.rate:.1%} vs {p.theirs.rate:.1%}."
    facts = _facts(p, "10-20")
    if p.verdict == "worse":  # always the lead: partial goes first on a tie
        return f"When they blunder you often take only part of it: {facts}."
    if lead:
        return (
            f"When they blunder you take only part of it less often than your opponents: {facts}."
        )
    return f"You take only part of it less often than they do: {facts}."


def _clear(c: Compare, lead: bool = False, also: bool = False) -> str:
    if c.verdict == "same":
        return f"Clear misses are the same as theirs: {c.yours.rate:.1%} vs {c.theirs.rate:.1%}."
    facts = _facts(c, "20 or more")
    more = "more" if c.verdict == "worse" else "less"
    if lead:
        return f"When they blunder you miss it outright {more} often than your opponents: {facts}."
    if also:
        return f"You also miss it outright {more} often: {facts}."
    return f"You miss it outright {more} often than they do: {facts}."


def _facts(c: Compare, band: str) -> str:
    """Both rates and the gap with its interval. Only for a gap that clears zero: a verdict
    other than same always has a gap and an interval."""
    low, high = c.interval
    return (
        f"your reply gave back {band} points after {c.yours.rate:.1%} of their blunders (your "
        f"opponents: {c.theirs.rate:.1%}), {100 * c.gap:+.1f} points (95% interval "
        f"{100 * low:+.1f} to {100 * high:+.1f})"
    )


def _when(g: Gift) -> str:
    return g.game.played_at.isoformat() if g.game.played_at else ""


def costliest(misses: Iterable[Gift], limit: int) -> list[Gift]:
    """The misses that gave back the most (the reply's loss), newest first among equals."""
    return sorted(misses, key=lambda g: (g.reply.wp_loss, _when(g)), reverse=True)[:limit]


def trend(found: Sequence[Gift], size: int = TREND_GIFTS) -> list[Compare]:
    """Your rate against theirs over time, oldest first: stretches of consecutive games holding
    `size` of your gifts each, counted back from the newest game (the oldest may hold fewer).
    Both rates in a stretch come from the same games, so a change in the mix of speeds or
    openings moves both."""
    dated = sorted(
        (g for g in found if g.game.played_at),
        key=lambda g: (_when(g), g.game.id, g.reply.ply),
        reverse=True,
    )
    stretches: list[list[Gift]] = []
    current: list[Gift] = []
    for i, g in enumerate(dated):
        current.append(g)
        last_of_game = i + 1 == len(dated) or dated[i + 1].game.id != g.game.id
        if last_of_game and sum(x.yours for x in current) >= size:
            stretches.append(current)
            current = []
    if any(x.yours for x in current):
        stretches.append(current)
    return [compare(_span(s), s) for s in reversed(stretches)]


def _span(stretch: Sequence[Gift]) -> str:
    """'2025-07-02 to 2025-08-15' for a stretch listed newest first."""
    first, last = stretch[-1].game.played_at, stretch[0].game.played_at
    return f"{first:%Y-%m-%d} to {last:%Y-%m-%d}"


@dataclass(slots=True)
class Family:
    """An opening where you clearly miss more than your opponents do (its own 95% interval leaves
    out zero), with your costliest misses there."""

    compare: Compare
    examples: list[Gift] = field(default_factory=list)


@dataclass(slots=True)
class Report:
    groups: list[Split]  # bullet (moves 1-10), then blitz and rapid (and slower: group_names)
    # The groups where you take only part of their blunders more often than your opponents
    # (`partial_focus`, their labels), and your partial misses there that gave back the most.
    partial_focus: list[str]
    partials: list[Gift]
    # The rest count a miss as partial or clear together (Gift.missed).
    kinds: list[Compare]  # by the punishing move: capture, check, quiet (labels are KINDS keys)
    trend: list[Compare]
    # Where the gap comes from, inside the groups where you miss more than your opponents
    # (`focus`, their labels): the openings where you clearly miss more too. None without such a
    # group. Your costliest misses come from those groups, or from all of them when there is none.
    focus: list[str]
    families: list[Family]
    worst: list[Gift]

    @property
    def gifts(self) -> int:
        return sum(s.chances for s in self.groups)

    @property
    def lessons(self) -> list[str]:
        """Only for a kind of punishment you miss clearly more often than your opponents do."""
        return [LESSONS[c.label] for c in self.kinds if c.verdict == "worse"]

    @property
    def partial_lesson(self) -> str | None:
        """Take the whole gift: only where you take part of it more often than your opponents."""
        return LESSONS["partial"] if self.partial_focus else None


def report(
    games: Sequence[Game],
    pairs: Iterable[tuple[MoveFact, MoveFact]],
    *,
    families: int = 4,
    examples: int = 2,
    worst: int = 3,
    partials: int = 3,
) -> Report:
    """Everything above over `games`. Openings are only looked at inside a group where you miss
    (partial and clear together) more than your opponents, and one is listed only when its own
    gap clears zero as well: a raw excess would list 1-of-1 samples. They are ranked by
    `Compare.excess`, so the first are where most of the gap comes from, not just where you play
    most. Partial misses are shown only from a group whose partial gap clears zero."""
    found = gifts(games, pairs)
    names = group_names(found)
    by_group = {key: [g for g in found if g.group == key] for key in names}
    splits = {key: split(names[key], gs) for key, gs in by_group.items()}
    worse = [key for key, gs in by_group.items() if compare(names[key], gs).verdict == "worse"]
    taken_part = [key for key, s in splits.items() if s.partial.verdict == "worse"]
    focused = [g for g in found if g.group in worse]
    by_family: dict[str, list[Gift]] = {}
    for g in focused:
        if family := g.game.opening_family:
            by_family.setdefault(family, []).append(g)
    standing_out = [
        c for c in (compare(f, gs) for f, gs in by_family.items()) if c.verdict == "worse"
    ]
    misses = [g for g in (focused if worse else found) if g.yours and g.missed]
    partial_misses = [
        g for g in found if g.group in taken_part and g.yours and g.outcome == "partial"
    ]
    return Report(
        groups=list(splits.values()),
        partial_focus=[names[key] for key in taken_part],
        partials=costliest(partial_misses, partials),
        kinds=[compare(kind, (g for g in found if g.kind == kind)) for kind in KINDS],
        trend=trend(found),
        focus=[names[key] for key in worse],
        families=[
            Family(c, costliest((g for g in misses if g.game.opening_family == c.label), examples))
            for c in sorted(standing_out, key=lambda c: -c.excess)[:families]
        ],
        worst=costliest(misses, worst),
    )
