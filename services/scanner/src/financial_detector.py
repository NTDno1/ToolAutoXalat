from __future__ import annotations

from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import re

import cv2
import numpy as np
import pytesseract

from screen_geometry import ReferenceLayout


class FinancialDetectionError(RuntimeError):
    pass


@dataclass(frozen=True)
class FinancialDetection:
    balance_units: int
    own_bets: dict[str, int]
    raw_balance_text: str


def parse_money_text(raw_text: str) -> int:
    """Parse an integer coin amount while tolerating thousands separators."""
    groups = re.findall(r"\d[\d.,\s]*", raw_text)
    candidates = []
    for group in groups:
        digits = re.sub(r"\D", "", group)
        if digits:
            candidates.append(digits)
    if not candidates:
        raise FinancialDetectionError(f"Coin amount not found; OCR={raw_text!r}")
    return int(max(candidates, key=len))


def parse_own_bet_text(raw_text: str) -> int | None:
    """Return the amount only when OCR contains the explicit player label."""
    normalized = re.sub(r"\s+", " ", raw_text)
    match = re.search(
        r"\byou\s*[:;.：]?\s*(\d[\d.,\s]*)",
        normalized,
        flags=re.IGNORECASE,
    )
    return parse_money_text(match.group(1)) if match else None


class FinancialDetector:
    """Read the player's available balance and per-item placed amounts."""

    def __init__(self, config: dict):
        self.enabled = bool(config.get("enabled", False))
        self.layout = ReferenceLayout(config)
        self.balance_crop = dict(config.get("balance_crop", {}))
        self.unobstructed_header_region = dict(config.get("unobstructed_header_region", {}))
        self.minimum_header_median = float(config.get("minimum_header_median", 200))
        self.item_crops = {
            str(item["code"]): dict(item["amount_region"])
            for item in config.get("items", [])
            if item.get("code") and item.get("amount_region")
        }
        tesseract_path = Path(config.get("tesseract_path", ""))
        if tesseract_path.is_file():
            pytesseract.pytesseract.tesseract_cmd = str(tesseract_path)

    def _read_amount(
        self,
        crop: np.ndarray,
        *,
        empty_is_zero: bool,
        minimum_confidence: float = 0,
    ) -> tuple[int, str]:
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        otsu = cv2.threshold(
            gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU
        )[1]
        readings: list[str] = []
        values: list[int] = []
        for image in (gray, otsu):
            enlarged = cv2.resize(
                image, None, fx=5, fy=5, interpolation=cv2.INTER_CUBIC
            )
            try:
                data = pytesseract.image_to_data(
                    enlarged,
                    config="--psm 7 -c tessedit_char_whitelist=0123456789,.",
                    timeout=4,
                    output_type=pytesseract.Output.DICT,
                )
            except (RuntimeError, pytesseract.TesseractError) as exc:
                raise FinancialDetectionError(f"Tesseract failed: {exc}") from exc
            tokens = [
                (str(text).strip(), float(confidence))
                for text, confidence in zip(data["text"], data["conf"])
                if str(text).strip()
            ]
            raw = " ".join(text for text, _ in tokens)
            readings.append(raw)
            if not tokens or max(confidence for _, confidence in tokens) < minimum_confidence:
                continue
            try:
                values.append(parse_money_text(raw))
            except FinancialDetectionError:
                pass
        if not values:
            if empty_is_zero:
                return 0, " | ".join(readings)
            raise FinancialDetectionError(f"Balance OCR empty; readings={readings!r}")
        if empty_is_zero and (len(values) < 2 or len(set(values)) != 1):
            # Item icons, wheel borders and the animated hand can resemble a
            # digit in one preprocessing pass. Verification data is safer as
            # zero than as a false placed amount; LIVE will stop on mismatch.
            return 0, " | ".join(readings)
        # Two preprocessing passes normally agree. When they do not, prefer
        # the longer numeric reading because separators are frequently dropped.
        value = max(values, key=lambda candidate: (len(str(candidate)), values.count(candidate)))
        return value, " | ".join(readings)

    def _read_own_bet(self, crop: np.ndarray) -> tuple[int, str]:
        """Read only the game's explicit ``You: <amount>`` label.

        The wheel artwork, payout multiplier and animated effects frequently
        resemble isolated digits. Requiring the accompanying ``You`` label is
        substantially safer than numeric-only OCR for LIVE verification.
        """
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        variants = (crop, gray)
        readings: list[str] = []
        saw_player_label = False
        for image in variants:
            enlarged = cv2.resize(
                image, None, fx=5, fy=5, interpolation=cv2.INTER_CUBIC
            )
            try:
                raw = pytesseract.image_to_string(
                    enlarged,
                    config="--psm 7",
                    timeout=4,
                ).strip()
            except (RuntimeError, pytesseract.TesseractError) as exc:
                raise FinancialDetectionError(f"Tesseract failed: {exc}") from exc
            readings.append(raw)
            amount = parse_own_bet_text(raw)
            if amount is not None:
                return amount, " | ".join(readings)
            normalized = re.sub(r"\s+", " ", raw)
            if re.search(r"\byou\b", normalized, flags=re.IGNORECASE):
                saw_player_label = True
        if saw_player_label:
            raise FinancialDetectionError(
                f"Own-bet label found but amount is unreadable; readings={readings!r}"
            )
        return 0, " | ".join(readings)

    def detect(self, frame: np.ndarray) -> FinancialDetection:
        if not self.enabled:
            raise FinancialDetectionError("Financial OCR is disabled")
        if frame is None or frame.ndim != 3:
            raise FinancialDetectionError("Financial frame is empty or invalid")
        try:
            if self.unobstructed_header_region:
                header = self.layout.crop(frame, self.unobstructed_header_region)
                median_brightness = float(np.median(cv2.cvtColor(header, cv2.COLOR_BGR2GRAY)))
                if median_brightness < self.minimum_header_median:
                    raise FinancialDetectionError("Game is dimmed by a dialog; balance OCR skipped")
            balance_crop = self.layout.crop(frame, self.balance_crop)
            item_crops: list[tuple[str, np.ndarray]] = []
            for code, crop_config in self.item_crops.items():
                item_crops.append((code, self.layout.crop(frame, crop_config)))
            # Tesseract runs as child processes, so the independent ROIs can
            # be read concurrently. This keeps post-bet verification inside
            # the seven-second safe betting window.
            with ThreadPoolExecutor(max_workers=4) as executor:
                balance_future = executor.submit(
                    self._read_amount, balance_crop, empty_is_zero=False
                )
                item_futures = {
                    code: executor.submit(
                        self._read_own_bet,
                        crop,
                    )
                    for code, crop in item_crops
                }
                balance, raw_balance = balance_future.result()
                own_bets = {
                    code: future.result()[0]
                    for code, future in item_futures.items()
                }
            if balance <= 0:
                own_bets = {code: 0 for code in own_bets}
        except RuntimeError as exc:
            raise FinancialDetectionError(str(exc)) from exc
        return FinancialDetection(balance, own_bets, raw_balance)
