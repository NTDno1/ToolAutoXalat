# ToolAutoXalat

## Hệ thống thống kê Greedy mới

Ba dự án chạy độc lập đã được tổ chức trong `apps/` và `services/`:

- `services/scanner`: Python quét BlueStacks hoặc điện thoại Android qua USB/Wi-Fi ADB, OCR Round/countdown và đọc đủ 8 ô/10 vật phẩm; chu kỳ quét theo cấu hình.
- `apps/backend/GreedyStats.Api`: ASP.NET Core API, SQLite, thống kê bệt theo ngày, dự đoán và webhook Zalo/admin.
- `apps/frontend`: React dashboard realtime, tự cập nhật mỗi 1,5 giây.

Khởi động toàn bộ bằng tiến trình ẩn (không chiếm chuột/focus):

```powershell
.\scripts\start-all.ps1
```

Mở dashboard tại `http://127.0.0.1:5173`. Xem thiết kế và hướng dẫn deploy tại
[`docs/GREEDY_STATS_ARCHITECTURE.md`](docs/GREEDY_STATS_ARCHITECTURE.md).

### Scanner thống kê trên điện thoại thật

Dùng chung scanner hiện tại với cấu hình điện thoại riêng, giữ tỉ lệ ảnh và cho
phép căn vùng quét theo từng màn hình:

```powershell
python services/scanner/scanner.py --config services/scanner/config.phone.json --list-devices
python services/scanner/scanner.py --config services/scanner/config.redmi-k30.json --check
```

Sau khi kiểm tra nhận diện thành công, chuyển scanner sang điện thoại:

```powershell
.\scripts\restart-scanner.ps1 -ConfigPath .\services\scanner\config.redmi-k30.json
```

Hướng dẫn kết nối, nhiều độ phân giải và cấu hình Redmi K30 5G:
[`docs/PHONE_SCANNER.md`](docs/PHONE_SCANNER.md).
Greedy chặn lệnh chụp PNG thông thường nhưng scanner dùng luồng video scrcpy trên
Redmi Android 10. Đã xác nhận trực tiếp 8 kết quả, vòng, countdown và popup.
`RealPhone_Plane` phía dưới là chương trình tự động hóa cũ, không phải scanner
thống kê dùng cho dashboard.

Hệ thống tự động hóa mini-game **Greedy BIGO** trên thiết bị Android.  
Hỗ trợ: Điện thoại thật (USB/WiFi ADB), Box Phone, Giả lập (BlueStacks/LDPlayer).

---

## 📁 Cấu trúc Dự án (2 Phase)

```
ToolAutoXalat/
│
├── Emulator_Plane/                    ← PHASE 1: Giả lập BlueStacks
│   ├── main.py                        # Entry point
│   ├── config.json                    # Config cho emulator (127.0.0.1:5575)
│   ├── requirements.txt              # Python dependencies
│   ├── strategies/
│   │   └── sinh_ton_19.json          # Data chiến thuật 19 vòng
│   ├── src/
│   │   ├── __init__.py
│   │   ├── adb_controller.py         # ADB qua localhost
│   │   ├── vision.py                 # OCR + Color Detection
│   │   ├── strategy.py               # Quản lý chiến thuật
│   │   ├── state_machine.py          # 5-state game loop
│   │   ├── database.py               # SQLite logging
│   │   └── logger.py                 # Console + File logging
│   ├── data/                         # SQLite DB files
│   └── logs/                         # Log files
│
├── RealPhone_Plane/                   ← PHASE 2: Điện thoại thật USB
│   ├── main.py                        # Entry point + pre-flight check
│   ├── config.json                    # Config cho real phone (USB + auto-scale)
│   ├── requirements.txt              # Python dependencies
│   ├── strategies/
│   │   └── sinh_ton_19.json          # Data chiến thuật 19 vòng
│   ├── src/
│   │   ├── __init__.py
│   │   ├── adb_controller.py         # ADB USB + auto-scale tọa độ
│   │   ├── device_manager.py         # Device detection & health ★
│   │   ├── vision.py                 # OCR + auto-scale ROI + retry
│   │   ├── strategy.py               # Quản lý chiến thuật
│   │   ├── state_machine.py          # 5-state + watchdog ★
│   │   ├── database.py               # SQLite logging
│   │   └── logger.py                 # Console + File + device info
│   ├── data/                         # SQLite DB files
│   └── logs/                         # Log files
│
├── docs/                              # 📚 Tài liệu
│   ├── Module_AI_Auto_TOOL.md        # Tài liệu kỹ thuật chính
│   └── Module AI Auto TOOL.docx      # Tài liệu gốc
│
├── config/                            # ⚙️ Config gốc
│   └── config.json
│
├── .gitignore
└── README.md                          # File này
```

