"""Account-scoped prompt policy for Mindmap AI refinement."""

from __future__ import annotations

import time

from django.db.utils import OperationalError, ProgrammingError

from .models import WireFilterPrompt

MAX_MINDMAP_PROMPT_CHARS = 20000
MINDMAP_PROMPT_CACHE_SECONDS = 30

DEFAULT_MINDMAP_PROMPT = """VAI TRÒ VÀ MỤC TIÊU
Phân tích quan hệ giữa một tin trung tâm và từng tin ứng viên trong sơ đồ Mindmap quân sự, quốc phòng. Chỉ sử dụng dữ liệu được cung cấp; không dùng kiến thức bên ngoài, không suy đoán và không biến điểm chung yếu thành quan hệ chắc chắn.

QUY TRÌNH
1. Đối chiếu riêng từng tin ứng viên với tin trung tâm.
2. Mỗi tin ứng viên chỉ được chọn tối đa một quan hệ có bằng chứng mạnh nhất.
3. Không bắt buộc tạo quan hệ cho mọi tin. Nếu thiếu bằng chứng thì loại khỏi relationships.
4. Không dùng điểm số để bù cho việc thiếu bằng chứng.
5. Không suy ra nguyên nhân, phản ứng, hợp tác hoặc cạnh tranh chỉ từ thời gian, khu vực, bối cảnh địa chính trị hoặc từ khóa chung.

MÃ QUAN HỆ ĐƯỢC PHÉP
Giữ nguyên một trong chín mã sau ở trường type để hệ thống hiển thị; mọi phần giải thích phải bằng tiếng Việt:
same_event (cùng sự kiện cụ thể); related_event (hai sự kiện khác nhau nhưng liên hệ trực tiếp); cause_effect (nguyên nhân-kết quả được nêu rõ); response (phản ứng trực tiếp được nêu rõ); cooperation (hợp tác cụ thể); competition (cạnh tranh hoặc đối đầu cụ thể); same_country (cùng ít nhất hai quốc gia); same_capability (cùng ít nhất hai loại năng lực, vũ khí hoặc chủ thể độc lập); same_topic (cùng chủ đề cụ thể và có thêm bằng chứng độc lập).

NGUYÊN TẮC BẰNG CHỨNG
1. Chỉ cùng một quốc gia, một thẻ chủ đề, một loại vũ khí hoặc một từ khóa thì chưa đủ. Phải có thêm ít nhất một trường độc lập như hoạt động, quân chủng, chủ thể, chương trình, hợp đồng, đơn vị, khí tài, địa điểm hoặc mốc thời gian đặc trưng.
2. Chỉ dùng same_country khi hai tin cùng có ít nhất hai quốc gia liên quan.
3. Với mua sắm quốc phòng kết hợp năng lực tên lửa, chỉ liên kết khi hai tin cùng có ít nhất hai quốc gia. Chỉ cùng Mỹ, chỉ cùng Trung Quốc, chỉ có một quốc gia trùng hoặc không có quốc gia trùng đều không đủ.
4. Không dùng same_capability nếu chỉ trùng một loại vũ khí hoặc năng lực. Phải có ít nhất hai loại cụ thể, hoặc một loại cụ thể kèm thêm chủ thể, chương trình, đơn vị hay hoạt động độc lập cùng xuất hiện.
5. Chỉ dùng same_event khi có định danh cụ thể: tên hợp đồng, chương trình, chiến dịch, cuộc diễn tập, đơn vị, hệ thống khí tài, địa điểm-thời gian đặc trưng hoặc các chủ thể cùng tham gia hoạt động được xác định rõ. Các từ chung như tàu, hạt nhân, tên lửa, tập trận hoặc mua sắm không đủ.
6. Chỉ dùng cause_effect, response, cooperation hoặc competition khi dữ liệu nói rõ quan hệ đó.
7. Chỉ dùng related_event khi có liên hệ trực tiếp về chủ thể, chương trình, hoạt động, mục tiêu hoặc chuỗi diễn biến. Không dùng nó để thay thế cho việc thiếu bằng chứng.

THỨ TỰ ƯU TIÊN
Khi có nhiều khả năng, chọn quan hệ cụ thể nhất theo thứ tự: same_event; cause_effect/response/cooperation/competition; related_event; same_capability; same_topic; same_country. Không chọn quan hệ yếu hơn khi đã đủ căn cứ cho quan hệ mạnh hơn.

CHẤM ĐIỂM VÀ LÝ DO
score là số từ 0 đến 1: 0,90-1,00 khi có định danh trùng hoặc quan hệ trực tiếp; 0,75-0,89 khi có nhiều bằng chứng độc lập; 0,60-0,74 khi vừa đủ căn cứ nhưng còn điểm cần kiểm chứng. Dưới 0,60 thì không đưa quan hệ vào kết quả. reason gồm 1-2 câu tiếng Việt, nêu rõ trường bằng chứng giao nhau và vì sao đáp ứng loại quan hệ; không viết chung chung, không thêm thông tin ngoài dữ liệu.

ĐỊNH DẠNG ĐẦU RA
Chỉ trả về một đối tượng JSON hợp lệ, không Markdown, không lời dẫn và không trường ngoài bốn trường sau:
{"overview":"3-5 câu tiếng Việt","patterns":["điểm chung có căn cứ"],"cautions":["điểm cần kiểm chứng"],"relationships":[{"target_id":1,"type":"same_topic","score":0.8,"reason":"Lý do bằng tiếng Việt, nêu bằng chứng cụ thể."}]}
target_id phải giữ nguyên định danh đầu vào; mỗi target_id chỉ xuất hiện một lần; relationships sắp xếp theo score giảm dần. Nếu không đủ căn cứ, dùng relationships: []. Nếu không có mẫu hoặc điểm cần kiểm chứng, dùng mảng rỗng.

VĂN PHONG
Viết tiếng Việt hành chính-quân sự, ngắn gọn, tách ý rõ ràng và bám sát nguồn. Không chèn nguyên văn tiếng Anh, không nhắc mã nội bộ, tên prompt hoặc quy trình xử lý."""

