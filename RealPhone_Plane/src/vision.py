"""
Vision Engine cho RealPhone Plane.
OCR (Tesseract) + Color Detection cho nhận diện game state.
Tự động scale ROI theo resolution thực tế.
"""

import re
import cv2
import numpy as np
import pytesseract
from typing import Optional, Tuple, List, Dict

from src.logger import GameLogger


class VisionEngine:
    """
    Computer Vision engine cho game Greedy BIGO - RealPhone mode.
    Tự động scale ROI theo resolution thực tế của điện thoại.
    Có retry mechanism khi OCR fail.
    """

    RAU_NAMES = ["tomato", "cabbage", "corn", "carrot"]
    THIT_NAMES = ["steak", "chicken", "skewer", "hotdog"]

    def __init__(self, config: dict, logger: GameLogger,
                 scale_x: float = 1.0, scale_y: float = 1.0):
        self.logger = logger
        self.scale_x = scale_x
        self.scale_y = scale_y

        # ROI base configs (sẽ được scale)
        roi = config.get("roi_base", config.get("roi", {}))
        self.roi_timer_base = roi.get("timer", {"x": 180, "y": 320, "w": 80, "h": 40})
        self.roi_history_base = roi.get("history", {"x": 10, "y": 620, "w": 390, "h": 50})
        self.roi_balance_base = roi.get("balance", {"x": 30, "y": 660, "w": 120, "h": 25})

        # OCR config
        self.ocr_engine = config.get("ocr_engine", "tesseract")
        tesseract_path = config.get("tesseract_path", "")
        if tesseract_path:
            pytesseract.pytesseract.tesseract_cmd = tesseract_path

        # Retry config
        self.ocr_max_retries = 3

        # Frame diff
        self._prev_history_hash: Optional[int] = None
        self.frame_diff_threshold = config.get("frame_diff_threshold", 30)

        # Precompute scaled ROIs
        self._roi_timer = self._scale_roi(self.roi_timer_base)
        self._roi_history = self._scale_roi(self.roi_history_base)
        self._roi_balance = self._scale_roi(self.roi_balance_base)

    def update_scale(self, scale_x: float, scale_y: float):
        """Cập nhật scale factor khi resolution thay đổi."""
        self.scale_x = scale_x
        self.scale_y = scale_y
        self._roi_timer = self._scale_roi(self.roi_timer_base)
        self._roi_history = self._scale_roi(self.roi_history_base)
        self._roi_balance = self._scale_roi(self.roi_balance_base)
        self.logger.debug(
            f"Vision scale updated: ({scale_x:.3f}, {scale_y:.3f})"
        )

    def _scale_roi(self, roi: dict) -> dict:
        """Scale ROI theo resolution thực tế."""
        return {
            "x": int(roi["x"] * self.scale_x),
            "y": int(roi["y"] * self.scale_y),
            "w": int(roi["w"] * self.scale_x),
            "h": int(roi["h"] * self.scale_y),
        }

    def crop_roi(self, frame: np.ndarray, roi: dict) -> np.ndarray:
        """Cắt vùng ROI từ frame."""
        x, y, w, h = roi["x"], roi["y"], roi["w"], roi["h"]
        # Clamp to frame bounds
        fh, fw = frame.shape[:2]
        x = max(0, min(x, fw - 1))
        y = max(0, min(y, fh - 1))
        w = min(w, fw - x)
        h = min(h, fh - y)
        return frame[y:y+h, x:x+w]

    def read_timer(self, frame: np.ndarray) -> Optional[int]:
        """
        Đọc timer từ ROI 1 với retry mechanism.
        Trả về số giây, hoặc None nếu không đọc được.
        """
        for attempt in range(self.ocr_max_retries):
            try:
                roi_img = self.crop_roi(frame, self._roi_timer)
                if roi_img.size == 0:
                    continue

                gray = cv2.cvtColor(roi_img, cv2.COLOR_BGR2GRAY)

                # Thử nhiều phương pháp tiền xử lý
                if attempt == 0:
                    processed = cv2.convertScaleAbs(gray, alpha=2.0, beta=0)
                    _, processed = cv2.threshold(processed, 150, 255, cv2.THRESH_BINARY)
                elif attempt == 1:
                    processed = cv2.adaptiveThreshold(
                        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                        cv2.THRESH_BINARY, 11, 2
                    )
                else:
                    processed = cv2.convertScaleAbs(gray, alpha=3.0, beta=-50)
                    _, processed = cv2.threshold(processed, 0, 255,
                                                  cv2.THRESH_BINARY + cv2.THRESH_OTSU)

                text = pytesseract.image_to_string(
                    processed,
                    config="--psm 7 --oem 3 -c tessedit_char_whitelist=0123456789Selectim"
                ).strip()

                numbers = re.findall(r'\d+', text)
                if numbers:
                    seconds = int(numbers[0])
                    if 0 <= seconds <= 60:
                        return seconds

            except Exception as e:
                self.logger.debug(f"OCR timer attempt {attempt+1} error: {e}")

        return None

    def read_balance(self, frame: np.ndarray) -> Optional[int]:
        """
        Đọc số dư Kim Cương với retry mechanism.
        Trả về số xu, hoặc None nếu không đọc được.
        """
        for attempt in range(self.ocr_max_retries):
            try:
                roi_img = self.crop_roi(frame, self._roi_balance)
                if roi_img.size == 0:
                    continue

                gray = cv2.cvtColor(roi_img, cv2.COLOR_BGR2GRAY)

                if attempt == 0:
                    processed = cv2.convertScaleAbs(gray, alpha=2.5, beta=10)
                    _, processed = cv2.threshold(processed, 140, 255, cv2.THRESH_BINARY)
                elif attempt == 1:
                    processed = cv2.adaptiveThreshold(
                        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                        cv2.THRESH_BINARY, 15, 3
                    )
                else:
                    # Dùng OTSU
                    processed = cv2.GaussianBlur(gray, (3, 3), 0)
                    _, processed = cv2.threshold(processed, 0, 255,
                                                  cv2.THRESH_BINARY + cv2.THRESH_OTSU)

                text = pytesseract.image_to_string(
                    processed,
                    config="--psm 7 --oem 3 -c tessedit_char_whitelist=0123456789,."
                ).strip()

                text = text.replace(",", "").replace(".", "").replace(" ", "")
                numbers = re.findall(r'\d+', text)
                if numbers:
                    return int(numbers[0])

            except Exception as e:
                self.logger.debug(f"OCR balance attempt {attempt+1} error: {e}")

        return None

    def read_history(self, frame: np.ndarray) -> List[str]:
        """
        Phân tích dải lịch sử kết quả (ROI 2).
        Dùng color detection để phân loại Rau vs Thịt.
        Trả về list: ["RAU", "RAU", "THIT", "RAU", ...]
        """
        try:
            roi_img = self.crop_roi(frame, self._roi_history)
            if roi_img.size == 0:
                return []

            hsv = cv2.cvtColor(roi_img, cv2.COLOR_BGR2HSV)
            h, w = roi_img.shape[:2]
            results = []

            icon_width = w // 10
            if icon_width < 5:
                return []

            for i in range(10):
                x_start = i * icon_width
                x_end = min((i + 1) * icon_width, w)
                icon_region = hsv[2:h-2, x_start:x_end]

                if icon_region.size == 0:
                    continue

                category = self._classify_icon_by_color(icon_region)
                if category:
                    results.append(category)

            return results

        except Exception as e:
            self.logger.debug(f"History read error: {e}")
            return []

    def _classify_icon_by_color(self, hsv_region: np.ndarray) -> Optional[str]:
        """
        Phân loại icon là RAU hay THỊT dựa trên color histogram.
        """
        if hsv_region.size == 0:
            return None

        green_mask = cv2.inRange(hsv_region, (35, 40, 40), (85, 255, 255))
        yellow_mask = cv2.inRange(hsv_region, (15, 40, 40), (35, 255, 255))
        brown_mask = cv2.inRange(hsv_region, (0, 30, 30), (20, 200, 150))

        total_pixels = hsv_region.shape[0] * hsv_region.shape[1]
        if total_pixels == 0:
            return None

        rau_pixels = cv2.countNonZero(green_mask) + cv2.countNonZero(yellow_mask)
        thit_pixels = cv2.countNonZero(brown_mask)

        rau_ratio = rau_pixels / total_pixels
        thit_ratio = thit_pixels / total_pixels

        if rau_ratio > thit_ratio and rau_ratio > 0.05:
            return "RAU"
        elif thit_ratio > rau_ratio and thit_ratio > 0.05:
            return "THIT"

        return None

    def count_rau_streak(self, frame: np.ndarray) -> int:
        """Đếm số kèo RAU liên tiếp từ kết quả mới nhất."""
        history = self.read_history(frame)
        if not history:
            return 0

        streak = 0
        for item in reversed(history):
            if item == "RAU":
                streak += 1
            else:
                break
        return streak

    def has_history_changed(self, frame: np.ndarray) -> bool:
        """Kiểm tra dải lịch sử có thay đổi không."""
        roi_img = self.crop_roi(frame, self._roi_history)
        if roi_img.size == 0:
            return False
        gray = cv2.cvtColor(roi_img, cv2.COLOR_BGR2GRAY)
        small = cv2.resize(gray, (32, 8))
        current_hash = hash(small.tobytes())

        if self._prev_history_hash is None:
            self._prev_history_hash = current_hash
            return True

        changed = current_hash != self._prev_history_hash
        self._prev_history_hash = current_hash
        return changed

    def detect_new_icon(self, frame: np.ndarray) -> bool:
        """Phát hiện icon mới xuất hiện (có chữ "New")."""
        try:
            roi_img = self.crop_roi(frame, self._roi_history)
            if roi_img.size == 0:
                return False

            h, w = roi_img.shape[:2]
            right_region = roi_img[:, w*3//4:]

            gray = cv2.cvtColor(right_region, cv2.COLOR_BGR2GRAY)
            _, thresh = cv2.threshold(gray, 200, 255, cv2.THRESH_BINARY)

            text = pytesseract.image_to_string(
                thresh, config="--psm 7"
            ).strip().lower()

            return "new" in text
        except Exception:
            return False

    def save_screenshot(self, frame: np.ndarray, filepath: str):
        """Lưu frame ra file để debug."""
        cv2.imwrite(filepath, frame)
