"""Read the game's participation dialog from one ADB screenshot.

This command never sends screen input. Auto-play uses it to verify the
diamond fee and round before sending a separate confirmation tap.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
import time
import unicodedata

import cv2
import numpy as np
import pytesseract


def _ascii_text(value: str) -> str:
    return "".join(
        char for char in unicodedata.normalize("NFKD", value.lower())
        if not unicodedata.combining(char)
    )


def _ocr(frame: np.ndarray, x: int, y: int, width: int, height: int) -> str:
    crop = frame[y : y + height, x : x + width]
    enlarged = cv2.resize(crop, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
    return pytesseract.image_to_string(enlarged, config="--psm 6", timeout=3).strip()


def inspect_dialog(frame: np.ndarray) -> dict:
    if frame is None or frame.ndim != 3:
        raise ValueError("Invalid phone screenshot")
    normalized = cv2.resize(frame, (720, 1600), interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(normalized, cv2.COLOR_BGR2GRAY)
    header_median = float(np.median(gray[80:135, 370:490]))
    panel_median = float(np.median(gray[700:845, 130:580]))
    visible = header_median < 180 and panel_median > 225
    result = {
        "dialogVisible": visible,
        "costDiamonds": None,
        "roundNumber": None,
        "confirmButtonVisible": False,
        "rawCostText": "",
    }
    if not visible:
        return result
    cost_text = _ocr(normalized, 170, 725, 370, 70)
    confirm_text = _ocr(normalized, 365, 865, 220, 65)
    round_text = _ocr(normalized, 470, 170, 235, 65)
    cost_match = re.search(r"^\s*(\d{1,3})\b", cost_text)
    round_match = re.search(r"(\d{1,5})round", re.sub(r"\s+", "", _ascii_text(round_text)))
    result["rawCostText"] = cost_text
    if "tham gia" in _ascii_text(cost_text) and cost_match:
        result["costDiamonds"] = int(cost_match.group(1))
    if round_match:
        result["roundNumber"] = int(round_match.group(1))
    result["confirmButtonVisible"] = "tham gia ngay" in _ascii_text(confirm_text)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--image", help="Inspect an existing image without ADB")
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text(encoding="utf-8-sig"))
    tesseract = Path(config.get("financials", {}).get("tesseract_path", ""))
    if tesseract.is_file():
        pytesseract.pytesseract.tesseract_cmd = str(tesseract)
    if args.image:
        frame = cv2.imread(args.image)
        observed_at = datetime.fromtimestamp(Path(args.image).stat().st_mtime, timezone.utc)
    else:
        live_frame = Path(config.get("paths", {}).get("live_frame", ""))
        if not live_frame.is_file():
            raise ValueError("Fresh scanner live frame is unavailable")
        if time.time() - live_frame.stat().st_mtime > 2:
            raise ValueError("Scanner live frame is stale")
        observed_at = datetime.fromtimestamp(live_frame.stat().st_mtime, timezone.utc)
        frame = cv2.imread(str(live_frame))
    result = inspect_dialog(frame)
    result["observedAtUtc"] = observed_at.isoformat()
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1) from exc
