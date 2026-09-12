# Scanner ADB cho điện thoại thật và giả lập

Scanner thống kê dùng chung tại `services/scanner/scanner.py`, giữ nguyên bộ nhận diện
8 ô lịch sử, popup kết quả, OCR số vòng, countdown, SQLite và dashboard.
Không cần chạy `RealPhone_Plane/main.py`: đó là chương trình tự động hóa/đặt cược cũ,
dùng bộ nhận diện và database khác.

## Kết quả kiểm tra trên điện thoại hiện tại

Ngày 12/09/2026, ADB nhận **Redmi K30 5G**, serial `c6ac0650`, Android 10,
độ phân giải thực **1080 × 2400**. Cửa sổ Greedy có cờ `SECURE`, nên lệnh
`adb exec-out screencap -p` trả ảnh rỗng. Nguồn `scrcpy` mới dùng luồng video
MediaCodec liên tục và đã chụp được Greedy trực tiếp, không root và không sửa APK.

Trên máy đã kiểm tra, Platform Tools `37.0.x` nhận thiết bị nhưng kẹt ở trạng
thái `offline`. Cấu hình Redmi dùng `C:\Program Files\BlueStacks_nxt\HD-Adb.exe`
(`ADB 1.0.36`) vì bản này bắt tay ổn định với Android 10. Đây chỉ là ADB client
kết nối tới điện thoại thật; scanner không đọc ảnh hay cửa sổ của BlueStacks.

Đã căn cấu hình [`config.redmi-k30.json`](../services/scanner/config.redmi-k30.json)
theo khung hình thực 720 × 1600. Kiểm tra live nhận diện được đủ 8 kết quả,
vòng 223 và countdown 26 giây. Ảnh popup tiếp theo nhận diện vòng 224, kết quả Ngô
với confidence 0,896. Popup tự đóng vẫn tắt theo mặc định để scanner chỉ đọc.

