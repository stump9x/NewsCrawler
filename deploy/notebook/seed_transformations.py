#!/usr/bin/env python3
"""Seed / refresh Notebook AI transformations for NewsCrawler.

Idempotent: matches by transformation ``name``, then PUT; creates if missing.
Also sets shared default transformation instructions (hành chính–quân sự VN).

Usage (host or bootstrap container):
  NOTEBOOK_API=http://notebook-gateway:80 python3 seed_transformations.py
  NOTEBOOK_API=http://127.0.0.1:8502 python3 seed_transformations.py
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

API = (os.environ.get("NOTEBOOK_API") or os.environ.get("NOTEBOOK_BOOTSTRAP_URL") or "").rstrip("/")
if not API:
    print("NOTEBOOK_API / NOTEBOOK_BOOTSTRAP_URL required", file=sys.stderr)
    sys.exit(1)

# Shared doctrine prepended to every transform (Open Notebook default-prompt).
DEFAULT_INSTRUCTIONS = """\
# CHỈ THỊ CHUNG — BIẾN ĐỔI NỘI DUNG NGUỒN
Bạn là trợ lý biên tập tin tức / quốc phòng / hành chính. Chỉ xử lý văn bản nguồn được cung cấp.
INPUT thường là plain text đã làm sạch (không HTML/markdown thô). OUTPUT BẮT BUỘC bằng tiếng Việt hành chính–quân sự, đúng chủ đề nguồn.

## Nguyên tắc bắt buộc
1. Bám sát nguồn: không bịa số liệu, địa danh, nhân vật, tổ chức, kết luận ngoài văn bản.
2. Văn phong tiếng Việt hành chính–quân sự: rõ ràng, chính xác, trung tính, chuyên nghiệp (cùng chuẩn dịch tiêu đề Wire). Dù nguồn tiếng Anh/ngôn ngữ khác, kết quả vẫn phải là tiếng Việt.
3. Giữ nguyên mức độ chắc chắn của nguồn (có thể / được cho là / theo nguồn / reportedly…). Không làm mạnh hoặc làm yếu nhận định.
4. Thuật ngữ ưu tiên khi phù hợp: Biển Đông; Eo biển Đài Loan; tập trận quân sự; mua sắm quốc phòng; bố trí lực lượng; tác chiến mạng; cảnh sát biển; Lực lượng Phòng vệ Nhật Bản.
5. Tên riêng / viết tắt chưa chắc: giữ nguyên (NATO, PLA, F-35…). Không đoán.
6. Chỉ xuất đúng nội dung yêu cầu — không xã giao, không ghi chú dịch giả, không cảnh báo bản quyền, không nhắc lại HTML/markup.
""".strip()

# Update existing Open Notebook built-ins by ``name``; prompts are self-contained
# so transforms stay grounded even if default-prompt fails to load.
PRESETS = [
    {
        "name": "Simple Summary",
        "title": "Tóm tắt tình hình",
        "description": "Tóm tắt ngắn (5–8 câu) các sự kiện / luận điểm chính trong nguồn — bám sát văn bản.",
        "prompt": """\
# NHIỆM VỤ
Viết tóm tắt tình hình bằng tiếng Việt hành chính–quân sự, CHỈ dựa trên nguồn dưới đây (plain text đã làm sạch). OUTPUT bắt buộc tiếng Việt.

# CẤU TRÚC BẮT BUỘC
1. **Tóm tắt** — 5–8 câu hoàn chỉnh, theo trình tự logic (ai / cái gì / ở đâu / khi nào / hệ quả nêu trong nguồn).
2. **Sự kiện then chốt** — 3–6 gạch đầu dòng, mỗi dòng một sự kiện cụ thể (có số liệu nếu nguồn có).

# CẤM
- Không thêm bình luận, khuyến nghị, suy đoán.
- Không đưa thông tin ngoài nguồn; nếu thiếu chi tiết, ghi «nguồn không nêu».
- Không xuất HTML/markdown thô; không trả lời bằng tiếng Anh.
""".strip(),
        "apply_default": True,
    },
    {
        "name": "Key Insights",
        "title": "Điểm then chốt",
        "description": "Liệt kê sự kiện, số liệu, tuyên bố quan trọng — không triết lý / không «bài học cuộc sống».",
        "prompt": """\
# NHIỆM VỤ
Trích các điểm then chốt phục vụ theo dõi tin quốc phòng / đối ngoại / an ninh, CHỈ từ nguồn.

# ĐỊNH DẠNG
- 6–12 gạch đầu dòng.
- Mỗi dòng: một sự kiện / số liệu / tuyên bố cụ thể (≤ 25 từ), trung tính.
- Ưu tiên: lực lượng, địa bàn, thời điểm, vũ khí/hệ thống, quyết định chính sách, số liệu.

# CẤM
- Không viết insight mang tính triết lý, self-help, hay suy diễn chiến lược ngoài nguồn.
- Không bịa diễn biến.
""".strip(),
        "apply_default": False,
    },
    {
        "name": "Table of Contents",
        "title": "Cấu trúc nội dung",
        "description": "Mục lục / đề mục theo thứ tự xuất hiện trong nguồn — giúp định hướng đọc nhanh.",
        "prompt": """\
# NHIỆM VỤ
Lập cấu trúc nội dung (mục lục) theo đúng thứ tự xuất hiện trong nguồn.

# ĐỊNH DẠNG
1. Đánh số 1., 2., 3.… theo trình tự văn bản.
2. Mỗi mục: tiêu đề ngắn (tiếng Việt) + nửa câu mô tả phạm vi (không tóm tắt toàn bài).
3. Có thể dùng 1.1, 1.2 cho tiểu mục nếu nguồn phân đoạn rõ.

