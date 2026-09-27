from pathlib import Path
import sys
import unittest


SRC_DIR = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC_DIR))

from round_detector import RoundDetector  # noqa: E402


class RoundLabelParsingTests(unittest.TestCase):
    def test_reads_number_between_full_label_anchors(self):
        self.assertEqual(669, RoundDetector.parse_label("Today's 669 Round"))

    def test_accepts_common_apostrophe_and_today_ocr_variants(self):
        self.assertEqual(669, RoundDetector.parse_label("Today’s 669 Round"))
        self.assertEqual(669, RoundDetector.parse_label("Taday's 669 Round"))

    def test_rejects_unanchored_digits_that_can_create_false_prefix(self):
        self.assertIsNone(RoundDetector.parse_label("5 669 Round"))
        self.assertIsNone(RoundDetector.parse_label("5602"))


if __name__ == "__main__":
    unittest.main()
