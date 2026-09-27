# Mô hình dự đoán HOT và xu

Kênh `market-signals-v2-color-sides` được tách hoàn toàn khỏi `local-full-db-v3`. Endpoint cũ
`/api/predictions/next` và công thức cũ không bị thay đổi; endpoint mới là
`/api/predictions/market`.

## Dữ liệu

- Scanner lưu một snapshot mạnh nhất cho từng `source_serial + ngày + round`.
- Mỗi snapshot gồm vật phẩm HOT, cấp xu/mức hoạt động của tám vật phẩm và thời điểm quan sát.
  Mức cao nhất của round được giữ lại kể cả khi scanner khởi động lại giữa round.
- Backend nối snapshot với kết quả thật theo đúng máy, ngày và round. Dữ liệu mới chỉ
  được tăng trọng số sau khi đã có kết quả để đối chiếu.
- Mỗi lần phân tích chỉ đọc tối đa 500 kết quả và 500 snapshot đã ghép gần nhất.
- API trả trạng thái chờ trong đầu round, chờ HOT xuất hiện, tiếp tục gom mức xu và
  tự chốt khi đồng hồ còn 20 giây nếu chưa thấy tín hiệu.

## Công thức

1. Lấy phân phối của mô hình lịch sử cũ làm prior.
2. So độ giống của vector xu hiện tại với các round đã hoàn tất; HOT trùng được tăng
   độ gần, HOT khác bị giảm độ gần.
3. Ghi và tính riêng ma trận chuyển tiếp `HOT → kết quả thật` (ví dụ HOT Xiên → Bò),
   sau đó co mạnh về phân phối nền khi số mẫu của HOT hiện tại còn ít.
4. Tính phân phối kết quả thực nghiệm có trọng số độ mới.
5. Co Bayes về prior với 24 mẫu giả để vài quan sát đầu không làm lệch mạnh kết quả.
6. Ước lượng nghĩa vụ từng cửa bằng `cấp xu × hệ số trả thưởng`. Trên 500
   cầu, hệ thống so kết quả thật với phân phối ngẫu nhiên lý thuyết để đo xem cửa có
   nghĩa vụ thấp có xuất hiện nhiều bất thường hay không. Chỉ khi số liệu cho thấy
   thiên lệch, đặc trưng phơi nhiễm mới được tăng tối đa 14%.
7. Trọng số HOT/xu tăng dần theo cỡ mẫu hiệu dụng và bị chặn; khi chưa đủ dữ liệu,
   kết quả tự quay về mô hình nền.
8. Cửa đề xuất được xếp theo `xác suất ước lượng × hệ số trả thưởng`, thay vì chỉ chọn
   vật phẩm có xác suất thô cao nhất.

## Chỉ số nhà cái

- `tiền vào ước tính`: tổng số biểu tượng xu quan sát được ở tám cửa;
- `trả thưởng ước tính`: xu ở cửa thắng nhân hệ số trả thưởng;
- `lãi/lỗ ước tính`: tiền vào trừ trả thưởng;
- `dấu hiệu né nghĩa vụ`: 50% là chưa thấy khác biệt rõ so với phân phối lý thuyết,
  trên 50% nghĩa là kết quả quan sát nghiêng hơn về các cửa có nghĩa vụ thấp.
- `kịch bản hiện tại`: với từng cửa, tính `tổng cấp xu - cấp xu cửa đó × hệ số`; số
  âm là nhà cái lỗ tương đối nếu trả cửa đó, số dương là nhà cái lãi tương đối.

Các đại lượng này là đơn vị tương đối vì detector chỉ thấy số biểu tượng xu trên màn
hình, không thấy số tiền thật hoặc toàn bộ sổ cược của nhà cung cấp.

## Cơ sở và giới hạn

- UK Gambling Commission RTS 7 yêu cầu RNG chấp nhận được về tính ngẫu nhiên và không
  cho phép hành vi thích nghi/“percentage compensation” với sản phẩm thuộc phạm vi
  tiêu chuẩn: https://www.gamblingcommission.gov.uk/manual/remote-gambling-and-software-technical-standards/rts-7-generation-of-random-outcomes
- Nghiên cứu về cấu trúc thị trường cược cho thấy lợi nhuận nhà cái và thiên lệch giá
  không đồng nghĩa với việc kết quả từng ván bị điều khiển theo tiền cược:
  https://academic.oup.com/oep/article/78/1/90/8244336
- Mô hình risk-averse bookmaking cho thấy nhà cái có thể giảm rủi ro bằng cách điều
  chỉnh giá/odds theo lượng cược, thay vì điều khiển kết quả:
  https://onlinelibrary.wiley.com/doi/10.1111/ecca.12500
- Nghiên cứu online learning mô tả thuật toán cập nhật giá sau luồng cược để tối ưu
  lợi nhuận; đây là cơ sở để đo phản ứng theo dòng xu nhưng không phải bằng chứng trò
  chơi đang dùng chính thuật toán đó: https://arxiv.org/abs/2406.04062
- Vì chưa có tài liệu kỹ thuật của trò chơi đang quan sát, giao diện phải hiển thị đây
  là ước lượng thống kê, cỡ mẫu và mức tin cậy; không mô tả như công thức chắc thắng.
