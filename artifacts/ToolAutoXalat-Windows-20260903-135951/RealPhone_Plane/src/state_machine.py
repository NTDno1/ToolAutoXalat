"""
State Machine cho RealPhone Plane.
5 trạng thái: WAIT → ANALYZE → BET → SPINNING → RESULT.
Thêm: Connection watchdog, anti-detection, battery monitoring.
"""

import time
import threading
from enum import Enum, auto
from datetime import datetime
from typing import Optional

from src.adb_controller import RealPhoneAdbController
from src.vision import VisionEngine
from src.strategy import StrategyManager
from src.database import Database
from src.logger import GameLogger


class GameState(Enum):
    """5 trạng thái của game loop."""
    WAIT = auto()
    ANALYZE = auto()
    BET = auto()
    SPINNING = auto()
    RESULT = auto()
    PAUSED = auto()     # Tạm dừng (mất kết nối, pin thấp)
    STOPPED = auto()


class GameStateMachine:
    """
    State Machine cho game Greedy BIGO - RealPhone mode.

    Bổ sung so với Emulator:
    - Connection watchdog: Monitor USB, tự pause/reconnect
    - Anti-detection: Random delay giữa các state
    - Battery monitoring: Cảnh báo pin thấp
    - Screen-on check: Verify màn hình sáng trước khi thao tác
    """

    def __init__(self, adb: RealPhoneAdbController, vision: VisionEngine,
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
        self.health_check_interval = config.get("health_check_interval_s", 30)

        # Chip & bet slot coordinates (base resolution)
        chips_config = config.get("chips_base", config.get("chips", {}))
        bet_slots_config = config.get("bet_slots_base", config.get("bet_slots", {}))
        self.chips = chips_config
        self.bet_slots = bet_slots_config

        # State
        self.state = GameState.WAIT
        self.current_round = 1
        self.old_balance: Optional[int] = None
        self.session_id: Optional[int] = None
        self._running = False
        self._paused = False
        self._streak_start_time: Optional[datetime] = None

        # Session stats
        self.total_bets = 0
        self.total_wins = 0
        self.total_losses = 0

        # Watchdog thread
        self._watchdog_thread: Optional[threading.Thread] = None
        self._last_health_check = datetime.now()
        self._low_battery_threshold = 15

    def start(self, mode: str = "auto_betting"):
        """Bắt đầu vòng lặp game."""
        self._running = True
        self._paused = False
        self.state = GameState.WAIT
        self.current_round = 1

        self.logger.separator("═", 55)
        self.logger.info(f"🚀 BẮT ĐẦU - Mode: {mode.upper()} (RealPhone)")
        self.logger.info(self.strategy.get_summary())
        self.logger.info(f"🌿 Rình mồi: {self.so_keo_rinh_moi} kèo Rau")
        self.logger.info(f"🛑 Stop-loss: {self.stop_loss_budget:,} xu")
        self.logger.separator("═", 55)

        # Đảm bảo màn hình sáng
        self.adb.wake_screen()

        # Đọc balance ban đầu
        frame = self.adb.screencap()
        if frame is not None:
            balance = self.vision.read_balance(frame)
            if balance:
                self.logger.balance_info(balance, "Khởi đầu")
                self.session_id = self.db.start_session(mode, balance)

        # Khởi động watchdog
        self._start_watchdog()

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

        # Dừng watchdog
        if self._watchdog_thread and self._watchdog_thread.is_alive():
            self._watchdog_thread.join(timeout=5)

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

    # ─────────────────────────────────────────────────────────
    # WATCHDOG (Connection Health Monitor)
    # ─────────────────────────────────────────────────────────

    def _start_watchdog(self):
        """Khởi động thread giám sát kết nối."""
        self._watchdog_thread = threading.Thread(
            target=self._watchdog_loop, daemon=True
        )
        self._watchdog_thread.start()
        self.logger.info("🐕 Watchdog đã khởi động")

    def _watchdog_loop(self):
        """Thread giám sát: check USB, battery, screen."""
        while self._running:
            try:
                time.sleep(self.health_check_interval)
                if not self._running:
                    break

                # Check USB connection
                if not self.adb.is_connected():
                    self.logger.error("⚡ MẤT KẾT NỐI USB!")
                    self._paused = True
                    self.state = GameState.PAUSED

                    # Thử reconnect
                    if self.adb.reconnect(max_retries=10, wait_s=5):
                        self._paused = False
                        self.state = GameState.WAIT
                        self.logger.info("✅ Đã kết nối lại → Tiếp tục")
                    else:
                        self.logger.critical(
                            "❌ Không thể kết nối lại! Dừng chương trình."
                        )
                        self._running = False
                        break

                # Check battery
                battery = self.adb.get_battery_level()
                if 0 <= battery <= self._low_battery_threshold:
                    self.logger.warning(
                        f"🔋 PIN THẤP: {battery}%! "
                        f"Cắm sạc để tiếp tục."
                    )

                # Check screen
                if not self.adb.is_screen_on():
                    self.logger.warning("📱 Màn hình tắt! Đang bật lại...")
                    self.adb.wake_screen()

                # Log health
                self.adb.health_check()

            except Exception as e:
                self.logger.error(f"Watchdog error: {e}")

    # ─────────────────────────────────────────────────────────
    # MAIN LOOPS
    # ─────────────────────────────────────────────────────────

    def _auto_betting_loop(self):
        """Vòng lặp chính cho chế độ Auto Betting."""
        while self._running:
            try:
                # Kiểm tra pause
                if self._paused:
                    self.logger.debug("⏸ Đang tạm dừng (mất kết nối)...")
                    time.sleep(2)
                    continue

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
                elif self.state == GameState.PAUSED:
                    time.sleep(2)
                    continue
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
                if self._paused:
                    time.sleep(2)
                    continue

                frame = self.adb.screencap()
                if frame is None:
                    time.sleep(1)
                    continue

                timer = self.vision.read_timer(frame)
                streak = self.vision.count_rau_streak(frame)
                balance = self.vision.read_balance(frame)

                if streak != last_streak:
                    self.logger.streak_info(streak, self.so_keo_rinh_moi)
                    if streak == 0 and last_streak > 0:
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
        """State_Wait: Đợi vòng mới + kiểm tra sức khỏe thiết bị."""
        self.logger.state("WAIT", "Đợi vòng mới...")

        # Pre-check: màn hình sáng?
        if not self.adb.is_screen_on():
            self.adb.wake_screen()
            time.sleep(0.5)

        frame = self.adb.screencap()
        if frame is None:
            # Có thể mất kết nối
            if not self.adb.is_connected():
                self._paused = True
                self.state = GameState.PAUSED
            return

        timer = self.vision.read_timer(frame)
        if timer is not None and timer > self.min_timer_seconds:
            self.logger.info(f"⏱ Timer detected: {timer}s → Chuyển ANALYZE")
            self.state = GameState.ANALYZE

    def _state_analyze(self):
        """State_Analyze: Đếm bệt Rau, kiểm tra trigger."""
        self.logger.state("ANALYZE", "Phân tích lịch sử...")

        frame = self.adb.screencap()
        if frame is None:
            self.state = GameState.WAIT
            return

        streak = self.vision.count_rau_streak(frame)
        self.logger.streak_info(streak, self.so_keo_rinh_moi)

        if streak > 0:
            if self._streak_start_time is None:
                self._streak_start_time = datetime.now()
        else:
            if self._streak_start_time:
                self.db.log_streak(
                    0, "THIT", self._streak_start_time, datetime.now()
                )
            self._streak_start_time = None

        if streak >= self.so_keo_rinh_moi:
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
            self.state = GameState.WAIT

    def _state_bet(self):
        """
        State_Bet: Đặt cược theo chiến thuật.
        RealPhone: tap dùng base coordinates (ADB controller tự scale).
        """
        self.logger.state("BET", f"Đặt cược Kèo {self.current_round}...")

        # Pre-check screen
        if not self.adb.is_screen_on():
            self.adb.wake_screen()
            time.sleep(1)

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

        # Tính tap actions
        actions = self.strategy.calculate_taps(round_data)

        # Thực thi (ADB controller tự scale + anti-detection)
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
        """State_Spinning: Đợi vòng quay kết thúc."""
        self.logger.state("SPINNING", "Đợi vòng quay...")

        frame = self.adb.screencap()
        if frame is None:
            if not self.adb.is_connected():
                self._paused = True
                self.state = GameState.PAUSED
            return

        if self.vision.has_history_changed(frame):
            self.logger.info("🎯 Phát hiện kết quả mới!")
            time.sleep(1)
            self.state = GameState.RESULT

    def _state_result(self):
        """State_Result: Xử lý kết quả WIN/LOSE."""
        self.logger.state("RESULT", "Xử lý kết quả...")

        frame = self.adb.screencap()
        if frame is None:
            self.state = GameState.WAIT
            return

        new_balance = self.vision.read_balance(frame)
        if new_balance is None:
            self.logger.warning("⚠️ Không đọc được balance, retry...")
            time.sleep(1.5)
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

            self.current_round += 1
            self.old_balance = new_balance

            if self.strategy.check_stop_loss(self.current_round, self.stop_loss_budget):
                self.logger.state(
                    "STOP_LOSS",
                    f"🛑 CẮT LỖ sau Kèo {self.current_round - 1}!"
                )
                self.current_round = 1
                self.old_balance = None
                self.state = GameState.WAIT
            else:
                self.logger.info(
                    f"   ↗ Gấp thếp → Kèo {self.current_round}"
                )
                self.state = GameState.WAIT

    @property
    def is_running(self) -> bool:
        return self._running
