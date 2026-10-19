import contextlib
import io
import sys
import tempfile
import unittest
from fractions import Fraction
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import komicount as kc  # noqa: E402

EXAMPLE = ROOT / "examples" / "practice-9x9.txt"

# A 5x5 ko shape. Setup stones are placed before Black plays D3, which
# captures C3. White's recapture at C3 would recreate the starting position,
# which positional superko forbids.
KO_GAME = """\
size 5
setup B C4 B3 C2
setup W D4 C3 E3 D2
1 B D3
2 W C3
"""


def analyse_text(text):
    return kc.analyse(text)


class ParsingTests(unittest.TestCase):
    def test_parse_point_uses_go_coordinates(self):
        # Row 0 is the top line, so D4 on a 9x9 board is row 5, column 3.
        self.assertEqual(kc.parse_point("D4", 9), (5, 3))
        self.assertEqual(kc.parse_point("j9", 9), (0, 8))

    def test_parse_point_rejects_i_and_off_board(self):
        with self.assertRaises(ValueError):
            kc.parse_point("I4", 9)
        with self.assertRaises(ValueError):
            kc.parse_point("A10", 9)

    def test_bad_lines_report_their_line_numbers(self):
        text = "size 9\nkomi 6.3\nfoo\n1 B Z9\n"
        _, _, issues = analyse_text(text)
        self.assertEqual(sorted(line for line, _ in issues), [2, 3, 4])

    def test_komi_must_be_a_half_point(self):
        _, _, issues = analyse_text("size 9\nkomi 6.3\n")
        self.assertEqual(issues[0][0], 2)
        self.assertIn("multiple of 0.5", issues[0][1])

    def test_move_numbers_must_be_in_order(self):
        _, _, issues = analyse_text("size 5\n1 B A1\n3 W B2\n")
        self.assertTrue(any("move number 3 should be 2" in msg for _, msg in issues))


class RulesTests(unittest.TestCase):
    def test_capture_removes_the_stone_and_counts_prisoners(self):
        text = "size 5\n1 B E5\n2 W A1\n3 B A2\n4 W pass\n5 B B1\n"
        _, rep, issues = analyse_text(text)
        self.assertEqual(issues, [])
        self.assertEqual(rep.captures["B"], 1)
        self.assertEqual(rep.board.grid[4][0], kc.EMPTY)

    def test_suicide_is_rejected(self):
        text = "size 5\n1 B A2\n2 W C3\n3 B B1\n4 W A1\n"
        _, _, issues = analyse_text(text)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0][0], 5)
        self.assertIn("suicide", issues[0][1])

    def test_positional_superko_blocks_immediate_ko_recapture(self):
        _, rep, issues = analyse_text(KO_GAME)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0][0], 5)
        self.assertIn("superko", issues[0][1])
        self.assertEqual(rep.plays, 1)

    def test_colours_must_alternate(self):
        _, _, issues = analyse_text("size 5\n1 W C3\n")
        self.assertEqual(issues, [(2, "expected Black to move")])

    def test_handicap_stones_make_white_move_first(self):
        game, rep, issues = analyse_text("size 9\nhandicap 2\n1 W E5\n")
        self.assertEqual(issues, [])
        self.assertEqual(rep.board.grid[4][4], "W")
        self.assertEqual(rep.board.grid[2][6], "B")  # the TR star point


class ScoringTests(unittest.TestCase):
    def setUp(self):
        self.text = EXAMPLE.read_text(encoding="utf-8")

    def test_example_scores_japanese_territory_with_dead_stones(self):
        game, rep, issues = analyse_text(self.text)
        self.assertEqual(issues, [])
        score = kc.score_game(game, rep)
        # Black: 36 territory + 1 dead white stone. White: 27 territory.
        self.assertEqual(score.result, "B+3.5")
        self.assertEqual(score.margin, Fraction(7, 2))

    def test_example_scores_chinese_area(self):
        game, rep, _ = analyse_text(self.text.replace("rules japanese", "rules chinese"))
        score = kc.score_game(game, rep)
        # Black: 9 stones + 36 points. White: 9 stones + 27 points.
        self.assertEqual(score.result, "B+2.5")

    def test_resignation_overrides_the_score(self):
        game, rep, issues = analyse_text(self.text + "\nresign W\n")
        self.assertEqual(issues, [])
        self.assertEqual(kc.score_game(game, rep).result, "B+R")

    def test_margin_formatting(self):
        self.assertEqual(kc.describe_margin(Fraction(-7, 2)), "W+3.5")
        self.assertEqual(kc.describe_margin(Fraction(0)), "jigo")
        self.assertEqual(kc.describe_margin(Fraction(12)), "B+12")

    def test_check_command_passes_for_matching_result(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = kc.main(["check", str(EXAMPLE)])
        self.assertEqual(code, 0)
        self.assertIn("matches", out.getvalue())

    def test_check_command_fails_on_mismatch(self):
        bad = self.text.replace("result B+3.5", "result W+1.5")
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "bad.txt"
            target.write_text(bad, encoding="utf-8")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = kc.main(["check", str(target)])
        self.assertEqual(code, 1)
        self.assertIn("MISMATCH", out.getvalue())


class BoardTests(unittest.TestCase):
    def test_handicap_four_on_nineteen_uses_star_points(self):
        names = [kc.point_name(r, c, 19) for r, c in kc.handicap_points(4, 19)]
        self.assertEqual(names, ["Q16", "D4", "D16", "Q4"])

    def test_handicap_nine_gives_nine_distinct_points(self):
        points = kc.handicap_points(9, 19)
        self.assertEqual(len(points), 9)
        self.assertEqual(len(set(points)), 9)

    def test_group_liberties_on_an_open_board(self):
        _, rep, issues = analyse_text("size 5\n1 B C3\n")
        self.assertEqual(issues, [])
        stones, liberties = rep.board.group(2, 2)
        self.assertEqual(stones, {(2, 2)})
        self.assertEqual(len(liberties), 4)

    def test_sgf_export_has_root_properties_and_moves(self):
        game, rep, _ = analyse_text(EXAMPLE.read_text(encoding="utf-8"))
        sgf = kc.to_sgf(game, rep, "B+3.5")
        self.assertTrue(sgf.startswith("(;SZ[9]"))
        self.assertIn("KM[6.5]", sgf)
        self.assertIn(";B[ei]", sgf)  # E1 is column 4, row 8 counted from the top
        self.assertTrue(sgf.rstrip().endswith(")"))

    def test_sgf_escape_protects_brackets(self):
        self.assertEqual(kc.sgf_escape("a]b"), "a\\]b")


class CommandTests(unittest.TestCase):
    def test_lint_flags_a_dead_point_with_no_stone(self):
        _, _, issues = analyse_text("size 5\n1 B C3\ndead D4\n")
        self.assertEqual(issues, [(3, "dead point D4 has no stone")])

    def test_lint_command_returns_zero_for_the_example(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(kc.main(["lint", str(EXAMPLE)]), 0)
        self.assertIn("OK", out.getvalue())

    def test_show_command_draws_the_board(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(kc.main(["show", str(EXAMPLE), "--move", "1"]), 0)
        # Only E1 is played, so row 1 is four empty points, the lower-case last move, then empty points.
        self.assertIn(" ".join(["."] * 4 + ["x"] + ["."] * 4), out.getvalue())


if __name__ == "__main__":
    unittest.main()
