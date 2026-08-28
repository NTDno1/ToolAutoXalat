# Greedy Stats Platform

## Cấu trúc triển khai

```text
ToolAutoXalat/
├─ apps/
│  ├─ backend/GreedyStats.Api/   ASP.NET Core 8 API + webhook worker
│  └─ frontend/                  React + TypeScript + Vite dashboard
├─ services/
│  └─ scanner/                   Python/Win32 scanner cho BlueStacks
│     ├─ assets/templates/       Mẫu 8 vật phẩm
│     ├─ src/                    Capture, detect, SQLite
│     ├─ scanner.py
│     └─ config.json
├─ data/
│  ├─ greedy_stats.db            SQLite dùng WAL
│  └─ captures/                  Crop lịch sử và ảnh debug lỗi
├─ runtime/
│  ├─ logs/
│  └─ processes.json             PID do start-all.ps1 quản lý
└─ scripts/
   ├─ start-all.ps1
   ├─ stop-all.ps1
   └─ status.ps1
```

Scanner phải chạy trên máy Windows đang chạy BlueStacks vì dùng `PrintWindow` để
đọc surface bị `FLAG_SECURE`. Nó không gọi `SetForegroundWindow`, không gửi input
và không di chuyển chuột. Backend/frontend có thể đóng gói container khi deploy;
scanner vẫn chạy như host service cạnh BlueStacks.

Web stack có thể build bằng `deploy/docker-compose.web.yml`; truyền webhook qua
biến môi trường `GREEDY_ALERT_WEBHOOK_URL`. Không container hóa scanner vì nó cần
Win32 surface của BlueStacks trên chính máy host.

## Luồng dữ liệu

1. Python kết nối ADB `127.0.0.1:5575` để kiểm tra emulator.
2. Mỗi 3 giây, scanner chụp surface BlueStacks trong nền và chuẩn hóa `720×1280`.
3. Tám tâm lịch sử là `x=[180,247,313,380,446,512,579,646], y=1187`.
4. Detector so khớp đa tỉ lệ đủ 8 icon và trả chuỗi newest-first.
5. Kèo mới hợp lệ khi `current[1:] == previous[:7]`. Nhờ so toàn chuỗi, hai kết
   quả giống nhau liên tiếp vẫn được ghi đúng. Scanner cũng có thể phục hồi nếu
   bỏ lỡ 2–4 lần dịch; ít hơn bốn ô trùng nhau sẽ bị coi là không đủ bằng chứng.
6. Python ghi SQLite WAL. ASP.NET đọc cùng DB, tính thống kê và cung cấp API.
7. React gọi API mỗi 3 giây.

## Cảnh báo

- `VEGETABLE_STREAK_15`: 15 Rau liên tiếp.
- `MEAT_STREAK_3`: 3 Thịt liên tiếp.
- `DETECTION_FAILED`: không đọc đủ 8 ô sau 4 lần quét.
- `RESULT_TIMEOUT`: sau thời điểm dự kiến 30 giây, 4 lần quét vẫn chưa có kèo.
- `SEQUENCE_DESYNC`: chuỗi thay đổi nhưng không thể căn chỉnh an toàn.

Điền API sau này tại `apps/backend/GreedyStats.Api/appsettings.json`:

```json
"Alerts": {
  "VegetableStreakThreshold": 15,
  "MeatStreakThreshold": 3,
  "WebhookUrl": "https://api-cua-ban.example/webhook"
}
```

Backend tạo khóa cảnh báo từ loại rule và ID đầu chuỗi nên không gửi trùng khi
worker poll nhiều lần.

## API

- `GET /health`
- `GET /api/results?page=1&pageSize=50` (`pageSize` từ 1 tới 10000)
- `GET /api/stats/today`
- `GET /api/scanner/status`
- `GET|POST /api/scanner/events`
- `PATCH /api/scanner/events/{id}/acknowledge`
- `GET /api/alerts`

## Chạy local

```powershell
.\scripts\start-all.ps1
.\scripts\status.ps1
.\scripts\stop-all.ps1
```

Frontend: `http://127.0.0.1:5173`  
Backend: `http://127.0.0.1:5117`
