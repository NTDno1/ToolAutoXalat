from pathlib import Path
import sys
import unittest


SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from countdown_detector import CountdownDetectionError, parse_countdown_text  # noqa: E402


class CountdownTextTests(unittest.TestCase):
    def test_reads_normal_game_labels(self):
        self.assertEqual(20, parse_countdown_text("20s"))
        self.assertEqual(18, parse_countdown_text("18s"))
        self.assertEqual(5, parse_countdown_text("5s"))
        self.assertEqual(0, parse_countdown_text("0s"))

    def test_removes_suffix_s_misread_as_five(self):
        self.assertEqual(23, parse_countdown_text("235"))
        self.assertEqual(13, parse_countdown_text("135s"))

    def test_recovers_eight_when_ocr_reads_both_glyphs_as_s(self):
        self.assertEqual(8, parse_countdown_text("Ss"))

    def test_rejects_values_outside_game_range(self):
        with self.assertRaises(CountdownDetectionError):
            parse_countdown_text("72s")


if __name__ == "__main__":
    unittest.main()
