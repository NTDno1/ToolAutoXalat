from pathlib import Path
from contextlib import closing
import sqlite3
import sys
import tempfile
import unittest


SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from database import ScannerDatabase  # noqa: E402


class ResultReconciliationTests(unittest.TestCase):
    def test_provisional_result_is_corrected_in_place(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "scanner.db"
            database = ScannerDatabase(path)
            result_id = database.insert_result(
                item_code="CAI",
                item_name="Cải",
                category="VEGETABLE",
                confidence=0.82,
                sequence=["CAI"] * 8,
                detection_reason="result_popup_pending_history",
                source_serial="test",
                round_number=10,
                round_local_date="2026-09-05",
            )

            verified_sequence = [
                "NGO", "CAI", "CAI", "CAI", "CAI", "CAI", "CAI", "CAI"
            ]
            database.reconcile_result(
                result_id=result_id,
                item_code="NGO",
                item_name="Ngô",
                category="VEGETABLE",
                confidence=0.91,
                sequence=verified_sequence,
                detection_reason="result_popup_corrected_by_history",
            )
            database.set_states(
                {
                    "scanner_status": "RUNNING",
                    "scanner_heartbeat_utc": "2026-09-05T00:00:00.000Z",
                    "last_result_id": str(result_id),
                }
            )

            with closing(sqlite3.connect(path)) as connection:
                row = connection.execute(
                    """
                    SELECT id, item_code, confidence, sequence_json,
                           detection_reason, round_number
                    FROM results WHERE id=?
                    """,
                    (result_id,),
                ).fetchone()
                states = dict(
                    connection.execute(
                        "SELECT state_key, state_value FROM scanner_state"
                    ).fetchall()
                )

            self.assertEqual(result_id, row[0])
            self.assertEqual("NGO", row[1])
            self.assertAlmostEqual(0.91, row[2])
            self.assertIn('"NGO"', row[3])
            self.assertEqual("result_popup_corrected_by_history", row[4])
            self.assertEqual(10, row[5])
            self.assertEqual("RUNNING", states["scanner_status"])
            self.assertEqual(str(result_id), states["last_result_id"])

    def test_verified_sequence_backfills_gaps_and_corrects_existing_rounds(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "scanner.db"
            database = ScannerDatabase(path)
            sequence = [
                "CAI", "XIEN", "NGO", "BO", "BANH_MI", "BANH_MI", "NGO", "BO"
            ]
            anchor_id = database.insert_result(
                item_code="CAI",
                item_name="Cai",
                category="VEGETABLE",
                confidence=0.9,
                sequence=sequence,
                detection_reason="result_popup_verified_history",
                source_serial="test",
                round_number=337,
                round_local_date="2026-09-05",
            )
            wrong_id = database.insert_result(
                item_code="CA_ROT",
                item_name="Ca rot",
                category="VEGETABLE",
                confidence=0.7,
                sequence=sequence,
                detection_reason="sequence_shift",
                source_serial="test",
                round_number=335,
                round_local_date="2026-09-05",
            )
            category_by_code = {
                "CAI": "VEGETABLE",
                "XIEN": "MEAT",
                "NGO": "VEGETABLE",
                "BO": "MEAT",
                "BANH_MI": "MEAT",
            }
            outcomes = [
                (code, code, category_by_code[code], 0.95) for code in sequence
            ]

            actions = database.synchronize_verified_sequence(
                source_serial="test",
                round_local_date="2026-09-05",
                latest_round=337,
                outcomes=outcomes,
                sequence=sequence,
                anchor_result_id=anchor_id,
                round_interval_seconds=40,
            )

            with closing(sqlite3.connect(path)) as connection:
                rows = connection.execute(
                    """
                    SELECT id, round_number, item_code, detection_reason, detected_at_utc
                    FROM results
                    WHERE source_serial='test' AND round_local_date='2026-09-05'
                    ORDER BY round_number DESC
                    """
                ).fetchall()

            self.assertEqual(sequence, [row[2] for row in rows])
            self.assertEqual(list(range(337, 329, -1)), [row[1] for row in rows])
            self.assertEqual(wrong_id, next(row[0] for row in rows if row[1] == 335))
            self.assertEqual(
                "history_corrected_verified_8",
                next(row[3] for row in rows if row[1] == 335),
            )
            detected_times = [row[4] for row in rows]
            self.assertEqual(detected_times, sorted(detected_times, reverse=True))
            self.assertEqual(6, sum(action["action"] == "inserted" for action in actions))
            self.assertEqual(1, sum(action["action"] == "corrected" for action in actions))

            second_actions = database.synchronize_verified_sequence(
                source_serial="test",
                round_local_date="2026-09-05",
                latest_round=337,
                outcomes=outcomes,
                sequence=sequence,
                anchor_result_id=anchor_id,
                round_interval_seconds=40,
            )
            self.assertEqual([], second_actions)


if __name__ == "__main__":
    unittest.main()
