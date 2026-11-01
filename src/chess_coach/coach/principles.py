"""Plain chess reasons for why a move falls short, read from the board alone (no engine, no model).

Each check compares the position after your move with the position after the engine's move and
only speaks when they differ: a piece left hanging, castling given up, the king's pawn cover
loosened, the queen out early, development skipped, a centre pawn given away, pawns doubled or
isolated, a passed pawn allowed. `faults` describe your move and are safe to show before the
answer is revealed; `ideas` describe what the better move does, so they give it away.

Openings get a short note on what they are about (`opening_idea`), hand-written like the lessons
in coach.habits.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import chess

from chess_coach.engine.motifs import PIECE_VALUES
from chess_coach.engine.phases import phase_of
from chess_coach.models import Phase

CENTRE = (chess.D4, chess.E4, chess.D5, chess.E5)
OPENING_MOVES = 12  # development and early-queen checks apply up to this move
CENTRE_MOVES = 20  # centre advice stops here: later the position decides
MAX_FAULTS = 3
MAX_IDEAS = 2

_MINOR_HOME = {
    chess.WHITE: (chess.B1, chess.G1, chess.C1, chess.F1),
    chess.BLACK: (chess.B8, chess.G8, chess.C8, chess.F8),
}
_WING_FILES = (0, 1, 6, 7)  # a, b, g, h

OPENING_IDEAS: dict[str, str] = {
    "Modern Defense": (
        "Black lets White build a big centre and then attacks it: the bishop on g7 hits d4 and "
        "the long diagonal, and the breaks ...c5, ...e5 or ...d5 must come before White's centre "
        "gets rolling. Don't fall behind in development while waiting."
    ),
    "Pirc Defense": (
        "Black develops with ...d6, ...Nf6, ...g6, ...Bg7 and castles, then challenges the centre "
        "with ...e5 or ...c5. White often attacks fast (f4, or Be3, Qd2, Bh6), so castling and the "
        "central break can't wait."
    ),
    "Queen's Pawn Game": (
        "Queen's pawn openings are a fight over e4/e5 and the c-pawn break (c4 for White, ...c5 "
        "for Black): develop, castle, and don't hand over the centre for free."
    ),
    "Sicilian Defense": (
        "Black trades the c-pawn for White's d-pawn: an extra centre pawn and the half-open c-file "
        "in return for slower development. White plays for quick development and a kingside "
        "attack, Black for counterplay on the c-file and the ...d5 or ...b5 breaks."
    ),
    "Danish Gambit": (
        "White gives one or two pawns for a big lead in development and bishops aimed at f7 and "
        "g7. With White every tempo counts: develop and open lines before Black consolidates. With "
        "Black, giving material back (...d5) to catch up in development is often best."
    ),
    "French Defense": (
        "Black meets e4 with ...e6 and ...d5 and attacks White's pawn chain with ...c5 (and "
        "...f6). The light-squared bishop behind the pawns is Black's problem piece; White has "
        "more space and chances on the kingside."
    ),
    "Caro-Kann Defense": (
        "Black supports ...d5 with ...c6 for a sound structure and gets the light-squared bishop "
        "out before ...e6. It's about solid pawns and good endgames, so weaknesses need a reason."
    ),
    "Scotch Game": (
        "White opens the centre at once with d4: after ...exd4 Nxd4 White has easy development "
        "and space. Black hits the knight on d4 (...Nf6, ...Bc5, ...Qf6) and frees the game with "
        "...d5 when possible."
    ),
    "Blackmar-Diemer Gambit": (
        "White gives a pawn (d4, e4, f3) for the open f-file, quick development and a kingside "
        "attack. Slow moves let Black keep the pawn for nothing: White has to keep the pressure up."
    ),
    "Van't Kruijs Opening": (
        "1.e3 is quiet: develop normally and claim the centre with d4 or c4 rather than drift."
    ),
    "Center Game": (
        "White takes on d4 with the queen, which ...Nc6 chases with tempo. White usually goes "
        "Qe3, Nc3, Bd2 and castles long; Black develops fast and hits the queen and e4."
    ),
    "Scandinavian Defense": (
        "Black challenges e4 at once; after exd5 Qxd5 Nc3 the queen has to move again, so Black "
        "makes up the time with quick development (...Qa5 or ...Qd6, ...Nf6, ...c6, ...Bf5)."
    ),
    "Nimzowitsch Defense": (
        "After 1...Nc6 Black presses d4 and e5 with pieces; White can gain space with d4 and d5 "
        "to kick the knight, so Black needs ...d5 or ...e5 in time."
    ),
    "London System": (
        "White sets up d4, Bf4, e3, c3, Nf3, Bd3 and Nbd2 whatever Black does, then plays e4 or "
        "attacks with Ne5. Black aims for ...c5 and pressure on b2 and d4."
    ),
    "King's Pawn Game": (
        "Open games are about the centre (d4, d5, e5): develop knights and bishops toward it, "
        "castle early, and don't move a piece twice without a reason."
    ),
    "Zukertort Opening": (
        "1.Nf3 develops first and keeps the central pawn moves (d4, c4, e4) for later."
    ),
    "English Opening": (
        "White fights for d5 from the side with c4, often with g3 and Bg2 on the long diagonal. "
        "Piece placement and pawn breaks (d4, b4, f4) matter more than early tactics."
    ),
    "Owen Defense": (
        "Black fianchettoes the queen's bishop to hit e4 along the long diagonal, but still has to "
        "contest the centre (...e6, ...c5 or ...d5) or White's centre gets too strong."
    ),
    "Nimzo-Larsen Attack": (
        "White's bishop on b2 controls e5 and d4 from afar: the centre is fought for with pieces "
        "first and pawns later."
    ),
    "Italian Game": (
        "Both sides develop fast with bishops aimed at f7 and f2. White plans c3 and d4 (or a slow "
        "d3 with Re1 and Nbd2); Black keeps e5 solid and plays ...d6 or ...d5 at the right moment."
    ),
    "Ruy Lopez": (
        "White's bishop pressures the knight that defends e5, for a lasting edge in the centre; "
        "Black usually answers with ...a6, ...b5 and ...d6 and keeps e5."
    ),
    "King's Indian Defense": (
        "Black lets White take the centre, castles quickly and strikes with ...e5 (or ...c5), "
        "often followed by ...f5 and a kingside attack while White pushes on the queenside."
    ),
    "Philidor Defense": (
        "Black holds e5 with ...d6: solid but cramped, so keep e5 supported and give the pieces "
        "room."
    ),
    "Rat Defense": (
        "A flexible ...d6 set-up like the Pirc and Modern: develop, castle and pick the central "
        "break (...e5 or ...c5)."
    ),
    "Hungarian Opening": (
        "1.g3 puts the bishop on the long diagonal to control the centre from afar."
    ),
    "Mieses Opening": "1.d3 is quiet: develop first and claim the centre later.",
    "Ponziani Opening": (
        "White plays c3 to support d4; Black usually hits back quickly with ...d5 or ...Nf6."
    ),
    "Indian Defense": (
        "1...Nf6 controls e4 and keeps Black's pawn structure open to choose later."
    ),
    "Queen's Gambit": (
        "White offers the c-pawn to pull Black's d-pawn away from the centre; Black either holds "
        "d5 (...e6, ...c6) or takes and gives the pawn back later."
    ),
    "Slav Defense": (
        "Black holds d5 with ...c6 and keeps the light-squared bishop free to come out to f5 or g4."
    ),
    "Dutch Defense": (
        "...f5 controls e4 and prepares a kingside attack, at the cost of loosening Black's king."
    ),
    "Alekhine Defense": (
        "Black invites White's pawns forward to attack them later: the centre becomes a target "
        "if Black develops quickly."
    ),
    "Vienna Game": ("White develops Nc3 first and keeps f4 as an option to open the f-file."),
    "King's Gambit": (
        "White gives the f-pawn to open the f-file and build a big centre; Black can keep the "
        "pawn or give it back for quick development."
    ),
}


def opening_idea(family: str | None) -> str | None:
    """The note for an opening family; variations like "Danish Gambit Accepted" use the longest
    name they start with."""
    if not family:
        return None
    names = [name for name in OPENING_IDEAS if family.startswith(name)]
    return OPENING_IDEAS[max(names, key=len)] if names else None


@dataclass(slots=True)
class Reasons:
    faults: list[str] = field(default_factory=list)  # about your move; reveal nothing
    ideas: list[str] = field(default_factory=list)  # what the better move does

    def all(self) -> list[str]:
        return [*self.faults, *self.ideas]


def _after(board: chess.Board, move: chess.Move) -> chess.Board:
    after = board.copy(stack=False)
    after.push(move)
    return after


def hanging(board: chess.Board, color: chess.Color) -> dict[tuple[int, int], str]:
    """`color`'s pieces (knight or more) the side to move can take for free or for less, as
    {(piece type, square): how}. Pins count: only legal captures are looked at."""
    if board.turn == color or board.is_check():
        return {}
    cheapest: dict[int, int] = {}
    for move in board.legal_moves:
        if board.is_capture(move) and not board.is_en_passant(move):
            value = PIECE_VALUES[board.piece_type_at(move.from_square)] or 100  # king last
            cheapest[move.to_square] = min(value, cheapest.get(move.to_square, 100))
    out = {}
    for square, attacker in cheapest.items():
        piece = board.piece_at(square)
        if piece is None or piece.color != color or PIECE_VALUES[piece.piece_type] < 3:
            continue
        if not board.attackers(color, square):
            out[(piece.piece_type, square)] = "hanging (attacked and not defended)"
        elif attacker < PIECE_VALUES[piece.piece_type]:
            out[(piece.piece_type, square)] = "attacked by a cheaper piece"
    return out


def threatened(board: chess.Board, by: chess.Color) -> set[tuple[int, int]]:
    """The other side's pieces that `by` could win if it were `by`'s move: (piece type, square)."""
    turn = board.copy(stack=False)
    if turn.turn != by:
        turn.push(chess.Move.null())
    return set(hanging(turn, not by))


def doubled(board: chess.Board, color: chess.Color) -> set[int]:
    files = [chess.square_file(s) for s in board.pieces(chess.PAWN, color)]
    return {f for f in files if files.count(f) > 1}


def isolated(board: chess.Board, color: chess.Color) -> set[int]:
    files = {chess.square_file(s) for s in board.pieces(chess.PAWN, color)}
    return {f for f in files if f - 1 not in files and f + 1 not in files}


def passed(board: chess.Board, color: chess.Color) -> set[int]:
    """Squares of `color`'s passed pawns."""
    enemy = board.pieces(chess.PAWN, not color)
    step = 1 if color == chess.WHITE else -1
    out = set()
    for square in board.pieces(chess.PAWN, color):
        file, rank = chess.square_file(square), chess.square_rank(square)
        if not any(
            abs(chess.square_file(e) - file) <= 1 and (chess.square_rank(e) - rank) * step > 0
            for e in enemy
        ):
            out.add(square)
    return out


