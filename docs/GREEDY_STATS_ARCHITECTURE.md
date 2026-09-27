# Greedy Stats Platform

## Cấu trúc triển khai

```text
ToolAutoXalat/
├─ apps/
│  ├─ backend/GreedyStats.Api/   ASP.NET Core 8 API + webhook/prediction worker
│  └─ frontend/                  React + TypeScript + Vite dashboard
├─ services/
│  └─ scanner/                   Python/Win32 scanner cho BlueStacks
│     ├─ assets/templates/       Mẫu 8 vật phẩm + Pizza/Salad
│     ├─ src/                    Capture, detect, OCR Round, SQLite
│     ├─ scanner.py
│     └─ config.json
├─ data/
│  ├─ greedy_stats.db            SQLite WAL
│  ├─ backups/                   Backup trước migration
│  └─ captures/                  Crop lịch sử và ảnh debug lỗi
├─ runtime/
│  ├─ logs/
│  └─ processes.json             PID do start-all.ps1 quản lý
└─ scripts/
   ├─ start-all.ps1
   ├─ stop-all.ps1
   └─ status.ps1
```

Scanner chạy trên máy Windows chứa BlueStacks vì dùng `PrintWindow` để đọc surface bị
`FLAG_SECURE`. Scanner không gọi `SetForegroundWindow`, không gửi input và không di
chuyển chuột. Backend/frontend có thể đóng gói container; scanner vẫn là host service
bên cạnh BlueStacks.

## Scanner 5555

- ADB: `127.0.0.1:5555`.
- Kích thước chuẩn hóa: `720×1500`.
- Chu kỳ quét: `1,5 giây`.
- Tâm 8 ô lịch sử: `x=[178,242,306,370,434,498,562,626], y=1300`.
- Vùng lịch sử: `x=95, y=1245, w=600, h=110`.
- Vùng OCR `Today's N Round`: `x=450, y=135, w=265, h=80`.
- Tesseract được gọi khi tạo baseline/đồng bộ ngoại lệ; các Round tiếp theo tăng theo
  số ô dịch chuyển đã xác nhận để không làm chậm chu kỳ 1,5 giây.
- 10 loại kết quả: Cà rốt, Ngô, Cải, Cà chua, Bánh mì, Xiên, Đùi, Bò, Nổ Pizza,
  Nổ Xà lách. Pizza/Salad thuộc nhóm `SPECIAL` và ngắt bệt Rau/Thịt.

Kèo mới hợp lệ khi chuỗi newest-first dịch từ chuỗi trước. Detector đọc cả 8 ô nên
kết quả lặp (ví dụ hai Đùi hoặc hai Cà chua liên tiếp) vẫn được ghi đúng. Scanner có
thể phục hồi 2–4 dịch chuyển bị lỡ. Unique key `source_serial + round_local_date +
round_number` ngăn ghi trùng.

## Database và thống kê

Mỗi kết quả lưu Round, ngày Round, nguồn emulator, vật phẩm, nhóm, thời gian và chuỗi
8 ô. API cung cấp:

- thống kê Rau/Thịt/Special và từng vật phẩm theo ngày;
- bệt hiện tại, bệt dài nhất, histogram bệt 2, 3, 4… và chi tiết giờ/Round;
- danh sách kết quả phân trang 1–10000 dòng;
- danh sách ngày đã có dữ liệu;
- đăng ký số điện thoại nhận cảnh báo;
- dự đoán 8 vật phẩm và context để tích hợp AI ngoài.

## Webhook

Cấu hình trong `apps/backend/GreedyStats.Api/appsettings.json` hoặc biến môi trường:

```json
"Alerts": {
  "VegetableStreakThreshold": 10,
  "MeatStreakThreshold": 3,
  "WebhookUrl": "https://webhook-chung-cua-ban.example",
  "WebhookRetrySeconds": 30,
  "SystemEventBootstrapMaxAgeMinutes": 15,
  "PollIntervalSeconds": 2
}
```

