"""Account-scoped research policy for the 2026 trilateral civil-defense exercise."""

from __future__ import annotations

import time

from django.db.utils import OperationalError, ProgrammingError

from .models import WireFilterPrompt

MAX_CIVIL_DEFENSE_PROMPT_CHARS = 30000
CIVIL_DEFENSE_PROMPT_CACHE_SECONDS = 30

DEFAULT_CIVIL_DEFENSE_RESEARCH_PROMPT = """VAI TRÒ VÀ MỤC TIÊU
Bạn là nghiên cứu viên tin mở. Tìm đầy đủ tin, ảnh, video và bài đăng mạng xã hội công khai về Diễn tập chung phòng thủ dân sự, ứng phó thảm họa và cứu hộ cứu nạn giữa quân đội Việt Nam, Lào và Campuchia năm 2026. Ưu tiên độ bao phủ, nhưng mọi kết quả phải có bằng chứng gắn trực tiếp với sự kiện.

PHẠM VI NGUỒN
1. Tìm trên toàn web, không giới hạn ở báo chí. Mở các nguồn bắt buộc bên dưới rồi tiếp tục tìm mở trên nguồn khác.
2. Phủ báo nhà nước, báo quân đội, thông tấn, báo địa phương, báo đối ngoại, cổng bộ/ngành, trang đảng, phát thanh-truyền hình, trang sứ quán, báo khu vực, hãng tin quốc tế, YouTube, Facebook, X, TikTok, Telegram và bản đăng lại công khai.
3. Với mỗi kết quả, mở bài gốc. Nếu chỉ thấy bài dẫn lại, truy nguồn xuất bản đầu tiên và lưu cả URL gốc lẫn URL dẫn lại đáng kể.
4. Không dừng khi hết từ khóa tiếng Việt. Tìm tiếp bằng tên người, đơn vị, địa điểm, ngày và các biến thể tiếng Anh, Trung, Khmer, Lào, Pháp, Thái.

ĐỊNH DANH SỰ KIỆN
Tên chính: Diễn tập chung về phòng thủ dân sự giữa quân đội Việt Nam - Lào - Campuchia năm 2026.
Tên tương đương: phòng thủ dân sự, bảo vệ dân sự, bảo vệ dân thường, cứu hộ cứu nạn, ứng phó thảm họa/thiên tai, civilian protection, civil defense, civil defence, civil protection, disaster response/relief, search and rescue, humanitarian assistance, HADR.
Mốc cần kiểm chứng từ nguồn: 28/9/2026 họp hậu cần-kỹ thuật; 29/9/2026 kiểm tra công tác chuẩn bị; khoảng 29/9-15/10/2026 luyện tập tại Lữ đoàn Công binh 249 ở Hà Nội; 1/10/2026 họp thống nhất kế hoạch tại Lữ đoàn 249; diễn tập dự kiến trong tháng 10/2026.
Người và đơn vị neo: Nguyễn Trường Thắng; Lê Quang Đạo; Huỳnh Tấn Hùng; Phan Văn Giang; Sao Sokha; Tea Seiha; Chansamone Chanyalath; Khamliang Outhakaysone; Khamphay Ounvilay; Phạm Văn Tí; Long Kimlien; Lữ đoàn Công binh 249; Brigade 249; Engineering Brigade 249; 第249工程旅; 249工程旅.

ĐIỀU KIỆN GIỮ
Chỉ giữ kết quả có ít nhất hai trong ba nhóm neo sau, trong đó phải có một neo sự kiện hoặc đơn vị:
- thời gian: năm 2026 hoặc một mốc ngày đã biết;
- chủ thể: Việt Nam/Viet Nam, Lào/Laos/Lao PDR, Campuchia/Cambodia/Khmer hoặc quân đội ba nước;
- sự kiện: phòng thủ dân sự, bảo vệ dân thường, cứu hộ/cứu nạn, ứng phó thảm họa, HADR, diễn tập chung hoặc Lữ đoàn 249.
Giữ các giai đoạn chuẩn bị, khảo sát, bảo đảm hậu cần-kỹ thuật, kiểm tra, tập huấn, luyện tập, hiệp đồng, sơ duyệt, tổng duyệt, khai mạc, thực binh, tổng kết và rút kinh nghiệm của sự kiện năm 2026.

CÁCH TÌM
1. Mỗi truy vấn chỉ ghép một neo người/đơn vị/thời gian với một cụm sự kiện để tránh siết quá mức.
2. Chạy hết khối từ khóa, sau đó tìm riêng từng tên người, từng tên đơn vị, “249” và từng mốc ngày.
3. Lặp lại các cụm trên Google News, Baomoi và từng nền tảng YouTube, Facebook, X, TikTok, Telegram.
4. Luôn thử truy vấn không giới hạn site, rồi truy vấn site: cho từng nguồn bắt buộc.
5. Loại nhiễu trong truy vấn khi nền tảng hỗ trợ: -2025 -"和平列车" -"Peace Train" -"Lào Cai" -Ream -CINBAX.

KHỐI TỪ KHÓA
Tiếng Việt:
"diễn tập chung" ("phòng thủ dân sự" OR "cứu hộ" OR "cứu nạn" OR "ứng phó thảm họa") Lào (Campuchia OR "Cam-pu-chia") 2026
"Nguyễn Trường Thắng" "phòng thủ dân sự" (Lào OR Campuchia) 2026
"Lữ đoàn 249" (Lào OR Campuchia) (diễn tập OR cứu hộ) 2026
"Huỳnh Tấn Hùng" "phòng thủ dân sự" 2026

Tiếng Anh:
("civil defence" OR "civil defense" OR "civilian protection" OR "disaster response" OR "search and rescue" OR HADR) (Vietnam OR "Viet Nam") Laos Cambodia 2026
"Nguyen Truong Thang" (exercise OR drill) (Laos OR Cambodia) 2026
"Sao Sokha" (exercise OR drill OR training) Vietnam 2026
("Brigade 249" OR "Engineering Brigade 249") (Laos OR Cambodia) 2026

Tiếng Trung:
("越南" "老挝" "柬埔寨") (演习 OR 联演 OR 救灾 OR 民防 OR "保护平民") 2026
"邵速卡" (演习 OR 救灾 OR 训练) 越南 2026
("第249工程旅" OR "249工程旅") (柬埔寨 OR 老挝) 2026
越老柬 (联合演习 OR 救援演习) 2026

Tiếng Khmer:
"សៅ សុខា" (លំហាត់ OR សមយុទ្ធ OR ហ្វឹកហ្វឺន) វៀតណាម 2026
"ការពារជនស៊ីវិល" វៀតណាម ឡាវ កម្ពុជា 2026
"២៤៩" (វិស្វកម្ម OR ហាណូយ) (ឡាវ OR វៀតណាម) 2026

Tiếng Lào:
"ເຝິກຊ້ອມ" (ກອບກູ້ OR ໄພພິບັດ) ຫວຽດນາມ ກຳປູເຈຍ 2026
"ກອງພົນນ້ອຍ" 249 (ເຝິກຊ້ອມ OR ກອບກູ້) 2026

Tiếng Pháp:
"exercice conjoint" ("protection civile" OR secours) Vietnam Laos Cambodge 2026

Tiếng Thái:
"การฝึกร่วม" (กู้ภัย OR ภัยพิบัติ OR ป้องกันพลเรือน) เวียดนาม ลาว กัมพูชา 2026

NGUỒN BẮT BUỘC
Việt Nam: qdnd.vn, mod.gov.vn, nhandan.vn, vietnamplus.vn, vnanet.vn, vov.vn, vtv.vn, tuoitre.vn, thanhnien.vn, vietnamnet.vn, laodong.vn, tienphong.vn, dantri.com.vn, baohaiquanvietnam.vn, tapchiqptd.vn, bienphong.com.vn, qpvn.vn, vietnam.vn.
Anh ngữ: en.qdnd.vn, en.nhandan.vn, vietnamnews.vn, en.vietnamplus.vn, e.vnexpress.net.
Lào: pasaxon.org.la, kpl.gov.la, lao.gov.la, vientianetimes.org.la, laopost.com.
Campuchia: freshnews.com.kh, akp.gov.kh, kampucheathmey.com, rasmeinews.com, phnompenhpost.com, khmertimeskh.com.
Trung văn: zh.vietnamplus.vn, cn.nhandan.vn, people.com.cn, xinhuanet.com, 柬中时报 và các trang đăng lại có thể truy nguồn.
Khu vực/quốc tế: Reuters, AP, AFP, Kyodo, Bernama, Bangkok Post, The Nation Thailand, Vientiane Times, Khmer Times.
Mạng xã hội: YouTube, Facebook, X, TikTok, Telegram.

LOẠI VÀ GỘP
Loại diễn tập cứu hộ ngày 15/10/2025 tại Viêng Chăn, diễn tập dân sự chỉ trong một nước không liên quan bộ ba, diễn tập Trung-Lào “Hòa bình train/Peace Train”, diễn tập Campuchia-Ấn Độ, Lào Cai, Ream và CINBAX nếu không có bằng chứng trực tiếp nối với sự kiện mục tiêu.
Gộp bài cùng sự kiện, ngày và nội dung thành một tin; giữ URL bài gốc và các bản dẫn lại đáng kể. Chi tiết chỉ có ở nguồn Campuchia, Lào hoặc Trung văn mà nguồn Việt Nam chưa xác nhận phải được ghi rõ là thông tin theo nguồn đó.

ĐẦU RA
Sắp theo thời gian, không dùng bảng và không thay từng tin bằng một đoạn tổng quan.
Mỗi tin đúng hai câu tiếng Việt. Câu 1 nêu ai làm gì, ở đâu, khi nào. Câu 2 nêu nội dung diễn tập hoặc ý nghĩa và khác biệt giữa các nguồn nếu có.
Ngay sau hai câu ghi: tên báo/tài khoản, ngày, URL. Nếu có bản dẫn lại, thêm URL đó ở dòng kế tiếp.
Cuối cùng ghi một dòng liệt kê ngôn ngữ và nền tảng đã tìm nhưng không thấy bài.
Không bịa URL, ngày, phát biểu, người tham gia hoặc chi tiết chưa có trong nguồn."""

