"""Evidence-based Wire scope shared by ingest, discovery and recommendations.

This is a deterministic relevance classifier, not a factual-verification model.
Only article text is evidence. Publisher geography, URL and user keep phrases
cannot make an otherwise out-of-scope item relevant.
"""
from __future__ import annotations

import html
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache

POLICY_VERSION = "2026-09-topics-v2"
TOPIC_TAG_PREFIX = "wire-topic-"


def normalize(text: str) -> str:
    text = re.sub(r"<[^>]*(?:>|$)", " ", html.unescape(str(text or "")))
    text = unicodedata.normalize("NFKC", text).casefold()
    return " ".join(text.replace("–", "-").replace("‑", "-").split())


@lru_cache(maxsize=512)
def _pattern(terms: str) -> re.Pattern:
    parts = []
    for term in terms.split("|"):
        term = normalize(term)
        if not term:
            continue
        escaped = re.escape(term)
        # Chinese phrases are normally adjacent to other Han characters.
        parts.append(escaped if re.search(r"[\u3400-\u9fff]", term) else rf"(?<!\w){escaped}(?!\w)")
    return re.compile("|".join(parts))


def has(text: str, terms: str) -> bool:
    return bool(_pattern(terms).search(text))


CHINA = "Trung Quốc|China|Chinese|Beijing|Bắc Kinh|中国|中國|中方|国务院|Quốc vụ viện|Quảng Tây|Guangxi|广西|Vân Nam|Yunnan|云南|Hải Nam|Hainan|海南|Quảng Châu|Guangzhou|广州|Phòng Thành Cảng|Fangchenggang|防城港|Long Châu|Longzhou|龙州"
VIETNAM = "Việt Nam|Vietnam|Viet Nam|Vietnamese|越南|Hà Nội|Hanoi"
REGION = CHINA + "|" + VIETNAM + "|Nhật Bản|Japan|Japanese|日本|Đài Loan|Taiwan|Taiwanese|台湾|台灣|臺灣|Philippines|Philippine|菲律宾|Malaysia|马来西亚|Indonesia|Indonesian|IND|印尼|Singapore|Xin-ga-po|新加坡|Campuchia|Cambodia|Cambodian|柬埔寨|Lào|Laos|Lao|老挝|Thái Lan|Thailand|Thai|泰国|Myanmar|缅甸|Úc|Australia|Australian|澳大利亚|Mỹ|Hoa Kỳ|United States|U.S.|US|U.S. Navy|American|US Navy|US military|美国|Nga|Russia|Russian|俄罗斯|Ấn Độ|India|Indian|印度"
SEA = "Biển Đông|South China Sea|West Philippine Sea|南海|Xca-bơ-rô|Scarborough|Huangyan|黄岩岛|Cỏ Mây|Second Thomas|Ayungin|仁爱礁|Hoa Lau|Swallow Reef|Layang Layang|弹丸礁|Trường Sa|Spratly|南沙|Hoàng Sa|Paracel|Paracels|西沙"
DEVELOPMENT = "ban hành|công bố|phê duyệt|thông qua|sửa đổi|triển khai|thành lập|kiện toàn|kế hoạch|KH|quy hoạch|chính sách|quy định|chủ trương|đề xuất|chuẩn bị|tổ chức|kết quả|động thái|hoạt động|phản ứng|dư luận|đánh giá|phát biểu|thỏa thuận|ký kết|tiếp nhận|bàn giao|hỗ trợ|tăng cường|đẩy mạnh|hoàn thành|nâng cấp|phát triển|diễn tập|huấn luyện|tuần tra|va chạm|họp báo|approve|approves|approved|announce|announces|announced|adopt|adopts|adopted|issue|issues|issued|launch|launches|launched|plan|plans|planning|policy|regulation|regulations|propose|proposes|proposed|establish|establishes|established|deploy|deploys|deployed|deployment|exercise|exercises|drill|drills|patrol|patrols|collision|clash|reaction|reactions|response|review|assessment|analysis|sign|signs|signed|agreement|receive|receives|received|deliver|delivers|delivered|upgrade|upgrades|modernization|develop|develops|development|press conference|briefing|decision|decisions|results|speech|发布|公布|批准|通过|修订|实施|部署|成立|举行|开展|计划|规划|政策|条例|办法|演习|训练|巡航|碰撞|回应|反应|合作|援助|研发|建设|升级|会议|记者会"