- `WebhookUrl`: nhận cả `STREAK_ALERT` khi đạt 10 Rau/3 Thịt liên tục và
  `SCANNER_SYSTEM_EVENT` cho lỗi `ERROR`/`CRITICAL`.
- Khóa cảnh báo dựa trên rule + ID đầu chuỗi nên worker không gửi trùng.
- Lỗi HTTP hoặc mất n8n được giữ ở trạng thái `RETRY` và gửi lại sau
  `WebhookRetrySeconds`.
- Khi bật webhook lần đầu, chỉ sự kiện hệ thống mới trong khoảng
  `SystemEventBootstrapMaxAgeMinutes` được đưa vào hàng đợi; sự kiện đã vào hàng
  đợi tiếp tục retry đến khi n8n nhận thành công.

Mọi thông báo dùng chung envelope `schemaVersion`, `source`, `notificationId`,
`type`, `eventCode`, `severity`, `title`, `message`, `occurredAtUtc`, `data`.
Với cảnh báo bệt, `data` có thêm `deliveryMode: "BROADCAST"`, `recipientCount`,
`alert` và `recipients`. Dịch vụ webhook chung chịu trách nhiệm phát đồng loạt;
backend không gọi riêng từng số.

Ví dụ payload gửi sang n8n:

```json
{
  "schemaVersion": "1.0",
  "source": "ToolAutoXalat",
  "notificationId": "MEAT_STREAK_3:17321",
  "type": "STREAK_ALERT",
  "eventCode": "MEAT_STREAK_3",
  "severity": "WARNING",
  "title": "Bệt Thịt đạt 3 cầu",
  "message": "Đã xuất hiện 3 kết quả Thịt liên tục.",
  "occurredAtUtc": "2026-09-13T17:00:00Z",
  "data": {
    "deliveryMode": "BROADCAST",
    "recipientCount": 0,
    "alert": {
      "category": "MEAT",
      "streakLength": 3,
      "threshold": 3,
      "roundNumber": 95,
      "itemCode": "BO",
      "itemName": "Bò"
    },
    "recipients": []
  }
}
```

n8n có thể rẽ nhánh bằng `{{$json.type}}` hoặc `{{$json.eventCode}}`; ví dụ
`STREAK_ALERT` là cảnh báo bệt, còn `SCANNER_SYSTEM_EVENT` là lỗi scanner/kết nối.

### Thanh toán đăng ký

Màn thanh toán được mở ngay sau khi đăng ký số điện thoại. Cấu hình QR công khai
trong `apps/backend/GreedyStats.Api/appsettings.json`:

```json
"Payment": {
  "QrImageUrl": "https://img.vietqr.io/image/NGANHANG-SOTAIKHOAN-compact2.png?amount={amount}&addInfo={content}",
  "Amount": 100000,
  "Currency": "VND",
  "BankName": "Tên ngân hàng",
  "AccountName": "Tên chủ tài khoản",
  "AccountNumber": "Số tài khoản",
  "TransferPrefix": "DK",
  "Instructions": "Sau khi thanh toán, quản trị viên sẽ đối soát và thêm số điện thoại vào webhook chung."
}
```

`QrImageUrl` có thể là URL ảnh QR tĩnh hoặc URL mẫu với `{amount}`, `{content}`
và `{phone}`. Frontend thay các biến này theo từng đăng ký. Không đặt secret vào
nhóm `Payment` vì endpoint này được cung cấp công khai cho người thanh toán.

Docker Compose nhận `GREEDY_WEBHOOK_URL`; hai biến webhook cũ vẫn được giữ để
tương thích cấu hình trước đây.

## Dự đoán và cổng AI

Mặc định `local-full-db-v3` đọc toàn bộ database, tách chuỗi theo ngày kèo
23:00–23:00 và theo từng nguồn máy, sau đó kết hợp:

- mẫu chuyển tiếp có phần đuôi 1–8 cầu giống chuỗi hiện tại;
- hành vi tiếp diễn/đảo nhóm sau bệt Rau hoặc Thịt có độ dài tương tự;
- tần suất của toàn bộ lịch sử, ngày được chọn và trọng số độ mới trên mọi bản ghi;
- một lượng nhỏ xác suất nền theo hệ số trả thưởng;
- temperature sharpening để tách ứng viên có bằng chứng điều kiện mạnh hơn.