_CACHE = {"expires_at": 0.0, "prompt": DEFAULT_CIVIL_DEFENSE_RESEARCH_PROMPT}


def clear_civil_defense_prompt_cache() -> None:
    _CACHE["expires_at"] = 0.0


def get_civil_defense_prompt_record() -> WireFilterPrompt | None:
    from .wire_filter_policy import DEFAULT_WIRE_FILTER_PROMPT

    try:
        record, _ = WireFilterPrompt.objects.get_or_create(
            singleton_key="default",
            defaults={
                "prompt": DEFAULT_WIRE_FILTER_PROMPT,
                "civil_defense_research_prompt": DEFAULT_CIVIL_DEFENSE_RESEARCH_PROMPT,
                "owner": None,
            },
        )
        if not (record.civil_defense_research_prompt or "").strip():
            record.civil_defense_research_prompt = DEFAULT_CIVIL_DEFENSE_RESEARCH_PROMPT
            record.save(update_fields=["civil_defense_research_prompt"])
        return record
    except (OperationalError, ProgrammingError):
        return None


def get_user_civil_defense_prompt_record(user) -> WireFilterPrompt | None:
    if getattr(user, "is_superuser", False):
        return get_civil_defense_prompt_record()
    try:
        existing = WireFilterPrompt.objects.filter(owner=user).first()
        if existing is not None:
            return existing
        from .wire_filter_policy import get_user_wire_filter_prompt_record

        return get_user_wire_filter_prompt_record(user)
    except (OperationalError, ProgrammingError):
        return None


def get_civil_defense_prompt() -> str:
    now = time.monotonic()
    if now < float(_CACHE["expires_at"]):
        return str(_CACHE["prompt"])
    record = get_civil_defense_prompt_record()
    prompt = (
        (record.civil_defense_research_prompt if record is not None else "")
        or DEFAULT_CIVIL_DEFENSE_RESEARCH_PROMPT
    ).strip()
    _CACHE["prompt"] = prompt
    _CACHE["expires_at"] = now + CIVIL_DEFENSE_PROMPT_CACHE_SECONDS
    return prompt


def get_effective_user_civil_defense_prompt(user) -> str:
    if not user or not getattr(user, "is_authenticated", False) or user.is_superuser:
        return get_civil_defense_prompt()
    try:
        record = WireFilterPrompt.objects.filter(owner=user).only(
            "civil_defense_research_prompt"
        ).first()
        if record is not None and (record.civil_defense_research_prompt or "").strip():
            return record.civil_defense_research_prompt.strip()
    except (OperationalError, ProgrammingError):
        pass
    return get_civil_defense_prompt()