# Alternate surface forms of the same editorial actions (not additional topics).
DEVELOPMENT += "|thông báo|bổ sung|xây dựng|hiện đại hóa|sửa chữa|đóng mới|publish|publishes|published|modernisation|build|builds|building|construction|announcement|ramps up|ramp up|steps up|participating|took part|rolls out|roll out|introducing|expanding|unveiling|launching|推出|推动|加速|推进|批量生产|量产|發表|發布|計畫"
DEVELOPMENT += "|unveil|unveils|unveiled|introduce|introduces|introduced|install|installs|installed|installation|retrofit|retrofits|retrofitting|modernizing|modernising|commission|commissions|commissioned|transfer|transfers|transferred|take delivery|takes delivery|taking delivery|participate|participates|participated|held|conduct|conducts|conducted|train|trains|trained|training|refuel|refuels|refueling|refuelling|escort|escorts|escorted|arrive|arrives|arrived|test|tests|tested|test-fired|test-fires|prepare|prepares|preparing|strengthen|strengthens|strengthening|launching|developing|developed|deploying|expand|expands|expansion|tập trận|thử nghiệm|thử tên lửa|lắp đặt|đưa vào biên chế|hạ thủy|ra mắt|ra khơi|điều động|bắn thử|tăng tốc|试验|试射|下水|入列|服役|改装|测试|演练|启用"
MILITARY_SUBJECT = "warship|warships|frigate|frigates|destroyer|destroyers|fighter|fighters|naval|navy|military|army|missile|missiles|defense system|defence system|radar|air force|aircraft carrier|readiness|USAF|USMC|JMSDF|JASDF|USS|electronic warfare|hải quân|quân đội|quân sự|tàu chiến|tên lửa|phòng thủ|tàu khu trục|chiến đấu cơ|quốc phòng|军舰|护卫舰|驱逐舰|战机|导弹|海军|军事|国防"
REGION += "|美军|美國|美方|美战争部|美國防部|俄军|俄羅斯"
VIETNAM += "|VN"
RETIRED_TOPIC_TAGS = ("wire-topic-6", "wire-topic-7")


@dataclass(frozen=True)
class Topic:
    code: str
    label: str
    context: str
    focus: str
    qualifiers: str = ""

    @property
    def tag(self) -> str:
        return TOPIC_TAG_PREFIX + self.code