Chỉ phản ánh những gì nguồn thực sự đề cập.
""".strip(),
        "apply_default": False,
    },
    {
        "name": "Analyze Paper",
        "title": "Phân tích tài liệu",
        "description": "Phân tích có khung: vấn đề → luận điểm → bằng chứng → hạn chế (theo nguồn) — phù hợp báo cáo / nghiên cứu.",
        "prompt": """\
# NHIỆM VỤ
Phân tích tài liệu nguồn theo khung hành chính–quân sự / nghiên cứu, CHỈ dựa trên nội dung đã cho.

# CẤU TRÚC BẮT BUỘC
## Vấn đề / đối tượng
## Luận điểm chính
## Bằng chứng & số liệu (trích từ nguồn)
## Phương pháp / cách tiếp cận (nếu nguồn nêu)
## Hạn chế / điểm nguồn thừa nhận
## Kết luận trong nguồn (không thêm kết luận mới)

Viết tiếng Việt rõ, trung tính. Không khuyến nghị chính sách trừ khi nguồn nêu.
""".strip(),
        "apply_default": False,
    },
    {
        "name": "Reflections",
        "title": "Câu hỏi làm rõ",
        "description": "Sinh câu hỏi kiểm chứng / làm rõ khoảng trống thông tin để khai thác tiếp nguồn — không triết lý.",
        "prompt": """\
# NHIỆM VỤ
Đặt câu hỏi làm rõ phục vụ theo dõi tin / thẩm định nguồn, CHỈ dựa trên nội dung đã cho.

# ĐỊNH DẠNG
- 6–10 câu hỏi đánh số.
- Mỗi câu nhắm một khoảng trống, mâu thuẫn, hoặc chi tiết cần xác minh (ai, đâu, khi nào, bao nhiêu, nguồn gốc tuyên bố…).
- Viết tiếng Việt hành chính, ngắn gọn.

# CẤM
- Không hỏi mang tính triết lý / tự sự / «ý nghĩa cuộc sống».
- Không giả định sự kiện ngoài nguồn.
""".strip(),
        "apply_default": False,
    },
    {
        "name": "Translate Formal VN",
        "title": "Dịch chuẩn hóa (VN)",
        "description": "Dịch toàn bộ sang tiếng Việt hành chính–quân sự; giữ nghĩa, không tóm tắt, không thêm nội dung.",
        "prompt": """\
# NHIỆM VỤ
Dịch toàn bộ nội dung nguồn sang tiếng Việt hành chính–quân sự (chuẩn dịch tiêu đề Wire).

# YÊU CẦU
1. Giữ đủ nghĩa; không tóm tắt, không bỏ ý, không thêm thông tin.
2. Giữ mức độ chắc chắn; không bịa địa danh / quốc gia.
3. Tên riêng / viết tắt chưa chắc: giữ nguyên.
4. Xuất chỉ bản dịch hoàn chỉnh — không ghi chú, không tiêu đề phụ trừ khi nguồn có.

Nếu nguồn đã là tiếng Việt đạt chuẩn: chỉnh nhẹ câu vụng / thuật ngữ, không viết lại lan man.
""".strip(),
        "apply_default": False,
    },
]


def req(method: str, path: str, body: dict | None = None):
    url = path if path.startswith("http") else f"{API}/api{path}"
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=60) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw) if raw else None
        except json.JSONDecodeError:
            payload = raw
        return e.code, payload


def main() -> int:
    print(f"[seed-transformations] API={API}")
    code, existing = req("GET", "/transformations")
    if code != 200 or not isinstance(existing, list):
        print(f"ERROR list transformations HTTP {code}: {existing!r}", file=sys.stderr)
        return 1
    by_name = {t.get("name"): t for t in existing if isinstance(t, dict)}

    # Retire Dense Summary / Trích yếu hành chính from Surreal if still present.
    for t in existing:
        if not isinstance(t, dict) or not t.get("id"):
            continue
        name = str(t.get("name") or "")
        title = str(t.get("title") or "")
        if name == "Dense Summary" or "trích yếu hành chính" in title.lower():
            dcode, dout = req("DELETE", f"/transformations/{t['id']}")
            if dcode in (200, 204):
                print(f"[seed-transformations] deleted retired {name or title}")
            else:
                print(
                    f"WARN delete {name or title} HTTP {dcode}: {dout!r}",
                    file=sys.stderr,
                )
            by_name.pop(name, None)

    code, _ = req(
        "PUT",
        "/transformations/default-prompt",
        {"transformation_instructions": DEFAULT_INSTRUCTIONS},
    )
    if code == 200:
        print("[seed-transformations] default-prompt updated")
    else:
        print(f"WARN default-prompt HTTP {code}", file=sys.stderr)

    for preset in PRESETS:
        name = preset["name"]
        cur = by_name.get(name)
        payload = {
            "name": preset["name"],
            "title": preset["title"],
            "description": preset["description"],
            "prompt": preset["prompt"],
            "apply_default": preset["apply_default"],
        }
        if cur and cur.get("id"):
            code, out = req("PUT", f"/transformations/{cur['id']}", payload)
            action = "updated"
        else:
            code, out = req("POST", "/transformations", payload)
            action = "created"
        if code in (200, 201):
            print(f"[seed-transformations] {action} {name} → {preset['title']}")
        else:
            print(f"WARN {action} {name} HTTP {code}: {out!r}", file=sys.stderr)

    print("[seed-transformations] done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
