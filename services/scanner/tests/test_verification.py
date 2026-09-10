from pathlib import Path
import sys
import unittest


SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from verification import StableSequenceVerifier  # noqa: E402


class StableSequenceVerifierTests(unittest.TestCase):
    def setUp(self):
        self.verifier = StableSequenceVerifier(
            required_confirmations=3, timeout_seconds=2.0
        )
        self.sequence = ["NGO", "CAI", "BO", "DUI", "XIEN", "CA_ROT", "CA_CHUA", "CAI"]

    def test_requires_three_identical_complete_scans(self):
        self.assertFalse(self.verifier.observe(self.sequence, 10.0))
        self.assertFalse(self.verifier.observe(self.sequence, 10.2))
        self.assertTrue(self.verifier.observe(self.sequence, 10.4))
        self.assertEqual(3, self.verifier.confirmations)

    def test_different_scan_restarts_confirmation(self):
        changed = ["CAI", *self.sequence[:7]]
        self.verifier.observe(self.sequence, 10.0)
        self.verifier.observe(self.sequence, 10.2)
        self.assertFalse(self.verifier.observe(changed, 10.4))
        self.assertEqual(1, self.verifier.confirmations)

    def test_timeout_restarts_confirmation(self):
        self.verifier.observe(self.sequence, 10.0)
        self.verifier.observe(self.sequence, 10.2)
        self.assertFalse(self.verifier.observe(self.sequence, 12.3))
        self.assertEqual(1, self.verifier.confirmations)

    def test_incomplete_history_is_never_confirmed(self):
        self.assertFalse(self.verifier.observe(self.sequence[:7], 10.0))
        self.assertEqual(0, self.verifier.confirmations)


if __name__ == "__main__":
    unittest.main()
