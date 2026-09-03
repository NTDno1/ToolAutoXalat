"""
Logger module cho Emulator Plane.
Console + File logging với timestamp và màu sắc.
"""

import logging
import os
import sys
from datetime import datetime
from colorama import init, Fore, Style

init(autoreset=True)


class GameLogger:
    """Logger tùy chỉnh cho game automation - Emulator mode."""

    # Map mức log → màu console
    COLORS = {
        "DEBUG": Fore.CYAN,
        "INFO": Fore.GREEN,
        "WARNING": Fore.YELLOW,
        "ERROR": Fore.RED,
        "CRITICAL": Fore.RED + Style.BRIGHT,
    }

    # Map state → icon
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
    }

    def __init__(self, log_dir: str = "logs", log_level: str = "INFO"):
        self.log_dir = log_dir
        os.makedirs(log_dir, exist_ok=True)

        # File logger
        log_filename = datetime.now().strftime("emulator_%Y%m%d_%H%M%S.log")
        log_path = os.path.join(log_dir, log_filename)

        self.logger = logging.getLogger("EmulatorPlane")
        self.logger.setLevel(getattr(logging, log_level, logging.INFO))
        self.logger.handlers.clear()

        # File handler
        fh = logging.FileHandler(log_path, encoding="utf-8")
        fh.setFormatter(logging.Formatter(
            "%(asctime)s | %(levelname)-8s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        ))
        self.logger.addHandler(fh)

        # Console handler (không dùng format chuẩn, dùng custom print)
        self._console_enabled = True

    def _console_print(self, level: str, message: str):
        """In ra console với màu sắc."""
        if not self._console_enabled:
            return
        color = self.COLORS.get(level, "")
        timestamp = datetime.now().strftime("%H:%M:%S")
        line = f"{Fore.WHITE}[{timestamp}]{Style.RESET_ALL} {color}{message}{Style.RESET_ALL}"
        try:
            print(line)
        except UnicodeEncodeError:
            # Legacy Windows consoles often expose cp1252. Logging must never
            # terminate capture just because an icon cannot be represented.
            encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
            print(line.encode(encoding, errors="replace").decode(encoding))

    def debug(self, msg: str):
        self.logger.debug(msg)
        self._console_print("DEBUG", msg)

    def info(self, msg: str):
        self.logger.info(msg)
        self._console_print("INFO", msg)

    def warning(self, msg: str):
        self.logger.warning(msg)
        self._console_print("WARNING", msg)

    def error(self, msg: str):
        self.logger.error(msg)
        self._console_print("ERROR", msg)

    def critical(self, msg: str):
        self.logger.critical(msg)
        self._console_print("CRITICAL", msg)

    def state(self, state_name: str, detail: str = ""):
        """Log chuyển state với icon."""
        icon = self.STATE_ICONS.get(state_name, "▶")
        msg = f"{icon} [{state_name}] {detail}"
        self.info(msg)

    def bet_result(self, round_num: int, cost: int, result: str,
                   balance_before: int, balance_after: int):
        """Log kết quả cược."""
        profit = balance_after - balance_before
        icon = "✅" if result == "WIN" else "❌"
        msg = (
            f"{icon} Kèo {round_num} | Đặt: {cost}xu | "
            f"KQ: {result} | Lãi: {profit:+d}xu | "
            f"Số dư: {balance_before} → {balance_after}"
        )
        self.info(msg)

    def streak_info(self, streak_length: int, trigger: int):
        """Log thông tin chuỗi bệt Rau."""
        if streak_length >= trigger:
            msg = f"🔥 Bệt RAU: {streak_length} kèo (≥ {trigger}) → VÀO LỆNH!"
            self.warning(msg)
        else:
            msg = f"🌿 Bệt RAU: {streak_length}/{trigger} kèo → Tiếp tục rình..."
            self.info(msg)

    def balance_info(self, balance: int, label: str = ""):
        """Log số dư."""
        msg = f"💎 Số dư{' (' + label + ')' if label else ''}: {balance:,} xu"
        self.info(msg)

    def separator(self, char: str = "─", length: int = 50):
        """In đường kẻ phân cách."""
        print(f"{Fore.WHITE}{char * length}{Style.RESET_ALL}")
