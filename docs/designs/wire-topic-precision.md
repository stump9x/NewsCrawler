# Trạm tin tức: lọc sát bảy nhóm nhiệm vụ

Chỉ giữ tin có chủ thể/địa bàn, lĩnh vực cụ thể và diễn biến hoặc quyết sách được nêu trong nội dung. Tên nguồn, thẻ quốc gia và từ khóa chung không thay thế bằng chứng. Các ví dụ người dùng cung cấp là mẫu phạm vi, không phải sự kiện đã được xác minh.

Triển khai đồng bộ danh mục chủ đề, prompt, truy vấn tìm kiếm và đề xuất theo tin theo dõi. Đánh giá lại tin cũ bằng cờ `wire_relevant`, không xóa tin hoặc danh sách theo dõi. Cập nhật prompt quản trị có lưu phiên bản cũ; giữ nguyên chính sách riêng đã tùy chỉnh.

Kiểm chứng bằng ví dụ dương/âm tiếng Việt, Anh, Trung; kiểm tra chỉ dẫn GIỮ không vượt rào chắn, không ghép bằng chứng từ các tin không liên quan, truy vấn xoay vòng đủ nhóm và đề xuất không dựa trên nguồn/quốc gia chung.

## Thực thi

- `apps/core/wire_topics.py`: 14 phân nhóm thuộc 7 nhóm nhiệm vụ; nhận diện Việt/Anh/Trung, ba điều kiện đồng thời trong đoạn bằng chứng giới hạn. Chỉ dùng tiêu đề/phần đầu nội dung; nguồn và URL không làm chứng cứ. Bộ lọc xác định không gọi LLM và không khẳng định độ tin cậy của nguồn.
- `apps/core/wire_prompt.py`: prompt tiếng Việt có tiêu chí bao gồm/loại, ví dụ, nguyên tắc không suy diễn và hướng dẫn GIỮ/LOẠI. Văn xuôi mô tả hợp đồng biên tập; thay đổi văn xuôi tự do không biến thành quy tắc AI.
- RSS, Exa và X cùng dùng bộ lọc; Exa xoay vòng đủ phân nhóm theo giờ trong hạn mức cũ, giữ chỗ cho từng truy vấn và không tính tin nhiễu vào hạn mức kết quả. Cấu hình truy vấn riêng của quản trị vẫn được giữ.
- Đề xuất cần cùng phân nhóm cụ thể và quốc gia với **cùng một** tin theo dõi còn hợp lệ. Không cộng nguồn hoặc ghép hai tin yêu thích khác nhau. Thứ tự thời gian xuất bản không đổi.
- Tin mới và tin được phân loại lại lưu `raw_payload.wire_scope` và thẻ có tên tiếng Việt. Quốc gia được suy ra từ chứng cứ nội dung, không từ tên miền nguồn. Giao diện có bộ chọn đủ 14 phân nhóm.
- `reclassify_wire_topics`: mặc định chỉ xem trước. Khi áp dụng phải có file JSONL mới để sao lưu các cờ, thẻ và prompt trước khi sửa. Không xóa bản tin, tài khoản hoặc mục theo dõi. Có lệnh khôi phục. Prompt quản trị và bản sao kế thừa được cập nhật; giữ tùy chỉnh riêng của người dùng.

## Kiểm tra

```sh
cd backend
python manage.py test apps.workers.tests.test_wire_topics apps.workers.tests.test_wire_noise_filter apps.workers.tests.test_wire_discovery_scope apps.core.tests.test_wire_topic_recommendations apps.core.tests.test_wire_filter_policy_api --settings=config.test_settings --noinput
```

Bộ kiểm tra gồm 69 ví dụ phạm vi (một số tiêu đề mơ hồ được bổ sung đoạn dẫn giả lập để có chứng cứ), các tin gần giống nhưng ngoài phạm vi, truy vấn xoay vòng, đề xuất riêng từng tài khoản và cập nhật/khôi phục dữ liệu. SQLite dùng cho kiểm tra ORM độc lập; không thay cấu hình PostgreSQL sản xuất. Kết quả này không phải tỷ lệ chính xác đo trên dữ liệu tin thực tế. Cần xem trước kết quả phân loại kho tin trên VPS khi triển khai.

## Triển khai bằng terminal, sau khi push GitHub

Cùng cách với the12w, tại thư mục NewsCrawler trên VPS:

```sh
git pull --ff-only origin main && docker compose up -d --build
```

Compose gọi `backend/start-backend.sh`: chạy migration, tạo quản trị, gọi
`prepare_wire_topics`, seed nguồn và khởi động Gunicorn. Worker/giao diện dùng
điều kiện backend khỏe sẵn có. Healthcheck dành thêm thời gian cho nâng cấp lần đầu.

`WireTopicRollout` lưu phiên bản hoàn tất trong PostgreSQL, không dùng file đánh
dấu trên container. Khóa hàng/khóa duy nhất ngăn hai backend nâng cấp đồng thời.
Toàn bộ cập nhật dữ liệu và dấu hoàn tất nằm trong một transaction; nếu lỗi,
transaction rollback, API chưa phục vụ và lần khởi động sau thử lại. Backup JSONL
được ghi trước các thay đổi, ở bind mount `backend/.wire-backups/`, loại khỏi Docker
build context. Sau khi đã hoàn tất, khởi động lại không ghi đè prompt tùy chỉnh.

Vẫn có thể dùng `reclassify_wire_topics` để xem trước/áp dụng thủ công khi cần,
nhưng không cần các lệnh đó trong cập nhật thông thường. Giữ nguyên `.env`, cổng
3000, container và volume hiện có. Nếu Git có xung đột, xử lý trước khi tiếp tục.

Khôi phục dữ liệu trước khi quay về phiên bản mã cũ (dùng đúng tên file đã tạo):

```sh
docker compose exec -T backend python manage.py reclassify_wire_topics --restore /app/.wire-backups/wire-TIMESTAMP.jsonl
docker compose exec -T backend python manage.py reclassify_wire_topics --apply --restore /app/.wire-backups/wire-TIMESTAMP.jsonl
```

Các lệnh trên là quy trình triển khai; chỉ đánh dấu triển khai hoàn tất sau khi chạy thật và xác nhận dịch vụ cổng 3000 hoạt động.