TOPICS = (
    Topic("1a", "Biển Đông: hiện diện, chấp pháp và thực địa", SEA,
          "tuần tra|huấn luyện|diễn tập|hải cảnh|cảnh sát biển|chấp pháp|quân sự|tàu chiến|va chạm|đâm va|vòi rồng|ra-đa|radar|căn cứ|bồi đắp|triển khai|coast guard|patrol|patrols|exercise|exercises|naval|military|collision|clash|water cannon|radar|reclamation|deployment|escort|escorted|navy|warship|warships|frigate|carrier|construction|fortification|海警|巡航|演习|军事|碰撞|雷达|填海"),
    Topic("1b", "Biển Đông: biện pháp chủ quyền", SEA,
          "biện pháp quản lý|khu bảo tồn|du lịch|đánh bắt chung|chủ quyền|tuyên truyền pháp lý|lệnh cấm đánh bắt|cấm đánh bắt|nature reserve|tourism|joint fishing|sovereignty|fishing ban|maritime law|administrative measures|自然保护区|旅游|共同捕鱼|主权|禁渔|管控"),
    Topic("2a", "Trung Quốc: biên giới, cửa khẩu và ven biển", CHINA,
          "biên giới|cửa khẩu|xuất nhập cảnh|xuất cảnh|hoàn thuế|khai hải|mở biển|khai biển|Phòng Thành Cảng|Vân Nam|border management|border security|border defense|border defence|border development|border infrastructure|port modernization|border crossing|entry and exit|exit-entry|immigration regulation|departure tax refund|fishing season|Fangchenggang|Yunnan|边境|边防|口岸|出入境|离境退税|开海|防城港|云南",
          "quản lý|quy định|kế hoạch|KH|chính sách|phát triển|mở cửa|đội tiên phong|bảo vệ|phòng thủ|diễn tập|dự án|hoàn thành|khai hải|mở biển|khai biển|regulation|regulations|plan|plans|policy|modernization|development|defense|defence|drill|exercise|project|projects|fishing season|管理|条例|计划|规划|政策|发展|边防|演习|项目|开海"),
    Topic("2b", "Trung Quốc: quốc phòng và lĩnh vực nhạy cảm", CHINA,
          "BQP|Bộ Quốc phòng|tàu quân sự|tàu chiến|đóng mới tàu|sửa chữa tàu|tổ chức phi chính phủ nước ngoài|vật liệu nổ dân dụng|mô-đun tác chiến|module tác chiến|khoáng sản lưỡng dụng|khoáng sản chiến lược|defense ministry|defence ministry|naval shipbuilding|warship|Chinese warships|Chinese frigate|guided-missile frigate|PLA Navy|foreign NGO|foreign NGOs|civil explosives|containerized weapon|containerised weapon|containerized combat|dual-use mineral|critical minerals|国防部|军舰|护卫舰|驱逐舰|境外非政府组织|民用爆炸|集装箱作战|两用矿产|战略矿产"),
    Topic("2c", "Trung Quốc: công nghệ, năng lượng, hạ tầng chiến lược", CHINA,
          "điện hạt nhân|mạch tích hợp|IPv6|Internet phiên bản 6|trí tuệ nhân tạo|kinh tế biển|mạng lưới GTVT|mạng lưới giao thông|giao thông vận tải|nuclear power|integrated circuit|artificial intelligence|marine economy|maritime economy|transport network|核电|集成电路|人工智能|海洋经济|交通运输",
          "phê duyệt|ban hành|quy định|kế hoạch|KH|quy hoạch|chính sách|hành động|Quốc vụ viện|chính phủ|tỉnh|approve|approves|approved|regulation|regulations|plan|plans|policy|action plan|government|state council|province|批准|规划|计划|条例|政策|行动|国务院|政府"),
    Topic("3a", "Tổ chức và chiến lược quốc phòng, an ninh", REGION,
          "Cục Tình báo Quốc gia|Sách trắng Quốc phòng|chiến lược quốc phòng|chiến lược an ninh quốc gia|cơ quan tình báo|national intelligence|defense white paper|defence white paper|defense strategy|defence strategy|national security strategy|intelligence agency|defense cyber strategy|military cyber strategy|cyber strategy|国防部网络战略|网络战略|網路戰略|国家情报|国防白皮书|国防战略|国家安全战略",
          "ban hành|công bố|thành lập|kiện toàn|kế hoạch|KH|chiến lược|sách trắng|strategy|white paper|establish|establishes|established|reorganize|reorganization|publish|publishes|published|announce|announces|announced|plan|plans|formed|发布|公布|成立|设立|战略|戰略|白皮书|計畫|计划"),
    Topic("3b", "Hội nghị, quyết sách chính trị và đối ngoại", REGION,
          "Đảng Nhân dân Cách mạng Lào|Hội nghị Trung ương|Hội nghị TW|Trung ương Đảng|Ban Chấp hành TW|Hội đồng Bộ trưởng|Bộ Ngoại giao|BNG Trung Quốc|Lao People's Revolutionary Party|central committee plenum|central committee meeting|party plenum|council of ministers|foreign ministry|中全会|中央全会|部长会议|外交部",
          "nghị quyết|dự thảo|quyết sách|chính sách|kết quả|đánh giá|trước thềm|họp báo|chiến lược|resolution|draft|decision|decisions|policy|results|assessment|preview|press conference|briefing|strategy|决议|草案|政策|成果|评估|记者会"),
    Topic("3c", "Chính sách công nghệ và hiện đại hóa quốc phòng", REGION,
          "Hội nghị Trí tuệ nhân tạo|Hội nghị AI|World Artificial Intelligence Conference|WAIC|世界人工智能大会|Zumwalt",
          "chính sách|phát biểu|đánh giá|hội nghị|nâng cấp|hiện đại hóa|speech|assessment|policy|conference|upgrade|modernization|modernisation|installation|retrofit|radar|hypersonic|讲话|评估|政策|大会|升级|现代化"),
    Topic("4a", "Diễn tập, huấn luyện và hoạt động quân sự", REGION + "|SEACAT|Hán Quang|Han Kuang|汉光|Guam|关岛",
          "diễn tập|huấn luyện|hoạt động quân sự|hợp tác hàng hải|phòng thủ đô thị|SEACAT|Hán Quang|Han Kuang|exercise|exercises|drill|drills|military training|naval training|military operation|military operations|maritime cooperative activity|military deployment|carrier deployment|carrier strike group|joint training|combat training|live-fire|air strikes|airstrikes|naval patrol|coastguard patrols|coast guard patrols|演习|军演|军事训练|海军训练|军事行动|汉光",
          "quân sự|quân đội|QĐ|hải quân|tàu chiến|phòng thủ|hàng hải đa phương|Hán Quang|Han Kuang|SEACAT|military|naval|navy|army|marines|air force|fighter|fighters|combat|troops|forces|USAF|USMC|JMSDF|JASDF|coastguard|coast guard|defense|defence|maritime cooperative|军演|军事|军队|海军|空军|汉光"),
    Topic("4b", "Hợp tác quốc phòng, biên giới và chuyển giao trang bị", REGION,
          "hợp tác quốc phòng|hỗ trợ an ninh|Bộ trưởng Quốc phòng|Ủy ban biên giới chung|Stryker|mua sắm quốc phòng|chuyển giao vũ khí|tiếp nhận tàu|defense cooperation|defence cooperation|security assistance|defense ministers|defence ministers|joint border committee|arms transfer|military aid|air-defense missile agreement|military sale|military sales|arms deal|arms sale|arms sales|defense export|defence export|military helicopter sale|export customer|defense partnership|defence partnership|security cooperation|missile sale|helicopter sale|fighter sale|equipment transfer|weapons transfer|warship transfer|防务合作|安全援助|国防部长|联合边界|武器转让|军事援助",
          "hội nghị|kết quả|thỏa thuận|ký|tiếp nhận|bàn giao|chuyển giao|hỗ trợ|hợp tác|chuẩn bị|mua sắm|agreement|sign|signs|signed|receive|receives|received|deliver|delivers|delivered|transfer|conference|meeting|assistance|aid|cooperation|procurement|sale|sales|export|exports|buy|purchase|transfer|transfers|receive|receives|received|deliver|delivers|delivered|会议|协议|接收|交付|援助|合作"),
    Topic("4c", "Học thuyết, hệ thống phòng thủ và công nghệ mới", REGION + "|Guam|关岛",
          "học thuyết|Lá chắn ba mũi giáo|phòng thủ ở đảo Guam|phòng thủ Guam|chỉ huy - điều khiển|chỉ huy-điều khiển|chỉ huy điều khiển|tàu không người lái|mạng 6G|nghiên cứu 6G|trí tuệ nhân tạo tiếng Lào|doctrine|Guam missile defense|Guam missile defence|missile defense|missile defence|air defense system|air defence system|hypersonic missile|hypersonic weapon|directed-energy weapon|command and control|command-and-control|unmanned trimaran|uncrewed trimaran|unmanned surface vessel|uncrewed surface vessel|military AI|electronic warfare system|military drone|collaborative combat aircraft|6G network|6G research|Lao language AI|Lao-language AI|军用版ChatGPT|军事人工智能|军用人工智能|电子战系统|电子战装备|無人機戰略|无人机战略|无人作战|军事大模型|军用大模型|作战理论|关岛防御|指挥控制|无人艇|6G研发|老挝语人工智能",
          "phát triển|triển khai|xây dựng|nâng cấp|nghiên cứu|đề xuất|hỗ trợ|học thuyết|develop|develops|development|deploy|deploys|deployment|build|builds|building|upgrade|upgrades|research|propose|proposes|support|doctrine|installed|installation|retrofit|production|producing|test|tests|testing|test-fired|initial operational capability|研发|发展|部署|建设|升级|研究|援助|理论|批量生产|量产|推出|推动|推進|推进|發表|改装|发展|開發"),
    Topic("5", "Trừng phạt, đối phó và kiểm soát xuất khẩu", CHINA + "|Mỹ|Hoa Kỳ|United States|U.S.|USTR|美国|Nga|Russia|俄罗斯|Liên minh Châu Âu|European Union|EU|欧盟",
          "trừng phạt|biện pháp đối phó|kiểm soát xuất khẩu|danh sách thực thể|lao động cưỡng bức|sanctions|countermeasures|export control|export controls|entity list|forced labor|forced labour|UFLPA|制裁|反制|出口管制|实体清单|强迫劳动",
          "đưa|bổ sung|ban hành|công bố|thông báo|áp dụng|tăng cường|kết luận|điều tra|announce|announces|announced|add|adds|added|impose|imposes|imposed|list|lists|listed|investigation|findings|tighten|tightens|expand|expands|publish|publishes|sanction|sanctions|sanctioned|restriction|restrictions|ban|bans|banned|enforcement|发布|公布|列入|新增|实施|调查|反制"),
)
TOPIC_LABELS = {topic.tag: topic.label for topic in TOPICS}

