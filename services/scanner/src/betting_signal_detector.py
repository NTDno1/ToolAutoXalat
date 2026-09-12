"""Detect the live Hot badge and crowd coin markers on the Greedy wheel."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import cv2
import numpy as np

from screen_geometry import ReferenceLayout


@dataclass(frozen=True)
class BettingItemSignal:
    item_code: str
    coin_count: int
    hot_score: float


@dataclass(frozen=True)
class BettingSignalDetection:
    items: tuple[BettingItemSignal, ...]
    hot_item_code: Optional[str]


class BettingSignalDetector:
    """Color/shape detector using coordinates from a reference phone layout.

    The app renders a coin as a compact yellow circle and the Hot label as one
    large connected red patch. Requiring both geometry and color avoids the
    yellow game background and the red wheel borders.
    """

    def __init__(self, config: dict):
        self.config = config
        self.enabled = bool(config.get("enabled", True))
        self.layout = ReferenceLayout(config)
        self.items = tuple(config.get("items", ()))
        self.max_coins = max(1, int(config.get("max_coins", 3)))
        self.coin_hsv_low = tuple(config.get("coin_hsv_low", [18, 150, 170]))
        self.coin_hsv_high = tuple(config.get("coin_hsv_high", [40, 255, 255]))
        self.coin_min_area = float(config.get("coin_min_area", 100))
        self.coin_max_area = float(config.get("coin_max_area", 800))
        self.coin_min_size = float(config.get("coin_min_size", 10))
        self.coin_max_size = float(config.get("coin_max_size", 35))
        self.coin_min_fill = float(config.get("coin_min_fill", 0.35))
        self.hot_min_area = float(config.get("hot_min_area", 650))
        self.hot_min_fraction = float(config.get("hot_min_fraction", 0.18))

    @staticmethod
    def _red_mask(hsv: np.ndarray) -> np.ndarray:
        return cv2.bitwise_or(
            cv2.inRange(hsv, (0, 150, 130), (12, 255, 255)),
            cv2.inRange(hsv, (168, 150, 130), (179, 255, 255)),
        )

    def _coin_count(self, frame: np.ndarray, rect: dict) -> int:
        crop = self.layout.crop(frame, rect)
        hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(hsv, self.coin_hsv_low, self.coin_hsv_high)
        _, _, stats, _ = cv2.connectedComponentsWithStats(mask)
        sx, sy, _ = self.layout.transform(frame.shape)
        area_scale = max(0.01, sx * sy)
        size_scale = max(0.01, (sx + sy) / 2)
        count = 0
        for x, y, width, height, area in stats[1:]:
            if not self.coin_min_area * area_scale <= area <= self.coin_max_area * area_scale:
                continue
            if not self.coin_min_size * size_scale <= width <= self.coin_max_size * size_scale:
                continue
            if not self.coin_min_size * size_scale <= height <= self.coin_max_size * size_scale:
                continue
            aspect = width / max(1, height)
            fill = area / max(1, width * height)
            if 0.62 <= aspect <= 1.62 and fill >= self.coin_min_fill:
                count += 1
        return min(self.max_coins, count)

    def _hot_score(self, frame: np.ndarray, rect: dict) -> float:
        crop = self.layout.crop(frame, rect)
        hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
        mask = self._red_mask(hsv)
        _, _, stats, _ = cv2.connectedComponentsWithStats(mask)
        largest = float(max(stats[1:, cv2.CC_STAT_AREA], default=0))
        sx, sy, _ = self.layout.transform(frame.shape)
        scaled_area = max(1.0, self.hot_min_area * sx * sy)
        red_fraction = float(np.count_nonzero(mask)) / max(1, mask.size)
        area_score = largest / scaled_area
        fraction_score = red_fraction / max(0.001, self.hot_min_fraction)
        return min(area_score, fraction_score)

    def detect(self, frame: np.ndarray) -> BettingSignalDetection:
        if not self.enabled:
            return BettingSignalDetection((), None)
        detected: list[BettingItemSignal] = []
        for item in self.items:
            detected.append(
                BettingItemSignal(
                    item_code=str(item["code"]),
                    coin_count=self._coin_count(frame, item["coin_region"]),
                    hot_score=self._hot_score(frame, item["hot_region"]),
                )
            )
        hot_candidates = [item for item in detected if item.hot_score >= 1.0]
        hot_item = max(hot_candidates, key=lambda item: item.hot_score).item_code if hot_candidates else None
        return BettingSignalDetection(tuple(detected), hot_item)

    def annotate(self, frame: np.ndarray, detection: BettingSignalDetection) -> np.ndarray:
        output = frame.copy()
        indexed = {item.item_code: item for item in detection.items}
        for item in self.items:
            code = str(item["code"])
            signal = indexed[code]
            coin_x, coin_y, coin_w, coin_h = self.layout.rect(frame.shape, item["coin_region"])
            hot_x, hot_y, hot_w, hot_h = self.layout.rect(frame.shape, item["hot_region"])
            cv2.rectangle(output, (coin_x, coin_y), (coin_x + coin_w, coin_y + coin_h), (0, 220, 255), 2)
            cv2.rectangle(
                output,
                (hot_x, hot_y),
                (hot_x + hot_w, hot_y + hot_h),
                (0, 0, 255) if detection.hot_item_code == code else (150, 80, 255),
                2,
            )
            cv2.putText(
                output,
                f"{code}: coin={signal.coin_count} hot={signal.hot_score:.2f}",
                (coin_x, max(15, coin_y - 5)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.38,
                (255, 255, 255),
                1,
                cv2.LINE_AA,
            )
        return output
