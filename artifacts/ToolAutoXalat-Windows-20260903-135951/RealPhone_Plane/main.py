"""
╔═══════════════════════════════════════════════════════════╗
║          REALPHONE PLANE - ToolAutoXalat                  ║
║          Greedy BIGO Automation (Điện thoại thật USB)      ║
╚═══════════════════════════════════════════════════════════╝

Chạy trên điện thoại Android thật qua USB Cable.
Tự động detect thiết bị, scale tọa độ, monitor health.

Usage:
    python main.py                         # Auto Betting (mặc định)
    python main.py --mode auto_betting     # Auto Betting
    python main.py --mode data_mining      # Chỉ đọc, ghi log
    python main.py --check                 # Pre-flight check
    python main.py --list-devices          # Liệt kê thiết bị
    python main.py --device SERIAL         # Chọn thiết bị cụ thể
"""

import os
import sys
import json
import argparse
import signal

from src.logger import GameLogger
from src.device_manager import DeviceManager
from src.adb_controller import RealPhoneAdbController
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


def print_banner():
    """In banner khởi động."""
    banner = """
╔═══════════════════════════════════════════════════════════╗
║   🎮  ToolAutoXalat - REALPHONE PLANE                     ║
║   📱  Target: Điện thoại Android thật (USB)                ║
║   🎯  Game: Greedy BIGO                                    ║
╚═══════════════════════════════════════════════════════════╝
    """
    print(banner)


def list_devices_cmd(config: dict, logger: GameLogger):
    """Liệt kê tất cả thiết bị kết nối."""
    dm = DeviceManager(
        adb_path=config["device"].get("adb_path", "adb"),
        logger=logger
    )
    devices = dm.list_devices()

    logger.separator("═", 55)
    logger.info("📱 DANH SÁCH THIẾT BỊ")
    logger.separator("─", 55)

    if not devices:
        logger.error("  Không tìm thấy thiết bị nào!")
        return

    for i, dev in enumerate(devices, 1):
        icon = "📱" if dev["type"] == "real_phone" else "💻"
        status_icon = "✅" if dev["status"] == "device" else "❌"
        model = dev.get("model", "Unknown")
        logger.info(
            f"  {i}. {icon} {dev['serial']} | "
            f"{status_icon} {dev['status']} | "
            f"Model: {model} | Type: {dev['type']}"
        )

    logger.separator("═", 55)
    phones = [d for d in devices if d["type"] == "real_phone"]
    logger.info(f"  📱 Điện thoại thật: {len(phones)}")


def pre_flight_check(config: dict, logger: GameLogger,
                     device_id: str = "") -> bool:
    """Chạy pre-flight check toàn diện."""
    dm = DeviceManager(
        adb_path=config["device"].get("adb_path", "adb"),
        logger=logger
    )

    # Chọn device
    if device_id:
        dm.select_device(device_id)
    else:
        selected = dm.auto_select_phone()
        if not selected:
            return False

    # Pre-flight check
    checks = dm.pre_flight_check()
    return all(checks.values())


def main():
    # Parse arguments
    parser = argparse.ArgumentParser(
        description="ToolAutoXalat - RealPhone Plane (USB)"
    )
    parser.add_argument(
        "--mode", choices=["auto_betting", "data_mining"],
        default="auto_betting",
        help="Chế độ chạy: auto_betting (mặc định) hoặc data_mining"
    )
    parser.add_argument(
        "--check", action="store_true",
        help="Chạy pre-flight check"
    )
    parser.add_argument(
        "--list-devices", action="store_true",
        help="Liệt kê tất cả thiết bị kết nối"
    )
    parser.add_argument(
        "--device", type=str, default="",
        help="Serial number của thiết bị (nếu có nhiều thiết bị)"
    )
    parser.add_argument(
        "--config", default="config.json",
        help="Đường dẫn file config (mặc định: config.json)"
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

    # Override mode
    mode = args.mode or config.get("app", {}).get("mode", "auto_betting")

    # Init logger
    log_config = config.get("logging", {})
    logger = GameLogger(
        log_dir=log_config.get("log_dir", "logs"),
        log_level=log_config.get("log_level", "INFO")
    )

    # ── List devices ──
    if args.list_devices:
        list_devices_cmd(config, logger)
        sys.exit(0)

    # ── Pre-flight check ──
    if args.check:
        success = pre_flight_check(config, logger, args.device)
        sys.exit(0 if success else 1)

    # ══════════════════════════════════════════════════════
    # MAIN FLOW
    # ══════════════════════════════════════════════════════
    logger.info("🔧 Khởi tạo components...")

    # ── Device Manager ──
    dm = DeviceManager(
        adb_path=config["device"].get("adb_path", "adb"),
        logger=logger
    )

    # Chọn thiết bị
    device_id = args.device or config["device"].get("device_id", "")
    if device_id:
        if not dm.select_device(device_id):
            logger.critical(f"❌ Thiết bị {device_id} không tìm thấy!")
            sys.exit(1)
    else:
        selected = dm.auto_select_phone()
        if not selected:
            logger.critical(
                "❌ Không tìm thấy điện thoại!\n"
                "   Chạy: python main.py --list-devices"
            )
            sys.exit(1)
        device_id = selected

    # Pre-flight check
    logger.info("🔍 Chạy pre-flight check...")
    checks = dm.pre_flight_check()

    if not checks.get("usb_debugging", False):
        logger.critical("❌ USB Debugging không hoạt động!")
        sys.exit(1)

    # Giữ màn hình sáng
    if config["device"].get("keep_screen_on", True):
        dm.keep_screen_on()

    # ── ADB Controller ──
    anti_detection = config.get("anti_detection", {})
    adb_config = {
        **config["device"],
        "device_id": device_id,
        "anti_detection_enabled": anti_detection.get("enabled", True),
        "min_delay_ms": anti_detection.get("min_delay_ms", 50),
        "max_delay_ms": anti_detection.get("max_delay_ms", 200),
        "random_offset_px": anti_detection.get("random_offset_px", 3),
    }
    adb = RealPhoneAdbController(adb_config, logger)

    if not adb.connect(device_id):
        logger.critical("❌ Không kết nối được thiết bị!")
        sys.exit(1)

    # ── Vision Engine ──
    # Dùng scale factor từ ADB controller
    vision = VisionEngine(
        config["vision"], logger,
        scale_x=adb._scale_x,
        scale_y=adb._scale_y
    )

    # ── Strategy Manager ──
    script_dir = os.path.dirname(os.path.abspath(__file__))
    strategy_path = os.path.join(
        script_dir, config["game"]["strategy_file"]
    )
    strategy = StrategyManager(strategy_path)
    logger.info(strategy.get_summary())

    # ── Database ──
    db_path = os.path.join(
        script_dir, log_config.get("db_path", "data/game_log.db")
    )
    db = Database(db_path)

    # ── Game Config ──
    game_config = {
        **config["game"],
        "scan_interval_ms": config["vision"].get("scan_interval_ms", 500),
        "health_check_interval_s": config["device"].get(
            "health_check_interval_s", 30
        ),
        "chips_base": config["vision"].get("chips_base",
                                            config["vision"].get("chips", {})),
        "bet_slots_base": config["vision"].get("bet_slots_base",
                                                config["vision"].get("bet_slots", {})),
    }

    # ── State Machine ──
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
