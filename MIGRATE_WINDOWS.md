# Chuyển ToolAutoXalat sang máy Windows khác bằng Git

## Phần mềm cần cài

- Git for Windows.
- BlueStacks 5 và bật Android Debug Bridge (ADB).
- .NET 8 SDK.
- Node.js LTS.
- Python 3.11 trở lên.
- Tesseract OCR, mặc định tại `C:\Program Files\Tesseract-OCR\tesseract.exe`.
- Android Platform Tools (`adb.exe`).
- Cloudflared nếu cần mở dashboard ra Internet.

## Lấy source code và database

Mở PowerShell trên máy mới:

```powershell
git clone https://github.com/NTDno1/ToolAutoXalat.git
cd ToolAutoXalat
```

Database đã được lưu trong Git tại `data\greedy_stats.db`. Sau khi clone, toàn bộ dữ liệu lịch sử trong bản commit sẽ có sẵn, không cần import thủ công.

## Cấu hình máy mới

Ứng dụng hiện được hiệu chỉnh cho BlueStacks ADB port `5555`, ảnh Android `720 x 1500`. Nếu đường dẫn ADB khác, sửa `emulator.adb_path` trong `services\scanner\config.json`.

Hai file sau không được đưa lên Git vì chứa mật khẩu và API key:

- `apps\backend\GreedyStats.Api\appsettings.Admin.json`
- `apps\backend\GreedyStats.Api\appsettings.Secrets.json`

Hãy tạo hoặc chép riêng hai file này vào máy mới. Không đăng chúng lên repository công khai.

## Cài dependencies và chạy

```powershell
Set-ExecutionPolicy -Scope Process Bypass
python -m pip install -r services\scanner\requirements.txt
npm.cmd install --prefix apps\frontend
.\scripts\start-all.ps1
```

Dashboard nội bộ chạy tại `http://127.0.0.1:5173`. Kiểm tra trạng thái bằng:

```powershell
.\scripts\status.ps1
```

Nếu cần mở ra Internet:

```powershell
cloudflared tunnel --url http://127.0.0.1:5173 --protocol http2
```

## Chuyển hẳn sang máy mới

Ngay trước khi chuyển chính thức, dừng scanner trên máy cũ, tạo commit database cuối cùng rồi push. Sau đó `git pull` trên máy mới và mới khởi động scanner. Không chạy đồng thời hai scanner nếu muốn dữ liệu round không bị trùng.
