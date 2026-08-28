"""
╔═══════════════════════════════════════════════════════════╗
║          EMULATOR PLANE - ToolAutoXalat                   ║
║          Greedy BIGO Automation (BlueStacks)               ║
╚═══════════════════════════════════════════════════════════╝

Chạy trên giả lập BlueStacks qua ADB localhost (port lấy từ config.json).

Usage:
    python main.py                         # Auto Betting (mặc định)
    python main.py --mode auto_betting     # Auto Betting
    python main.py --mode data_mining      # Chỉ đọc, ghi log
    python main.py --check                 # Kiểm tra kết nối
"""

import os
import sys
import json
import argparse
import signal
from datetime import datetime

from PIL import Image

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(errors="replace")

from src.logger import GameLogger
from src.adb_controller import EmulatorAdbController
from src.vision import VisionEngine
from src.strategy import StrategyManager
from src.database import Database
from src.state_machine import GameStateMachine


def load_config(config_path: str = "config.json") -> dict:
    """Load config từ file JSON."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    full_path = os.path.join(script_dir, config_path)
    with open(full_path, "r", encoding="utf-8") as f:
        return json.load(f)


def check_connection(config: dict, logger: GameLogger) -> bool:
    """Kiểm tra kết nối đến BlueStacks."""
    logger.separator("═", 55)
    logger.info("🔍 KIỂM TRA KẾT NỐI BLUESTACKS")
    logger.separator("─", 55)

    adb = EmulatorAdbController(config["emulator"], logger)

    # 1. Kết nối
    connected = adb.connect()
    icon = "✅" if connected else "❌"
    logger.info(f"  {icon} Kết nối ADB: {'OK' if connected else 'FAIL'}")

    if not connected:
        logger.error(
            "\n  ❌ Không kết nối được BlueStacks!\n"
            "  Kiểm tra:\n"
            "  1. BlueStacks đang chạy?\n"
            "  2. ADB đã cài đặt? (chạy 'adb version')\n"
            f"  3. Port đúng không? ({config['emulator']['adb_port']})\n"
            f"  4. Thử: adb connect 127.0.0.1:{config['emulator']['adb_port']}"
        )
        return False

    # 2. Resolution
    res = adb.resolution
    logger.info(f"  📐 Resolution: {res}")

    # 3. Screencap test
    import time
    start = time.time()
    frame = adb.screencap()
    elapsed = (time.time() - start) * 1000
    has_frame = frame is not None
    icon = "✅" if has_frame else "❌"
    logger.info(f"  {icon} Screencap: {'OK' if has_frame else 'FAIL'} ({elapsed:.0f}ms)")
    if has_frame:
        logger.info(f"  Capture backend: {adb.capture_backend} | Shape: {frame.shape}")

    # 4. Screen on
    screen = adb.is_screen_on()
    icon = "✅" if screen else "⚠️"
    logger.info(f"  {icon} Màn hình: {'Sáng' if screen else 'Tắt'}")

    logger.separator("═", 55)
    all_ok = connected and has_frame
    logger.info(f"  {'🎉 SẴN SÀNG!' if all_ok else '⚠️ Cần kiểm tra lại'}")

    adb.disconnect()
    return all_ok


def save_screenshot(config: dict, logger: GameLogger, output_path: str = "") -> bool:
    """Capture one OCR-ready frame without focusing the emulator window."""
    adb = EmulatorAdbController(config["emulator"], logger)
    if not adb.connect():
        return False

    try:
        frame = adb.screencap()
        if frame is None:
            logger.error("Không chụp được khung hình BlueStacks")
            return False

        if not output_path:
            script_dir = os.path.dirname(os.path.abspath(__file__))
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_path = os.path.join(
                script_dir,
                "data",
                "screenshots",
                f"bluestacks_{adb.port}_{timestamp}.png",
            )
        output_path = os.path.abspath(output_path)
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        Image.fromarray(frame[:, :, ::-1]).save(output_path)
        logger.info(
            f"Đã lưu screenshot nền: {output_path} | "
            f"backend={adb.capture_backend} | shape={frame.shape}"
        )
        return True
    finally:
        adb.disconnect()


def print_banner():
    """In banner khởi động."""
    banner = """
