#!/usr/bin/env python3
"""komicount - offline Go (weiqi/baduk) game checker and scorer.

A game is a plain-text record. komicount replays every move under the rules
of Go (captures, suicide, positional superko and alternating colours), then
scores the final position under Japanese (territory) or Chinese (area) rules
with exact half-point komi, using Fraction arithmetic.

Game file format (one directive or move per line, '#' starts a comment):

    size 9                  board size, 5 to 25 (default 19)
    komi 6.5                a multiple of 0.5 (default 6.5)
    rules japanese          japanese or chinese (default japanese)
    handicap 2              0, or 2 to 9 standard star points, Black placed first
    setup B C3 G7           extra stones placed before play
    name Friendly game      free text
    1 B D4                  a move: optional number, colour, then point or pass
    2 W pass
    dead D4 F6              stones to treat as dead when scoring
    resign W                White resigns (this overrides the score)
    result B+3.5            the expected result, checked by the check command

Commands:
    lint      check the file and every move, with line-numbered errors
    show      draw the board after N moves (default: the whole game)
    groups    list the groups on the final board with their liberties
    score     draw the ownership map and show the score breakdown
    check     compare the computed result with the file's result line
    stats     count moves, passes, captures, groups and atari
    sgf       export the game as an SGF string
    handicap  print the standard handicap points for a board size

Exit codes: 0 success, 1 problems found (errors or a result mismatch),
2 usage or file error.
"""

import argparse
import re
import sys
from dataclasses import dataclass, field
from fractions import Fraction

COLUMNS = "ABCDEFGHJKLMNOPQRSTUVWXYZ"  # Go boards skip the letter I
SGF_LETTERS = "abcdefghijklmnopqrstuvwxyz"
EMPTY, BLACK, WHITE = ".", "B", "W"
OTHER = {BLACK: WHITE, WHITE: BLACK}
COLOUR_NAMES = {BLACK: "Black", WHITE: "White"}
STONE_SYMBOL = {EMPTY: ".", BLACK: "X", WHITE: "O"}
DIRECTIVES = ("size", "komi", "rules", "handicap", "setup", "dead", "resign", "result", "name")
RULESETS = ("japanese", "chinese")
RESULT_RE = re.compile(r"(B|W)\+(R|\d+(\.5)?)", re.IGNORECASE)
MOVE_NUMBER_RE = re.compile(r"\d+\.?")
MIN_SIZE, MAX_SIZE, DEFAULT_SIZE = 5, 25, 19
DEFAULT_KOMI = Fraction(13, 2)


@dataclass
class Placement:
    """A stone (or a dead-stone mark when colour is EMPTY) on a point."""

    line: int
    colour: str
    r: int
    c: int


@dataclass
class Move:
    line: int
    colour: str
    point: tuple | None  # (row, col); None means a pass


@dataclass
class Game:
    size: int = DEFAULT_SIZE
    komi: Fraction = DEFAULT_KOMI
    rules: str = "japanese"
    handicap: int = 0
    handicap_line: int = 0
    name: str = ""
    resign: str | None = None
    result: str | None = None
    setup: list = field(default_factory=list)
    dead: list = field(default_factory=list)
    moves: list = field(default_factory=list)


class IllegalMove(ValueError):
    """A move that breaks the rules of Go."""


class Board:
    """A square grid of '.', 'B' and 'W', with row 0 at the top."""

    def __init__(self, size):
        self.size = size
        self.grid = [[EMPTY] * size for _ in range(size)]

    def copy(self):
        clone = Board(self.size)
        clone.grid = [row[:] for row in self.grid]
        return clone

    def neighbours(self, r, c):
        for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            nr, nc = r + dr, c + dc
            if 0 <= nr < self.size and 0 <= nc < self.size:
                yield nr, nc

    def group(self, r, c):
        """Return (stones, liberties) for the group that contains (r, c)."""
        colour = self.grid[r][c]
        stones, liberties, stack = {(r, c)}, set(), [(r, c)]
        while stack:
            pr, pc = stack.pop()
            for nr, nc in self.neighbours(pr, pc):
                value = self.grid[nr][nc]
                if value == EMPTY:
                    liberties.add((nr, nc))
                elif value == colour and (nr, nc) not in stones:
                    stones.add((nr, nc))
                    stack.append((nr, nc))
        return stones, liberties

    def groups(self):
        """Return (colour, stones, liberties) for every group on the board."""
        seen, found = set(), []
        for r in range(self.size):
            for c in range(self.size):
                if self.grid[r][c] != EMPTY and (r, c) not in seen:
                    stones, liberties = self.group(r, c)
                    seen |= stones
                    found.append((self.grid[r][c], stones, liberties))
        return found

    def remove(self, cells):
        for r, c in cells:
            self.grid[r][c] = EMPTY

    def key(self):
        return "".join("".join(row) for row in self.grid)


