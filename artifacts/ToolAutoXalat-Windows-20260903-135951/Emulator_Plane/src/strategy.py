"""
Strategy Manager - Quản lý chiến thuật đặt cược.
Load JSON 11 trường, tính toán taps, check stop-loss.
"""

import json
from typing import List, Dict, Any, Optional


class StrategyManager:
    """Quản lý chiến thuật đặt cược Martingale biến thể."""

    # Chip values có sẵn trong game
    CHIP_VALUES = [1000, 100, 50, 10]

    # Hệ số nhân trả thưởng
    MULTIPLIERS = {
        "bo": 45,
        "ga": 25,
        "xien": 15,
        "xuc": 10,
    }

    def __init__(self, strategy_path: str):
        self.strategy_path = strategy_path
        self.rounds: List[Dict[str, Any]] = []
        self.strategy_name = ""
        self._load_strategy()

    def _load_strategy(self):
        """Load file JSON chiến thuật."""
        with open(self.strategy_path, "r", encoding="utf-8") as f:
            self.rounds = json.load(f)
        # Lấy tên chiến thuật từ tên file
        self.strategy_name = self.strategy_path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1].replace(".json", "")

    def get_round(self, round_number: int) -> Optional[Dict[str, Any]]:
        """
        Lấy dữ liệu kèo theo số thứ tự (1-indexed).
        Trả None nếu vượt quá số vòng.
        """
        idx = round_number - 1
        if 0 <= idx < len(self.rounds):
            return self.rounds[idx]
        return None

    @property
    def max_rounds(self) -> int:
        """Số vòng tối đa của chiến thuật."""
        return len(self.rounds)

    @property
    def total_cost(self) -> int:
        """Tổng vốn cần cho toàn bộ chiến thuật."""
        if self.rounds:
            return self.rounds[-1]["TotalCost"]
        return 0

    def calculate_taps(self, round_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Tính toán chuỗi thao tác tap cho 1 kèo.

        Trả về danh sách các action:
        [
            {"type": "select_chip", "chip": 10, "coord_key": "chip_10"},
            {"type": "tap_slot", "slot": "bo", "coord_key": "bo", "count": 1},
            ...
        ]

        Thuật toán: Cho mỗi ô cược (bo, ga, xien, xuc), tìm cách tap
        ít lần nhất bằng cách dùng chip lớn nhất trước (greedy).
        """
        actions = []
        bet_slots = {
            "bo": round_data["Bo"],
            "ga": round_data["Ga"],
            "xien": round_data["Xien"],
            "xuc": round_data["Xuc"],
        }

        # Nhóm theo chip value để giảm số lần chuyển chip
        # Duyệt từ chip lớn → nhỏ
        for chip_value in self.CHIP_VALUES:
            chip_key = f"chip_{chip_value}"
            has_taps_for_this_chip = False
            slot_taps = {}

            for slot_name, bet_amount in bet_slots.items():
                if bet_amount <= 0:
                    continue
                tap_count = bet_amount // chip_value
                if tap_count > 0:
                    slot_taps[slot_name] = tap_count
                    bet_slots[slot_name] = bet_amount % chip_value
                    has_taps_for_this_chip = True

            if has_taps_for_this_chip:
                # Chọn chip
                actions.append({
                    "type": "select_chip",
                    "chip": chip_value,
                    "coord_key": chip_key,
                })
                # Tap từng ô
                for slot_name, tap_count in slot_taps.items():
                    actions.append({
                        "type": "tap_slot",
                        "slot": slot_name,
                        "coord_key": slot_name,
                        "count": tap_count,
                    })

        return actions

    def check_stop_loss(self, current_round: int, budget: int) -> bool:
        """
        Kiểm tra stop-loss.
        True = cần cắt lỗ (TotalCost vượt ngân sách).
        """
        round_data = self.get_round(current_round)
        if round_data is None:
            return True  # Hết vòng → cắt lỗ
        return round_data["TotalCost"] > budget

    def get_expected_profit(self, round_data: Dict[str, Any]) -> Dict[str, int]:
        """Trả về lãi dự kiến cho từng cửa nếu trúng."""
        return {
            "bo": round_data["ProfitBo"],
            "ga": round_data["ProfitGa"],
            "xien": round_data["ProfitXien"],
            "xuc": round_data["ProfitXuc"],
        }

    def get_summary(self) -> str:
        """Tóm tắt chiến thuật."""
        return (
            f"📋 Chiến thuật: {self.strategy_name}\n"
            f"   Số vòng: {self.max_rounds}\n"
            f"   Tổng vốn cần: {self.total_cost:,} xu\n"
            f"   Kèo 1: {self.rounds[0]['Cost']} xu\n"
            f"   Kèo cuối: {self.rounds[-1]['Cost']:,} xu"
        )
