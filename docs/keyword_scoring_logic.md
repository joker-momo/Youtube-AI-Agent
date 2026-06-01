# Hướng dẫn Logic Chấm điểm Từ khóa (Keyword Scoring Logic)

Tài liệu này mô tả chi tiết quy trình tự động hóa tìm kiếm và chấm điểm từ khóa cơ hội cao (high-opportunity keywords) sử dụng Playwright, một **extension chấm điểm từ khóa** trên YouTube và ChatGPT để lên ý tưởng video.

---

## 1. Tổng quan kiến trúc (Overview)
Thay vì sử dụng API chấm điểm từ khóa trả phí, hệ thống sử dụng một trình duyệt giả lập (Playwright) mở trang kết quả tìm kiếm YouTube (`https://www.youtube.com/results?search_query={keyword}`) có cài đặt sẵn **extension chấm điểm từ khóa cho Chrome**.

Hệ thống sẽ cào dữ liệu từ bảng điều khiển (overlay panel) do extension tiêm (inject) vào thanh bên phải của trang tìm kiếm YouTube.

---

## 2. Các chỉ số thu thập (Metrics collected)
Mỗi từ khóa được quét sẽ trả về cấu trúc dữ liệu sau:
* **`score` (Overall Score / Điểm tổng quan):** Thang điểm từ 0 đến 100, đại diện cho mức độ cơ hội của từ khóa. Điểm càng cao nghĩa là từ khóa càng tiềm năng.
* **`volume` (Search Volume / Lượng tìm kiếm):** Mức độ tìm kiếm của người dùng (ví dụ: High, Medium, Low).
* **`competition` (Competition / Độ cạnh tranh):** Mức độ cạnh tranh của các kênh khác (ví dụ: High, Low, Very Low).
* **`related` (Related Keywords / Từ khóa liên quan):** Danh sách các từ khóa gợi ý liên quan đi kèm điểm số tương ứng do extension gợi ý.

---

## 3. Thuật toán lọc & chấm điểm: `_discover_top_keywords`

Quy trình chấm điểm và chọn ra từ khóa tối ưu nhất trải qua 2 giai đoạn:

```
  [Seeds ban đầu] ---> [Giai đoạn 1: Chấm điểm Seeds] ---> Lấy Related Keywords
                                                                    │
  [Top Keywords tốt nhất] <--- [Sắp xếp score DESC] <--- [Gộp & Khử trùng] <--- [Giai đoạn 2: Chấm điểm Related]
```

### Bước 1: Giai đoạn 1 - Chấm điểm Seeds gốc (Phase 1: Score Seeds)
* Hệ thống nhận vào danh sách các từ khóa hạt giống (`seeds`) được nhập thủ công hoặc lấy tự động từ các chủ đề thịnh hành trên Google Trends phù hợp với ngách của kênh (`niche`).
* Chạy trình duyệt để chấm điểm từng từ khóa hạt giống này qua extension.

### Bước 2: Trích xuất từ khóa liên quan (Related Keyword Extraction)
* Từ kết quả chấm điểm của các `seeds` ở Bước 1, hệ thống thu thập các từ khóa liên quan (`related keywords`) trong bảng điều khiển của extension.
* Loại bỏ các từ khóa trùng lặp và các từ khóa trùng với `seeds` gốc để tạo ra một "hồ chứa từ khóa liên quan" (`related_pool`).

### Bước 3: Giai đoạn 2 - Chấm điểm từ khóa liên quan (Phase 2: Score Related)
* Lấy ra tối đa `max_related` từ khóa (mặc định là 15) từ `related_pool`.
* Tiếp tục chạy trình duyệt để chấm điểm toàn bộ danh sách từ khóa liên quan này.

### Bước 4: Hợp nhất & Deduplicate (Merge & Deduplicate)
* Gộp kết quả chấm điểm của cả **Seeds gốc** và **Từ khóa liên quan**.
* Nếu một từ khóa xuất hiện nhiều lần, hệ thống sẽ thực hiện loại bỏ trùng lặp và **chỉ giữ lại bản ghi có điểm `score` cao nhất**.

### Bước 5: Sắp xếp & Chọn lọc (Sort & Select)
* Sắp xếp danh sách từ khóa đã gộp theo thứ tự **`score` giảm dần (DESC)**.
* Lấy ra **Top N** từ khóa tốt nhất (mặc định là `top_n = 8`) để làm cơ sở sản xuất kịch bản.

---

## 4. Xử lý các trường hợp đặc biệt (Edge Cases)
* **Lỗi "Not enough search data":** Đối với những từ khóa quá ngách hoặc lượng tìm kiếm quá thấp, extension không trả về điểm số. Hệ thống sẽ ghi nhận `score = None` và đánh dấu `note = "not_enough_search_data"`. Các từ khóa này sẽ được xử lý như các từ khóa có tín hiệu thấp để tránh gây gián đoạn luồng chạy của hệ thống.
* **Không bật được extension:** Nếu extension không hoạt động hoặc không đăng nhập, hệ thống sẽ báo lỗi `BrowserDriverError` cho từ khóa đó và chuyển sang từ khóa tiếp theo thay vì dừng toàn bộ tiến trình.

---

## 5. Ứng dụng kết quả vào ChatGPT (Integration with ChatGPT)
Sau khi có được **Top N** từ khóa cơ hội tốt nhất (điểm cân bằng giữa lượng tìm kiếm cao và độ cạnh tranh thấp), hệ thống sẽ gửi danh sách từ khóa này kèm theo thông tin chi tiết về kênh (Niche, Target Audience, Language...) cho ChatGPT với yêu cầu:
1. Tạo ra chính xác **X ý tưởng video**.
2. **Mỗi ý tưởng phải bám sát và nhắm mục tiêu trực tiếp vào một từ khóa duy nhất trong danh sách Top N**.
3. Tiêu đề gợi ý của video (`title_seed`) phải chứa hoặc phản ánh tự nhiên từ khóa mục tiêu đó.