def point_name(r, c, size):
    return f"{COLUMNS[c]}{size - r}"


def parse_point(text, size):
    """Turn a point such as 'D4' into (row, col), with row 0 at the top."""
    upper = text.upper()
    if len(upper) < 2 or upper[0] not in COLUMNS or not upper[1:].isdigit():
        raise ValueError(f"'{text}' is not a point such as D4")
    number = int(upper[1:])
    col = COLUMNS.index(upper[0])
    if col >= size or not 1 <= number <= size:
        raise ValueError(f"point '{text}' is off the {size}x{size} board")
    return size - number, col


def handicap_points(count, size):
    """Standard star points for handicap stones, in placement order."""
    if count not in range(2, 10):
        raise ValueError("handicap must be between 2 and 9")
    if size < 7:
        raise ValueError("handicap needs a board of at least 7x7")
    near = 3 if size >= 13 else 2
    far = size - 1 - near
    mid = size // 2
    tr, bl, tl, br = (near, far), (far, near), (near, near), (far, far)
    left, right = (mid, near), (mid, far)
    top, bottom = (near, mid), (far, mid)
    centre = (mid, mid)
    layouts = {
        2: [tr, bl],
        3: [tr, bl, tl],
        4: [tr, bl, tl, br],
        5: [tr, bl, tl, br, centre],
        6: [tr, bl, tl, br, left, right],
        7: [tr, bl, tl, br, left, right, centre],
        8: [tr, bl, tl, br, left, right, top, bottom],
        9: [tr, bl, tl, br, left, right, top, bottom, centre],
    }
    return layouts[count]


def play_stone(board, r, c, colour):
    """Return (new_board, captured_cells) for a legal move, or raise IllegalMove."""
    if board.grid[r][c] != EMPTY:
        raise IllegalMove("the point already has a stone")
    trial = board.copy()
    trial.grid[r][c] = colour
    captured = set()
    for nr, nc in trial.neighbours(r, c):
        if trial.grid[nr][nc] == OTHER[colour]:
            stones, liberties = trial.group(nr, nc)
            if not liberties:
                captured |= stones
    trial.remove(captured)
    _, liberties = trial.group(r, c)
    if not liberties:
        raise IllegalMove("suicide, the stone would have no liberties")
    return trial, captured


@dataclass
class Replay:
    board: Board
    initial: Board
    snapshots: list
    captures: dict
    issues: list
    plays: int
    passes: int


def replay(game):
    """Place the handicap and setup stones, then play every move in order."""
    board = Board(game.size)
    issues = []
    if game.handicap:
        try:
            for r, c in handicap_points(game.handicap, game.size):
                board.grid[r][c] = BLACK
        except ValueError as exc:
            issues.append((game.handicap_line, str(exc)))
    for stone in game.setup:
        if board.grid[stone.r][stone.c] != EMPTY:
            issues.append((stone.line, f"{point_name(stone.r, stone.c, game.size)} already has a stone"))
        else:
            board.grid[stone.r][stone.c] = stone.colour
    initial = board.copy()
    seen = {board.key()}
    captures = {BLACK: 0, WHITE: 0}
    snapshots = []
    plays = passes = 0
    previous = None
    first = WHITE if game.handicap >= 2 else BLACK
    for move in game.moves:
        expected = first if previous is None else OTHER[previous]
        if move.colour != expected:
            issues.append((move.line, f"expected {COLOUR_NAMES[expected]} to move"))
            break
        if move.point is None:
            passes += 1
        else:
            r, c = move.point
            try:
                new_board, captured = play_stone(board, r, c, move.colour)
            except IllegalMove as exc:
                issues.append((move.line, f"illegal move: {exc}"))
                break
            if new_board.key() in seen:
                issues.append((move.line, "illegal move: this position has occurred before (superko)"))
                break
            seen.add(new_board.key())
            board = new_board
            captures[move.colour] += len(captured)
            plays += 1
        previous = move.colour
        snapshots.append(board.copy())
    return Replay(board, initial, snapshots, captures, issues, plays, passes)