def minors_at_home(board: chess.Board, color: chess.Color) -> int:
    return sum(
        1
        for home in _MINOR_HOME[color]
        if (p := board.piece_at(home)) is not None
        and p.color == color
        and p.piece_type in (chess.KNIGHT, chess.BISHOP)
    )


def centre_pawns(board: chess.Board, color: chess.Color) -> int:
    return sum(chess.square_file(s) in (3, 4) for s in board.pieces(chess.PAWN, color))


def centre_control(board: chess.Board, color: chess.Color) -> int:
    return sum(len(board.attackers(color, s)) for s in CENTRE)


def _file(f: int) -> str:
    return chess.FILE_NAMES[f]


def _shelter_files(king: chess.Square) -> tuple[int, ...] | None:
    """The files of the pawns covering a castled king, or None when it isn't castled."""
    file = chess.square_file(king)
    if file >= 6:
        return (5, 6, 7)
    if file <= 2:
        return (0, 1, 2)
    return None


def explain(fen: str, played: str, best: str | None, *, theirs: bool = False) -> Reasons:
    """Why `played` (UCI) falls short of `best` in the position `fen`, in chess terms. With
    `theirs` it is the opponent's move: the faults speak of their pieces, and there are no ideas
    (their better move is not yours to learn)."""
    my, you, them = ("their", "they", "you") if theirs else ("your", "you", "them")
    board = chess.Board(fen)
    me = board.turn
    move = chess.Move.from_uci(played)
    if move not in board.legal_moves:
        return Reasons()
    better = chess.Move.from_uci(best) if best else None
    if better is not None and better not in board.legal_moves:
        better = None
    mine = _after(board, move)
    other = _after(board, better) if better is not None else None
    phase = phase_of(board)
    opening = phase is Phase.OPENING and board.fullmove_number <= OPENING_MOVES
    piece = board.piece_type_at(move.from_square)
    reasons = Reasons()
    faults = reasons.faults

    # -- material left en prise --------------------------------------------------------------
    left = hanging(mine, me)
    safe_anyway = hanging(other, me) if other is not None else hanging(_null(board), me)
    taken = board.piece_type_at(move.to_square) if board.is_capture(move) else None
    for (kind, square), how in sorted(left.items(), key=lambda kv: -PIECE_VALUES[kv[0][0]]):
        if (kind, square) in safe_anyway:
            continue
        if square == move.to_square and taken and PIECE_VALUES[taken] >= PIECE_VALUES[kind]:
            continue  # a trade: it took something worth as much
        faults.append(
            f"It leaves {my} {chess.piece_name(kind)} on {chess.square_name(square)} {how}."
        )
        break

    # -- king safety ---------------------------------------------------------------------------
    castles = better is not None and board.is_castling(better)
    if (
        board.has_castling_rights(me)
        and not mine.has_castling_rights(me)
        and not board.is_castling(move)
        and (other is None or other.has_castling_rights(me) or castles)
    ):
        faults.append(f"It gives up {my} right to castle, so {my} king may be stuck in the centre.")
    king = board.king(me)
    shelter = _shelter_files(king) if king is not None else None
    home_rank = 0 if me == chess.WHITE else 7
    if (
        shelter is not None
        and chess.square_rank(king) == home_rank
        and phase is not Phase.ENDGAME
        and piece == chess.PAWN
        and chess.square_file(move.from_square) in shelter
        and not board.is_capture(move)
        and not (
            better is not None
            and board.piece_type_at(better.from_square) == chess.PAWN
            and chess.square_file(better.from_square) in shelter
        )
    ):
        faults.append(
            f"It pushes the {_file(chess.square_file(move.from_square))}-pawn in front of {my} "
            "castled king, loosening its cover."
        )

    # -- development ---------------------------------------------------------------------------
    at_home = minors_at_home(board, me)
    if opening and at_home >= 2 and not board.is_capture(move) and not board.gives_check(move):
        best_piece = board.piece_type_at(better.from_square) if better is not None else None
        if piece == chess.QUEEN and best_piece != chess.QUEEN:
            faults.append(
                f"It brings {my} queen out while {at_home} of {my} minor pieces are still at "
                f"home: the queen gets chased and {you} lose time."
            )
        elif piece == chess.PAWN and chess.square_file(move.from_square) in _WING_FILES:
            faults.append(
                f"It spends a move on a wing pawn while {at_home} of {my} minor pieces are still "
                "at home."
            )
        elif minors_at_home(mine, me) >= at_home and not board.is_castling(move):
            if other is not None and minors_at_home(other, me) < at_home:
                faults.append(
                    f"It doesn't develop a piece, and {at_home} of {my} minor pieces are still "
                    "at home."
                )

    # -- the centre and the pawns --------------------------------------------------------------
    if centre_pawns(mine, me) < centre_pawns(board, me) and (
        other is None or centre_pawns(other, me) >= centre_pawns(board, me)
    ):
        faults.append(
            "It takes a pawn off the d- and e-files: centre pawns control the squares that "
            "matter most."
        )
    if other is not None:
        new_doubled = doubled(mine, me) - doubled(other, me) - doubled(board, me)
        new_isolated = isolated(mine, me) - isolated(other, me) - isolated(board, me)
        if new_doubled:
            faults.append(f"It doubles {my} pawns on the {_file(min(new_doubled))}-file.")
        if new_isolated:
            faults.append(
                f"It leaves {my} {_file(min(new_isolated))}-pawn isolated: no pawn beside it "
                "can defend it."
            )
        their_passed = passed(mine, not me) - passed(board, not me) - passed(other, not me)
        if their_passed:
            square = chess.square_name(min(their_passed))
            faults.append(f"It gives {them} a passed pawn on {square}.")
    del faults[MAX_FAULTS:]

    # -- what the better move does -------------------------------------------------------------
    if theirs or better is None or other is None:
        return reasons
    ideas = reasons.ideas
    if castles and not board.is_castling(move):
        ideas.append("The better move castles: get your king safe before anything else.")
    forcing = [
        word
        for word, yes in (
            ("a check", board.gives_check(better)),
            ("a capture", board.is_capture(better)),
        )
        if yes
    ]
    if other.is_checkmate():
        ideas.append("The better move is checkmate: before any move, look at every check.")
    elif forcing and not (board.gives_check(move) or board.is_capture(move)):
        ideas.append(
            f"The better move is {' and '.join(forcing)}: look at checks, captures and threats "
            "before quiet moves."
        )
    elif not other.is_check():
        hit = threatened(other, me) - threatened(mine, me)
        if hit:
            kind, square = max(hit, key=lambda ks: PIECE_VALUES[ks[0]])
            ideas.append(
                f"The better move attacks their {chess.piece_name(kind)} on "
                f"{chess.square_name(square)}, so they have to spend a move on it."
            )
    if opening and minors_at_home(other, me) < minors_at_home(mine, me):
        ideas.append("The better move develops a piece: get the knights and bishops out early.")
    if phase is not Phase.ENDGAME and board.fullmove_number <= CENTRE_MOVES:
        if centre_pawns(other, me) > centre_pawns(mine, me):
            ideas.append("The better move gets a pawn into the centre.")
        elif centre_control(other, me) >= centre_control(mine, me) + 2:
            ideas.append("The better move fights for the centre (d4, e4, d5, e5).")
    if phase is Phase.ENDGAME:
        best_piece = board.piece_type_at(better.from_square)
        if best_piece == chess.KING and piece != chess.KING:
            if _centre_distance(better.to_square) < _centre_distance(better.from_square):
                ideas.append(
                    "In the endgame the king is a strong piece: the better move brings it "
                    "toward the centre."
                )
        if (
            best_piece == chess.PAWN
            and better.from_square in passed(board, me)
            and not (piece == chess.PAWN and move.from_square in passed(board, me))
        ):
            ideas.append("The better move pushes your passed pawn: in endgames, passed pawns run.")
    del ideas[MAX_IDEAS:]
    return reasons