---

## 🚀 Bắt đầu nhanh

### 1. Chọn Phase phù hợp

| Phase | Thư mục | Thiết bị | Kết nối |
|:---|:---|:---|:---|
| **Phase 1** | `Emulator_Plane/` | BlueStacks | ADB qua `127.0.0.1:5575` |
| **Phase 2** | `RealPhone_Plane/` | Điện thoại thật | USB Cable |

### 2. Cài đặt

```bash
# Phase 1: Emulator
cd Emulator_Plane
pip install -r requirements.txt

# Phase 2: RealPhone
cd RealPhone_Plane
pip install -r requirements.txt
```

### 3. Kiểm tra kết nối

```bash
# Phase 1: Kiểm tra BlueStacks
cd Emulator_Plane
python main.py --check

# Chụp một frame PNG trong nền (không focus/click cửa sổ BlueStacks)
python main.py --screenshot

# Hoặc chọn file đầu ra
python main.py --screenshot ..\artifacts\bluestacks_5575.png

# Phase 2: Liệt kê thiết bị + pre-flight check
cd RealPhone_Plane
python main.py --list-devices
python main.py --check
```

### 4. Chạy

```bash
# Auto Betting (tự động đặt cược)
python main.py --mode auto_betting

# Data Mining (chỉ đọc, ghi log - KHÔNG click)
python main.py --mode data_mining
```

---

## ⚡ So sánh 2 Phase

| Tiêu chí | Emulator_Plane | RealPhone_Plane |
|:---|:---|:---|
| **Thiết bị** | BlueStacks emulator | Điện thoại Android thật |
| **Kết nối** | `127.0.0.1:5575` (localhost) | USB Cable (`adb devices`) |
| **Tọa độ ROI** | Cố định | Auto-scale theo resolution |
| **Anti-detection** | Không | Random delay + offset |
| **Health check** | Không | Watchdog thread (USB, pin, màn hình) |
| **Device Manager** | Không | Có (detect, select, info) |
| **Pre-flight check** | Connection test | Full check (USB, pin, BIGO, screen) |

---

## 📋 Tóm tắt Kiến trúc

```
main.py
  ├── ADB Controller ─── screencap() / tap() / multi_tap()
  ├── Vision Engine ──── read_timer() / read_history() / read_balance()
  ├── Strategy Manager ── load() / get_round() / calculate_taps()
  ├── Database ────────── log_streak() / log_bet() / get_stats()
  └── State Machine ───── WAIT → ANALYZE → BET → SPINNING → RESULT
```

### State Machine Flow

```
WAIT ──(timer > 5s)──→ ANALYZE ──(bệt < N)──→ WAIT
                           │
                     (bệt ≥ N kèo Rau)
                           │
                           ↓
                         BET ──→ SPINNING ──→ RESULT
                                                │
                                           WIN → WAIT (reset)
                                           LOSE → BET (round+1)
                                           STOP_LOSS → WAIT (reset)
```

---

## 📄 License

Private project - Internal use only.
