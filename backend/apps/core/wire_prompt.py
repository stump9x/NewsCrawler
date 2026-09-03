"""Human-readable editorial contract for the five operational news clusters."""

DEFAULT_WIRE_FILTER_PROMPT = """NHIỆM VỤ: LỌC SÁT NĂM NHÓM CHỦ ĐỀ
Cân bằng độ sát chủ đề và khả năng tìm đủ diễn biến trong ngày. Chỉ đưa vào Trạm tin tức tin có diễn biến, quyết sách, kế hoạch, năng lực hoặc phản ứng cụ thể thuộc một trong các nhóm dưới đây. Ví dụ minh họa phạm vi, không phải danh sách sự kiện hoặc cách diễn đạt duy nhất được phép giữ. Nhận cả từ đồng nghĩa, biến thể ngôn ngữ và diễn biến tương đương có bằng chứng.

ĐIỀU KIỆN BẮT BUỘC
1. Đọc tiêu đề và phần đầu nội dung: phải xác định được chủ thể/địa bàn + vấn đề chuyên biệt + hành động/quyết định/phản ứng được nguồn nêu. Tiêu đề có thể nêu hệ thống, lực lượng hoặc sự kiện; phần dẫn bổ sung chủ thể và hành động. Không bắt buộc mọi từ khóa đều nằm trong tiêu đề. Các yếu tố phải cùng ngữ cảnh, không ghép từ nhiều bản tin hoặc liên kết gợi ý.
2. Tên quốc gia, tên nguồn, tên quân đội, chữ “an ninh”, “AI”, “chiến lược” hay thẻ chủ đề đơn lẻ không đủ. Không suy ra tác động tới Việt Nam từ xuất xứ của nguồn báo.
3. Giữ tin chính sách dân sự nếu đúng lĩnh vực chiến lược/địa bàn bên dưới; không bắt buộc mọi tin phải có từ khóa quân sự. Ngược lại, không giữ mọi tin quân sự thế giới.
4. Phải có nguồn, đường dẫn, ngày xuất bản hợp lệ trong cửa sổ thời gian của hệ thống. Ví dụ chỉ minh họa phạm vi, KHÔNG chứng minh sự kiện đã xảy ra. Không gán ngày hiện tại cho bài không rõ ngày; không tự đổi năm để biến tin cũ thành tin mới.
5. Giữ bài đánh giá khi gắn một sự kiện/quyết sách/năng lực xác định và có căn cứ trong nguồn; loại suy đoán thuần túy, hồi cố, quảng cáo, danh sách tổng hợp thiếu diễn biến.

PHẠM VI QUỐC GIA
Ưu tiên Trung Quốc, Mỹ, Đài Loan, Nhật Bản, Việt Nam, các nước Đông Nam Á,
Úc, Triều Tiên và Hàn Quốc. Bài chỉ gồm các nước ngoài phạm vi (ví dụ Anh,
Iraq) thì loại. Bài Mỹ kèm một nước ngoài phạm vi cũng loại nếu không có đối
tác thuộc phạm vi hoặc nội dung tác chiến mạng/an ninh mạng nổi bật. Không
loại tin tác chiến mạng nổi bật chỉ vì bài có thêm một nước ngoài phạm vi.

NHÓM 1 — BIỂN ĐÔNG, KHU VỰC NHẠY CẢM/TRANH CHẤP
Hiện diện, tuần tra, chấp pháp, huấn luyện, diễn tập, va chạm và tăng cường năng lực thực địa: Xca-bơ-rô/Scarborough/Huangyan/黄岩岛; Cỏ Mây/Second Thomas/Ayungin/仁爱礁; Hoa Lau/Swallow Reef/Layang Layang; Trường Sa/Spratly; Hoàng Sa/Paracel. Ví dụ: tuần tra Trung Quốc, động thái Trung Quốc–Philippines, Malaysia lắp radar ở Hoa Lau, phản ứng vụ va chạm Cỏ Mây.
Biện pháp hành chính, pháp lý, kinh tế, tuyên truyền liên quan thực thi chủ quyền: quy chế khu bảo tồn Xca-bơ-rô, chính sách du lịch Hoàng Sa, đánh bắt chung, lệnh cấm đánh bắt và lập luận pháp lý về vùng tranh chấp. Loại cẩm nang du lịch, quảng bá tour và tin thủy sản thông thường.

NHÓM 2 — TRUNG QUỐC, BIÊN GIỚI VÀ LĨNH VỰC CHIẾN LƯỢC
Quy định xuất nhập cảnh/cửa khẩu, hoàn thuế xuất cảnh, khai hải–mở biển, mô hình bảo vệ biên giới Long Châu/Quảng Tây; chính sách phát triển biên giới; diễn tập quân đội–công an; dự án trọng điểm Phòng Thành Cảng; kế hoạch phát triển/mở cửa Vân Nam. Phải có biện pháp quản lý, kế hoạch, dự án hoặc hoạt động cụ thể; loại du lịch, tiêu dùng, lễ hội, số liệu thương mại đơn thuần ở cùng tỉnh.
Nội dung quốc phòng cụ thể tại họp báo BQP; sửa chữa/đóng mới tàu quân sự tại Quảng Châu; quản lý NGO nước ngoài; kế hoạch an toàn vật liệu nổ dân dụng; mô-đun tác chiến container; kiểm soát và tố giác vi phạm xuất khẩu khoáng sản lưỡng dụng. Loại vụ án dân sự, giới thiệu sản phẩm và tin tàu dân dụng không có nội dung chiến lược.
Phê duyệt dự án điện hạt nhân; bảo vệ thiết kế bố cục mạch tích hợp; kế hoạch IPv6, hành động hợp tác AI; quy hoạch kinh tế biển Hải Nam, mạng lưới giao thông quốc gia. Cần chính sách, phê duyệt, quy hoạch hoặc chương trình cấp nhà nước/tỉnh; loại tin cổ phiếu, ứng dụng AI tiêu dùng và quảng cáo công nghệ.

NHÓM 3 — TỔ CHỨC, HỘI NGHỊ VÀ QUYẾT SÁCH TRỌNG ĐIỂM
Thành lập/kiện toàn cơ quan tình báo quốc gia; Sách trắng Quốc phòng; chiến lược và kế hoạch thực hiện quốc phòng/an ninh quốc gia, chiến lược mạng của bộ quốc phòng. Ví dụ Nhật Bản, Malaysia. Loại lễ nhậm chức, bổ nhiệm đơn lẻ thiếu thay đổi cơ cấu/chức năng.
Nghị quyết/dự thảo của Đảng NDCM Lào; đánh giá/kết quả Hội nghị Trung ương ĐCSTQ; quyết sách Hội đồng Bộ trưởng Campuchia; nội dung chính sách trong họp báo ngoại giao Trung Quốc. Loại lịch gặp xã giao, thông điệp chung và họp báo không nêu vấn đề.
Chính sách/phát biểu/đánh giá có căn cứ tại Hội nghị AI thế giới ở Trung Quốc; chương trình hiện đại hóa quốc phòng cụ thể như tàu Zumwalt của Mỹ. Loại giới thiệu lịch hội chợ hoặc sản phẩm AI đơn thuần.

NHÓM 4 — QUÂN SỰ, HỢP TÁC VÀ NĂNG LỰC MỚI
Diễn tập Hán Quang, SEACAT, phòng thủ đô thị Đài Loan; huấn luyện hải quân Campuchia với tàu tiếp nhận; hợp tác hàng hải Mỹ–Nhật–Philippines; hoạt động quân sự/diễn tập Indonesia và các nước tại khu vực. Cần lực lượng, địa bàn, hoạt động cụ thể; không giữ thể thao, cứu trợ thông thường hay sinh hoạt đơn vị chỉ vì có quân nhân.
Thỏa thuận/kết quả hội nghị quốc phòng Úc–Philippines; Thái Lan nhận Stryker; Ủy ban biên giới Lào–Campuchia; hỗ trợ an ninh Nhật–Campuchia; chuyển giao/mua sắm có quan hệ hợp tác hoặc tác động khu vực. Loại hợp đồng vũ khí nội địa thông thường ngoài trọng tâm.
Học thuyết mới của Indonesia; hệ thống phòng thủ Guam; nâng cấp chỉ huy–điều khiển phòng không Đài Loan; tàu không người lái Trung Quốc; chương trình nghiên cứu 6G; dự án AI tiếng Lào có hợp tác Trung Quốc. Nhận các diễn biến tương đương về hệ thống phòng thủ tên lửa, tác chiến điện tử, vũ khí công nghệ mới và triển khai AI quân sự. Cần chương trình/học thuyết/năng lực xác định; loại suy đoán tính năng và bài công nghệ đại trà.

NHÓM 5 — TRỪNG PHẠT, ĐỐI PHÓ VÀ CẠNH TRANH CHIẾN LƯỢC
Quyết định áp dụng/bổ sung/sửa đổi trừng phạt, danh sách thực thể, kiểm soát xuất khẩu UAV/công nghệ lưỡng dụng; thực thi UFLPA; kết luận điều tra USTR về lao động cưỡng bức. Cần chủ thể ban hành, biện pháp và đối tượng/lĩnh vực cụ thể. Loại bình luận thương mại chung, giá cổ phiếu và số liệu xuất khẩu không có biện pháp mới.

CHỐNG DƯƠNG TÍNH GIẢ
Loại tin đời sống, thể thao, gia đình quân nhân, nghi lễ/kỷ niệm, lịch sử vũ khí, quảng cáo, tai nạn dân sự, tin tài chính thường nhật; bài chỉ kể tên nước/đơn vị/AI; nội dung ngoài năm nhóm; chứng cứ chỉ nằm trong URL, tên nguồn hoặc thẻ. Không dùng số tin cần đạt làm lý do hạ chuẩn.

TÙY CHỈNH VÀ ĐỀ XUẤT
Năm nhóm được thực thi bằng bộ quy tắc xác định trong hệ thống, không gọi AI. Văn xuôi ở đây mô tả tiêu chí; chỉ dòng GIỮ:/LOẠI: là tùy chỉnh từ khóa chạy trực tiếp. LOẠI ưu tiên hơn GIỮ; GIỮ chỉ ưu tiên tin đã vượt các điều kiện, không mở rộng phạm vi. Tin đề xuất theo dõi phải cùng phân nhóm cụ thể và quốc gia với một tin đã theo dõi; cùng nguồn hoặc hai thẻ chung không đủ.
GIỮ: Xca-bơ-rô; Cỏ Mây; Hoa Lau; Scarborough; Second Thomas; Phòng Thành Cảng; Sách trắng Quốc phòng; Hán Quang; SEACAT; kiểm soát xuất khẩu
LOẠI: cẩm nang du lịch; mẹo du lịch; đời sống quân nhân; thể thao quân đội; quảng cáo sản phẩm

KẾT QUẢ
Tin đạt được gắn phân nhóm và lưu đoạn bằng chứng đối chiếu. Đây là kết quả lọc độ liên quan, không phải xác minh tính đúng/sai của nguồn. Tin thiếu chứng cứ không tự động được đề xuất."""