def allows(fen: str, punish: str) -> list[str]:
    """What the opponent's mistake lets you do, in chess terms: `fen` is the position after it
    (you to move) and `punish` the engine's answer (UCI). Empty when there's nothing to name."""
    board = chess.Board(fen)
    move = chess.Move.from_uci(punish)
    if move not in board.legal_moves:
        return []
    me, san = board.turn, board.san(move)
    after = _after(board, move)
    if after.is_checkmate():
        return [f"It allows {san}, checkmate."]
    if after.is_check():
        return [f"It lets {san} come with check."]
    hit = sorted(threatened(after, me) - threatened(board, me), key=lambda ks: -PIECE_VALUES[ks[0]])
    if not hit:
        return []
    names = [f"{chess.piece_name(kind)} on {chess.square_name(square)}" for kind, square in hit]
    if len(names) > 1:
        return [f"It lets {san} hit their {names[0]} and {names[1]} at once."]
    return [f"It lets {san} hit their {names[0]}."]


def _does(board: chess.Board, move: chess.Move, mate: int | None = None) -> str:
    """What `move` (legal in `board`) does to the side not moving, said to that side: "Nxd6,
    taking your queen on d6", "Qxf7#, checkmate", "Nd5, hitting your queen on c7"."""
    me, san = board.turn, board.san(move)
    after = _after(board, move)
    if after.is_checkmate():
        return f"{san}, checkmate"
    if mate is not None and mate > 0:
        return f"{san}, with mate in {mate}"
    if board.is_capture(move):
        kind = chess.PAWN if board.is_en_passant(move) else board.piece_type_at(move.to_square)
        square = move.to_square
        if board.is_en_passant(move):
            square += -8 if me == chess.WHITE else 8
        check = " with check" if after.is_check() else ""
        return f"{san}, taking your {chess.piece_name(kind)} on {chess.square_name(square)}{check}"
    hit = sorted(threatened(after, me) - threatened(board, me), key=lambda ks: -PIECE_VALUES[ks[0]])
    names = [f"{chess.piece_name(kind)} on {chess.square_name(square)}" for kind, square in hit]
    if after.is_check():
        return f"{san}, a check that also hits your {names[0]}" if names else f"{san}, with check"
    if len(names) > 1:
        return f"{san}, hitting your {names[0]} and {names[1]} at once"
    return f"{san}, hitting your {names[0]}" if names else san