# Reject topical wrappers around lifestyle, retrospectives and speculation.
TITLE_NOISE = "giải bóng đá|thể thao|ẩm thực|mẹo du lịch|cẩm nang du lịch|đặt tour|giảm giá|lễ kỷ niệm|sinh nhật|đời sống quân nhân|bóng rổ|football|basketball|sports|recipe|recipes|travel guide|book a tour|discount|birthday|anniversary|veteran reunion|military spouse|military families|video game|gaming|football championship|体育|美食|旅游攻略|优惠|周年纪念"
SPECULATION = "có thể sẽ|có thể xảy ra|giả định|tin đồn|đồn đoán|what if|could one day|might someday|rumor|rumour|hypothetical|假设|传闻"
TITLE_NOISE += "|cafe|restaurant|hotel booking|bus timetable|consumer phone|stock prices|medical equipment sales|history of|cẩm nang|quán cà phê|giá cổ phiếu"


@dataclass(frozen=True)
class TopicMatch:
    codes: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()
    reason: str = "Không có diễn biến cụ thể thuộc năm nhóm nhiệm vụ"

    @property
    def relevant(self) -> bool:
        return bool(self.codes)

    @property
    def tags(self) -> tuple[str, ...]:
        return tuple(TOPIC_TAG_PREFIX + code for code in self.codes)

    def as_payload(self) -> dict:
        return {"version": POLICY_VERSION, "topics": list(self.codes),
                "evidence": list(self.evidence), "reason": self.reason}