def _point_or_issue(token, size, line, issues):
    try:
        return parse_point(token, size)
    except ValueError as exc:
        issues.append((line, str(exc)))
        return None


def parse_game(text):
    """Read a game file. Returns (Game, issues) where issues are (line, message)."""
    game = Game()
    issues = []
    setup_lines, dead_lines, move_lines = [], [], []
    for number, raw in enumerate(text.splitlines(), 1):
        body = raw.split("#", 1)[0].strip()
        if not body:
            continue
        tokens = body.split()
        head = tokens[0].lower()
        args = tokens[1:]
        arg = args[0] if args else ""
        if head in DIRECTIVES:
            if head == "size":
                if len(args) == 1 and arg.isdigit() and MIN_SIZE <= int(arg) <= MAX_SIZE:
                    game.size = int(arg)
                else:
                    issues.append((number, f"size must be a whole number from {MIN_SIZE} to {MAX_SIZE}"))
            elif head == "komi":
                try:
                    komi = Fraction(arg)
                except (ValueError, ZeroDivisionError):
                    komi = None
                if len(args) != 1 or komi is None or (komi * 2).denominator != 1 or abs(komi) > 100:
                    issues.append((number, "komi must be a multiple of 0.5 such as 6.5"))
                else:
                    game.komi = komi
            elif head == "rules":
                if len(args) == 1 and arg.lower() in RULESETS:
                    game.rules = arg.lower()
                else:
                    issues.append((number, "rules must be japanese or chinese"))
            elif head == "handicap":
                if len(args) == 1 and arg.isdigit() and (int(arg) == 0 or 2 <= int(arg) <= 9):
                    game.handicap = int(arg)
                    game.handicap_line = number
                else:
                    issues.append((number, "handicap must be 0, or a number from 2 to 9"))
            elif head == "name":
                game.name = " ".join(args)
            elif head == "resign":
                if len(args) == 1 and arg.upper() in (BLACK, WHITE):
                    game.resign = arg.upper()
                else:
                    issues.append((number, "resign must name the side that resigns: B or W"))
            elif head == "result":
                value = " ".join(args)
                if value.lower() == "jigo":
                    game.result = "jigo"
                elif RESULT_RE.fullmatch(value):
                    game.result = value.upper()
                else:
                    issues.append((number, "result must look like B+3.5, W+R, B+12 or jigo"))
            elif head == "setup":
                if len(args) >= 2 and arg.upper() in (BLACK, WHITE):
                    setup_lines.append((number, arg.upper(), args[1:]))
                else:
                    issues.append((number, "setup needs a colour (B or W) and at least one point"))
            elif head == "dead":
                if args:
                    dead_lines.append((number, args))
                else:
                    issues.append((number, "dead needs at least one point"))
            continue
        index = 0
        numbered = None
        if MOVE_NUMBER_RE.fullmatch(tokens[0]):
            numbered = int(tokens[0].rstrip("."))
            index = 1
        colour = tokens[index].upper() if index < len(tokens) else ""
        if colour not in (BLACK, WHITE):
            issues.append((number, f"unrecognised line, '{tokens[0]}' is not a directive or a move"))
        elif len(tokens) != index + 2:
            issues.append((number, "a move is a colour followed by one point or 'pass'"))
        else:
            move_lines.append((number, numbered, colour, tokens[index + 1]))

    for number, colour, tokens in setup_lines:
        for token in tokens:
            point = _point_or_issue(token, game.size, number, issues)
            if point:
                game.setup.append(Placement(number, colour, *point))
    for number, tokens in dead_lines:
        for token in tokens:
            point = _point_or_issue(token, game.size, number, issues)
            if point:
                game.dead.append(Placement(number, EMPTY, *point))
    for index, (number, numbered, colour, token) in enumerate(move_lines, 1):
        if numbered is not None and numbered != index:
            issues.append((number, f"move number {numbered} should be {index}"))
        if token.lower() == "pass":
            point = None
        else:
            point = _point_or_issue(token, game.size, number, issues)
            if point is None:
                continue
        game.moves.append(Move(number, colour, point))
    return game, issues