def their_threat(
    fen: str, threat: str, *, last_san: str = "", last_uci: str = "", mate: int | None = None
) -> str | None:
    """What they threatened in `fen` (you to move), in chess terms: `threat` is the move they
    would play if you passed (UCI), `last_*` their move that made it. "Their Nf5 attacked your
    queen on d6". None when the threat isn't a legal move for them."""
    board = _null(chess.Board(fen))
    move = chess.Move.from_uci(threat)
    if move not in board.legal_moves:
        return None
    who = f"Their {last_san}" if last_san else "They"
    moved_to = chess.Move.from_uci(last_uci).to_square if last_uci else None
    taking = board.is_capture(move) and not board.is_en_passant(move)
    if taking and move.from_square == moved_to and not _after(board, move).is_checkmate():
        # The piece they just moved is the one attacking.
        name = chess.piece_name(board.piece_type_at(move.to_square))
        return f"{who} attacked your {name} on {chess.square_name(move.to_square)}"
    return f"{who} threatened {_does(board, move, mate)}"


def ignored_threat(
    fen: str,
    played: str,
    threat: str,
    *,
    last_san: str = "",
    last_uci: str = "",
    mate: int | None = None,
) -> str | None:
    """Why your move fell short when they had just made a threat: "Their Nf5 attacked your queen
    on d6, and your Rd8 left it there." `fen` is before your move, `played` your move and
    `threat` theirs had you passed (UCI)."""
    head = their_threat(fen, threat, last_san=last_san, last_uci=last_uci, mate=mate)
    board = chess.Board(fen)
    move = chess.Move.from_uci(played)
    if head is None or move not in board.legal_moves:
        return None
    taken = chess.Move.from_uci(threat)
    piece = board.piece_at(taken.to_square)
    left = (
        piece is not None
        and piece.color == board.turn
        and not _after(_null(board), taken).is_checkmate()
        and _after(board, move).piece_at(taken.to_square) == piece
    )
    tail = "left it there" if left else "didn't stop it"
    return f"{head}, and your {board.san(move)} {tail}."


def unsafe_move(fen: str, played: str, reply: str, *, mate: int | None = None) -> str | None:
    """Why your move fell short when nothing was threatened: "Nothing was threatened, but your
    Ng5 allows Qxg5, taking your knight on g5." `reply` is their best answer (UCI)."""
    board = chess.Board(fen)
    move = chess.Move.from_uci(played)
    if move not in board.legal_moves:
        return None
    after = _after(board, move)
    answer = chess.Move.from_uci(reply)
    if answer not in after.legal_moves:
        return None
    return (
        f"Nothing was threatened, but your {board.san(move)} allows {_does(after, answer, mate)}."
    )


def _null(board: chess.Board) -> chess.Board:
    """The position with the other side to move, to compare against when there's no best move."""
    passed_turn = board.copy(stack=False)
    passed_turn.push(chess.Move.null())
    return passed_turn


def _centre_distance(square: chess.Square) -> int:
    return min(chess.square_distance(square, c) for c in CENTRE)
