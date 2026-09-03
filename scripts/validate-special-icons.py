from __future__ import annotations

import argparse
import heapq
import json
from pathlib import Path
import sys

import cv2
import numpy as np


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCANNER_DIR = PROJECT_ROOT / "services" / "scanner"
sys.path.insert(0, str(SCANNER_DIR / "src"))

from detector import HistoryDetector  # noqa: E402


def slot_from_history_crop(
    image: np.ndarray,
    index: int,
    centers: list[int],
    half_size: int,
) -> np.ndarray:
    center_x = centers[index]
    center_y = image.shape[0] // 2
    return image[
        max(0, center_y - half_size) : min(image.shape[0], center_y + half_size),
        max(0, center_x - half_size) : min(image.shape[1], center_x + half_size),
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
    parser = argparse.ArgumentParser()
    parser.add_argument("--all", action="store_true", help="Scan every saved result crop")
    parser.add_argument("--top", type=int, default=20)
    args = parser.parse_args()
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
    )
    if not args.all:
        regular_paths = regular_paths[:30]
    crop_left = int(config["history"]["crop"]["x"])
    slot_centers = [
        int(center) - crop_left for center in config["history"]["slot_centers_x"]
    ]
    half_size = int(config["history"]["slot_half_size"])
    pizza_variants = detector.templates["PIZZA"]
    salad_variants = detector.templates["SALAD"]
    if args.all:
        # The full archive scan uses the native uploaded icons as a fast
        # candidate finder. Promising crops are re-checked at every scale later.
        pizza_variants = [
            cv2.imread(
                str(SCANNER_DIR / "assets" / "templates" / "PizzaIcon.png"),
                cv2.IMREAD_COLOR,
            )
        ]
        salad_variants = [
            cv2.imread(
                str(SCANNER_DIR / "assets" / "templates" / "SalatIcon.png"),
                cv2.IMREAD_COLOR,
            )
        ]
    maximum_regular_special_score = -1.0
    maximum_regular = None
    checked_slots = 0
    pizza_candidates: list[tuple[float, str, int, float]] = []
    for image_path in regular_paths:
        image = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
        if image is None or image.shape[1] < 550:
            continue
        for index in range(8):
            slot = slot_from_history_crop(image, index, slot_centers, half_size)
            pizza = detector._best_template_score(slot, pizza_variants)
            salad = detector._best_template_score(slot, salad_variants)
            candidate = (pizza, str(image_path), index + 1, salad)
            if len(pizza_candidates) < max(1, args.top):
                heapq.heappush(pizza_candidates, candidate)
            elif pizza > pizza_candidates[0][0]:
                heapq.heapreplace(pizza_candidates, candidate)
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
            "topPizzaCandidates": [
                {
                    "path": path,
                    "slot": slot,
                    "pizzaScore": round(pizza, 4),
                    "saladScore": round(salad, 4),
                }
                for pizza, path, slot, salad in sorted(pizza_candidates, reverse=True)
            ],
        },
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))

    if uploaded["PIZZA"]["classifiedAs"] != "PIZZA":
        raise SystemExit("Pizza template self-check failed")
    if uploaded["SALAD"]["classifiedAs"] != "SALAD":
        raise SystemExit("Salad template self-check failed")
    if (
        not args.all
        and maximum_regular_special_score >= config["detection"]["minimum_confidence"]
    ):
        raise SystemExit("Special templates produce a false positive on regular history")


if __name__ == "__main__":
    main()