def analyse(text):
    """Parse and replay a game. Returns (Game, Replay, sorted issues)."""
    game, issues = parse_game(text)
    rep = replay(game)
    issues = issues + rep.issues
    for mark in game.dead:
        if rep.board.grid[mark.r][mark.c] == EMPTY:
            issues.append((mark.line, f"dead point {point_name(mark.r, mark.c, game.size)} has no stone"))
    issues.sort(key=lambda item: item[0])
    return game, rep, issues


def territories(board):
    """Map each empty point to its owner (BLACK, WHITE or None for neutral)."""
    owners, seen = {}, set()
    for r in range(board.size):
        for c in range(board.size):
            if board.grid[r][c] != EMPTY or (r, c) in seen:
                continue
            seen.add((r, c))
            region, touching, stack = [(r, c)], set(), [(r, c)]
            while stack:
                pr, pc = stack.pop()
                for nr, nc in board.neighbours(pr, pc):
                    value = board.grid[nr][nc]
                    if value != EMPTY:
                        touching.add(value)
                    elif (nr, nc) not in seen:
                        seen.add((nr, nc))
                        region.append((nr, nc))
                        stack.append((nr, nc))
            owner = touching.pop() if len(touching) == 1 else None
            for cell in region:
                owners[cell] = owner
    return owners


def format_number(value):
    value = Fraction(value)
    if value.denominator == 1:
        return str(value.numerator)
    return f"{value.numerator / value.denominator:.1f}"


def describe_margin(margin):
    if margin > 0:
        return f"B+{format_number(margin)}"
    if margin < 0:
        return f"W+{format_number(-margin)}"
    return "jigo"


@dataclass
class Score:
    rules: str
    komi: Fraction
    black_parts: list
    white_parts: list
    margin: Fraction
    result: str
    owners: dict
    dead_cells: set
    dame: int


def score_game(game, rep):
    """Score a clean replay. Dead stones are removed before counting."""
    board = rep.board.copy()
    dead_cells = {(mark.r, mark.c) for mark in game.dead}
    dead = {BLACK: 0, WHITE: 0}
    for r, c in dead_cells:
        if board.grid[r][c] != EMPTY:
            dead[board.grid[r][c]] += 1
    board.remove(dead_cells)
    alive = {BLACK: 0, WHITE: 0}
    for row in board.grid:
        for value in row:
            if value in alive:
                alive[value] += 1
    owners = territories(board)
    terr = {BLACK: 0, WHITE: 0}
    dame = 0
    for owner in owners.values():
        if owner:
            terr[owner] += 1
        else:
            dame += 1
    if game.rules == "japanese":
        black_parts = [("territory", terr[BLACK]), ("prisoners", rep.captures[BLACK]), ("dead white", dead[WHITE])]
        white_parts = [("territory", terr[WHITE]), ("prisoners", rep.captures[WHITE]), ("dead black", dead[BLACK])]
    else:
        black_parts = [("stones", alive[BLACK]), ("territory", terr[BLACK])]
        white_parts = [("stones", alive[WHITE]), ("territory", terr[WHITE])]
    black_points = sum(value for _, value in black_parts)
    white_points = sum(value for _, value in white_parts)
    margin = black_points - white_points - game.komi
    if game.resign:
        result = f"{OTHER[game.resign]}+R"
    else:
        result = describe_margin(margin)
    return Score(game.rules, game.komi, black_parts, white_parts, margin, result, owners, dead_cells, dame)


def board_cells(board, last=None):
    cells = [[STONE_SYMBOL[value] for value in row] for row in board.grid]
    if last is not None:
        r, c = last
        cells[r][c] = cells[r][c].lower()
    return cells