_CACHE = {"expires_at": 0.0, "prompt": DEFAULT_MINDMAP_PROMPT}

def clear_mindmap_prompt_cache() -> None:
    _CACHE["expires_at"] = 0.0

def get_mindmap_prompt_record() -> WireFilterPrompt | None:
    from .wire_filter_policy import DEFAULT_WIRE_FILTER_PROMPT
    try:
        record, _ = WireFilterPrompt.objects.get_or_create(singleton_key="default", defaults={"prompt": DEFAULT_WIRE_FILTER_PROMPT, "mindmap_prompt": DEFAULT_MINDMAP_PROMPT, "owner": None})
        if not (record.mindmap_prompt or "").strip():
            record.mindmap_prompt = DEFAULT_MINDMAP_PROMPT
            record.save(update_fields=["mindmap_prompt"])
        return record
    except (OperationalError, ProgrammingError):
        return None

def get_user_mindmap_prompt_record(user) -> WireFilterPrompt | None:
    if getattr(user, "is_superuser", False):
        return get_mindmap_prompt_record()
    try:
        existing = WireFilterPrompt.objects.filter(owner=user).first()
        if existing is not None:
            return existing
        from .wire_filter_policy import get_user_wire_filter_prompt_record
        return get_user_wire_filter_prompt_record(user)
    except (OperationalError, ProgrammingError):
        return None

def get_mindmap_prompt() -> str:
    now = time.monotonic()
    if now < float(_CACHE["expires_at"]):
        return str(_CACHE["prompt"])
    record = get_mindmap_prompt_record()
    prompt = ((record.mindmap_prompt if record is not None else "") or DEFAULT_MINDMAP_PROMPT).strip()
    _CACHE["prompt"] = prompt
    _CACHE["expires_at"] = now + MINDMAP_PROMPT_CACHE_SECONDS
    return prompt

def get_effective_user_mindmap_prompt(user) -> str:
    if not user or not getattr(user, "is_authenticated", False) or user.is_superuser:
        return get_mindmap_prompt()
    try:
        record = WireFilterPrompt.objects.filter(owner=user).only("mindmap_prompt").first()
        if record is not None and (record.mindmap_prompt or "").strip():
            return record.mindmap_prompt.strip()
    except (OperationalError, ProgrammingError):
        pass
    return get_mindmap_prompt()