def classify_wire_topics(item: dict) -> TopicMatch:
    """Require context + subject + development in one bounded evidence passage.

    The headline must itself anchor the topic, or name its actor/place and a
    development supported in the lead. Do not aggregate keywords over an entire
    page (related links, navigation and separate stories are common feed noise).
    """
    titles = [normalize(item.get(key, ""))[:600] for key in ("title", "title_vi")]
    titles = [title for title in titles if title]
    if not titles:
        return TopicMatch(reason="Thiếu tiêu đề")
    if any(has(title, TITLE_NOISE) for title in titles):
        return TopicMatch(reason="Tin đời sống, quảng bá hoặc nghi lễ")
    if any(has(title, SPECULATION) for title in titles):
        return TopicMatch(reason="Giả định hoặc tin đồn chưa có diễn biến xác nhận")
    leads = []
    for key in ("summary", "description", "content"):
        text = normalize(item.get(key, ""))[:1400]
        # A bounded lead, not a concatenation of all article and metadata fields.
        if text and text not in leads:
            leads.append(text[:650])
    codes, evidence = [], []
    for topic in TOPICS:
        if topic.code in {"2a", "2b", "2c", "4a"} and not any(
            has(text, topic.context) for text in titles + [lead[:200] for lead in leads]
        ):
            # A late comparison with China/the US does not establish the
            # subject of a China policy or military-operation article.
            continue
        for title in titles:
            headline_anchor = has(title, topic.focus) or (
                has(title, topic.context) and has(title, DEVELOPMENT)
            ) or (
                topic.code in {"1a", "2b", "3c", "4a", "4b", "4c"}
                and has(title, MILITARY_SUBJECT)
            )
            if not headline_anchor:
                continue
            candidates = [title] + [f"{title}. {lead}"[:1000] for lead in leads]
            matched = None
            for text in candidates:
                groups = [topic.context, topic.focus, DEVELOPMENT]
                if topic.qualifiers:
                    groups.append(topic.qualifiers)
                matches = [list(_pattern(group).finditer(text)) for group in groups]
                if not all(matches):
                    continue
                # Find a shared 420-character passage, regardless of the
                # actor's position. An actor at the start of a lead must not
                # lose half the evidence budget. Distant links still cannot
                # supply missing evidence.
                starts = sorted({match.start() for group in matches for match in group})
                for start in starts:
                    passage = text[start:start + 420]
                    if all(has(passage, group) for group in groups):
                        matched = passage
                        break
                if matched:
                    break
            if matched:
                codes.append(topic.code)
                evidence.append(matched[:450])
                break
    if not codes:
        return TopicMatch()
    return TopicMatch(tuple(codes), tuple(evidence), "Có chủ thể/địa bàn, nội dung và diễn biến cùng ngữ cảnh")


