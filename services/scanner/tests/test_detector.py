from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import numpy as np


SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from detector import DetectionError, HistoryDetector, find_sequence_shift  # noqa: E402


class SequenceShiftTests(unittest.TestCase):
    def test_identical_repeating_sequence_is_not_a_shift(self):
        sequence = ["CAI", "NGO", "CA_CHUA", "NGO", "CA_CHUA", "CAI", "BO", "CAI"]
        self.assertEqual(0, find_sequence_shift(sequence, sequence))

    def test_duplicate_new_item_is_detected_from_all_eight_slots(self):
        previous = ["CAI", "CAI", "NGO", "BO", "CA_ROT", "XIEN", "DUI", "CA_CHUA"]
        current = ["CAI", "CAI", "CAI", "NGO", "BO", "CA_ROT", "XIEN", "DUI"]
        self.assertEqual(1, find_sequence_shift(previous, current))

    def test_two_missed_results_are_recovered(self):
        previous = ["NGO", "BO", "CA_ROT", "XIEN", "DUI", "CA_CHUA", "CAI", "BANH_MI"]
        current = ["CAI", "CAI", "NGO", "BO", "CA_ROT", "XIEN", "DUI", "CA_CHUA"]
        self.assertEqual(2, find_sequence_shift(previous, current))

    def test_single_accidental_overlap_is_not_accepted(self):
        previous = ["CAI", "BANH_MI", "NGO", "DUI", "CA_CHUA", "XIEN", "BO", "CA_ROT"]
        current = ["NGO", "NGO", "NGO", "NGO", "NGO", "NGO", "NGO", "CAI"]
        self.assertEqual(0, find_sequence_shift(previous, current))


class PizzaOverrideTests(unittest.TestCase):
    def setUp(self):
        self.detector = HistoryDetector.__new__(HistoryDetector)
        self.detector.pizza_override_confidence = 0.58

    def test_real_pizza_signal_overrides_accidental_cabbage_match(self):
        code, confidence, strongest_other, overridden = self.detector._rank_scores({
            "CAI": 0.756,
            "CA_CHUA": 0.639,
            "PIZZA": 0.612,
            "XIEN": 0.498,
        })
        self.assertEqual("PIZZA", code)
        self.assertAlmostEqual(0.612, confidence)
        self.assertAlmostEqual(0.756, strongest_other)
        self.assertTrue(overridden)

    def test_ordinary_item_below_pizza_threshold_is_unchanged(self):
        code, confidence, _, overridden = self.detector._rank_scores({
            "CAI": 0.938,
            "CA_CHUA": 0.730,
            "PIZZA": 0.474,
            "XIEN": 0.561,
        })
        self.assertEqual("CAI", code)
        self.assertAlmostEqual(0.938, confidence)
        self.assertFalse(overridden)


class PopupResultTests(unittest.TestCase):
    def setUp(self):
        self.detector = HistoryDetector.__new__(HistoryDetector)
        self.detector.reference_width = 720
        self.detector.reference_height = 1500
        self.detector.templates = {
            "NGO": [np.zeros((8, 8, 3), dtype=np.uint8)],
            "CAI": [np.zeros((8, 8, 3), dtype=np.uint8)],
        }
        self.frame = np.zeros((1500, 720, 3), dtype=np.uint8)
        self.config = {
            "result_icon": {"x": 250, "y": 900, "width": 220, "height": 230},
            "result_minimum_confidence": 0.65,
            "result_minimum_margin": 0.08,
        }

    def test_primary_popup_icon_is_classified(self):
        with patch.object(
            self.detector, "_best_template_score", side_effect=[0.90, 0.51]
        ):
            result = self.detector.detect_popup_result(self.frame, self.config)

        self.assertEqual("NGO", result.code)
        self.assertAlmostEqual(0.90, result.confidence)
        self.assertAlmostEqual(0.39, result.margin)
        self.assertEqual((230, 220, 3), result.crop.shape)

    def test_ambiguous_popup_icon_is_rejected(self):
        with patch.object(
            self.detector, "_best_template_score", side_effect=[0.70, 0.66]
        ):
            with self.assertRaises(DetectionError):
                self.detector.detect_popup_result(self.frame, self.config)


if __name__ == "__main__":
    unittest.main()
