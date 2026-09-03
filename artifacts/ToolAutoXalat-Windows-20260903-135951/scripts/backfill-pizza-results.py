from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import sqlite3
import sys

import cv2


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCANNER_DIR = PROJECT_ROOT / "services" / "scanner"
sys.path.insert(0, str(SCANNER_DIR / "src"))

from detector import HistoryDetector  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Find historical first-slot Pizza results that were classified as another item."
    )
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    config = json.loads((SCANNER_DIR / "config.json").read_text(encoding="utf-8"))
    detector = HistoryDetector(
        SCANNER_DIR / "assets" / "templates",
        config["history"],
        config["detection"],
    )
    native_pizza = cv2.imread(
        str(SCANNER_DIR / "assets" / "templates" / "PizzaIcon.png"),
        cv2.IMREAD_COLOR,
    )
    if native_pizza is None:
        raise RuntimeError("Cannot load PizzaIcon.png")

    database_path = PROJECT_ROOT / "data" / "greedy_stats.db"
    connection = sqlite3.connect(database_path, timeout=30)
    connection.row_factory = sqlite3.Row
    rows = connection.execute(
        """
        SELECT id, item_code, sequence_json, capture_path
        FROM results
        WHERE capture_path IS NOT NULL
          AND item_code <> 'PIZZA'
          AND detection_reason = 'sequence_shift'
        ORDER BY id
        """
    ).fetchall()

    crop_left = int(config["history"]["crop"]["x"])
    center_x = int(config["history"]["slot_centers_x"][0]) - crop_left
    half_size = int(config["history"]["slot_half_size"])
    candidates: list[dict] = []
    for row in rows:
        capture_path = Path(row["capture_path"])
        if not capture_path.is_file():
            continue
        image = cv2.imread(str(capture_path), cv2.IMREAD_COLOR)
        if image is None:
            continue
        center_y = image.shape[0] // 2
        slot = image[
            center_y - half_size : center_y + half_size,
            center_x - half_size : center_x + half_size,
        ]
        native_score = detector._best_template_score(slot, [native_pizza])
        if native_score < 0.50:
            continue
        detection = detector._classify_slot(slot, 0, center_x, center_y)
        if detection.code != "PIZZA":
            continue
        candidates.append(
            {
                "id": int(row["id"]),
                "oldItemCode": row["item_code"],
                "confidence": detection.confidence,
                "sequence": json.loads(row["sequence_json"]),
                "capturePath": row["capture_path"],
            }
        )

    backup_path = None
    if args.apply and candidates:
        backup_dir = PROJECT_ROOT / "data" / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup_path = backup_dir / (
            "greedy_stats_before_pizza_backfill_"
            + datetime.now().strftime("%Y%m%d_%H%M%S")
            + ".db"
        )
        backup_connection = sqlite3.connect(backup_path)
        connection.backup(backup_connection)
        backup_connection.close()

        with connection:
            for candidate in candidates:
                sequence = candidate["sequence"]
                if sequence:
                    sequence[0] = "PIZZA"
                connection.execute(
                    """
                    UPDATE results
                    SET item_code = 'PIZZA',
                        item_name = 'Nổ Pizza',
                        category = 'SPECIAL',
                        confidence = ?,
                        sequence_json = ?,
                        detection_reason = 'pizza_backfill:' || detection_reason
                    WHERE id = ?
                    """,
                    (
                        candidate["confidence"],
                        json.dumps(sequence, ensure_ascii=False),
                        candidate["id"],
                    ),
                )

    print(
        json.dumps(
            {
                "mode": "apply" if args.apply else "dry-run",
                "scanned": len(rows),
                "updated": len(candidates) if args.apply else 0,
                "backup": str(backup_path) if backup_path else None,
                "candidates": [
                    {
                        "id": item["id"],
                        "oldItemCode": item["oldItemCode"],
                        "pizzaConfidence": round(item["confidence"], 4),
                        "capturePath": item["capturePath"],
                    }
                    for item in candidates
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    connection.close()


if __name__ == "__main__":
    main()