def score_cells(board, score):
    cells = []
    for r, row in enumerate(board.grid):
        line = []
        for c, value in enumerate(row):
            if value != EMPTY:
                symbol = STONE_SYMBOL[value]
                line.append(symbol.lower() if (r, c) in score.dead_cells else symbol)
            else:
                owner = score.owners.get((r, c))
                line.append({BLACK: "+", WHITE: "-", None: "?"}[owner])
        cells.append(line)
    return cells


def render(cells, size):
    header = "   " + " ".join(COLUMNS[:size])
    lines = [header]
    for r in range(size):
        label = f"{size - r:>2}"
        lines.append(f"{label} " + " ".join(cells[r]) + f" {label}")
    lines.append(header)
    return "\n".join(lines)


def describe_group(colour, stones, liberties, size):
    names = ", ".join(point_name(r, c, size) for r, c in sorted(stones))
    atari = " ATARI" if len(liberties) == 1 else ""
    return f"{COLOUR_NAMES[colour]:<6} {len(stones):>3} stones {len(liberties):>3} liberties{atari}  {names}"


def sgf_escape(text):
    return text.replace("\\", "\\\\").replace("]", "\\]")


def to_sgf(game, rep, result):
    def sgf_point(r, c):
        return SGF_LETTERS[c] + SGF_LETTERS[r]

    props = [f"SZ[{game.size}]", "GM[1]", "FF[4]", "CA[UTF-8]", f"KM[{format_number(game.komi)}]",
             f"RU[{game.rules.capitalize()}]", f"RE[{result}]"]
    if game.name:
        props.append(f"GN[{sgf_escape(game.name)}]")
    if game.handicap:
        props.append(f"HA[{game.handicap}]")
    for colour in (BLACK, WHITE):
        points = [sgf_point(r, c) for r, row in enumerate(rep.initial.grid)
                  for c, value in enumerate(row) if value == colour]
        if points:
            props.append(f"A{colour}" + "".join(f"[{point}]" for point in points))
    lines = ["(;" + "".join(props)]
    for move in game.moves:
        value = "" if move.point is None else sgf_point(*move.point)
        lines.append(f";{move.colour}[{value}]")
    return "\n".join(lines) + ")\n"


def load(path):
    try:
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
    except OSError as exc:
        print(f"komicount: cannot read {path}: {exc.strerror}", file=sys.stderr)
        raise SystemExit(2)
    return analyse(text)


def clean_analysis(path):
    """Return (game, replay) or None after printing the problems."""
    game, rep, issues = load(path)
    if issues:
        for line, message in issues:
            print(f"{path}:{line}: error: {message}")
        print(f"{len(issues)} problem(s) found in {path}")
        return None
    return game, rep


def cmd_lint(args):
    game, rep, issues = load(args.file)
    if issues:
        for line, message in issues:
            print(f"{args.file}:{line}: error: {message}")
        print(f"{len(issues)} problem(s) found in {args.file}")
        return 1
    print(f"{args.file}: OK, {game.size}x{game.size}, {rep.plays} plays and {rep.passes} passes")
    return 0


def cmd_show(args):
    loaded = clean_analysis(args.file)
    if loaded is None:
        return 1
    game, rep = loaded
    total = len(rep.snapshots)
    move = total if args.move is None else args.move
    if not 0 <= move <= total:
        print(f"komicount: --move must be between 0 and {total}", file=sys.stderr)
        return 2
    if move == 0:
        board, last = rep.initial, None
    else:
        board, last = rep.snapshots[move - 1], game.moves[move - 1].point
    print(f"After move {move} of {total} (last move highlighted in lower case)")
    print(render(board_cells(board, last), game.size))
    return 0


def cmd_groups(args):
    loaded = clean_analysis(args.file)
    if loaded is None:
        return 1
    game, rep = loaded
    groups = sorted(rep.board.groups(), key=lambda item: (item[0], -len(item[1])))
    if not groups:
        print("No stones on the final board.")
    for colour, stones, liberties in groups:
        print(describe_group(colour, stones, liberties, game.size))
    return 0


