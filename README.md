# komicount

**Offline Go scorer: a plain-text game record in, rule-checked moves and an exact result out.**

komicount replays every move under the rules of Go (captures, suicide, positional superko and alternating colours). It then scores the final position under Japanese or Chinese rules, using exact half-point komi. Every error points at a line in your file. It needs only the Python 3 standard library and no network.

## Features

- **Plain-text game files** with directives for board size, komi, rules, handicap, setup stones, dead stones, resignation, and the expected result.
- **Line-numbered lint**: unknown directives, bad points, off-board stones, out-of-order move numbers, and wrong colour order are all reported as `file:line: error: ...`.
- **Full rule replay**: captures, suicide, and positional superko (no repeated board position, so simple ko and longer cycles are both caught).
- **Turn-order checks**: colours must alternate, with White moving first after a handicap.
- **Standard handicap placement** for 2 to 9 stones on 7x7 and larger boards, in the usual star-point order.
- **Japanese (territory) and Chinese (area) scoring**, with prisoners, dead stones and komi handled in exact `Fraction` arithmetic.
- **Dead-stone marking** at scoring time. Dead stones are removed and counted as prisoners or as area, as the ruleset requires.
- **Resignation** overrides the score and reports `B+R` or `W+R`.
- **Ownership map** showing black territory (`+`), white territory (`-`), neutral points (`?`) and dead stones (lower case).
- **Board snapshots**: draw the board after any number of moves, with the last move highlighted.
- **Group report**: every group on the final board with its stone count, liberties and atari warnings.
- **Stats**: plays, passes, prisoners per side, stones left on the board, and groups in atari.
- **CI-friendly check**: `check` compares the computed result with the `result` line and exits 1 on a mismatch.
- **SGF export** with setup stones, handicap, komi, result and game name, ready for other Go software.
- **Handicap helper** that prints the star points and a diagram for any board size.

## Quick start

A game file is plain text. The first lines of `examples/practice-9x9.txt` look like this:

```text
name Wall practice
size 9
komi 6.5
rules japanese
1 B E1
2 W F1
3 B E2
4 W F2
```

Black walls off column E, White walls off column F, and the file ends with a loose White stone at B5, marked `dead B5`, then `result B+3.5`. Run it from the project root:

```console
$ python3 komicount.py check examples/practice-9x9.txt
OK: examples/practice-9x9.txt result B+3.5 matches

$ python3 komicount.py lint examples/practice-9x9.txt
examples/practice-9x9.txt: OK, 9x9, 19 plays and 3 passes

$ python3 komicount.py score examples/practice-9x9.txt
...
Black: territory 36 + prisoners 0 + dead white 1 = 37
White: territory 27 + prisoners 0 + dead black 0 = 27
Neutral points: 0
Result: B+3.5
```

If you change `rules japanese` to `rules chinese`, the same game scores `B+2.5`, because Chinese area scoring counts the living stones too.

## Game file reference

| Line | Meaning |
| --- | --- |
| `size 9` | Board size, 5 to 25 (default 19) |
| `komi 6.5` | Komi, a multiple of 0.5 (default 6.5) |
| `rules japanese` | `japanese` or `chinese` (default `japanese`) |
| `handicap 4` | 0, or 2 to 9 standard star points; White then moves first |
| `setup B C3 G7` | Extra stones placed before play |
| `name ...` | Free text |
| `1 B D4` | A move: optional number, colour, then a point or `pass` |
| `dead D4 F6` | Stones to treat as dead when scoring |
| `resign W` | White resigns |
| `result B+3.5` | Expected result, checked by `check`. Also accepts `W+12`, `B+R` and `jigo` |

`#` starts a comment, and blank lines are ignored.

## Commands

```text
python3 komicount.py lint    FILE         check the file and every move
python3 komicount.py show    FILE [--move N]   draw the board after N moves
python3 komicount.py groups  FILE         list final groups, liberties and atari
python3 komicount.py score   FILE         ownership map and score breakdown
python3 komicount.py check   FILE         compare with the result line (CI gate)
python3 komicount.py stats   FILE         move, capture and group counts
python3 komicount.py sgf     FILE         export SGF to stdout
python3 komicount.py handicap COUNT [SIZE]   standard handicap points and diagram
```

Exit codes: `0` on success, `1` when the file has errors or a result mismatches, and `2` for usage or file errors.

## Requirements

- Python 3.10 or newer (the code uses `X | None` annotations).
- No third-party packages for the tool itself.
- `pytest` is optional. The tests also run with the standard `unittest` runner.

## How to run

```console
$ python3 komicount.py score examples/practice-9x9.txt
```

Run any command against your own file in the same way. The tool reads only the file you name and writes only to standard output.

## How to test

From the project root:

```console
$ python3 -m unittest discover -s tests -v
```

or, with pytest installed:

```console
$ python3 -m pytest -q tests
```

The tests cover point parsing, line-numbered errors, captures, suicide, superko, colour alternation, handicap placement, Japanese and Chinese scoring, resignation, the `check` gate, SGF output and the board renderer.

## Project layout

```text
komicount.py                 the tool (single file, standard library only)
examples/practice-9x9.txt    a worked 9x9 game with a dead stone
tests/test_komicount.py      unit tests
README.md
```

## Notes and limits

- Scoring follows the usual Japanese and Chinese conventions. The tool does not guess which stones are dead; you mark them with `dead`.
- Only the rules listed above are checked. Time controls, sealed moves and handicap komi conventions are outside the scope of this file format.
- Superko is positional: a board position may not repeat, whichever colour is to move.
