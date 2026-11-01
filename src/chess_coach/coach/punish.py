"""When they blunder: how often you miss the punishment for your opponents' blunders, next to
how often they miss it for yours.

A gift is a move that loses at least 20 points of win probability; the reply is the other
player's next move, and it misses the punishment when the engine tagged it `missed_tactic` (an
inaccuracy or worse right after a gift: engine/motifs.py), so nothing is re-derived here. Your
opponents' replies to your own gifts are the yardstick: players at your level with the same kind
of chance (Lichess Insights calls the two rates Opportunism and Luck). A gap only counts when its
95% interval (Newcombe) leaves out zero.

The owner's rules apply to the replying move, on both sides alike: in bullet only moves 1-10
count (features.counted) and replies made short on time don't (features.rushed). Bullet is
reported apart from blitz and rapid (rapid alone is too small); classical, correspondence and
unknown-speed games count with blitz and rapid, and then the group's label names them.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from chess_coach.coach.principles import allows
from chess_coach.models import Color, Game, MoveFact, Speed
from chess_coach.patterns.features import BULLET_MOVES, counted, is_bullet, punish_kind, rushed
from chess_coach.patterns.mining import newcombe, wilson_lower, wilson_upper

GIFT_WP_LOSS = 20.0  # the same bar as missed_tactic (engine/motifs.py)
TREND_GIFTS = 150  # their blunders you could punish, per stretch of the trend: about ±8 points
GROUPS = {"bullet": f"Bullet, moves 1-{BULLET_MOVES}", "slower": "Blitz and rapid"}
# Other speeds that count with blitz and rapid, named in the label when there are any.
OTHER_SPEEDS = {
    Speed.CLASSICAL: "classical",
    Speed.CORRESPONDENCE: "correspondence",
    Speed.UNKNOWN: "games of unknown speed",
}
KINDS = {"capture": "a capture", "check": "a check", "quiet": "a quiet move"}
# Only shown when you miss that kind of punishment clearly more often than your opponents do:
# everyone misses quiet punishments more than captures, so that alone is no lesson.
LESSONS = {
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
        return "missed_tactic" in self.reply.motifs

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


def compare(name: str, found: Iterable[Gift]) -> Compare:
    yours, theirs = [0, 0], [0, 0]
    for g in found:
        side = yours if g.yours else theirs
        side[0] += g.missed
        side[1] += 1
    return Compare(name, Rate(*yours), Rate(*theirs))


def headline(c: Compare) -> str:
    """Your rate, theirs, and whether the gap between them is real."""
    if c.gap is None or c.interval is None:
        return "Not enough blunders on both sides to compare yet."
    text = (
        f"You missed the punishment for {c.yours.rate:.1%} of your opponents' blunders "
        f"({c.yours.missed} of {c.yours.n}); they missed it for {c.theirs.rate:.1%} of yours "
        f"({c.theirs.missed} of {c.theirs.n})."
    )
    low, high = c.interval
    gap = f"{100 * c.gap:+.1f} points (95% interval {100 * low:+.1f} to {100 * high:+.1f})"
    if c.verdict == "worse":
        return f"{text} You miss it more often than your opponents do: {gap}."
    if c.verdict == "better":
        return f"{text} You miss it less often than your opponents do: {gap}."
    return f"{text} That's about the same as your opponents: {gap}, which includes zero."


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
    groups: list[Compare]  # bullet (moves 1-10), then blitz and rapid (and slower: group_names)
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
        return sum(c.yours.n + c.theirs.n for c in self.groups)

    @property
    def lessons(self) -> list[str]:
        """Only for a kind of punishment you miss clearly more often than your opponents do."""
        return [LESSONS[c.label] for c in self.kinds if c.verdict == "worse"]


def report(
    games: Sequence[Game],
    pairs: Iterable[tuple[MoveFact, MoveFact]],
    *,
    families: int = 4,
    examples: int = 2,
    worst: int = 3,
) -> Report:
    """Everything above over `games`. Openings are only looked at inside a group where you miss
    more than your opponents, and one is listed only when its own gap clears zero as well: a
    raw excess would list 1-of-1 samples. They are ranked by `Compare.excess`, so the first are
    where most of the gap comes from, not just where you play most."""
    found = gifts(games, pairs)
    names = group_names(found)
    groups = {
        key: compare(name, (g for g in found if g.group == key)) for key, name in names.items()
    }
    worse = [key for key, c in groups.items() if c.verdict == "worse"]
    focused = [g for g in found if g.group in worse]
    by_family: dict[str, list[Gift]] = {}
    for g in focused:
        if family := g.game.opening_family:
            by_family.setdefault(family, []).append(g)
    clear = [c for c in (compare(f, gs) for f, gs in by_family.items()) if c.verdict == "worse"]
    misses = [g for g in (focused if worse else found) if g.yours and g.missed]
    return Report(
        groups=list(groups.values()),
        kinds=[compare(kind, (g for g in found if g.kind == kind)) for kind in KINDS],
        trend=trend(found),
        focus=[names[key] for key in worse],
        families=[
            Family(c, costliest((g for g in misses if g.game.opening_family == c.label), examples))
            for c in sorted(clear, key=lambda c: -c.excess)[:families]
        ],
        worst=costliest(misses, worst),
    )