def cmd_score(args):
    loaded = clean_analysis(args.file)
    if loaded is None:
        return 1
    game, rep = loaded
    score = score_game(game, rep)
    print(f"Rules: {game.rules.capitalize()}   Komi: {format_number(game.komi)}")
    print(render(score_cells(rep.board, score), game.size))
    print("Territory: + black, - white, ? neutral. Dead stones shown in lower case.")
    for name, parts, total in (("Black", score.black_parts, sum(v for _, v in score.black_parts)),
                               ("White", score.white_parts, sum(v for _, v in score.white_parts))):
        detail = " + ".join(f"{label} {value}" for label, value in parts)
        print(f"{name}: {detail} = {total}")
    print(f"Neutral points: {score.dame}")
    print(f"Result: {score.result}" + (f" ({COLOUR_NAMES[game.resign]} resigned)" if game.resign else ""))
    return 0


def cmd_check(args):
    loaded = clean_analysis(args.file)
    if loaded is None:
        return 1
    game, rep = loaded
    if game.result is None:
        print(f"{args.file}: no result line to check against")
        return 1
    computed = score_game(game, rep).result
    if computed.upper() != game.result.upper():
        print(f"MISMATCH: {args.file} records {game.result}, computed {computed}")
        return 1
    print(f"OK: {args.file} result {computed} matches")
    return 0


def cmd_stats(args):
    loaded = clean_analysis(args.file)
    if loaded is None:
        return 1
    game, rep = loaded
    groups = rep.board.groups()
    alive = {BLACK: 0, WHITE: 0}
    for row in rep.board.grid:
        for value in row:
            if value in alive:
                alive[value] += 1
    atari = sum(1 for _, _, liberties in groups if len(liberties) == 1)
    print(f"Moves: {len(game.moves)} ({rep.plays} plays, {rep.passes} passes)")
    print(f"Handicap stones: {game.handicap}")
    print(f"Captures: Black took {rep.captures[BLACK]}, White took {rep.captures[WHITE]}")
    print(f"Stones on the final board: Black {alive[BLACK]}, White {alive[WHITE]}")
    print(f"Groups: {len(groups)} ({atari} in atari)")
    return 0


def cmd_sgf(args):
    loaded = clean_analysis(args.file)
    if loaded is None:
        return 1
    game, rep = loaded
    print(to_sgf(game, rep, score_game(game, rep).result), end="")
    return 0


def cmd_handicap(args):
    try:
        points = handicap_points(args.count, args.size)
    except ValueError as exc:
        print(f"komicount: {exc}", file=sys.stderr)
        return 2
    board = Board(args.size)
    for r, c in points:
        board.grid[r][c] = BLACK
    print("Handicap points: " + ", ".join(point_name(r, c, args.size) for r, c in points))
    print(render(board_cells(board), args.size))
    return 0


def build_parser():
    parser = argparse.ArgumentParser(prog="komicount", description="Offline Go game checker and scorer.")
    commands = parser.add_subparsers(dest="command", required=True)
    simple = {
        "lint": (cmd_lint, "check the file and every move"),
        "groups": (cmd_groups, "list final groups with liberties"),
        "score": (cmd_score, "score the final position"),
        "check": (cmd_check, "compare the computed result with the result line"),
        "stats": (cmd_stats, "count moves, captures and groups"),
        "sgf": (cmd_sgf, "export the game as SGF"),
    }
    for name, (function, help_text) in simple.items():
        sub = commands.add_parser(name, help=help_text)
        sub.add_argument("file", help="game file")
        sub.set_defaults(run=function)
    show = commands.add_parser("show", help="draw the board after N moves")
    show.add_argument("file", help="game file")
    show.add_argument("--move", type=int, default=None, help="move count to show (default: all)")
    show.set_defaults(run=cmd_show)
    handicap = commands.add_parser("handicap", help="print standard handicap points")
    handicap.add_argument("count", type=int, help="number of handicap stones, 2 to 9")
    handicap.add_argument("size", type=int, nargs="?", default=DEFAULT_SIZE, help="board size (default 19)")
    handicap.set_defaults(run=cmd_handicap)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    return args.run(args)


if __name__ == "__main__":
    sys.exit(main())
