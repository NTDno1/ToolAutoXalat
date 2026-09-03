"""
State Machine cho Emulator Plane.
5 trạng thái: WAIT → ANALYZE → BET → SPINNING → RESULT.
Quản lý vòng lặp game automation.
"""

import time
from enum import Enum, auto
from datetime import datetime
from typing import Optional

from src.adb_controller import EmulatorAdbController
from src.vision import VisionEngine
from src.strategy import StrategyManager
from src.database import Database
from src.logger import GameLogger


class GameState(Enum):
    """5 trạng thái của game loop."""
    WAIT = auto()       # Đợi vòng mới bắt đầu
    ANALYZE = auto()    # Phân tích lịch sử, đếm bệt Rau
    BET = auto()        # Đặt cược
    SPINNING = auto()   # Đợi vòng quay kết thúc
    RESULT = auto()     # Xử lý kết quả
    STOPPED = auto()    # Đã dừng


class GameStateMachine:
    """
    State Machine cho game Greedy BIGO - Emulator mode.

    Flow:
        WAIT → ANALYZE → (chưa đủ bệt) → WAIT
                       → (đủ bệt) → BET → SPINNING → RESULT
                                                        ↓
                                                   WIN → WAIT (reset)
                                                   LOSE → BET (round+1)
                                                   STOP_LOSS → WAIT (reset)
    """

    def __init__(self, adb: EmulatorAdbController, vision: VisionEngine,
                 strategy: StrategyManager, db: Database, logger: GameLogger,
                 config: dict):
        self.adb = adb
        self.vision = vision
        self.strategy = strategy
        self.db = db
        self.logger = logger

        # Config
        self.so_keo_rinh_moi = config.get("so_keo_rinh_moi", 8)
        self.stop_loss_budget = config.get("stop_loss", 10186)
        self.min_timer_seconds = config.get("min_timer_seconds", 5)
        self.scan_interval = config.get("scan_interval_ms", 500) / 1000.0

        # Chip & bet slot coordinates
        self.chips = config.get("chips", {})
        self.bet_slots = config.get("bet_slots", {})

        # State
        self.state = GameState.WAIT
        self.current_round = 1
        self.old_balance: Optional[int] = None
        self.session_id: Optional[int] = None
        self._running = False
        self._streak_start_time: Optional[datetime] = None

        # Session stats
        self.total_bets = 0
        self.total_wins = 0
        self.total_losses = 0

    def start(self, mode: str = "auto_betting"):
        """Bắt đầu vòng lặp game."""
        self._running = True
        self.state = GameState.WAIT
        self.current_round = 1

        self.logger.separator("═", 55)
        self.logger.info(f"🚀 BẮT ĐẦU - Mode: {mode.upper()}")
        self.logger.info(self.strategy.get_summary())
        self.logger.info(f"🌿 Rình mồi: {self.so_keo_rinh_moi} kèo Rau")
        self.logger.info(f"🛑 Stop-loss: {self.stop_loss_budget:,} xu")
        self.logger.separator("═", 55)

        # Đọc balance ban đầu
        frame = self.adb.screencap()
        if frame is not None:
            balance = self.vision.read_balance(frame)
            if balance:
                self.logger.balance_info(balance, "Khởi đầu")
                self.session_id = self.db.start_session(mode, balance)

        # Main loop
        try:
            if mode == "auto_betting":
                self._auto_betting_loop()
            elif mode == "data_mining":
                self._data_mining_loop()
        except KeyboardInterrupt:
            self.logger.info("⚠️ Nhận tín hiệu dừng (Ctrl+C)")
        finally:
            self.stop()

    def stop(self):
        """Dừng vòng lặp."""
        self._running = False
        self.state = GameState.STOPPED

        # Kết thúc session
        if self.session_id:
            frame = self.adb.screencap()
            end_balance = 0
            if frame is not None:
                end_balance = self.vision.read_balance(frame) or 0
            net_profit = end_balance - (self.old_balance or 0)
            self.db.end_session(
                self.session_id, self.total_bets,
                self.total_wins, self.total_losses,
                net_profit, end_balance
            )

        self.logger.separator("═", 55)
        self.logger.info("🛑 ĐÃ DỪNG")
        self.logger.info(
            f"📊 Tổng kết: {self.total_bets} kèo | "
            f"✅ {self.total_wins} W | ❌ {self.total_losses} L"
        )
        self.logger.separator("═", 55)

    def _auto_betting_loop(self):
        """Vòng lặp chính cho chế độ Auto Betting."""
        while self._running:
            try:
                if self.state == GameState.WAIT:
                    self._state_wait()
                elif self.state == GameState.ANALYZE:
                    self._state_analyze()
                elif self.state == GameState.BET:
                    self._state_bet()
                elif self.state == GameState.SPINNING:
                    self._state_spinning()
                elif self.state == GameState.RESULT:
                    self._state_result()
                else:
                    break

                time.sleep(self.scan_interval)

            except Exception as e:
                self.logger.error(f"Lỗi trong game loop: {e}")
                time.sleep(2)

    def _data_mining_loop(self):
        """Vòng lặp cho chế độ Data Mining (chỉ đọc, không click)."""
        self.logger.info("📊 Chế độ DATA MINING - Chỉ đọc, không đặt cược")
        last_streak = 0

        while self._running:
            try:
                frame = self.adb.screencap()
                if frame is None:
                    time.sleep(1)
                    continue

                # Đọc timer
                timer = self.vision.read_timer(frame)

                # Đọc streak
                streak = self.vision.count_rau_streak(frame)

                # Đọc balance
                balance = self.vision.read_balance(frame)

                # Log nếu streak thay đổi
                if streak != last_streak:
                    self.logger.streak_info(streak, self.so_keo_rinh_moi)
                    if streak == 0 and last_streak > 0:
                        # Chuỗi Rau vừa kết thúc
                        self.db.log_streak(
                            last_streak, "RAU",
                            self._streak_start_time or datetime.now(),
                            datetime.now()
                        )
                        self.logger.info(
                            f"📊 Đã ghi nhận chuỗi RAU: {last_streak} kèo"
                        )
                    if streak > 0 and last_streak == 0:
                        self._streak_start_time = datetime.now()
                    last_streak = streak

                if balance:
                    self.logger.balance_info(balance)

                if timer:
                    self.logger.debug(f"⏱ Timer: {timer}s")

                time.sleep(self.scan_interval)

            except Exception as e:
                self.logger.error(f"Data mining error: {e}")
                time.sleep(2)

    # ─────────────────────────────────────────────────────────
    # STATE HANDLERS
    # ─────────────────────────────────────────────────────────

    def _state_wait(self):
        """
        State_Wait: Quét ROI 1 (đồng hồ).
        Đợi xuất hiện "Select time XXs" (> min_timer_seconds).
        """
        self.logger.state("WAIT", "Đợi vòng mới...")

        frame = self.adb.screencap()
        if frame is None:
            return

        timer = self.vision.read_timer(frame)
        if timer is not None and timer > self.min_timer_seconds:
            self.logger.info(f"⏱ Timer detected: {timer}s → Chuyển ANALYZE")
            self.state = GameState.ANALYZE

    def _state_analyze(self):
        """
        State_Analyze: Đếm bệt Rau liên tiếp.
        Nếu đủ trigger → chuyển BET.
        Nếu chưa đủ → quay lại WAIT.
        """
        self.logger.state("ANALYZE", "Phân tích lịch sử...")

        frame = self.adb.screencap()
        if frame is None:
            self.state = GameState.WAIT
            return

        streak = self.vision.count_rau_streak(frame)
        self.logger.streak_info(streak, self.so_keo_rinh_moi)

        # Log streak vào DB
        if streak > 0:
            if self._streak_start_time is None:
                self._streak_start_time = datetime.now()
        else:
            if self._streak_start_time:
                self.db.log_streak(
                    0, "THIT", self._streak_start_time, datetime.now()
                )
            self._streak_start_time = None

        # Kiểm tra trigger
        if streak >= self.so_keo_rinh_moi:
            # Đủ điều kiện → lưu balance → chuyển BET
            balance = self.vision.read_balance(frame)
            if balance is not None:
                self.old_balance = balance
                self.logger.balance_info(balance, "Before Bet")
            else:
                self.logger.warning("⚠️ Không đọc được balance, tiếp tục...")

            self.current_round = 1
            self.state = GameState.BET
            self.logger.state("ANALYZE", "✅ ĐỦ ĐIỀU KIỆN → VÀO LỆNH!")
        else:
            # Chưa đủ → quay lại WAIT
            self.state = GameState.WAIT

    def _state_bet(self):
        """
        State_Bet: Đặt cược theo chiến thuật.
        Đọc round data → tính taps → thực thi click.
        """
        self.logger.state("BET", f"Đặt cược Kèo {self.current_round}...")

        # Check stop-loss
        if self.strategy.check_stop_loss(self.current_round, self.stop_loss_budget):
            self.logger.state(
                "STOP_LOSS",
                f"🛑 CẮT LỖ! Round {self.current_round} vượt ngân sách "
                f"{self.stop_loss_budget:,} xu"
            )
            self.current_round = 1
            self.state = GameState.WAIT
            return

        # Lấy data round
        round_data = self.strategy.get_round(self.current_round)
        if round_data is None:
            self.logger.error(f"Hết round! Max: {self.strategy.max_rounds}")
            self.current_round = 1
            self.state = GameState.WAIT
            return

        self.logger.info(
            f"   Bò:{round_data['Bo']} | Gà:{round_data['Ga']} | "
            f"Xiên:{round_data['Xien']} | Xúc:{round_data['Xuc']} | "
            f"Cost: {round_data['Cost']}xu"
        )

        # Tính toán chuỗi tap
        actions = self.strategy.calculate_taps(round_data)

        # Thực thi click
        for action in actions:
            if action["type"] == "select_chip":
                coord = self.chips.get(action["coord_key"])
                if coord:
                    self.adb.tap(coord["x"], coord["y"])
                    self.logger.debug(f"   Chọn chip [{action['chip']}]")

            elif action["type"] == "tap_slot":
                coord = self.bet_slots.get(action["coord_key"])
                if coord:
                    for _ in range(action["count"]):
                        self.adb.tap(coord["x"], coord["y"])
                    self.logger.debug(
                        f"   Tap {action['slot']}: {action['count']} lần"
                    )

        self.logger.info(f"   ✅ Đã đặt cược xong Kèo {self.current_round}")
        self.state = GameState.SPINNING

    def _state_spinning(self):
        """
        State_Spinning: Đợi vòng quay kết thúc.
        Detect khi dải lịch sử thay đổi (icon mới xuất hiện).
        """
        self.logger.state("SPINNING", "Đợi vòng quay...")

        frame = self.adb.screencap()
        if frame is None:
            return

        # Kiểm tra lịch sử có thay đổi
        if self.vision.has_history_changed(frame):
            self.logger.info("🎯 Phát hiện kết quả mới!")
            time.sleep(1)  # Đợi animation hoàn tất
            self.state = GameState.RESULT

    def _state_result(self):
        """
        State_Result: Xử lý kết quả.
        So sánh balance trước/sau để xác định WIN/LOSE.
        """
        self.logger.state("RESULT", "Xử lý kết quả...")

        frame = self.adb.screencap()
        if frame is None:
            self.state = GameState.WAIT
            return

        new_balance = self.vision.read_balance(frame)
        if new_balance is None:
            self.logger.warning("⚠️ Không đọc được balance sau spin")
            # Retry
            time.sleep(1)
            frame = self.adb.screencap()
            if frame is not None:
                new_balance = self.vision.read_balance(frame)

        if new_balance is None:
            self.logger.error("❌ Vẫn không đọc được balance → Reset")
            self.current_round = 1
            self.state = GameState.WAIT
            return

        round_data = self.strategy.get_round(self.current_round)
        cost = round_data["Cost"] if round_data else 0

        if self.old_balance is not None and new_balance > self.old_balance:
            # ─── WIN ───
            result = "WIN"
            self.total_wins += 1
            self.total_bets += 1
            self.logger.bet_result(
                self.current_round, cost, result,
                self.old_balance, new_balance
            )
            self.db.log_bet(
                self.strategy.strategy_name, self.current_round,
                round_data["Bo"], round_data["Ga"],
                round_data["Xien"], round_data["Xuc"],
                round_data["TotalCost"], result,
                self.old_balance, new_balance
            )
            # Reset → quay lại rình mồi
            self.current_round = 1
            self.old_balance = None
            self.state = GameState.WAIT

        else:
            # ─── LOSE ───
            result = "LOSE"
            self.total_losses += 1
            self.total_bets += 1
            self.logger.bet_result(
                self.current_round, cost, result,
                self.old_balance or 0, new_balance
            )
            self.db.log_bet(
                self.strategy.strategy_name, self.current_round,
                round_data["Bo"] if round_data else 0,
                round_data["Ga"] if round_data else 0,
                round_data["Xien"] if round_data else 0,
                round_data["Xuc"] if round_data else 0,
                round_data["TotalCost"] if round_data else 0,
                result,
                self.old_balance or 0, new_balance
            )

            # Tăng round (Martingale gấp thếp)
            self.current_round += 1
            self.old_balance = new_balance

            # Check stop-loss cho round tiếp theo
            if self.strategy.check_stop_loss(self.current_round, self.stop_loss_budget):
                self.logger.state(
                    "STOP_LOSS",
                    f"🛑 CẮT LỖ sau Kèo {self.current_round - 1}!"
                )
                self.current_round = 1
                self.old_balance = None
                self.state = GameState.WAIT
            else:
                # Đợi vòng mới để đặt tiếp
                self.logger.info(
                    f"   ↗ Gấp thếp → Kèo {self.current_round}"
                )
                self.state = GameState.WAIT  # Đợi timer mới rồi vào BET

    @property
    def is_running(self) -> bool:
        return self._running
