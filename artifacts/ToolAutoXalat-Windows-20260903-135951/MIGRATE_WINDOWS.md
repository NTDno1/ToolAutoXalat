# Chuyển ToolAutoXalat sang máy Windows khác

## Thành phần cần cài trên máy mới

- BlueStacks 5, bật Android Debug Bridge (ADB).
- .NET 8 SDK.
- Node.js LTS.
- Python 3.11 trở lên.
- Tesseract OCR tại `C:\Program Files\Tesseract-OCR\tesseract.exe`.
- Android Platform Tools (`adb.exe`).
- Cloudflared nếu cần mở web ra Internet.

## Cấu hình BlueStacks

Ứng dụng hiện được hiệu chỉnh cho:

- ADB port: `5555`.
- Kích thước ảnh Android: `720 × 1500`.
- Ứng dụng Greedy mở đúng màn hình vòng quay.

Nếu đường dẫn ADB trên máy mới khác, sửa `emulator.adb_path` trong
`services\scanner\config.json`. Không cần và không nên điều khiển chuột khi chạy scanner.

## Khởi động lần đầu

Mở PowerShell tại thư mục đã giải nén rồi chạy:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
dotnet --version
node --version
python --version
python -m pip install -r services\scanner\requirements.txt
npm.cmd install --prefix apps\frontend
.\scripts\start-all.ps1
```

Mở dashboard nội bộ tại `http://127.0.0.1:5173` và kiểm tra trạng thái bằng:

```powershell
.\scripts\status.ps1
```

## Mở web ra Internet

```powershell
cloudflared tunnel --url http://127.0.0.1:5173 --protocol http2
```

Quick Tunnel sẽ tạo một địa chỉ `https://...trycloudflare.com` mới cho máy mới.

## Dữ liệu và cấu hình riêng

- Database nằm tại `data\greedy_stats.db` và đã được snapshot an toàn khi xuất gói.
- Cấu hình admin nằm trong `apps\backend\GreedyStats.Api\appsettings.Admin.json`.
- Cấu hình API/webhook nằm trong các file `appsettings*.json` của backend.
- Không chạy đồng thời hai máy cùng ghi vào một file database được chia sẻ qua mạng.
- File ZIP có thể chứa cấu hình riêng; không đăng công khai.
