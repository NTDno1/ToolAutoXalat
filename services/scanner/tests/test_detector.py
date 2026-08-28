from pathlib import Path
import sys
import unittest


SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from detector import find_sequence_shift  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
