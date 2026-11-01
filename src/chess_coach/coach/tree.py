"""Your openings as a tree of the moves actually played in your games, for learning them.

A node is a position reached by a sequence of moves from the start; an edge is a move played
there, with how often, how you scored after it and what it cost by the stored engine facts. Where
the opponent went wrong, the position after their move holds the punishment: the engine's best
answer there, and how you actually answered in your games. Nothing here runs the engine.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from statistics import median

import chess

from chess_coach.engine.evaluation import BLUNDER_WP_LOSS, INACCURACY_WP_LOSS, MISTAKE_WP_LOSS
from chess_coach.models import Color, Game, MoveFact
from chess_coach.patterns.features import result_of

TREE_PLIES = 16  # eight moves each
MIN_GAMES = 5  # an opening needs this many games to be worth a tree
PUNISH_WP_LOSS = INACCURACY_WP_LOSS  # an opponent move losing this much is worth punishing
PUNISH_MIN_CP = 50  # ...when the right answer leaves you at least this much better

_POINTS = {"win": 1.0, "draw": 0.5, "loss": 0.0}


@dataclass(slots=True)
class Node:
    fen: str
    ply: int
    games: list[str] = field(default_factory=list)
    facts: list[MoveFact] = field(default_factory=list)  # the moves played from here
    edges: dict[str, Edge] = field(default_factory=dict)

    @property
    def best(self) -> MoveFact | None:
        """A fact carrying the engine's usual choice here (its best move and score)."""
        if not self.facts:
            return None
        uci, _ = Counter(f.best_move for f in self.facts).most_common(1)[0]
        return next(f for f in self.facts if f.best_move == uci)


@dataclass(slots=True)
class Edge:
    uci: str
    san: str
    by_user: bool
    child: Node
    facts: list[MoveFact] = field(default_factory=list)

    @property
    def wp_loss(self) -> float:
        return sum(f.wp_loss for f in self.facts) / len(self.facts)

    @property
    def cp_loss(self) -> float:
        return sum(f.cp_loss for f in self.facts) / len(self.facts)

    @property
    def verdict(self) -> str:
        loss = self.wp_loss
        if loss >= BLUNDER_WP_LOSS:
            return "blunder"
        if loss >= MISTAKE_WP_LOSS:
            return "mistake"
        if loss >= INACCURACY_WP_LOSS:
            return "inaccuracy"
        best = self.facts[0].best_move
        return "best" if best == self.uci else "good"

    @property
    def punishable(self) -> bool:
        answer = self.child.best  # your move after theirs, scored for you
        return (
            not self.by_user
            and self.wp_loss >= PUNISH_WP_LOSS
            and answer is not None
            and answer.eval_before >= PUNISH_MIN_CP
        )


def build(games: Sequence[Game], moves: Iterable[MoveFact], plies: int = TREE_PLIES) -> Node:
    """The tree of the first `plies` moves of these games (all the user's, one colour)."""
    by_game: dict[str, list[MoveFact]] = {}
    for m in moves:
        if m.ply <= plies:
            by_game.setdefault(m.game_id, []).append(m)
    root = Node(fen=chess.STARTING_FEN, ply=0)
    start = chess.Board().board_fen()
    for game in games:
        facts = sorted(by_game.get(game.id, []), key=lambda m: m.ply)
        if not facts or facts[0].ply != 1 or chess.Board(facts[0].fen_before).board_fen() != start:
            continue  # not from the standard start (Chess960, a set-up position) or not analysed
        node = root
        node.games.append(game.id)
        for fact in facts:
            if fact.ply != node.ply + 1:
                break
            node.facts.append(fact)
            edge = node.edges.get(fact.uci)
            if edge is None:
                board = chess.Board(fact.fen_before)
                board.push(chess.Move.from_uci(fact.uci))
                child = Node(fen=board.fen(), ply=node.ply + 1)
                edge = Edge(fact.uci, fact.san, fact.is_user, child)
                node.edges[fact.uci] = edge
            edge.facts.append(fact)
            node = edge.child
            node.games.append(game.id)
    return root


def walk(root: Node, path: Sequence[str]) -> Node | None:
    """The node after these moves (UCI), or None when the path leaves your games."""
    node = root
    for uci in path:
        edge = node.edges.get(uci)
        if edge is None:
            return None
        node = edge.child
    return node


def score(game_ids: Iterable[str], results: dict[str, str]) -> float | None:
    """Your score (0-1) over these games."""
    points = [_POINTS[results[g]] for g in game_ids if results.get(g) in _POINTS]
    return sum(points) / len(points) if points else None


def scored(game_ids: Iterable[str], results: dict[str, str]) -> int:
    """How many of these games have a result that counts (bullet results don't)."""
    return sum(results.get(g) in _POINTS for g in game_ids)


def at_move_ten(game_ids: Iterable[str], evals: Mapping[str, int]) -> tuple[int, int] | None:
    """(how many of these games got to your 10th move, your median eval there), from a {game id:
    eval} map (repertoire.evals_after_ten); None when none did. The tree itself stops at move 8."""
    found = [evals[g] for g in game_ids if g in evals]
    return (len(found), round(median(found))) if found else None


@dataclass(slots=True)
class Opening:
    family: str
    color: str
    games: int
    score: float | None  # None when no result counts (only bullet games)
    scored: int  # the games the score is over


def openings(games: Sequence[Game], min_games: int = MIN_GAMES, top: int = 8) -> list[Opening]:
    """Your most-played opening families with each colour, most games first."""
    groups: dict[tuple[str, Color], list[Game]] = {}
    for g in games:
        if g.analyzed_depth and g.opening_family:
            groups.setdefault((g.opening_family, g.user_color), []).append(g)
    out = []
    for color in (Color.WHITE, Color.BLACK):
        mine = [(family, gs) for (family, c), gs in groups.items() if c is color]
        mine = [x for x in mine if len(x[1]) >= min_games]
        for family, gs in sorted(mine, key=lambda x: (-len(x[1]), x[0]))[:top]:
            results = {g.id: result_of(g) for g in gs}
            out.append(
                Opening(
                    family, color.value, len(gs), score(results, results), scored(results, results)
                )
            )
    return out


def mistakes(root: Node, min_games: int = 2) -> list[tuple[list[str], Edge]]:
    """Every punishable opponent move seen in `min_games`+ games, as (path to it, its edge), most
    often first. The path ends with their move, so you're to move: find the punishment."""
    out: list[tuple[list[str], Edge]] = []

    def visit(node: Node, path: list[str]) -> None:
        for edge in node.edges.values():
            here = [*path, edge.uci]
            if edge.punishable and len(edge.facts) >= min_games:
                out.append((here, edge))
            visit(edge.child, here)

    visit(root, [])
    return sorted(out, key=lambda pe: (-len(pe[1].facts), len(pe[0])))