Đây là đường chụp thay thế phù hợp cho Redmi đang dùng Android 10; không xóa cờ
bảo vệ của BIGO. Android quy định [`FLAG_SECURE`](https://developer.android.com/reference/android/view/WindowManager.LayoutParams#FLAG_SECURE)
ngăn chụp theo các API thông thường. Khả năng stream nội dung bảo vệ phụ thuộc
phiên bản Android/ROM; Android 12 trở lên thường chặn cả shell mirroring theo
[ghi nhận chính thức của scrcpy](https://github.com/Genymobile/scrcpy/issues/3323).

Đã kiểm tra hồi quy bằng ảnh game cũ: đủ 8 kết quả, vòng 472, countdown 15s.
Các bài kiểm tra mô phỏng thay đổi độ phân giải/tỉ lệ 720×1280, 1080×1920,
1080×2400, 1440×3200 và 720×1500; đây là kiểm tra hình học, không phải xác nhận
đã chạy trên tất cả các mẫu điện thoại.

## Chạy từ thư mục gốc repository

Cài Python dependencies, Android Platform Tools, scrcpy, FFmpeg và Tesseract OCR.
Máy hiện tại đã có scrcpy 4.1 và FFmpeg:

```powershell
python -m pip install -r services/scanner/requirements.txt
python services/scanner/scanner.py --config services/scanner/config.phone.json --list-devices
python services/scanner/scanner.py --config services/scanner/config.redmi-k30.json --check
```

`--check` chụp ảnh và kiểm tra nhận diện, không ghi database, không bấm vào điện thoại.
Xem `runtime/scanner-check/report.json`; khi có ảnh sẽ có `raw.png`, `normalized.png`,
`regions.png` và các ảnh crop OCR. Exit code: `0` nhận diện lịch sử hoặc popup và số vòng
thành công, `1` lỗi kết nối/chụp/cấu hình, `2` có ảnh nhưng vùng nhận diện chưa đúng.
Countdown có thể không xuất hiện khi đang quay hoặc hiển thị kết quả.
Nếu kiểm tra thất bại, chỉ `report.json` của lần chạy đó là kết quả hiện tại;
các ảnh còn lại trong thư mục có thể là ảnh của lần kiểm tra trước.

Mở game, giữ điện thoại ở chiều dọc, giữ màn hình sáng và tắt các lớp che nội dung.
Thiết lập USB debugging và xác nhận khóa RSA theo [hướng dẫn ADB của Android](https://developer.android.com/tools/adb#Enabling).
Ảnh Redmi được giải mã trực tiếp từ luồng H.264 của scrcpy/MediaCodec, không tạo
cửa sổ desktop và không ghi video ra đĩa. Scanner không phụ thuộc BlueStacks,
chuột hay focus của máy tính; cũng không tự mở app hay đổi thiết lập màn hình.

Khi `--check` thành công, chạy scanner thống kê ở foreground:

```powershell
python services/scanner/scanner.py --config services/scanner/config.redmi-k30.json
```

Để chuyển scanner đang chạy sang điện thoại, giữ backend/dashboard hiện tại:

```powershell
.\scripts\restart-scanner.ps1 -ConfigPath .\services\scanner\config.redmi-k30.json
```

Để khởi động cả hệ thống khi hệ thống đang dừng:

```powershell
.\scripts\start-all.ps1 -ScannerConfig .\services\scanner\config.redmi-k30.json
```

Hai script kiểm tra nguồn điện thoại trước khi chuyển/khởi động dịch vụ. Scheduled
Task lưu đúng config và serial đã kiểm tra để dùng lại khi đăng nhập Windows.

Quay lại cấu hình BlueStacks cũ:

```powershell
.\scripts\restart-scanner.ps1
```

Chỉ chạy **một scanner cho một dashboard/database đang dùng**. Dữ liệu kết quả và
trạng thái khôi phục có serial riêng, nhưng heartbeat và countdown của dashboard
được thiết kế cho một nguồn đang hoạt động. Không chạy đồng thời hai nguồn vào
`data/greedy_stats.db`.

## Chọn thiết bị và Wi-Fi

`source.type = "adb"` dùng chụp trực tiếp; bỏ `source` hoặc đặt
`source.type = "bluestacks"` tiếp tục dùng cấu hình `emulator` và Win32 PrintWindow cũ.

| Cấu hình | Ý nghĩa |
| --- | --- |
| `source.adb_path` | Để trống: tìm `adb` trong PATH/Android SDK; có thể chỉ định đường dẫn tuyệt đối |
| `source.serial` | Để trống: chọn điện thoại duy nhất đang sẵn sàng; nhiều máy sẽ yêu cầu chọn rõ |
| `source.device_kind` | `phone` (mặc định), `emulator`, hoặc `any` |
| `source.connect_address` | Địa chỉ Wi-Fi `IP:PORT` cần `adb connect`; để trống khi dùng USB |
| `source.capture_backend` | `scrcpy` cho Greedy trên Redmi; `screencap` dùng PNG ADB thông thường |
| `source.scrcpy_path` | Để trống để tìm `scrcpy`; có thể chỉ định đường dẫn tuyệt đối |
| `source.ffmpeg_path` | Để trống để tìm `ffmpeg`; có thể chỉ định đường dẫn tuyệt đối |
| `source.scrcpy_server_path` | Tùy chọn; server phải đúng phiên bản với scrcpy client |
| `source.video_max_size` | Cạnh dài tối đa của video; Redmi dùng 1600 → ảnh 720 × 1600 |
| `source.video_max_fps` | FPS tối đa của stream; mặc định 10 |
| `source.video_bit_rate` | Bitrate H.264; mặc định 8 Mbps |
| `source.capture_timeout_seconds` | Giới hạn thời gian một lần chụp |
| `source.reconnect_interval_seconds` | Khoảng nghỉ trước khi thử kết nối lại |

Nguồn scrcpy chạy read-only (`audio=false`, `control=false`), giải mã frame mới nhất
trong bộ nhớ và dọn ADB forward/server tạm khi dừng. `scrcpy-server` phải cùng phiên
bản client; scanner tự đọc phiên bản client và không dùng giao thức của phiên bản khác.

Scanner bỏ thiết bị `unauthorized`/`offline` khi tự chọn, phân biệt emulator và điện
thoại, và giữ nguyên serial sau lần kết nối đầu tiên. Rút cáp rồi cắm lại sẽ thử
kết nối lại đúng máy; không tự chuyển sang điện thoại khác hoặc BlueStacks.
`--device` ghi đè serial cho lần chạy và dùng kết nối đã có trong `adb devices`.
Nếu serial trùng `source.connect_address`, scanner vẫn giữ địa chỉ này để tự
`adb connect` lại sau khi mất kết nối hoặc đăng nhập Windows.

Android 10 trên Redmi cần kết nối USB ban đầu để bật ADB TCP/IP. Nếu muốn chuyển
sang Wi-Fi, thực hiện trên cùng mạng theo [hướng dẫn Android](https://developer.android.com/tools/adb#wireless):

```powershell
adb -s c6ac0650 tcpip 5555
adb connect IP_DIEN_THOAI:5555
```

Sau đó đặt cả `source.serial` và `source.connect_address` bằng `IP_DIEN_THOAI:5555`
trong bản sao cấu hình. Android mới có thể ghép đôi bằng `adb pair` từ trước;
scanner không tự bật/gỡ ghép đôi hoặc khởi động lại ADB server.

## Nhiều độ phân giải và tỉ lệ màn hình

Ảnh điện thoại được thu nhỏ về chiều rộng `screen.output_width` (mặc định 720),
giữ nguyên tỉ lệ. Ảnh 1080×2400 trở thành 720×1600; không ép thành 720×1500.

Mỗi vùng `history`, `round`, `countdown`, `popup_dismiss` có:

- `reference_width`, `reference_height`: kích thước ảnh khi căn tọa độ.
- `scale_mode: "width"`: scale đều theo chiều rộng, giữ hình dạng icon/chữ.
- `anchor_y: "top" | "center" | "bottom"`: điểm neo dọc khi chiều cao khác.
- `scale_mode: "stretch"`: cách scale X/Y riêng của cấu hình cũ, vẫn là mặc định
  nếu thiếu trường này để tương thích BlueStacks.

Cấu hình điện thoại ban đầu neo lịch sử và popup xuống dưới; OCR vòng/countdown
neo phía trên. Màn hình có bố cục khác cần căn lại, không thể suy ra mọi vị trí
chỉ từ độ phân giải. Template icon cũng được so khớp theo kích thước tham chiếu.

Để loại thanh trạng thái, điều hướng hoặc viền đen, đặt `screen.viewport` bằng
tỉ lệ **0..1** của ảnh ADB gốc. Ví dụ minh họa cắt 96 px đầu và 54 px cuối của
ảnh 1080×2400 (cần đo trên ảnh máy thực tế):

```json
"screen": {
  "output_width": 720,
  "orientation": "portrait",
  "viewport": {"x": 0, "y": 0.04, "width": 1, "height": 0.9375}
}
```

Nếu đã đổi viewport, căn lại các ROI trên ảnh `normalized.png`. Khi điện thoại xoay
ngang, scanner từ chối khung hình cho tới khi trở lại đúng chiều. Scanner không gọi
`wm size`, `wm density` để thay đổi cấu hình Android.

## Căn vùng bằng ảnh

Khi nguồn cho phép chụp và đang hiện 8 kết quả cùng countdown:

```powershell
python services/scanner/scanner.py --config services/scanner/config.phone.json --calibrate --output services/scanner/config.my-phone.json
```

Cửa sổ chọn vùng xuất hiện trên máy tính. Kéo hình chữ nhật, nhấn Enter xác nhận;
phím C hủy và không lưu cấu hình. Lần lượt chọn:

1. Cả hàng **8 icon**, từ cạnh trái ô đầu tới cạnh phải ô cuối, bỏ nhãn “Result”.
2. Vùng nhãn NEW dưới ô mới nhất (vị trí dự kiến nếu nhãn đang ẩn).
3. Chỉ phần chữ số vòng hiện tại.
4. Chữ số countdown và hậu tố `s`.

Các icon được giả định cách đều; nếu bố cục khác, chỉnh `history.slot_centers_x`
riêng cho từng ô trong JSON. Công cụ lưu cấu hình mới, không ghi đè tệp đã có.
Đường dẫn template/database được điều chỉnh theo vị trí tệp mới.

```powershell
python services/scanner/scanner.py --config services/scanner/config.my-phone.json --check
```

Để căn popup, chụp lúc hiển thị kết quả, rồi chọn icon thắng, vùng nền game tối và
vùng nền sáng của hộp kết quả:

```powershell
python services/scanner/scanner.py --config services/scanner/config.my-phone.json --calibrate-popup --output services/scanner/config.my-phone-popup.json
```

Có thể thêm `--image DUONG_DAN_ANH_GOC.png` cho `--check`, `--calibrate` hoặc
`--calibrate-popup` để làm trên ảnh lưu sẵn. Truyền **ảnh ADB gốc**, không truyền
ảnh đã crop/normalize lần nữa.

`popup_dismiss.enabled` mặc định **false** cho điện thoại: chỉ quét, không chạm màn
hình. Nếu bật sau khi căn chính xác, tọa độ popup được chuyển ngược về pixel thật
và gửi tới chính serial đang chụp. Nguồn ADB từ chối `popup_dismiss.input_serial`
riêng để tránh bấm nhầm thiết bị. Luồng này không đặt cược.

## Kiểm thử

```powershell
python -m unittest discover -s services/scanner/tests -v
```

Kiểm tra chọn thiết bị, mất kết nối, ảnh rỗng, FLAG_SECURE, xoay màn hình, crop
thanh hệ thống, chuyển tọa độ chạm, scale template, đọc lại ảnh game, khôi phục
trạng thái theo serial và các bài kiểm thử thống kê hiện có.
