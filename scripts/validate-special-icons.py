from __future__ import annotations

import json
from pathlib import Path
import sys

import cv2
import numpy as np


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCANNER_DIR = PROJECT_ROOT / "services" / "scanner"
sys.path.insert(0, str(SCANNER_DIR / "src"))

from detector import HistoryDetector  # noqa: E402


def slot_from_history_crop(image: np.ndarray, index: int) -> np.ndarray:
    centers = [83, 147, 211, 275, 339, 403, 467, 531]
    center_x = centers[index]
    center_y = image.shape[0] // 2
    return image[
        max(0, center_y - 36) : min(image.shape[0], center_y + 36),
        max(0, center_x - 36) : min(image.shape[1], center_x + 36),
    ]


def padded_icon(path: Path) -> np.ndarray:
    icon = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if icon is None:
        raise RuntimeError(f"Cannot read {path}")
    canvas = np.full((72, 72, 3), icon[0, 0], dtype=np.uint8)
    top = (canvas.shape[0] - icon.shape[0]) // 2
    left = (canvas.shape[1] - icon.shape[1]) // 2
    canvas[top : top + icon.shape[0], left : left + icon.shape[1]] = icon
    return canvas


def main() -> None:
    config = json.loads((SCANNER_DIR / "config.json").read_text(encoding="utf-8"))
    detector = HistoryDetector(
        SCANNER_DIR / "assets" / "templates",
        config["history"],
        config["detection"],
    )

    uploaded = {}
    for code, filename in (("PIZZA", "PizzaIcon.png"), ("SALAD", "SalatIcon.png")):
        detection = detector._classify_slot(
            padded_icon(PROJECT_ROOT / filename), 0, 36, 36
        )
        uploaded[code] = {
            "classifiedAs": detection.code,
            "confidence": round(detection.confidence, 4),
            "margin": round(detection.margin, 4),
            "pizzaScore": round(detection.scores["PIZZA"], 4),
            "saladScore": round(detection.scores["SALAD"], 4),
        }

    regular_paths = sorted(
        (PROJECT_ROOT / "data" / "captures").glob("**/result_*.png"),
        key=lambda value: value.stat().st_mtime,
        reverse=True,
    )[:30]
    maximum_regular_special_score = -1.0
    maximum_regular = None
    checked_slots = 0
    for image_path in regular_paths:
        image = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
        if image is None or image.shape[1] < 550:
            continue
        for index in range(8):
            slot = slot_from_history_crop(image, index)
            pizza = detector._best_template_score(slot, detector.templates["PIZZA"])
            salad = detector._best_template_score(slot, detector.templates["SALAD"])
            score = max(pizza, salad)
            checked_slots += 1
            if score > maximum_regular_special_score:
                maximum_regular_special_score = score
                maximum_regular = {
                    "path": str(image_path),
                    "slot": index + 1,
                    "pizzaScore": round(pizza, 4),
                    "saladScore": round(salad, 4),
                }

    result = {
        "uploadedIcons": uploaded,
        "regularRegression": {
            "images": len(regular_paths),
            "slots": checked_slots,
            "maximumSpecialScore": round(maximum_regular_special_score, 4),
            "maximum": maximum_regular,
        },
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))

    if uploaded["PIZZA"]["classifiedAs"] != "PIZZA":
        raise SystemExit("Pizza template self-check failed")
    if uploaded["SALAD"]["classifiedAs"] != "SALAD":
        raise SystemExit("Salad template self-check failed")
    if maximum_regular_special_score >= config["detection"]["minimum_confidence"]:
        raise SystemExit("Special templates produce a false positive on regular history")


if __name__ == "__main__":
    main()
