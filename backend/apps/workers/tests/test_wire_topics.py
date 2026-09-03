"""Editorial acceptance fixtures, including the user's examples and near misses.

Examples are synthetic relevance inputs, never assertions of historical fact.
Run without services: python -m unittest apps.workers.tests.test_wire_topics
"""
import unittest

from apps.core.wire_topics import classify_wire_topics, discovery_queries, WIRE_DISCOVERY_QUERIES
from apps.core.wire_prompt import DEFAULT_WIRE_FILTER_PROMPT


EXAMPLES = {
    "1a": (
        "Hoạt động tuần tra, huấn luyện của Trung Quốc ở khu vực bãi cạn Xca-bơ-rô gần đây",
        "Một số động thái đáng chú ý của Trung Quốc và Philippines ở khu vực bãi cạn Xca-bơ-rô",
        "Chủ trương của Malaysia lắp đặt hệ thống ra-đa trên đá Hoa Lau/Quần đảo Trường Sa",
        "Phản ứng xung quanh vụ va chạm giữa Philippines và Trung Quốc tại bãi cạn Cỏ Mây/Quần đảo Trường Sa",
    ),
    "1b": (
        "Trung Quốc ban hành Biện pháp quản lý Khu bảo tồn thiên nhiên cấp quốc gia Xca-bơ-rô",
        "Trung Quốc đẩy mạnh hoạt động du lịch tại Quần đảo Hoàng Sa",
        "Trung Quốc đề xuất thiết lập khu vực đánh bắt chung tại bãi cạn Xca-bơ-rô",
        "Trung Quốc đẩy mạnh tuyên truyền pháp lý liên quan đến vấn đề chủ quyền tại những vùng biển trọng điểm ở Biển Đông",
    ),
    "2a": (
        "Quốc vụ viện Trung Quốc ban hành Quy định về quản lý xuất nhập cảnh",
        "Hoạt động Khai hải, mở biển hằng năm của Trung Quốc",
        "Trung Quốc triển khai mô hình Đội tiên phong Đảng viên bảo vệ biên giới tại huyện Long Châu, tỉnh Quảng Tây",
        "Trung Quốc ban hành KH 5 năm lần thứ 15 về hiện đại hóa cửa khẩu và chính sách hoàn thuế đối với hàng hóa của người xuất cảnh",
        "Chính sách phát triển khu vực biên giới của tỉnh Quảng Tây/Trung Quốc giai đoạn 2023 - nửa đầu năm 2026",
        "Tỉnh Quảng Tây/Trung Quốc tổ chức đợt diễn tập kiểm tra công tác phối hợp giữa QĐ-CAND trong phòng thủ biên giới",
        "Trung Quốc hoàn thành 02 dự án trọng điểm thuộc Khu thí điểm y tế mở quốc tế Phòng Thành Cảng",
        "KH phát triển, mở cửa và đẩy mạnh ứng dụng các mô hình mới của tỉnh Vân Nam/Trung Quốc",
    ),
    "2b": (
        "Một số nội dung tại cuộc họp báo ngày 30.07.26 của BQP Trung Quốc",
        "Trung Quốc sửa chữa, đóng mới tàu tại Quảng Châu",
        "Thực tiễn, kinh nghiệm từ Trung Quốc liên quan đến công tác quản lý các tổ chức phi chính phủ nước ngoài",
        "Trung Quốc công bố KH 5 năm lần thứ 15 về phát triển ngành công nghiệp vật liệu nổ dân dụng an toàn",
        "Thông tin việc Trung Quốc phân tích hệ thống mô-đun tác chiến dạng container",
        "Trung Quốc khuyến khích người dân tố giác các hành vi vi phạm pháp luật về xuất khẩu khoáng sản lưỡng dụng chiến lược",
    ),
    "2c": (
        "Trung Quốc phê duyệt 04 dự án điện hạt nhân mới",
        "Trung Quốc ban hành Quy định sửa đổi về bảo vệ thiết kế bố cục mạch tích hợp",
        "KH thúc đẩy đổi mới công nghệ và ứng dụng tích hợp giao thức Internet phiên bản 6 giai đoạn 2026-2030 của Trung Quốc",
        "KH hành động về hợp tác phát triển Trí tuệ nhân tạo của Trung Quốc",
        "KH 5 năm lần thứ 15 về phát triển kinh tế biển của tỉnh Hải Nam giai đoạn 2026-2030",
        "KH 5 năm lần thứ 15 về quy hoạch mạng lưới GTVT giai đoạn 2026-2030 của Trung Quốc",
    ),
    "3a": (
        "Nhật Bản thành lập Cục Tình báo Quốc gia và dư luận, phản ứng liên quan",
        "Nhật Bản công bố Sách trắng Quốc phòng năm 2026",
        "KH thực hiện Chiến lược quốc phòng của Malaysia giai đoạn 2026-2030",
    ),
    "3b": (
        "Dự thảo nghị quyết thúc đẩy phát triển các thành phần kinh tế trong điều kiện mới của Đảng Nhân dân Cách mạng Lào",
        "Đánh giá trước thềm Hội nghị TW5 Ban Chấp hành TW Đảng Cộng sản Trung Quốc",
        "Kết quả phiên họp Hội đồng Bộ trưởng Campuchia",
        "Một số nội dung cuộc họp báo ngày 23/7 của BNG Trung Quốc",
    ),
    "3c": (
        "Đánh giá của giới chuyên gia, truyền thông xung quanh Hội nghị Trí tuệ nhân tạo năm 2026 tại Trung Quốc",
        "Bài phát biểu của Chủ tịch Trung Quốc Tập Cận Bình tại Hội nghị Trí tuệ nhân tạo thế giới năm 2026",
        "Chương trình hiện đại hóa tàu khu trục lớp Zumwalt của Mỹ",
    ),
    "4a": (
        "Cuộc diễn tập Hán Quang lần thứ 42 của Đài Loan",
        "Cuộc diễn tập SEACAT 2026 tại Xin-ga-po",
        "Hải quân Campuchia tổ chức huấn luyện đối với tàu chiến tiếp nhận từ Trung Quốc",
        "KH sơ bộ tổ chức Diễn tập phòng thủ đô thị của Đài Loan năm 2026",
        "Hoạt động hợp tác hàng hải đa phương giữa Mỹ, Nhật Bản và Philippines ở Biển Đông",
        "Hợp tác, diễn tập quân sự của QĐ IND gần đây",
        "Một số hoạt động quân sự đáng chú ý của Mỹ và các nước gần đây",
        "Dư luận xung quanh việc Hải quân Trung Quốc và Hải quân IND diễn tập trong vùng biển phía đông ĐL",
    ),
    "4b": (
        "Kết quả Hội nghị Bộ trưởng Quốc phòng Úc - Philippines lần thứ 3",
        "Thái Lan tiếp nhận lô xe thiết giáp Stryker mới từ Mỹ",
        "Lào chuẩn bị tổ chức Hội nghị Ủy ban biên giới chung Lào - Campuchia lần thứ IX",
        "Đánh giá của giới chuyên gia liên quan đến thỏa thuận Dự án hỗ trợ an ninh chính thức giữa Nhật Bản và Campuchia",
    ),
    "4c": (
        "Học thuyết Lá chắn ba mũi giáo quần đảo của QĐ IND",
        "Mỹ xây dựng hệ thống phòng thủ ở đảo Guam",
        "Đài Loan triển khai chương trình nâng cấp mạng lưới chỉ huy - điều khiển phòng không",
        "Trung Quốc phát triển tàu không người lái ba thân",
        "Trung Quốc đẩy mạnh nghiên cứu phát triển mạng 6G",
        "Lào đề xuất Trung Quốc hỗ trợ triển khai Dự án phát triển hệ thống xử lý Trí tuệ nhân tạo tiếng Lào",
    ),
    "5": (
        "Trung Quốc đưa 06 thực thể của Mỹ vào danh sách áp dụng biện pháp đối phó và tăng cường kiểm soát xuất khẩu UAV, công nghệ lưỡng dụng",
        "Trung Quốc thông báo đưa 14 doanh nghiệp của Liên minh Châu Âu vào danh sách kiểm soát xuất khẩu",
        "Mỹ bổ sung 43 doanh nghiệp Trung Quốc vào danh sách thực thể theo Đạo luật ngăn chặn lao động cưỡng bức người Duy Ngô Nhĩ",
        "Văn phòng Đại diện Thương mại Mỹ công bố kết luận điều tra đối với các quốc gia vi phạm các vấn đề liên quan đến lao động cưỡng bức",
    ),
    "6": (
        "Hoạt động chuẩn bị biểu tình của một số tổ chức Người Việt tại Úc phản đối chuyến thăm của TBT, CTN Tô Lâm",
        "Dư luận và hoạt động chống phá liên quan đến Biên đội tàu Hải quân Mỹ thăm VN",
        "Dư luận quốc tế đánh giá về chuyến thăm Việt Nam của Biên đội tàu sân bay CVN-73/Mỹ",
        "Thông tin liên quan đến các doanh nghiệp quốc phòng Nga tham gia Triển lãm Quốc phòng quốc tế VN 2026",
    ),
    "7": (
        "Các tổ chức, trang báo ở Úc phát tán các bài viết xuyên tạc lực lượng CAND Việt Nam",
        "Hoạt động chống phá liên quan đến NQ số 23-NQ/TW của BCT về công tác người Việt ở nước ngoài",
        "Dư luận, hoạt động chống phá kỳ họp không thường lệ lần thứ nhất, QH khóa XVI",
        "Dư luận, hoạt động chống phá liên quan đến Hội nghị lần thứ 3 Ban Chấp hành TWĐ",
        "Dư luận liên quan đến việc danh thắng Ngũ Hành Sơn, Đà Nẵng bị hiển thị trên KGM là thuộc Trung Quốc",
    ),
}

