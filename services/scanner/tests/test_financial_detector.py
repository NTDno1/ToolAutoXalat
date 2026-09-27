from __future__ import annotations

import sys
from pathlib import Path
import unittest

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from financial_detector import (  # noqa: E402
    FinancialDetectionError,
    FinancialDetector,
    parse_money_text,
    parse_own_bet_text,
)


class FinancialDetectorTests(unittest.TestCase):
    def test_parse_plain_amount(self) -> None:
        self.assertEqual(1250, parse_money_text("1,250"))

    def test_parse_spaced_amount(self) -> None:
        self.assertEqual(8054360, parse_money_text("8 054 360"))

    def test_reject_empty_text(self) -> None:
        with self.assertRaises(FinancialDetectionError):
            parse_money_text("xu")

    def test_parse_explicit_own_bet_label(self) -> None:
        self.assertEqual(2, parse_own_bet_text("You: 2"))
        self.assertEqual(1250, parse_own_bet_text("you 1,250"))

    def test_ignore_multiplier_without_own_bet_label(self) -> None:
        self.assertIsNone(parse_own_bet_text("win 5 times 2"))

    def test_dimmed_dialog_skips_balance_ocr(self) -> None:
        detector = FinancialDetector({
            "enabled": True,
            "reference_width": 720,
            "unobstructed_header_region": {"x": 370, "y": 80, "width": 120, "height": 55},
            "minimum_header_median": 200,
        })
        frame = np.full((1600, 720, 3), 127, dtype=np.uint8)
        with self.assertRaisesRegex(FinancialDetectionError, "dimmed by a dialog"):
            detector.detect(frame)


if __name__ == "__main__":
    unittest.main()