# Rotate one narrowly scoped query per subtopic, preserving the existing quota.
# Examples describe a search intent; dates/events must come from returned sources.
WIRE_DISCOVERY_QUERIES = (
    'Scarborough Second Thomas shoal South China Sea coast guard patrol collision radar latest developments',
    'Scarborough Paracel Spratly sovereignty nature reserve rules joint fishing tourism policy announcement',
    'China Guangxi Yunnan Fangchenggang border port entry exit regulations development plan announcement',
    'China defense ministry naval shipbuilding foreign NGO regulations civil explosives dual use minerals policy',
    'China government nuclear power approval integrated circuit IPv6 artificial intelligence Hainan marine economy transport plan',
    'Japan national intelligence agency defense white paper Malaysia national defense strategy announcement',
    'Laos party draft resolution China central committee plenum Cambodia council ministers foreign ministry policy decisions',
    'China World Artificial Intelligence Conference government policy speech US Zumwalt modernization program',
    'Taiwan Han Kuang SEACAT Cambodia naval training Indonesia military exercises Philippines maritime cooperation',
    'Australia Philippines defense ministers Thailand Stryker delivery Laos Cambodia border committee Japan security assistance',
    'Guam missile defense Taiwan command control Indonesia doctrine China unmanned trimaran 6G Laos language AI project',
    'China United States European Union export controls entity list dual use UAV UFLPA forced labor investigation measures',
)


def discovery_queries(*, count: int, slot: int) -> list[str]:
    count = max(1, min(int(count), len(WIRE_DISCOVERY_QUERIES)))
    start = (int(slot) * count) % len(WIRE_DISCOVERY_QUERIES)
    return [WIRE_DISCOVERY_QUERIES[(start + i) % len(WIRE_DISCOVERY_QUERIES)] for i in range(count)]
