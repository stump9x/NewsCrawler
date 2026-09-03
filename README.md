# NewsCrawler

## Cập nhật VPS đang chạy

Sau khi SSH vào VPS, chạy tại thư mục dự án:

```bash
cd /root/NewsCrawler
git pull --ff-only origin main && docker compose up -d --build
```

Trạm tin tức tiếp tục ở **http://107.161.168.82:3000**. Dùng lại file `.env` hiện có.

Docker Compose tự build và khởi động các dịch vụ. Backend tự chạy migration,
tạo bản sao prompt/thẻ/cờ hiển thị, áp dụng bảy nhóm chủ đề và phân loại lại tin cũ.
Sau khi API sẵn sàng, Compose khởi động worker và giao diện theo thứ tự phụ thuộc.
Không cần chạy riêng lệnh sửa prompt hay phân loại tin.

Mỗi phiên bản bộ lọc chỉ được tự áp dụng **một lần trên mỗi cơ sở dữ liệu**.
Khởi động lại container không ghi đè prompt bạn đã chỉnh sau đó. Nếu cập nhật
thất bại, thay đổi dữ liệu của bước này được hoàn tác và lần khởi động sau sẽ thử lại.

Tin ngoài phạm vi được ẩn; tin và danh sách theo dõi vẫn được giữ. Bản sao nằm ở
`backend/.wire-backups/`, ngoài image Docker và Git. Dữ liệu PostgreSQL và các volume
đang dùng được giữ nguyên.

## Kiểm tra khi cần

```bash
docker compose ps
docker compose logs --tail=80 backend
```

Lần cập nhật đầu có thể cần vài phút để phân loại kho tin. Nếu `git pull` báo xung
đột, xử lý thay đổi cục bộ trước khi tiếp tục. Không dùng `docker compose down -v`
khi cập nhật vì tùy chọn đó xóa volume dữ liệu.

[Thiết kế bộ lọc và cách khôi phục](docs/designs/wire-topic-precision.md).