# Title-only examples with no development/place need a source lead to qualify.
LEADS = {
    "Một số động thái đáng chú ý": "Hải cảnh Trung Quốc triển khai tuần tra ở Xca-bơ-rô; Philippines phản ứng về hoạt động tại bãi cạn.",
    "Thực tiễn, kinh nghiệm": "Báo cáo đánh giá chính sách quản lý các tổ chức phi chính phủ nước ngoài của Trung Quốc.",
    "Thông tin việc Trung Quốc": "Trung Quốc công bố phân tích hệ thống mô-đun tác chiến dạng container.",
    "Trung Quốc khuyến khích": "Trung Quốc công bố chính sách quản lý xuất khẩu khoáng sản lưỡng dụng chiến lược.",
    "Dự thảo nghị quyết": "Đảng Nhân dân Cách mạng Lào công bố dự thảo nghị quyết về chính sách phát triển kinh tế.",
    "Học thuyết Lá chắn": "QĐ IND công bố học thuyết mới về phòng thủ quần đảo.",
    "Thông tin liên quan đến các doanh nghiệp": "Doanh nghiệp Nga công bố kế hoạch tham gia triển lãm quốc phòng Việt Nam.",
}


class WireTopicTests(unittest.TestCase):
    def test_editorial_examples(self):
        for code, titles in EXAMPLES.items():
            for title in titles:
                lead = next((text for prefix, text in LEADS.items() if title.startswith(prefix)), "")
                with self.subTest(code=code, title=title):
                    result = classify_wire_topics({"title": title, "summary": lead})
                    self.assertIn(code, result.codes, result.reason)

    def test_english_chinese_and_vietnamese_aliases(self):
        cases = (
            ("China announces new coast guard patrols at Huangyan Island", "", "1a"),
            ("中国海警在黄岩岛开展巡航", "", "1a"),
            ("广西发布边境口岸发展规划", "", "2a"),
            ("国务院批准新核电项目", "", "2c"),
            ("Japan publishes defense white paper", "The government announced its new defense policy.", "3a"),
            ("US and Japan sign regional air-defense missile agreement", "", "4b"),
            ("USTR publishes forced labor investigation findings", "", "5"),
        )
        for title, summary, code in cases:
            with self.subTest(title=title):
                self.assertIn(code, classify_wire_topics({"title": title, "summary": summary}).codes)

    def test_near_miss_noise(self):
        titles = (
            "China AI smartphone sale: the best prices this week",
            "Vietnam military families celebrate birthday at naval base",
            "Philippines coast guard basketball championship results",
            "China launches travel guide to Paracel islands with discount tours",
            "China approves a new restaurant in Yunnan",
            "Guangxi tourism company announces new hotel",
            "Taiwan school organizes a fire drill",
            "China state media announces football exercise for school children",
            "US Navy announces birthday celebration in Guam",
            "China military factory wins employee sports competition",
            "China company develops a 6G consumer phone",
            "Stryker medical equipment sales in Thailand rise",
            "US company announces a civilian aircraft contract",
            "China artificial intelligence stock prices climb",
            "Australia announces domestic tank procurement contract",
            "China announces a local bus timetable in Yunnan",
            "China plans a cafe in Yunnan",
            "China publishes a history of military patrols at Scarborough",
            "Taiwan could one day deploy a hypothetical defense system",
            "Vietnam religion and ethnic communities celebrate annual festival",
            "Ý kiến phê bình chính sách Việt Nam của một cá nhân",
            "China sanctions debate: a history of trade relations",
        )
        for title in titles:
            with self.subTest(title=title):
                self.assertFalse(classify_wire_topics({"title": title}).relevant)

    def test_metadata_and_related_articles_are_not_evidence(self):
        self.assertFalse(classify_wire_topics({
            "title": "New product launch", "feed": "China military",
            "country": "China", "tags": ["wire-topic-4a"],
            "url": "https://example.com/China-military-drill",
        }).relevant)
        self.assertFalse(classify_wire_topics({
            "title": "China city opens cafe",
            "content": "Menu and local opening times. " * 70 + "China announces military drill in Taiwan Strait",
        }).relevant)
        self.assertFalse(classify_wire_topics({
            "title": "China approves local project",
            "summary": "A neighborhood playground. " * 30 + "New nuclear power plans approved",
        }).relevant)

    def test_query_rotation_covers_every_subtopic_without_extra_calls(self):
        for count in (1, 2, 3, 4, 5, 6):
            seen = set()
            for slot in range(len(WIRE_DISCOVERY_QUERIES)):
                queries = discovery_queries(count=count, slot=slot)
                self.assertEqual(len(queries), count)
                seen.update(queries)
            self.assertEqual(seen, set(WIRE_DISCOVERY_QUERIES))

    def test_prompt_fits_existing_editor(self):
        self.assertLessEqual(len(DEFAULT_WIRE_FILTER_PROMPT), 12000)


if __name__ == "__main__":
    unittest.main()
