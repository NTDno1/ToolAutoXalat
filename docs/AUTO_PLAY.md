# Tự động chơi theo xác suất (Admin)

## Cách hoạt động

Auto Play chạy ở backend và mỗi round chỉ tạo một quyết định trong khoảng còn
20–13 giây. Quyết định hiện chỉ dùng dự đoán xác suất từ lịch sử kết quả. Nhãn
HOT, mức xu đám đông, cầu Đỏ/Xanh và AI không được dùng để đặt cược.

Không có chiến thuật nào đảm bảo luôn thắng hoặc loại bỏ chuỗi thua. Phủ nhiều
cửa làm tăng xác suất trúng ít nhất một cửa nhưng có thể vẫn lỗ vì tổng tiền đặt
lớn hơn tiền trả của cửa thắng. Hệ thống vì vậy:

- Không all-in. Gỡ lỗ chỉ tăng tiền trong số bước đã cấu hình và chỉ khi cầu mới
  vẫn vượt toàn bộ bộ lọc xác suất/kỳ vọng.
- Giới hạn tổng tiền mỗi round theo phần trăm vốn, không chỉ giới hạn từng cửa.
- Giữ vốn dự phòng, dừng theo lỗ ngày và số lần thua liên tiếp.
- Bỏ round có độ tin cậy, khoảng cách top hoặc kỳ vọng không đạt ngưỡng.

## Năm chiến thuật

| Chiến thuật | Số cửa tối đa | Mức rủi ro | Mục tiêu |
|---|---:|---|---|
| Bảo toàn vốn | 1 | Thấp | Chỉ vào cửa rõ nhất, giữ 80% vốn |
| Một cửa có giá trị | 1 | Vừa | Chọn kỳ vọng dương tốt nhất |
| Cân bằng nhiều cửa | 2 | Vừa | Phủ chọn lọc hai cửa đạt chuẩn |
| Phủ hai cửa | 2 | Vừa | Ưu tiên hai xác suất cao nhất có kỳ vọng không âm |
| Phủ chọn lọc ba cửa | 3 | Cao | Mở rộng độ phủ nhưng vẫn chặn tổng tiền |

Mỗi kế hoạch chọn một mệnh giá `2`, `10`, `50`, `100` hoặc `1000` xu đúng một
lần. Sau đó backend chạm từng vật phẩm theo số lần đã cấu hình. Ví dụ chọn xu
`10` và đặt `2` lần cộng mệnh giá/cửa thì mỗi cửa được chọn nhận `20 xu`.

Mỗi chiến thuật có cấu hình riêng do người dùng lưu:

- Xác suất tối thiểu cho từng cửa **Rau**.
- Xác suất tối thiểu cho từng cửa **Thịt/Special**; có thể đặt thấp hơn Rau để
  tool vẫn theo Thịt khi mô hình báo cao dù tần suất Thịt vốn thấp hơn.
- Số cửa tối đa từ 1 đến 3.
- Số lần thua liên tiếp trước khi dừng, từ 1 đến 20; đặt `0` để tắt riêng giới
  hạn chuỗi thua. Giới hạn lỗ ngày vẫn luôn hoạt động.

Việc dừng theo chuỗi thua nhằm ngăn tool tiếp tục đặt khi mô hình có thể đang lệch
so với trạng thái hiện tại. Đây là giới hạn rủi ro, không phải dự đoán rằng cầu sau
chuỗi thua sẽ thắng hay thua.

## Gỡ lỗ có giới hạn

Mỗi chiến thuật có công tắc gỡ lỗ, hệ số từ `1` đến `3` và tối đa `0–5` bước.
Sau một round có lãi/lỗ âm, bước gỡ tăng lên; round hòa hoặc có lãi đưa bước gỡ về
0. Round bị bỏ vì xác suất thấp không làm tăng bước và cũng không đặt tiền.

Ví dụ mệnh giá `10`, số lần cơ sở `1`, hệ số `x2`: tiền mỗi cửa dự kiến lần lượt
là `10 → 20 → 40 → 80`. Số lần chạm thực tế bị giới hạn ở 10 và tổng tiền vẫn
phải nằm dưới trần phần trăm vốn, vốn dự phòng và lỗ ngày. Nếu không thể đặt đúng
trong các giới hạn này, tool bỏ round thay vì cố gỡ.

Gỡ lỗ không làm thay đổi xác suất kết quả tiếp theo và không bảo đảm thu hồi khoản
lỗ. Nó chỉ là cách phân bổ tiền cược có rủi ro cao hơn.

## Mô phỏng và máy thật

- **Mô phỏng**: không gửi ADB, dùng vốn cấu hình để tính kết quả.
- **Máy thật (LIVE)**: đọc số dư mới từ scanner, chọn ô mệnh giá một lần, chạm
  1–3 vật phẩm, rồi chờ scanner đọc lại số dư và tiền dưới từng vật phẩm.

Nếu số dư hoặc tiền cược không khớp, Auto Play chuyển `VERIFY_STOP`, tắt chính
nó và không thử đặt lại. Điều này tránh đặt trùng khi ảnh quét chậm hoặc không rõ.

LIVE đang khóa bằng `AutoPlay:LiveExecutionEnabled=false`. Chỉ mở sau khi đã thử
OCR và tọa độ trên màn hình không nhận cược thật. Tọa độ mệnh giá nằm tại
`AutoPlay:ChipTargets`; tám vật phẩm nằm tại `AutoPlay:ItemTargets` trong
`apps/backend/GreedyStats.Api/appsettings.json`.

## Nhật ký và phiên chạy

Mỗi lần chuyển từ tắt sang bật tạo một phiên riêng, gồm vốn đầu, vốn hiện tại/vốn
cuối, tổng cược, lãi lỗ, số cầu đặt, bỏ, thắng, thua và số cầu rủi ro cao đã bỏ.

Mỗi round—kể cả round bỏ—đều có log. Bấm vào log để xem các cửa, tiền từng cửa,
xác suất, hệ số trả, kỳ vọng, lý do quyết định, kết quả thực tế và trạng thái xác
minh OCR. Round bỏ được cập nhật kết quả khi scanner ghi nhận cầu đó, với lãi/lỗ 0.

## Cơ sở xác suất

Thiết kế tránh tuyên bố “luôn thắng”: bài toán **gambler's ruin** cho thấy vốn hữu
hạn luôn có xác suất chạm 0 khi lặp cược, còn nghiên cứu về các canh bạc công bằng
cho thấy việc đổi cách phân phối tiền cược không tự tạo thêm lợi thế kỳ vọng. Các
chiến thuật trong tool chỉ quản trị độ biến động và mức lỗ, không biến một trò chơi
bất lợi thành trò chơi chắc thắng.

- [Gambler's Ruin – mô phỏng và công thức xác suất](https://probability.ca/jeff/js/gambler.html)
- [A Lattice of Gambles – Cuff, Cover, Kumar, Zhao](https://arxiv.org/abs/1208.4414)