╔═══════════════════════════════════════════════════════════╗
║   🎮  ToolAutoXalat - EMULATOR PLANE                     ║
║   🖥️  Target: BlueStacks Emulator                        ║
║   🎯  Game: Greedy BIGO                                   ║
╚═══════════════════════════════════════════════════════════╝
    """
    print(banner)


def main():
    # Parse arguments
    parser = argparse.ArgumentParser(
        description="ToolAutoXalat - Emulator Plane (BlueStacks)"
    )
    parser.add_argument(
        "--mode", choices=["auto_betting", "data_mining"],
        default="auto_betting",
        help="Chế độ chạy: auto_betting (mặc định) hoặc data_mining"
    )
    parser.add_argument(
        "--check", action="store_true",
        help="Chỉ kiểm tra kết nối, không chạy automation"
    )
    parser.add_argument(
        "--config", default="config.json",
        help="Đường dẫn file config (mặc định: config.json)"
    )
    parser.add_argument(
        "--screenshot", nargs="?", const="", default=None,
        help="Chụp một frame nền rồi thoát; có thể truyền đường dẫn PNG"
    )
    args = parser.parse_args()

    # Banner
    print_banner()

    # Load config
    try:
        config = load_config(args.config)
    except FileNotFoundError:
        print(f"❌ Không tìm thấy file config: {args.config}")
        sys.exit(1)

    # Override mode từ config nếu có
    mode = args.mode or config.get("app", {}).get("mode", "auto_betting")

    # Init logger
    log_config = config.get("logging", {})
    logger = GameLogger(
        log_dir=log_config.get("log_dir", "logs"),
        log_level=log_config.get("log_level", "INFO")
    )

    if args.screenshot is not None:
        success = save_screenshot(config, logger, args.screenshot)
        sys.exit(0 if success else 1)

    # Check mode
    if args.check:
        success = check_connection(config, logger)
        sys.exit(0 if success else 1)

    # Init components
    logger.info("🔧 Khởi tạo components...")

    # ADB Controller
    adb = EmulatorAdbController(config["emulator"], logger)
    if not adb.connect():
        logger.critical("❌ Không kết nối được BlueStacks! Chạy --check để debug.")
        sys.exit(1)

    # Vision Engine
    vision = VisionEngine(config["vision"], logger)

    # Strategy Manager
    script_dir = os.path.dirname(os.path.abspath(__file__))
    strategy_path = os.path.join(
        script_dir, config["game"]["strategy_file"]
    )
    strategy = StrategyManager(strategy_path)
    logger.info(strategy.get_summary())

    # Database
    db_path = os.path.join(script_dir, log_config.get("db_path", "data/game_log.db"))
    db = Database(db_path)

    # Build game config for state machine
    game_config = {
        **config["game"],
        "scan_interval_ms": config["vision"].get("scan_interval_ms", 500),
        "chips": config["vision"].get("chips", {}),
        "bet_slots": config["vision"].get("bet_slots", {}),
    }

    # State Machine
    game = GameStateMachine(adb, vision, strategy, db, logger, game_config)

    # Graceful shutdown handler
    def signal_handler(sig, frame):
        logger.info("\n⚠️ Nhận tín hiệu dừng...")
        game.stop()
        db.close()
        adb.disconnect()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Start!
    try:
        game.start(mode=mode)
    except Exception as e:
        logger.critical(f"❌ Lỗi nghiêm trọng: {e}")
    finally:
        db.close()
        adb.disconnect()
        logger.info("👋 Kết thúc chương trình.")


if __name__ == "__main__":
    main()
