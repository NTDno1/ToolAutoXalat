"""
Logger module cho RealPhone Plane.
Console + File logging với timestamp, màu sắc, và thông tin thiết bị.
"""

import logging
import os
from datetime import datetime
from colorama import init, Fore, Style

init(autoreset=True)


class GameLogger:
    """Logger tùy chỉnh cho game automation - RealPhone mode."""

    COLORS = {
        "DEBUG": Fore.CYAN,
        "INFO": Fore.GREEN,
        "WARNING": Fore.YELLOW,
        "ERROR": Fore.RED,
        "CRITICAL": Fore.RED + Style.BRIGHT,
    }

    STATE_ICONS = {
        "WAIT": "⏳",
        "ANALYZE": "🔍",
        "BET": "💰",
        "SPINNING": "🎰",
        "RESULT": "📊",
        "WIN": "✅",
        "LOSE": "❌",
        "STOP_LOSS": "🛑",
        "CONNECT": "🔌",
        "DISCONNECT": "⚡",
        "DEVICE": "📱",
        "BATTERY": "🔋",
        "HEALTH": "💚",
        "RECONNECT": "🔄",
    }

    def __init__(self, log_dir: str = "logs", log_level: str = "INFO",
                 device_name: str = "unknown"):
        self.log_dir = log_dir
        self.device_name = device_name
        os.makedirs(log_dir, exist_ok=True)

        log_filename = datetime.now().strftime("realphone_%Y%m%d_%H%M%S.log")
        log_path = os.path.join(log_dir, log_filename)

        self.logger = logging.getLogger("RealPhonePlane")
        self.logger.setLevel(getattr(logging, log_level, logging.INFO))
        self.logger.handlers.clear()

        # File handler
        fh = logging.FileHandler(log_path, encoding="utf-8")
        fh.setFormatter(logging.Formatter(
            "%(asctime)s | %(levelname)-8s | [%(device)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        ))
        self.logger.addHandler(fh)

        self._console_enabled = True

    def _get_extra(self):
        """Thêm device name vào log record."""
        return {"device": self.device_name}

    def set_device_name(self, name: str):
        """Cập nhật tên thiết bị."""
        self.device_name = name

    def _console_print(self, level: str, message: str):
        if not self._console_enabled:
            return
        color = self.COLORS.get(level, "")
        timestamp = datetime.now().strftime("%H:%M:%S")
        device_tag = f"📱{self.device_name}" if self.device_name != "unknown" else ""
        print(
            f"{Fore.WHITE}[{timestamp}]{Style.RESET_ALL} "
            f"{Fore.MAGENTA}{device_tag}{Style.RESET_ALL} "
            f"{color}{message}{Style.RESET_ALL}"
        )

    def debug(self, msg: str):
        self.logger.debug(msg, extra=self._get_extra())
        self._console_print("DEBUG", msg)

    def info(self, msg: str):
        self.logger.info(msg, extra=self._get_extra())
        self._console_print("INFO", msg)

    def warning(self, msg: str):
        self.logger.warning(msg, extra=self._get_extra())
        self._console_print("WARNING", msg)

    def error(self, msg: str):
        self.logger.error(msg, extra=self._get_extra())
        self._console_print("ERROR", msg)

    def critical(self, msg: str):
        self.logger.critical(msg, extra=self._get_extra())
        self._console_print("CRITICAL", msg)

    def state(self, state_name: str, detail: str = ""):
        icon = self.STATE_ICONS.get(state_name, "▶")
        msg = f"{icon} [{state_name}] {detail}"
        self.info(msg)

    def bet_result(self, round_num: int, cost: int, result: str,
                   balance_before: int, balance_after: int):
        profit = balance_after - balance_before
        icon = "✅" if result == "WIN" else "❌"
        msg = (
            f"{icon} Kèo {round_num} | Đặt: {cost}xu | "
            f"KQ: {result} | Lãi: {profit:+d}xu | "
            f"Số dư: {balance_before} → {balance_after}"
        )
        self.info(msg)

    def streak_info(self, streak_length: int, trigger: int):
        if streak_length >= trigger:
            msg = f"🔥 Bệt RAU: {streak_length} kèo (≥ {trigger}) → VÀO LỆNH!"
            self.warning(msg)
        else:
            msg = f"🌿 Bệt RAU: {streak_length}/{trigger} kèo → Tiếp tục rình..."
            self.info(msg)

    def balance_info(self, balance: int, label: str = ""):
        msg = f"💎 Số dư{' (' + label + ')' if label else ''}: {balance:,} xu"
        self.info(msg)

    def device_info(self, model: str, android_ver: str, resolution: str,
                    battery: int = -1):
        """Log thông tin thiết bị (chỉ RealPhone)."""
        msg = (
            f"📱 Device: {model} | Android {android_ver} | "
            f"Res: {resolution}"
        )
        if battery >= 0:
            msg += f" | 🔋 {battery}%"
        self.info(msg)

    def health_check(self, connected: bool, battery: int = -1,
                     screen_on: bool = True):
        """Log health check thiết bị (chỉ RealPhone)."""
        status = "OK" if connected else "DISCONNECTED"
        icon = "💚" if connected else "💔"
        msg = f"{icon} Health: {status}"
        if battery >= 0:
            msg += f" | 🔋 {battery}%"
        if not screen_on:
            msg += " | ⚠️ Màn hình tắt!"
        level = "INFO" if connected else "ERROR"
        getattr(self, level.lower())(msg)

    def separator(self, char: str = "─", length: int = 50):
        print(f"{Fore.WHITE}{char * length}{Style.RESET_ALL}")