`probabilityPercent` của 8 vật phẩm luôn cộng xấp xỉ 100%. `signalScore` là chỉ số
dự đoán đã tách hạng 0–100 nên có thể cao hơn rõ rệt, nhưng không được hiểu là xác
suất thắng. Response còn có `patternMatches`, `reason`, `modelConfidencePercent`
và `leadingItemCode` để frontend giải thích kết quả.

Hệ số: Rau ×5, Bánh mì ×10, Xiên ×15, Đùi ×25, Bò ×45. Đây là thống kê tham khảo,
không bảo đảm kết quả.

## Hai bảng AI tách biệt

Bảng AI không thay thế `local-full-db-v3`. Backend cung cấp endpoint riêng
`GET /api/predictions/ai?date=yyyy-MM-dd&force=true`. Frontend không tự gọi AI theo Round;
chỉ khi người dùng bấm **Phân tích bằng AI** hoặc **Phân tích lại** mới phát sinh request.
Một bảng độc lập thứ hai dùng `groq/compound-mini` qua
`GET /api/predictions/compound?date=yyyy-MM-dd&force=true`; bảng này có cache, loading,
lỗi và nút phân tích riêng, không ghi đè kết quả GPT.

Cấu hình API OpenAI-compatible:

```json
"Prediction": {
  "AiBaseUrl": "https://api.groq.com/openai/v1",
  "AiRoute": "/chat/completions",
  "AiModel": "openai/gpt-oss-120b",
  "AiApiKey": "",
  "AiTestMode": false,
  "CompoundBaseUrl": "https://api.groq.com/openai/v1",
  "CompoundRoute": "/chat/completions",
  "CompoundModel": "groq/compound-mini",
  "CompoundApiKey": "",
  "CompoundTestMode": false
}
```

Có thể đặt `AiTestMode=true` để kiểm thử toàn bộ luồng frontend/backend bằng
`local-test-simulator` mà không gọi dịch vụ ngoài. Kết quả luôn được gắn nhãn
test rõ ràng. Khi có API key thật, đặt `AiTestMode=false` trước khi sử dụng.

Không commit API key. Local có thể đặt key trong `appsettings.Secrets.json` (đã Git-ignore),
biến môi trường `Prediction__AiApiKey`; Docker Compose dùng `GREEDY_AI_API_KEY`.
Nếu `CompoundApiKey` để trống, Compound Mini tự dùng lại `AiApiKey`; chỉ cấu hình key riêng
khi thực sự muốn tách tài khoản Groq.
Backend mã hóa một ký tự cho mỗi bản ghi và gửi đủ chuỗi của mọi ngày/nguồn, cùng ma trận
Markov, n-gram 1–8, survival của bệt, cửa sổ độ mới, thống kê theo ngày và baseline local.
AI phải xác nhận đúng số bản ghi/ngày, khai báo ít nhất bốn thuật toán và trả đủ đúng 8 mã;
backend từ chối kết quả sai, ensemble với baseline, chuẩn hóa 8 vật phẩm và hai nhóm Rau/Thịt
về 100%. Lỗi AI không ảnh hưởng bảng dự đoán nội bộ.

## API chính

- `GET /health`
- `GET /api/results?page=1&pageSize=50&date=yyyy-MM-dd`
- `GET /api/stats/today`
- `GET /api/stats/daily?date=yyyy-MM-dd`
- `GET /api/stats/days?limit=90`
- `GET /api/predictions/next?date=yyyy-MM-dd`
- `GET /api/predictions/ai?date=yyyy-MM-dd`
- `GET /api/predictions/compound?date=yyyy-MM-dd`
- `GET /api/predictions/context?date=yyyy-MM-dd`
- `GET|POST /api/subscribers`
- `DELETE /api/subscribers/{id}`
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
