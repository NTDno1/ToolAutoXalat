"""
Vision Engine cho Emulator Plane.
OCR (Tesseract) + Color Detection cho nhận diện game state.
Tọa độ ROI cố định cho emulator.
"""

import re
import cv2
import numpy as np
import pytesseract
from typing import Optional, Tuple, List, Dict

from src.logger import GameLogger


class VisionEngine:
    """
    Computer Vision engine cho game Greedy BIGO - Emulator mode.
    - ROI 1: Timer (OCR đọc "Select time XXs")
    - ROI 2: History (Color detection đếm Rau/Thịt)
    - ROI 3: Balance (OCR đọc số dư Kim Cương)
    """

    # Phân loại icon theo màu chủ đạo (HSV ranges)
    # Nhóm RAU: xanh lá (Tomato đỏ nhưng vẫn là rau, Cabbage xanh, Corn vàng, Carrot cam)
    # Nhóm THỊT: nâu/đỏ đậm (Steak, Chicken, Skewer, Hotdog)
    # Dùng color histogram để phân biệt 2 nhóm
    RAU_NAMES = ["tomato", "cabbage", "corn", "carrot"]
    THIT_NAMES = ["steak", "chicken", "skewer", "hotdog"]

    def __init__(self, config: dict, logger: GameLogger):
        self.logger = logger

        # ROI configs
        roi = config.get("roi", {})
        self.roi_timer = roi.get("timer", {"x": 180, "y": 320, "w": 80, "h": 40})
        self.roi_history = roi.get("history", {"x": 10, "y": 620, "w": 390, "h": 50})
        self.roi_balance = roi.get("balance", {"x": 30, "y": 660, "w": 120, "h": 25})

        # OCR config
        self.ocr_engine = config.get("ocr_engine", "tesseract")
        tesseract_path = config.get("tesseract_path", "")
        if tesseract_path:
            pytesseract.pytesseract.tesseract_cmd = tesseract_path

        # Frame diff
        self._prev_history_hash: Optional[int] = None
        self.frame_diff_threshold = config.get("frame_diff_threshold", 30)

    def crop_roi(self, frame: np.ndarray, roi: dict) -> np.ndarray:
        """Cắt vùng ROI từ frame."""
        x, y, w, h = roi["x"], roi["y"], roi["w"], roi["h"]
        return frame[y:y+h, x:x+w]

    def read_timer(self, frame: np.ndarray) -> Optional[int]:
        """
        Đọc timer từ ROI 1.
        Tìm số giây trong "Select time XXs".
        Trả về số giây, hoặc None nếu không đọc được.
        """
        try:
            roi_img = self.crop_roi(frame, self.roi_timer)

            # Tiền xử lý: grayscale → threshold → invert
            gray = cv2.cvtColor(roi_img, cv2.COLOR_BGR2GRAY)
            # Tăng contrast
            gray = cv2.convertScaleAbs(gray, alpha=2.0, beta=0)
            _, thresh = cv2.threshold(gray, 150, 255, cv2.THRESH_BINARY)

            # OCR
            text = pytesseract.image_to_string(
                thresh,
                config="--psm 7 --oem 3 -c tessedit_char_whitelist=0123456789Selectim"
            ).strip()

            # Tìm số trong text
            numbers = re.findall(r'\d+', text)
            if numbers:
                seconds = int(numbers[0])
                if 0 <= seconds <= 60:
                    return seconds

        except Exception as e:
            self.logger.debug(f"OCR timer error: {e}")

        return None

    def read_balance(self, frame: np.ndarray) -> Optional[int]:
        """
        Đọc số dư Kim Cương từ ROI 3.
        Trả về số xu, hoặc None nếu không đọc được.
        """
        try:
            roi_img = self.crop_roi(frame, self.roi_balance)

            # Tiền xử lý
            gray = cv2.cvtColor(roi_img, cv2.COLOR_BGR2GRAY)
            gray = cv2.convertScaleAbs(gray, alpha=2.5, beta=10)
            _, thresh = cv2.threshold(gray, 140, 255, cv2.THRESH_BINARY)

            # OCR - chỉ số
            text = pytesseract.image_to_string(
                thresh,
                config="--psm 7 --oem 3 -c tessedit_char_whitelist=0123456789,."
            ).strip()

            # Loại bỏ dấu phẩy, chấm
            text = text.replace(",", "").replace(".", "").replace(" ", "")
            numbers = re.findall(r'\d+', text)
            if numbers:
                return int(numbers[0])

        except Exception as e:
            self.logger.debug(f"OCR balance error: {e}")

        return None

    def read_history(self, frame: np.ndarray) -> List[str]:
        """
        Phân tích dải lịch sử kết quả (ROI 2).
        Dùng color detection để phân loại Rau vs Thịt.

        Trả về list: ["RAU", "RAU", "THIT", "RAU", ...]
        (từ cũ nhất → mới nhất, trái → phải)
        """
        try:
            roi_img = self.crop_roi(frame, self.roi_history)
            hsv = cv2.cvtColor(roi_img, cv2.COLOR_BGR2HSV)

            h, w = roi_img.shape[:2]
            results = []

            # Chia ROI thành các ô icon (ước lượng ~8-10 icon)
            icon_width = w // 10  # ~10 icon ngang
            if icon_width < 5:
                return []

            for i in range(10):
                x_start = i * icon_width
                x_end = min((i + 1) * icon_width, w)
                icon_region = hsv[2:h-2, x_start:x_end]

                if icon_region.size == 0:
                    continue

                # Phân loại bằng màu chủ đạo
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

        RAU: Xanh lá (Cabbage, Corn vàng, Cà rốt cam, Tomato đỏ tươi)
        THỊT: Nâu đỏ đậm (Steak, Chicken, Skewer, Hotdog)

        Cách tiếp cận: Tính tỷ lệ pixel xanh lá vs nâu đỏ.
        """
        if hsv_region.size == 0:
            return None

        # Mask cho màu xanh lá (Hue 35-85)
        green_mask = cv2.inRange(hsv_region, (35, 40, 40), (85, 255, 255))
        # Mask cho màu vàng/cam (Hue 15-35) - Corn, Carrot
        yellow_mask = cv2.inRange(hsv_region, (15, 40, 40), (35, 255, 255))
        # Mask cho màu đỏ tươi (Hue 0-10 hoặc 170-180) - Tomato
        red_bright_mask = cv2.inRange(hsv_region, (0, 100, 100), (10, 255, 255))

        # Mask cho màu nâu đậm (Hue 0-20, S thấp hơn) - Thịt
        brown_mask = cv2.inRange(hsv_region, (0, 30, 30), (20, 200, 150))

        total_pixels = hsv_region.shape[0] * hsv_region.shape[1]
        if total_pixels == 0:
            return None

        rau_pixels = cv2.countNonZero(green_mask) + cv2.countNonZero(yellow_mask)
        thit_pixels = cv2.countNonZero(brown_mask)

        rau_ratio = rau_pixels / total_pixels
        thit_ratio = thit_pixels / total_pixels

        # Quyết định
        if rau_ratio > thit_ratio and rau_ratio > 0.05:
            return "RAU"
        elif thit_ratio > rau_ratio and thit_ratio > 0.05:
            return "THIT"

        return None

    def count_rau_streak(self, frame: np.ndarray) -> int:
        """
        Đếm số kèo RAU liên tiếp từ kết quả mới nhất.
        Scan từ phải qua trái (mới → cũ).
        """
        history = self.read_history(frame)
        if not history:
            return 0

        streak = 0
        # Đếm từ cuối (mới nhất) về đầu
        for item in reversed(history):
            if item == "RAU":
                streak += 1
            else:
                break

        return streak

    def has_history_changed(self, frame: np.ndarray) -> bool:
        """
        Kiểm tra dải lịch sử có thay đổi so với lần trước không.
        Dùng image hash để so sánh nhanh.
        """
        roi_img = self.crop_roi(frame, self.roi_history)
        gray = cv2.cvtColor(roi_img, cv2.COLOR_BGR2GRAY)
        small = cv2.resize(gray, (32, 8))
        current_hash = hash(small.tobytes())

        if self._prev_history_hash is None:
            self._prev_history_hash = current_hash
            return True  # Lần đầu → coi như đã thay đổi

        changed = current_hash != self._prev_history_hash
        self._prev_history_hash = current_hash
        return changed

    def detect_new_icon(self, frame: np.ndarray) -> bool:
        """
        Phát hiện icon mới xuất hiện (có chữ "New").
        Dùng OCR trên phần bên phải của ROI history.
        """
        try:
            roi_img = self.crop_roi(frame, self.roi_history)
            h, w = roi_img.shape[:2]

            # Chỉ quét phần bên phải (icon mới nhất)
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
