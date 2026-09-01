/**
 * Same-origin Open Notebook API client (proxied at /api/nb → notebook-gateway /api).
 * Focus: notebooks, sources, notebook chat (context + execute), transformations, models.
 *
 * ## Chat speed path (Notebook AI)
 * Open Notebook `/chat/execute` is **synchronous** (no SSE). Source-chat has SSE;
 * notebook-level chat does not. We still surface answers ASAP by:
 * 0. Social/chitchat (xin chào / thanks / bạn là ai) → Groq GPT-OSS 20B only, no crawl.
 * 1. Preferring ShopAIKey, with Groq/OpenRouter as bounded fallbacks.
 * 2. Racing at most 2 cloud providers; interactive Chat never waits for
 *    Cerebras 402 or local Ollama.
 * 3. Shrinking scoped/whole context payloads before execute.
 * 4. Progressive pending status in the UI (`_status` / `_pending`) while waiting.
 * Interactive hard stop: 15 seconds (`CHAT_HANG_TIMEOUT_MS`).
 */

import { apiRequest } from "./client";

const NB_BASE = "/api/nb";

/** Safe rollout switch; the old per-provider path remains available. */
export const NOTEBOOK_CHAT_V2_ENABLED =
  String(import.meta.env.VITE_NOTEBOOK_CHAT_V2 || "false").toLowerCase() ===
  "true";
/** Start a backup provider only after the preferred provider is genuinely slow. */
export const NOTEBOOK_CHAT_HEDGE_DELAY_MS = Math.max(
  800,
  Number(import.meta.env.VITE_NOTEBOOK_CHAT_HEDGE_DELAY_MS || 1400)
);
export const NOTEBOOK_CHAT_CLOUD_TIMEOUT_MS = Math.max(
  8_000,
  Number(import.meta.env.VITE_NOTEBOOK_CHAT_CLOUD_TIMEOUT_MS || 28_000)
);
export const NOTEBOOK_CHAT_LOCAL_TIMEOUT_MS = Math.max(
  15_000,
  Number(import.meta.env.VITE_NOTEBOOK_CHAT_LOCAL_TIMEOUT_MS || 45_000)
);

/** UI watchdog fires before 15s and must replace the pending bubble immediately. */
export const CHAT_HANG_TIMEOUT_MS = 14 * 1000;

/** Per-source char caps after /chat/context (scoped = fuller; whole = leaner). */
export const CHAT_SCOPED_SOURCE_CHAR_CAP = 12_000;
export const CHAT_WHOLE_SOURCE_CHAR_CAP = 4_000;
export const CHAT_INSIGHT_CHAR_CAP = 1_600;
/** Fast-path caps for simple / short questions (target under 10 seconds). */
export const CHAT_FAST_SOURCE_CHAR_CAP = 6_000;
export const CHAT_FAST_INSIGHT_CHAR_CAP = 800;
export const CHAT_FAST_QUERY_MAX_CHARS = 160;

const CHAT_DEEP_QUERY_RE =
  /so\s*sánh|phân\s*tích|đối\s*chiếu|toàn\s*bộ|tất\s*cả\s*nguồn|chi\s*tiết|toàn\s*diện|ưu\s*nhược|deep\s*dive|compare|analy[sz]e|pros\s*and\s*cons|multi[- ]?source/i;

/** «Nội dung chính» / summarize / overview — triggers crawl+cloud digest API. */
export const MAIN_CONTENT_DIGEST_RE =
  /n[ộo]i\s*dung\s*ch[íi]nh|n[ộo]i\s*dung\s*b[àa]i|t[óo]m\s*t[ắa]t|tom\s*tat|t[óo]m\s*l[ượu]c|main\s*content|summarize|summarise|summary|overview|digest|tldr|tl;?\s*dr|what(?:'s|\s+is)\s+(?:this|the)\s+(?:article|page|story|piece)|key\s+(?:points?|takeaways?)/i;

/** Max length for social/chitchat fast path (no crawl / no grounding). */
export const SOCIAL_CHITCHAT_MAX_CHARS = 220;

/**
 * Basic social / rapport turns (EN+VI) — greetings, thanks, identity, ack.
 * Must NOT match notebook knowledge / source questions.
 */
export const SOCIAL_CHITCHAT_RE =
  /^(?:(?:xin\s+)?chào(?:\s+(?:bạn|anh|chị|em|mọi\s+người|cả\s+nhà))?[!?.…]*|hello[!?.…]*|hi+[!?.…]*|hey[!?.…]*|hola[!?.…]*|yo[!?.…]*|alo[!?.…]*|có\s+(?:đó|ai)\s+không\s*[?？!]*|bạn\s+là\s+ai\s*[?？!]*|who\s+are\s+you\s*[?？!]*|bạn\s+tên\s+(?:là\s+)?gì\s*[?？!]*|giới\s+thiệu\s+(?:về\s+)?bạn\s*[?？!]*|bạn\s+làm\s+được\s+gì\s*[?？!]*|what\s+can\s+you\s+do\s*[?？!]*|bạn\s+giúp\s+(?:được\s+)?gì\s*[?？!]*|cảm\s+ơn(?:\s+(?:bạn|nhiều|nhé|nha|em|anh|chị))?!*[!.…]*|thanks?(?:\s+you)?[!?.…]*|thank\s+you[!?.…]*|tạm\s+biệt[!?.…]*|bye+[!?.…]*|goodbye[!?.…]*|see\s+ya?!*[!.…]*|hẹn\s+gặp\s+lại[!?.…]*|bạn\s+(?:khỏe|ổn)\s+không\s*[?？!]*|how\s+are\s+you\s*[?？!]*|(?:khỏe|ổn)\s+không\s*[?？!]*|ok(?:ay)?[!?.…]*|oke[!?.…]*|được[!?.…]*|ừ+m?!*[!.…]*|uhm+[!.…]*|hmm+[!.…]*|test(?:ing)?[!?.…]*|ping[!?.…]*|good\s+morning[!?.…]*|good\s+evening[!?.…]*|chào\s+buổi\s+(?:sáng|chiều|tối)[!?.…]*)$/i;

/** Lifestyle / đời sống small-talk cues. */
export const SOCIAL_LIFESTYLE_RE =
  /h[oô]m\s+nay\s+(?:th[eế]\s+n[aà]o|ra\s+sao|th[eế]\s+n[aà]o\s+r[oồ]i)|b[aạ]n\s+th[ií]ch\s+(?:g[iì]|c[aá]i\s+g[iì]|nh[uữ]ng\s+g[iì])|k[eể]\s+(?:cho\s+t[oôi]\s+)?(?:một\s+)?chuy[eệ]n\s+vui|k[eể]\s+chuy[eệ]n|bu[oồ]n\s+(?:qu[aá]|kh[oô]ng)|ch[aá]n\s+qu[aá]|m[eệ]t\s+(?:qu[aá]|kh[oô]ng)|th[oờ]i\s+ti[eế]t\s+(?:h[oô]m\s+nay|th[eế]\s+n[aà]o)|l[aàm]\s+g[iì]\s+(?:vui|đi)|ăn\s+g[iì]\s+(?:ngon|b[aây]\s+gi[oờ])|tư\s+v[aấ]n\s+(?:t[aâm]\s+s[uự]|đ[oờ]i\s+s[oố]ng)|how(?:'s|\s+is)\s+(?:your\s+)?day|what\s+do\s+you\s+(?:like|enjoy)|tell\s+me\s+a\s+(?:joke|funny\s+story|story)|good\s+(?:luck|night|vibes)|ng[uủ]\s+ngon|ch[uú]c\s+(?:ng[uủ]\s+ngon|một\s+ngày\s+vui)|b[aạ]n\s+(?:đang\s+)?(?:làm\s+gì|nghĩ\s+gì)|c[oó]\s+khuy[eê]n\s+(?:gì|nh[ư]\s+gì)\s*(?:kh[oô]ng)?/i;

/** Knowledge / source cues that force the full notebook path. */
export const SOCIAL_KNOWLEDGE_BLOCK_RE =
  /tóm\s*tắt|tom\s*tat|n[ộo]i\s*dung|nguồn|bài\s+viết|bài\s+báo|so\s*sánh|phân\s*tích|đối\s*chiếu|trích\s*dẫn|summarize|summarise|summary|overview|digest|article|source|compare|analy[sz]e|explain|giải\s*thích|cho\s+tôi\s+biết\s+về|tell\s+me\s+about|what\s+(?:is|does|about)|notebook|crawl|đọc\s+(?:bài|nguồn|link)|https?:\/\/|www\.|\.com|\.vn|\blink\b/i;

/**
 * Detect social/chitchat that should get a fast AI reply (no crawl/grounding).
 */
export function isSocialChitchatQuery(text) {
  const raw = String(text || "").trim();
  if (!raw || raw.includes("\n")) return false;
  const q = raw.replace(/\s+/g, " ").trim();
  if (!q || q.length > SOCIAL_CHITCHAT_MAX_CHARS) return false;
  if (SOCIAL_KNOWLEDGE_BLOCK_RE.test(q)) return false;
  if (CHAT_DEEP_QUERY_RE.test(q) || MAIN_CONTENT_DIGEST_RE.test(q)) return false;
  if (SOCIAL_CHITCHAT_RE.test(q)) return true;
  if (SOCIAL_LIFESTYLE_RE.test(q)) return true;
  if (
    q.length <= 100 &&
    !/\b(?:pentagon|oracle|diu|g-?bam|ndaa|army|navy|drone|missile)\b/i.test(q) &&
    /(?:\?|không|nhỉ|nhé|hả|sao|gì|thế|vậy|đi|vui|buồn|thích)/i.test(q) &&
    !/(?:bài|nguồn|tóm|phân\s*tích|so\s*sánh|nội\s*dung)/i.test(q) &&
    /(?:bạn|mình|tôi|yourself|you|hôm\s+nay|cuối\s+tuần|thích|kể|chuyện|tâm\s+sự|tư\s+vấn)/i.test(
      q
    )
  ) {
    return true;
  }
  return false;
}

/**
 * Detect «nội dung chính» / summarize intents for the dedicated digest fast-path.
 * Scoped: 1–2 checked sources. Whole notebook (none checked): allowed — caller
 * picks up to 2 URL sources from the notebook list.
 */
export function isMainContentDigestQuery(
  text,
  { selectedSourceIds = [], sourceCount = null } = {}
) {
  const q = String(text || "").trim();
  if (!q || q.length > 280) return false;
  if (CHAT_DEEP_QUERY_RE.test(q)) return false;
  if (!MAIN_CONTENT_DIGEST_RE.test(q)) return false;
  const selected = (Array.isArray(selectedSourceIds) ? selectedSourceIds : [])
    .map(normalizeNotebookSourceId)
    .filter(Boolean);
  if (selected.length > 2) return false;
  if (selected.length >= 1) return true;
  // Whole notebook: allow digest when notebook is small enough to crawl (≤2).
  if (sourceCount == null) return true;
  const n = Number(sourceCount) || 0;
  return n >= 1 && n <= 2;
}

/**
 * Detect short/simple Notebook chat questions → single fastest healthy model,
 * tighter context (insights), no parallel race / Groq polish.
 */
export function isSimpleNotebookChatQuery(
  text,
  { selectedSourceIds = [] } = {}
) {
  const q = String(text || "").trim();
  if (!q) return false;
  const selected = (Array.isArray(selectedSourceIds) ? selectedSourceIds : [])
    .map(normalizeNotebookSourceId)
    .filter(Boolean);
  // Multi-source deep work stays on the full path.
  if (selected.length >= 3) return false;
  // Main-content digests are also "simple" (dedicated API or tight chat).
  if (isMainContentDigestQuery(q, { selectedSourceIds })) return true;
  // One scoped source → always prefer fast path.
  if (selected.length === 1) return true;
  if (CHAT_DEEP_QUERY_RE.test(q)) return false;
  if (q.length <= CHAT_FAST_QUERY_MAX_CHARS && !/\n{2,}/.test(q)) return true;
  const words = q.split(/\s+/).filter(Boolean);
  return words.length <= 12 && q.length <= 220;
}

/** Compact answer hint prepended on fast-path chats. */
export const NOTEBOOK_FAST_ANSWER_HINT =
  "[Trả lời trực tiếp ngay câu đầu; chỉ nêu dữ kiện cần để trả lời. " +
  "Câu hỏi sự kiện/định danh/số liệu: 1–3 câu. Tóm tắt: 2–3 đoạn ngắn. " +
  "Chỉ dùng gạch đầu dòng khi người dùng yêu cầu liệt kê hoặc so sánh. " +
  "CHỈ dùng văn bản nguồn dưới đây — không bịa quốc gia/chủ đề không có trong nguồn.]\n\n";

/** Compact style hint for Vietnamese answers (prepended to user messages). */
export const NOTEBOOK_ANSWER_STYLE_HINT =
  "[Hướng dẫn trả lời: Viết báo cáo tiếng Việt theo văn phong quân sự–hành chính, " +
  "trung tính, chặt chẽ, logic và rõ chủ thể. Chọn câu mở đầu tự nhiên theo nội dung, " +
  "không sao chép một mẫu cố định; liên kết các ý bằng «Theo đó», «Đồng thời», " +
  "«Bên cạnh đó», «Qua đó» khi phù hợp, không máy móc. " +
  "CHỈ dựa trên văn bản nguồn trong context (full_text / insights đã cho) — " +
  "nguồn có thể là tiếng Anh/Trung/Nhật/khác; đọc đúng nội dung, không bỏ CJK. " +
  "không bịa quốc gia, tổ chức hay chủ đề không xuất hiện trong nguồn " +
  "(ví dụ không nhắc Việt Nam/Nhật Bản nếu nguồn không có). " +
  "Tin vào nội dung bài, không suy diễn chỉ từ tiêu đề. " +
  "Trước khi viết, xác định đúng ý định: câu hỏi ai/cái gì/ở đâu/khi nào/bao nhiêu phải trả lời " +
  "thẳng dữ kiện trong câu đầu; yêu cầu tóm tắt viết 2–3 đoạn ngắn; yêu cầu phân tích/báo cáo " +
  "viết 3–5 đoạn theo trình tự sự việc–nội dung chính–tác động, mỗi đoạn một ý. " +
  "Chừa một dòng trống giữa các đoạn. Không chép URL, Published Time, nhãn kỹ thuật, metadata " +
  "hoặc các đoạn trích dài vào thân câu trả lời. Không dùng gạch đầu dòng trừ khi người dùng " +
  "yêu cầu liệt kê/so sánh. Không viết «Câu 1/Câu 2», không lặp yêu cầu, " +
  "chỉ nêu tác động hoặc kết luận khi nguồn nói rõ; không tự thêm câu kết kiểu «cho thấy», " +
  "«phản ánh», «hợp thức hóa». Khi đã trả lời đủ thì dừng, không diễn đạt lại cùng một ý. " +
  "không nhắc prompt, chỉ dẫn hệ thống, model, provider hoặc quá trình xử lý; " +
  "không viết bài luận dài / lặp lại. " +
  "Khi dùng thông tin từ nguồn, trích dẫn [source:…] đúng ID có trong context. " +
  "Nếu nguồn trống hoặc không đủ, nói rõ «không đọc được / thiếu nội dung» — không đoán.]\n\n";

/**
 * Build a browser text-fragment URL for highlighting a quoted span.
 * Falls back to the bare source URL when the excerpt is too short.
 */
export function buildTextFragmentHref(sourceUrl, excerpt) {
  const base = String(sourceUrl || "").trim();
  if (!/^https?:\/\//i.test(base)) return "";
  const bare = base.split("#")[0];
  const clean = String(excerpt || "")
    .replace(/\s+/g, " ")
    .trim();
  if (clean.length < 12) return bare;
  const start = clean.slice(0, 96);
  try {
    return `${bare}#:~:text=${encodeURIComponent(start)}`;
  } catch {
    return bare;
  }
}

/**
 * Interactive Chat preference: ShopAIKey → dedicated Groq → OpenRouter.
 * Cerebras (402) and Ollama (slow CPU inference) stay out of this user-facing path.
 * Dòng tin title translate keeps its own Groq translate pool — untouched here.
 */
export const CHAT_PROVIDER_ORDER = ["shopaikey", "groq", "openrouter"];

/**
 * Transformation preference (fast TTFT, cloud before local):
 * ShopAIKey deep → Groq notebook → OpenRouter → Ollama last.
 * SPA rolls silently on 402/429/quota; cooling providers sink via health router.
 * Never parks on Ollama/qwen 1.5b while any cloud tier is healthy.
 */
export const TRANSFORM_PROVIDER_ORDER = [
  "shopaikey",
  "groq",
  "openrouter",
  "ollama",
];

const SHOPAIKEY_FAST_MODEL_NAMES = new Set(["qwen-flash", "qwen-plus-latest"]);
const SHOPAIKEY_DEEP_MODEL_NAMES = new Set([
  "qwen3-next-80b-a3b-instruct",
  "gpt-5-mini",
]);
const SHOPAIKEY_MANUAL_MODEL_NAMES = new Set(["qwen3-235b-a22b"]);
const SHOPAIKEY_MODEL_NAMES = new Set([
  ...SHOPAIKEY_FAST_MODEL_NAMES,
  ...SHOPAIKEY_DEEP_MODEL_NAMES,
  ...SHOPAIKEY_MANUAL_MODEL_NAMES,
]);

export function isNotebookShopAIKeyModel(m) {
  if (!m || m.type !== "language") return false;
  const name = String(m.name || "").trim();
  return (
    m.provider === "openai_compatible" && SHOPAIKEY_MODEL_NAMES.has(name)
  );
}

const GROQ_STUDIO_MODEL_NAMES = new Set([
  "openai/gpt-oss-20b",
  "openai/gpt-oss-120b",
]);

/**
 * Studio only shows models proven useful for interactive transformations.
 * Unreliable free routes, Cerebras 402 entries and slow local CPU models stay
 * registered for background/recovery work but do not clutter this selector.
 */
export function isNotebookStudioModel(m) {
  if (!m || m.type !== "language") return false;
  if (isNotebookShopAIKeyModel(m)) return true;
  return (
    String(m.provider || "").toLowerCase() === "groq" &&
    GROQ_STUDIO_MODEL_NAMES.has(String(m.name || "").trim())
  );
}

/** Known Cerebras model ids registered via openai_compatible. */
const CEREBRAS_MODEL_NAMES = new Set([
  "gpt-oss-120b",
  "gemma-4-31b",
  "zai-glm-4.7",
]);

export function isNotebookCerebrasModel(m) {
  if (!m || m.type !== "language") return false;
  const name = String(m.name || "").trim();
  if (CEREBRAS_MODEL_NAMES.has(name)) return true;
  return (
    m.provider === "openai_compatible" && /gpt-oss|gemma-4|zai-glm/i.test(name)
  );
}

export function notebookProviderOfModel(model) {
  if (!model) return "";
  if (isNotebookShopAIKeyModel(model)) return "shopaikey";
  if (isNotebookCerebrasModel(model)) return "cerebras";
  return String(model.provider || "").toLowerCase();
}

function _pickTierModel(lang, provider, profile = "fast") {
  const first = (pred) => lang.find(pred);
  if (provider === "shopaikey") {
    const deep = String(profile || "").toLowerCase() === "deep";
    const preferred = deep
      ? first(
          (m) =>
            isNotebookShopAIKeyModel(m) &&
            SHOPAIKEY_DEEP_MODEL_NAMES.has(String(m.name || "").trim())
        )
      : first(
          (m) =>
            isNotebookShopAIKeyModel(m) &&
            SHOPAIKEY_FAST_MODEL_NAMES.has(String(m.name || "").trim())
        );
    return preferred || first(isNotebookShopAIKeyModel);
  }
  if (provider === "cerebras") return first(isNotebookCerebrasModel);
  if (provider === "openrouter") {
    // Prefer free-tier OpenRouter ids (fast + no paid burn) when registered.
    return (
      first(
        (m) =>
          m.provider === "openrouter" &&
          /(?:^|\/)free$|:free$/i.test(String(m.name || ""))
      ) ||
      first(
        (m) =>
          m.provider === "openrouter" &&
          String(m.name || "").toLowerCase().includes("free")
      ) ||
      first((m) => m.provider === "openrouter")
    );
  }
  if (provider === "groq") return first((m) => m.provider === "groq");
  if (provider === "ollama") {
    // Last-resort only — prefer smaller local for speed when cloud is gone.
    return (
      first(
        (m) => m.provider === "ollama" && String(m.name || "").includes("1.5b")
      ) ||
      first((m) => m.provider === "ollama" && m.name === "qwen2.5:3b") ||
      first((m) => m.provider === "ollama")
    );
  }
  return null;
}

/**
 * Best available models, one per tier, in preference order.
 * @param {object[]} list Open Notebook models
 * @param {string[]} [order]
 * @returns {{ ids: string[], byId: Record<string, object>, providers: string[] }}
 */
export function pickProviderFallbackChain(
  list,
  order = CHAT_PROVIDER_ORDER,
  { profile = "fast" } = {}
) {
  const lang = (list || []).filter((m) => m.type === "language");
  const ordered = (order || CHAT_PROVIDER_ORDER)
    .map((p) => _pickTierModel(lang, p, profile))
    .filter(Boolean);
  const seen = new Set();
  const ids = [];
  const byId = {};
  const providers = [];
  for (const m of ordered) {
    if (!m?.id || seen.has(m.id)) continue;
    seen.add(m.id);
    ids.push(m.id);
    byId[m.id] = m;
    providers.push(notebookProviderOfModel(m));
  }
  return { ids, byId, providers };
}

/** Transform fallback chain (OpenRouter → Groq → Cerebras → Ollama). */
export function pickTransformFallbackChain(list) {
  return pickProviderFallbackChain(list, TRANSFORM_PROVIDER_ORDER);
}

/**
 * Pick default transform model id: first healthy cloud in try-order.
 * Never returns an Ollama id when any cloud model is in the chain.
 */
export function pickTransformPreferredModelId(orderedIds, byId = {}, preferredId = "") {
  const ids = Array.isArray(orderedIds) ? orderedIds.filter(Boolean) : [];
  const isCloud = (id) => notebookProviderOfModel(byId[id]) !== "ollama";
  const cloudIds = ids.filter(isCloud);
  if (preferredId && cloudIds.includes(preferredId)) return preferredId;
  if (preferredId && ids.includes(preferredId) && !cloudIds.length) {
    return preferredId;
  }
  return cloudIds[0] || ids[0] || preferredId || "";
}

/**
 * Short max_tokens hint for summary-style presets (faster TTFT).
 * Longer presets keep a higher budget. Open Notebook may ignore if unsupported.
 */
export function transformMaxTokensForPreset(transform) {
  const name = String(transform?.name || "").trim().toLowerCase();
  const title = String(transform?.title || "").trim().toLowerCase();
  const shortNames = new Set([
    "simple summary",
    "key insights",
    "table of contents",
    "reflections",
  ]);
  if (shortNames.has(name)) return 1536;
  if (
    title.includes("tóm tắt tình hình") ||
    title.includes("điểm then chốt") ||
    title.includes("cấu trúc nội dung") ||
    title.includes("câu hỏi làm rõ")
  ) {
    return 1536;
  }
  if (name === "dense summary" || name === "analyze paper") return 3072;
  if (name === "translate formal vn") return 6144;
  return 4096;
}

/** Interactive Chat fallback chain: dedicated Groq → OpenRouter only. */
export function pickNotebookFallbackChain(list) {
  return pickProviderFallbackChain(list, CHAT_PROVIDER_ORDER);
}

/** True when transform should try the next provider (402/429/quota/down). */
export function isTransformFailoverError(err) {
  const status = err?.status || 0;
  if (
    status === 402 ||
    status === 404 ||
    status === 429 ||
    status === 502 ||
    status === 503
  ) {
    return true;
  }
  const s = String(err?.message || err || "").toLowerCase();
  return (
    s.includes("402") ||
    s.includes("404") ||
    s.includes("model_not_found") ||
    s.includes("does not exist") ||
    s.includes("do not have access") ||
    s.includes("429") ||
    s.includes("rate limit") ||
    s.includes("too many requests") ||
    s.includes("quota") ||
    s.includes("payment required") ||
    s.includes("insufficient") ||
    s.includes("credits") ||
    s.includes("timeout") ||
    s.includes("timed out") ||
    s.includes("unavailable") ||
    s.includes("503") ||
    s.includes("502") ||
    s.includes("overloaded") ||
    s.includes("capacity")
  );
}

/** Stagger between parallel transform race starters (avoids 429 stampede). */
export const TRANSFORM_RACE_STAGGER_MS = 320;

/**
 * Build try-order for transform: selected cloud model first (if any), then
 * remaining healthy chain. Never pins Ollama ahead of cloud — that was the
 * slow "ollama-first" path when defaults still pointed at local qwen.
 */
export function buildTransformModelTryOrder(chainIds, selectedId, byId = {}) {
  const chain = Array.isArray(chainIds) ? chainIds.filter(Boolean) : [];
  if (!selectedId) return chain.length ? chain : [undefined];
  const prefProvider = notebookProviderOfModel(byId[selectedId]) || "";
  const hasCloud = chain.some(
    (id) => notebookProviderOfModel(byId[id]) !== "ollama"
  );
  // Never pin Ollama ahead of cloud for transform speed path.
  if (prefProvider === "ollama" && hasCloud) {
    return chain.length ? chain : [selectedId];
  }
  if (!chain.includes(selectedId)) {
    return chain.length ? [selectedId, ...chain] : [selectedId];
  }
  return [selectedId, ...chain.filter((id) => id !== selectedId)];
}

/** Client-side provider cooldown (mirrors backend mark; avoids stampede). */
const _localUnhealthyUntil = new Map();
let _healthCache = { at: 0, ttlMs: 45000, payload: null };

export function markLocalProviderUnhealthy(provider, ttlSec = 60) {
  const p = String(provider || "").toLowerCase();
  if (!p) return;
  const ttl = Math.max(15, Number(ttlSec) || 60) * 1000;
  _localUnhealthyUntil.set(p, Date.now() + ttl);
}

export function clearLocalProviderUnhealthy(provider) {
  _localUnhealthyUntil.delete(String(provider || "").toLowerCase());
}

function _localCooling(provider) {
  const until = _localUnhealthyUntil.get(String(provider || "").toLowerCase()) || 0;
  return until > Date.now();
}

/**
 * Reorder model ids: healthy/idle cloud first, Ollama always last among peers.
 * Cooling providers stay in the chain but ranked after ready cloud.
 * Never drops the only available model — silent roll needs a full chain.
 */
export function orderModelsByHealthyProviders(chain, health, byId = {}) {
  const ids = Array.isArray(chain) ? chain.filter(Boolean) : [];
  if (!ids.length) return ids;
  const tryOrder = Array.isArray(health?.try_order)
    ? health.try_order
    : Array.isArray(health?.order)
      ? health.order
      : CHAT_PROVIDER_ORDER;
  const healthySet = new Set(
    Array.isArray(health?.healthy) ? health.healthy : []
  );
  const rank = (id) => {
    const p = notebookProviderOfModel(byId[id]) || "";
    // Hard demote Ollama so chat never parks on local 1.5b while cloud exists.
    if (p === "ollama") return 2000 + (tryOrder.indexOf(p) + 1 || 9);
    if (_localCooling(p)) return 1000 + (tryOrder.indexOf(p) + 1 || 9);
    const idx = tryOrder.indexOf(p);
    const base = idx >= 0 ? idx : 50;
    // Prefer explicitly healthy; unknown (no health payload) keeps preference.
    if (healthySet.size && p && !healthySet.has(p)) return 100 + base;
    return base;
  };
  return [...ids].sort((a, b) => {
    const pa = notebookProviderOfModel(byId[a]) || "";
    const pb = notebookProviderOfModel(byId[b]) || "";
    if (pa === "ollama" && pb !== "ollama") return 1;
    if (pb === "ollama" && pa !== "ollama") return -1;
    return rank(a) - rank(b);
  });
}

/**
 * Fetch provider readiness (30–60s TTL). Falls back to preference order on error.
 * @returns {Promise<object>}
 */
export async function listHealthyChatModels(purpose = "chat", { signal, force } = {}) {
  const ttlMs = Math.max(
    15000,
    Number(_healthCache.ttlMs) || 45000
  );
  if (
    !force &&
    _healthCache.payload &&
    Date.now() - _healthCache.at < ttlMs
  ) {
    return { ..._healthCache.payload, cached: true };
  }
  try {
    const data = await notebookCloudApi.listHealthyModels(
      { purpose },
      { signal }
    );
    const ttlSec = Number(data?.ttl_sec) || 45;
    _healthCache = {
      at: Date.now(),
      ttlMs: ttlSec * 1000,
      payload: data,
    };
    return data;
  } catch {
    const fallback = {
      ok: false,
      purpose,
      order: [...CHAT_PROVIDER_ORDER],
      try_order: [...CHAT_PROVIDER_ORDER],
      healthy: [...CHAT_PROVIDER_ORDER],
      providers: {},
      ttl_sec: 30,
      cached: false,
      error: "health_unreachable",
    };
    return fallback;
  }
}

/**
 * Resolve the live try-order for chat/transform from catalog + health probe.
 * Never pins an Ollama preferred model ahead of healthy cloud (chat fast-path).
 */
export async function resolveNotebookModelTryOrder(
  models,
  {
    purpose = "chat",
    profile = "",
    preferredId = "",
    signal,
    forceHealth,
  } = {}
) {
  const effectiveProfile = profile || (purpose === "transform" ? "deep" : "fast");
  const { ids, byId } = pickProviderFallbackChain(
    models,
    purpose === "transform" ? TRANSFORM_PROVIDER_ORDER : CHAT_PROVIDER_ORDER,
    { profile: effectiveProfile }
  );
  const health = await listHealthyChatModels(purpose, {
    signal,
    force: forceHealth,
  });
  let ordered = orderModelsByHealthyProviders(ids, health, byId);
  if (preferredId && ordered.includes(preferredId)) {
    const prefProvider = notebookProviderOfModel(byId[preferredId]) || "";
    const hasCloud = ordered.some(
      (id) => notebookProviderOfModel(byId[id]) !== "ollama"
    );
    // Preferring a saved Ollama default must not jump ahead of OpenRouter/Groq.
    if (!(prefProvider === "ollama" && hasCloud)) {
      ordered = [preferredId, ...ordered.filter((id) => id !== preferredId)];
      // Keep cloud-before-ollama even after pinning a cloud preferred id.
      if (prefProvider === "ollama") {
        ordered = orderModelsByHealthyProviders(ordered, health, byId);
      }
    }
  } else if (preferredId && !ordered.length) {
    ordered = [preferredId];
  }
  return { ids: ordered.length ? ordered : ids, byId, health };
}

/**
 * After a provider fails mid-request: local + backend cooldown, bust health cache.
 */
export async function reportNotebookProviderFailure(
  provider,
  reason = "",
  { signal, seconds, latencyMs } = {}
) {
  const p = String(provider || "").toLowerCase();
  if (!p) return;
  const is402 =
    String(reason || "").includes("402") ||
    /payment|quota/i.test(String(reason || ""));
  const ttl = seconds != null ? seconds : is402 ? 900 : 60;
  markLocalProviderUnhealthy(p, ttl);
  _healthCache = { at: 0, ttlMs: _healthCache.ttlMs, payload: null };
  try {
    await notebookCloudApi.markProvider(
      {
        provider: p,
        reason: String(reason || "").slice(0, 160),
        seconds: ttl,
        ...(latencyMs != null ? { latency_ms: Math.round(latencyMs) } : {}),
      },
      { signal }
    );
  } catch {
    // local cooldown still applies
  }
}

export async function reportNotebookProviderSuccess(
  provider,
  { signal, latencyMs } = {}
) {
  const p = String(provider || "").toLowerCase();
  if (!p) return;
  clearLocalProviderUnhealthy(p);
  try {
    await notebookCloudApi.markProvider(
      {
        provider: p,
        success: true,
        ...(latencyMs != null ? { latency_ms: Math.round(latencyMs) } : {}),
      },
      { signal }
    );
  } catch {
    // ignore
  }
}

/**
 * Strip HTML/markdown noise into readable plain text for Transformation input.
 * Keeps meaning; drops tags, scripts, link URLs, nav/share/ad chrome, and
 * collapsed whitespace. When ``titleHint`` is set, discards everything before
 * the headline so Studio preview always starts at the title.
 *
 * @param {string} raw
 * @param {{ titleHint?: string }} [opts]
 */
export function cleanSourcePlainText(raw, { titleHint = "" } = {}) {
  let s = String(raw || "");
  if (!s.trim()) return "";
  s = s
    .replace(/&nbsp;/gi, " ")
    .replace(/&amp;/gi, "&")
    .replace(/&lt;/gi, "<")
    .replace(/&gt;/gi, ">")
    .replace(/&quot;/gi, '"')
    .replace(/&#39;/gi, "'");
  s = s.replace(/<script[\s\S]*?<\/script>/gi, " ");
  s = s.replace(/<style[\s\S]*?<\/style>/gi, " ");
  s = s.replace(/<(nav|aside|footer|header|menu|form)[\s\S]*?<\/\1>/gi, " ");
  s = s.replace(/<(br|\/p|\/div|\/h[1-6]|\/li|\/tr)[^>]*>/gi, "\n");
  s = s.replace(/<li[^>]*>/gi, "• ");
  s = s.replace(/<(?:img|source|video|audio|picture|iframe|svg)[^>]*>/gi, " ");
  s = s.replace(/<[^>]+>/g, " ");
  s = s.replace(/```[\s\S]*?```/g, " ");
  s = s.replace(/`([^`]+)`/g, "$1");
  s = s.replace(/!\[[^\]]*\]\([^)]+\)/g, " ");
  s = s.replace(/\[([^\]]+)\]\([^)]+\)/g, "$1");
  s = s.replace(/^#{1,6}\s+/gm, "");
  s = s.replace(/^\s{0,3}[-*+]\s+/gm, "• ");
  s = s.replace(/[*_]{1,3}([^*_\n]+)[*_]{1,3}/g, "$1");
  s = s.replace(/https?:\/\/\S+\.(?:png|jpe?g|gif|webp|svg|ico)(?:\?\S*)?/gi, " ");
  // Ad / tracker / share-widget URLs
  s = s.replace(
    /https?:\/\/\S*(?:doubleclick\.net|googlesyndication\.com|googleadservices\.com|adservice\.google|pagead2\.googlesyndication|adnxs\.com|adsrvr\.org|moatads\.com|scorecardresearch\.com|facebook\.com\/(?:tr|sharer|share\.php)|(?:platform\.)?twitter\.com\/(?:intent|widgets|share)|x\.com\/intent|linkedin\.com\/(?:shareArticle|sharing)|addtoany\.com|addthis\.com|sharethis\.com)\S*/gi,
    " "
  );
  // Run-on nav: «Skip to content Search SubscribeSign In EnergyScience…»
  s = s.replace(
    /skip\s+to\s+(?:main\s+)?content[\s\S]{0,240}?(?:search|subscribe|sign\s*in|log\s*in)(?:[\s\S]{0,160}?(?:energy|science|politics|world|business|tech|sports|opinion|culture))*/gi,
    " "
  );
  s = s.replace(
    /\b(subscribe|signin|sign|login|search|energy|science|politics)(?=[A-Z])/g,
    "$1 "
  );
  s = s.replace(
    /^(?:\s*(?:home|news|world|politics|business|tech(?:nology)?|science|energy|sports|opinion|culture|search|subscribe|sign\s*in|log\s*in|skip\s+to\s+(?:main\s+)?content)(?:\s*[|/·•>»,]?\s*)?)+(?=[A-ZÀ-ỸĐ"“«\u3400-\u9fff]|\d)/iu,
    ""
  );
  s = s.replace(/\r\n?/g, "\n");
  // Drop "Markdown Content:" label (Jina / reader dumps).
  s = s.replace(/^\s*markdown\s+content\s*:?\s*\n?/i, "");
  // While markers still present: drop sponsored inserts + Topics trailer.
  s = stripSponsoredInserts(s);
  s = stripTopicsTrailer(s);
  const bullet = "(?:[•·●◦▪▸►]|\\d+\\.|[-*+])\\s*";
  const sectionChip =
    "air|land|naval|space|cyber|networks?|ai|business|congress|pentagon|global|intel(?:ligence)?|special\\s+ops|policy|budget|industry|tech(?:nology)?|international|air\\s+force|army|navy|marines?|coast\\s+guard|news|videos?|energy|science|military|health|transportation|innovation|culture|future\\s+of\\s+defense";
  const chromeLine = new RegExp(
    `^\\s*(?:${bullet})?(?:skip\\s+to\\s+(?:main\\s+)?content|skip\\s+navigation|open\\s+navigation|close\\s+navigation|toggle\\s+(?:navigation|search)|search\\s*subscribe|subscribe(?:\\s+sign\\s*in)?|sign\\s*in|log\\s*in|share\\s+(?:this|on|options?|article)|share\\s*options?|share\\s+a\\s+link(?:\\s+to\\s+(?:this\\s+)?article)?|copy\\s+link|follow\\s+us|connect\\s+with\\s+us|cookie(?:s)?\\s+(?:policy|settings|notice)?|advertisement|sponsored\\s+(?:content|posts?)\\b.*|presented\\s+by|related\\s+articles?|you\\s+may\\s+also\\s+like|read\\s+more:?|read\\s+next\\s*:?.*|recommended\\s+articles?\\s*:?|get\\s+your\\s+news\\s+from\\b.*|google\\s+news|sign\\s+up\\s+for\\s+free|enter\\s+your\\s+email|blueprint\\s+by\\s+interesting\\s+engineering|\\d+\\s+comments?|learn\\s+more\\s*(?:>>|>|…|\\.\\.\\.)?|featured(?:\\s+(?:stories|articles|posts))?|newsletter(?:\\s+signup)?|sign\\s+up\\s+for\\s+(?:our\\s+)?newsletter|markdown\\s+content:?|about\\s+us|webinars?|careers?|contact\\s+us|privacy(?:\\s+policy)?|terms(?:\\s+of\\s+(?:use|service))?|(?:europe|us|uk|asia|global)\\s+edition|search\\s+for\\s*:?.*|news\\s+video\\s*:.*|special\\s+features?|air\\s*&\\s+space\\s+chiefs|manned[-\\s]?unmanned\\s+teaming|all\\s+rights\\s+reserved|©\\s*\\d{4})\\b.*$`,
    "i"
  );
  const socialLabel = new RegExp(
    `^\\s*(?:${bullet})?(?:twitter|facebook|youtube|linkedin|instagram|tiktok|reddit|rss|envelope|email|e-?mail|mail|whatsapp|telegram|pinterest|threads|\\bx\\b|share|share\\s*options?|copy\\s*link|print|permalink)\\s*$`,
    "i"
  );
  const socialRow = new RegExp(
    `^\\s*(?:${bullet})?(?:(?:twitter|facebook|youtube|linkedin|instagram|tiktok|reddit|rss|envelope|email|e-?mail|mail|whatsapp|telegram|pinterest|threads|share|copy\\s*link)(?:\\s*[|/·•,]\\s*|\\s+)){2,}(?:twitter|facebook|youtube|linkedin|instagram|tiktok|reddit|rss|envelope|email|e-?mail|mail|whatsapp|telegram|pinterest|threads|share|copy\\s*link)?\\s*$`,
    "i"
  );
  const sectionMenu = new RegExp(
    `^\\s*(?:${bullet})?(?:${sectionChip})(?:\\s*[|/·•,]\\s*|\\s+)(?:${sectionChip})(?:(?:\\s*[|/·•,]\\s*|\\s+)(?:${sectionChip}))+\\s*$`,
    "i"
  );
  const sectionChipLine = new RegExp(
    `^\\s*(?:${bullet})?(?:${sectionChip})\\s*$`,
    "i"
  );
  const menuLine = new RegExp(
    `^\\s*(?:${bullet})?(?:home|news|world|politics|business|tech(?:nology)?|science|energy|sports|opinion|culture|air|land|naval|space|cyber)(?:\\s*[|/·•]\\s*(?:home|news|world|politics|business|tech|science|energy|sports|opinion|culture|search|subscribe|sign\\s*in|air|land|naval|space|cyber)){2,}\\s*$`,
    "i"
  );
  const leadingCrumb = new RegExp(
    `^\\s*(?:${bullet})?(?:markdown\\s+content:?|about\\s+us|webinars?|careers?|contact(?:\\s+us)?|home|privacy(?:\\s+policy)?|terms(?:\\s+of\\s+(?:use|service))?|(?:europe|us|uk|asia|global)\\s+edition(?:\\s*newsletter\\s*signup)?|newsletter\\s*signup|toggle\\s+search|search\\s+for\\s*:?\\s*(?:search)?|breaking\\s+defense|farnborough|networks?\\s*(?:&\\s*|and\\s+)?digital\\s+warfare|open\\s+navigation.*|close\\s+navigation.*|toggle\\s+(?:search|navigation).*)\\s*$`,
    "i"
  );
  const photoCaption = /^.+\((?:photo|image|credit|source)\s+by\s+[^)]+\)\s*$/i;
  const urlOnly = /^\s*https?:\/\/\S+\s*$/i;
  const isChromeLine = (line) => {
    if (!line) return false;
    if (chromeLine.test(line)) return true;
    if (socialLabel.test(line)) return true;
    if (socialRow.test(line)) return true;
    if (sectionMenu.test(line)) return true;
    if (sectionChipLine.test(line)) return true;
    if (menuLine.test(line)) return true;
    if (leadingCrumb.test(line)) return true;
    if (photoCaption.test(line)) return true;
    if (urlOnly.test(line)) return true;
    const low = line.toLowerCase();
    if (
      (low.includes("open navigation") ||
        low.includes("close navigation") ||
        low.includes("toggle search") ||
        low.replace(/\s+/g, "").includes("newslettersignup")) &&
      line.length < 220
    ) {
      return true;
    }
    return false;
  };
  // Drop leading chrome block (social bullets / nav) even before title cut.
  {
    const lines = s.split("\n");
    let i = 0;
    while (i < lines.length) {
      const line = lines[i].replace(/[ \t]{2,}/g, " ").trim();
      if (!line) {
        i += 1;
        continue;
      }
      if (isChromeLine(line) || line.length < 3) {
        i += 1;
        continue;
      }
      break;
    }
    s = lines.slice(i).join("\n");
  }
  s = s
    .split("\n")
    .map((line) => line.replace(/[ \t]{2,}/g, " ").trim())
    .filter((line) => {
      if (!line) return true;
      if (isChromeLine(line)) return false;
      if (line.length < 3) return false;
      return true;
    })
    .join("\n");
  s = s.replace(/[ \t]+\n/g, "\n");
  s = s.replace(/\n{3,}/g, "\n\n");
  s = s.replace(/[ \t]{2,}/g, " ");
  s = s.trim();
  const hint = String(titleHint || "")
    .replace(/\s+/g, " ")
    .trim();
  if (hint.length >= 8) {
    s = cutPlainTextAtTitle(s, hint);
  }
  // Safety pass for markers that survived line filters.
  s = stripSponsoredInserts(s);
  s = stripTopicsTrailer(s);
  if (hint.length >= 8) {
    s = ensureTitlePrefix(s, hint);
  }
  return s.replace(/\n{3,}/g, "\n\n").trim();
}

/** Studio renders plain text; remove bold/italic marker runs models may emit. */
export function sanitizeStudioOutput(text) {
  return String(text || "")
    .replace(/\*{2,}/g, "")
    .replace(/[ \t]+$/gm, "")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

/** Drop mid-article «presented by» / Sponsored Post blocks; keep real body after. */
export function stripSponsoredInserts(text) {
  const lines = String(text || "").replace(/\r\n?/g, "\n").split("\n");
  const out = [];
  let i = 0;
  const skipBlanks = () => {
    while (i < lines.length && !lines[i].trim()) i += 1;
  };
  const presentedBy = /^\s*presented\s+by\s*$/i;
  const sponsoredPost = /^\s*sponsored\s*posts?\b/i;
  const bylineShort =
    /^\s*by\s+(?:breaking\s+defense|[A-Z][\w.'-]+(?:\s+[A-Z][\w.'-]+){0,3})\s*$/i;
  while (i < lines.length) {
    const bare = lines[i].trim();
    if (presentedBy.test(bare) || sponsoredPost.test(bare)) {
      if (presentedBy.test(bare)) {
        i += 1;
        skipBlanks();
        if (i < lines.length && sponsoredPost.test(lines[i].trim())) i += 1;
      } else {
        i += 1;
      }
      skipBlanks();
      if (i < lines.length) {
        const n = lines[i].trim().length;
        if (n >= 8 && n <= 180) i += 1;
      }
      skipBlanks();
      if (i < lines.length && lines[i].trim().length >= 40) i += 1;
      skipBlanks();
      if (i < lines.length && bylineShort.test(lines[i].trim())) i += 1;
      continue;
    }
    out.push(lines[i]);
    i += 1;
  }
  return out.join("\n");
}

/** Cut trailing Topics / Recommended / newsletter / comments rails through EOF. */
export function stripTopicsTrailer(text) {
  const s = String(text || "");
  const m =
    /^[ \t]*(?:#{1,6}[ \t]*)?(?:topics\s*:|recommended\s+articles?\s*:?|you\s+may\s+(?:also\s+)?like\s*:?|related\s+(?:articles?|stories|posts?)\s*:?|blueprint\s+by\s+interesting\s+engineering|get\s+the\s+latest\s+in\s+engineering|sign\s+up\s+for\s+free|\d+\s+comments?)\s*/im.exec(
      s
    );
  if (m && typeof m.index === "number") {
    return s.slice(0, m.index).replace(/\s+$/, "");
  }
  return s;
}

/** Drop site suffix (« | Breaking Defense») from a title hint. */
function titleMainHint(title) {
  const t = String(title || "")
    .replace(/\s+/g, " ")
    .trim();
  if (!t) return "";
  const parts = t.split(/\s+[|\u2013\u2014\-]\s+/);
  const main = (parts[0] || "").trim();
  return main.length >= 8 ? main : t;
}

/**
 * Find headline in plain text and discard everything before it.
 * Prefers same-line full-title match so a lone «Pentagon» chip cannot steal the cut.
 * Accepts optional Jina ``Title:`` prefix before the headline words.
 * @param {string} text
 * @param {string} title
 */
export function cutPlainTextAtTitle(text, title) {
  const body = String(text || "");
  if (!body.trim()) return "";
  const rawTitle = String(title || "")
    .replace(/\s+/g, " ")
    .trim();
  if (rawTitle.length < 8) return body;
  const candidates = [rawTitle];
  const main = titleMainHint(rawTitle);
  if (main && main.toLowerCase() !== rawTitle.toLowerCase()) {
    candidates.push(main);
  }
  const bullet = "(?:[•·●◦▪▸►]|\\d+\\.|[-*+])\\s*";
  const titlePrefix = "(?:Title\\s*:\\s*)?";
  for (const cand of candidates) {
    const words = cand.split(/\s+/).filter(Boolean);
    if (words.length < 3 && cand.length < 24) continue;
    const esc = words.map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
    const linePat = new RegExp(
      `^[ \\t]*(?:${bullet})?${titlePrefix}${esc.join("[ \\t]+")}`,
      "im"
    );
    const lineMatch = linePat.exec(body);
    if (lineMatch && typeof lineMatch.index === "number") {
      const sliceFrom = lineMatch.index;
      const window = body.slice(sliceFrom, sliceFrom + cand.length + 48);
      const inner = new RegExp(esc.join("[ \\t]+"), "i").exec(window);
      const at =
        inner && typeof inner.index === "number"
          ? sliceFrom + inner.index
          : sliceFrom;
      return body.slice(at).replace(/^[\r\n \t]+/, "");
    }
    if (words.length >= 5) {
      const partial = words.slice(0, Math.min(8, words.length));
      if (partial.join(" ").length >= 20) {
        const pEsc = partial.map((w) =>
          w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")
        );
        const pPat = new RegExp(
          `^[ \\t]*(?:${bullet})?${titlePrefix}${pEsc.join("[ \\t]+")}`,
          "im"
        );
        const pMatch = pPat.exec(body);
        if (pMatch && typeof pMatch.index === "number") {
          const lineEnd = body.indexOf("\n", pMatch.index);
          const line = body
            .slice(pMatch.index, lineEnd >= 0 ? lineEnd : undefined)
            .trim();
          if (line.length >= Math.max(24, Math.floor(cand.length * 0.45))) {
            const window = body.slice(pMatch.index, pMatch.index + 120);
            const inner = new RegExp(pEsc.join("[ \\t]+"), "i").exec(window);
            const at =
              inner && typeof inner.index === "number"
                ? pMatch.index + inner.index
                : pMatch.index;
            return body.slice(at).replace(/^[\r\n \t]+/, "");
          }
        }
      }
    }
  }
  return body;
}

/** True when cleaned text is essentially just the headline / dek (no real body). */
export function textLooksLikeTitleOnly(text, title = "") {
  const raw = String(text || "");
  const body = raw.replace(/\s+/g, " ").trim();
  if (!body) return true;
  const paras = raw
    .split(/\n\s*\n/)
    .map((p) => p.trim())
    .filter(Boolean);
  const main = titleMainHint(title);
  if (paras.length >= 3 && body.length >= Math.max(240, (main || "").length + 120)) {
    return false;
  }
  if (main && body.toLowerCase() === main.toLowerCase()) return true;
  if (main && body.toLowerCase().startsWith(main.toLowerCase())) {
    const rest = body.slice(main.length).replace(/^[\s\-:|–—]+/, "");
    if (rest.length < 400) return true;
  }
  if (main && body.length < Math.max(400, main.length + 200)) return true;
  if (!main && body.length < 400) return true;
  return false;
}

/** Ensure Studio input starts at the headline when known. */
export function ensureTitlePrefix(text, title = "") {
  const s = String(text || "").trim();
  const main = titleMainHint(title);
  if (!main) return s;
  const head = s.replace(/\s+/g, " ").slice(0, 240).toLowerCase();
  const words = main.toLowerCase().split(/\s+/).filter(Boolean);
  const probe = words.slice(0, Math.min(5, words.length)).join(" ");
  if (probe && (head.startsWith(probe) || head.includes(probe))) return s;
  if (!s) return main;
  return `${main}\n\n${s}`.trim();
}

/** True when text still looks like site chrome rather than article body. */
export function textLooksLikePageChrome(text) {
  const s = String(text || "").replace(/\s+/g, " ").trim();
  if (!s) return false;
  const head = s.slice(0, 400).toLowerCase();
  if (
    /\b(captcha|recaptcha|just a moment|unusual traffic|verify you are|access denied|cf-browser-verification)\b/i.test(
      head
    )
  ) {
    return true;
  }
  if (head.includes("skip to content") || head.includes("skip to main content")) {
    return true;
  }
  if (head.includes("open navigation") || head.includes("close navigation")) {
    return true;
  }
  if (head.includes("share options") && head.includes("copy link")) {
    return true;
  }
  if (head.includes("doubleclick.net") || head.includes("googlesyndication")) {
    return true;
  }
  const compact = head.replace(/\s+/g, "");
  if (compact.includes("subscribesign") || compact.includes("signinenergy")) {
    return true;
  }
  const socialHits = [
    "twitter",
    "facebook",
    "youtube",
    "linkedin",
    "share options",
  ].filter((w) => head.includes(w)).length;
  if (socialHits >= 3 && s.length < 1200) {
    return true;
  }
  const navHits = ["subscribe", "sign in", "log in", "newsletter", "cookie"].filter(
    (w) => head.includes(w)
  ).length;
  return navHits >= 2 && s.length < 900;
}

/** Usable article body for chat/transform grounding (length + not chrome). */
export function isUsableArticleBody(text, { minChars = 160 } = {}) {
  const s = String(text || "").trim();
  if (s.length < minChars) return false;
  return !textLooksLikePageChrome(s);
}

/** Heuristic: text contains Vietnamese diacritics. */
export function textLooksVietnamese(text) {
  return /[àáạảãâầấậẩẫăằắặẳẵèéẹẻẽêềếệểễìíịỉĩòóọỏõôồốộổỗơờớợởỡùúụủũưừứựửữỳýỵỷỹđ]/i.test(
    String(text || "")
  );
}

/**
 * Split cleaned prose into 3–8 clear sentences for the Transformation preview.
 */
export function extractClearSentences(text, { min = 3, max = 8 } = {}) {
  const plain = String(text || "").replace(/\s+/g, " ").trim();
  if (!plain) return [];
  // Latin + CJK sentence enders; allow next token to be Latin or CJK.
  const parts = plain
    .split(/(?<=[.!?…。！？])\s*(?=[A-ZÀ-ỸĐ"“«0-9\u3400-\u9fff])/u)
    .map((p) => p.trim())
    .filter((p) => {
      if (!p) return false;
      // CJK sentences are shorter in char count for same meaning.
      const minLen = /[\u3400-\u9fff]/.test(p) ? 8 : 18;
      return p.length >= minLen;
    });
  if (!parts.length) {
    const chunk = plain.slice(0, 900);
    return chunk ? [`${chunk}${plain.length > 900 ? "…" : ""}`] : [];
  }
  const n = parts.length < min ? parts.length : Math.min(max, parts.length);
  return parts.slice(0, n);
}

/**
 * UI preview for Transformation: title + 3–8 readable sentences.
 * Prefer Vietnamese insight/summary when available; else cleaned source excerpt.
 */
export function buildSourceReadableExcerpt({
  title = "",
  plainText = "",
  insights = [],
  maxSentences = 8,
} = {}) {
  const titleClean = cleanSourcePlainText(title).split("\n")[0].trim();
  const insightTexts = (Array.isArray(insights) ? insights : [])
    .map((i) => cleanSourcePlainText(typeof i === "string" ? i : i?.content || ""))
    .filter(Boolean);
  const vnInsight = insightTexts.find((t) => textLooksVietnamese(t));
  const insightBody = vnInsight || insightTexts[0] || "";
  const bodySource = insightBody || plainText || "";
  const sentences = extractClearSentences(bodySource, {
    min: 3,
    max: maxSentences,
  });
  const body = sentences.join(" ");
  const language =
    textLooksVietnamese(body) || textLooksVietnamese(titleClean) ? "vi" : "en";
  const preview = [titleClean ? `Tiêu đề: ${titleClean}` : "", body]
    .filter(Boolean)
    .join("\n\n");
  return {
    title: titleClean,
    preview,
    language,
    fromInsight: Boolean(insightBody),
    sentenceCount: sentences.length,
  };
}

/**
 * Prepare Transformation payload: cleaned plain-text input + readable preview.
 * Prefer crawl-cache body when provided via full_text override.
 * Never collapse a long article down to title-only.
 */
export function prepareTransformSourcePayload(
  sourceDetail,
  { insights = [], fallbackTitle = "" } = {}
) {
  const title = sourceDetail?.title || fallbackTitle || "";
  const raw = sourceDetail?.full_text || title || "";
  let plain = cleanSourcePlainText(raw, { titleHint: title }).slice(0, 120000);
  // Safety: if chrome still leads and title is known, cut again.
  if (title && textLooksLikePageChrome(plain.slice(0, 500))) {
    plain = cutPlainTextAtTitle(plain, title);
  }
  // Hard guard: title-only after clean while raw still has a body → keep more.
  const titleLen = String(title || "").trim().length;
  const plainLen = String(plain || "").trim().length;
  const rawLen = String(raw || "").trim().length;
  if (
    titleLen >= 8 &&
    textLooksLikeTitleOnly(plain, title) &&
    rawLen > titleLen + 400
  ) {
    // Re-clean without over-cutting: strip topics/sponsored only, cut at title.
    let rescue = stripTopicsTrailer(stripSponsoredInserts(String(raw)));
    rescue = cutPlainTextAtTitle(rescue, title);
    rescue = stripTopicsTrailer(stripSponsoredInserts(rescue));
    rescue = ensureTitlePrefix(rescue, title);
    if (rescue.trim().length > plainLen) {
      plain = rescue.trim().slice(0, 120000);
    }
  }
  plain = ensureTitlePrefix(plain, title);
  const excerpt = buildSourceReadableExcerpt({
    title,
    plainText: plain,
    insights,
  });
  return {
    inputText: plain || cleanSourcePlainText(title) || "",
    preview: excerpt.preview,
    previewMeta: excerpt,
  };
}

const MIN_GOOD_CHARS = 80;
const MAX_GOOD_CHARS = 4500;

/**
 * Detect empty / too-short / rambling answers so the SPA can roll providers
 * or optionally request a light Groq polish (not on every message).
 * @returns {string|null} issue code or null when OK
 */
export function notebookAnswerQualityIssue(text) {
  const body = String(text || "").trim();
  if (!body) return "empty";
  const plain = body
    .replace(/\[(?:source|insight):[^\]]+\]/gi, "")
    .replace(/\s+/g, " ")
    .trim();
  if (plain.length < MIN_GOOD_CHARS) return "too_short";
  if (plain.length > MAX_GOOD_CHARS) return "too_long";
  const sentences = body.split(/(?<=[.!?…])\s+|\n{2,}/).filter(Boolean);
  const foreignSentence = sentences.some((sentence) => {
    const value = sentence.trim();
    const latinWords = value.match(/\b[A-Za-z]{3,}\b/g) || [];
    return value.length >= 40 && latinWords.length >= 6 && !textLooksVietnamese(value);
  });
  if (!textLooksVietnamese(plain) || foreignSentence) return "not_vietnamese";
  const lower = plain.toLowerCase();
  if (
    /hệ\s*thống\s+(?:nhắc\s*nhở|yêu\s*cầu|chỉ\s*dẫn)|chỉ\s*dẫn\s+(?:nội\s*bộ|hệ\s*thống)|(?:system|developer)\s+(?:prompt|message|instruction)|chúng\s+ta\s+cần\s+(?:sử\s+dụng|tạo|trả\s+lời)|không\s+sử\s+dụng\s+markdown/i.test(
      plain
    )
  ) {
    return "prompt_leakage";
  }
  if ((plain.match(/\bCâu\s+\d+\s*:/gi) || []).length >= 2) {
    return "numbered_dump";
  }
  const rambleHints = [
    "tóm lại một lần nữa",
    "như đã đề cập ở trên",
    "in conclusion",
    "to summarize everything",
    "as mentioned above",
  ];
  if (rambleHints.some((h) => lower.includes(h))) return "rambling";
  const paras = body.split(/\n{2,}/).filter((p) => p.trim());
  if (paras.length >= 8 && plain.length > 1800) return "rambling";
  return null;
}

const NOTEBOOK_PROMPT_LEAK_RE =
  /hệ\s*thống\s+(?:nhắc\s*nhở|yêu\s*cầu|chỉ\s*dẫn)|chỉ\s*dẫn\s+(?:nội\s*bộ|hệ\s*thống)|(?:system|developer)\s+(?:prompt|message|instruction)|(?:the\s+)?(?:system|developer)\s+(?:says|asks|requires)|you\s+(?:must|should|are\s+asked\s+to)\s+(?:answer|output|write)|không\s+sử\s+dụng\s+markdown|hãy\s+(?:tạo|trích\s*xuất|đưa\s+ra)\s+(?:các\s+)?(?:thông\s+tin|câu|tóm\s*tắt)|chúng\s+ta\s+cần\s+(?:sử\s+dụng|tạo|trả\s+lời)|(?:đầu\s+ra|output)\s+(?:phải|cần|should|must)/i;

/**
 * Output guard: remove leaked prompt/instructions, numbered sentence labels,
 * and exact duplicate statements before rendering or quality validation.
 */
export function sanitizeNotebookAnswer(text) {
  const raw = String(text || "")
    .replace(/\r\n?/g, "\n")
    .trim();
  if (!raw) return "";
  const paragraphs = raw
    .split(/\n{2,}/)
    .map((p) => p.trim())
    .filter(Boolean)
    .filter((p) => !NOTEBOOK_PROMPT_LEAK_RE.test(p))
    .map((p) =>
      p
        .split("\n")
        .map((line) => line.trim())
        .filter((line) => line && !NOTEBOOK_PROMPT_LEAK_RE.test(line))
        .join(" ")
    )
    .filter(Boolean);
  let clean = paragraphs.join("\n\n");
  const connectors = [
    "",
    "Theo đó, ",
    "Đồng thời, ",
    "Bên cạnh đó, ",
    "Qua đó, ",
  ];
  clean = clean.replace(
    /(?:^|\n)\s*(?:[-•*]\s*)?Câu\s+([1-9]\d*)\s*:\s*/gim,
    (_, n) => `\n${connectors[Math.min(Number(n) - 1, connectors.length - 1)]}`
  );
  clean = clean.replace(/^\s*[-•*]\s+(?=\S)/gm, "");
  clean = clean.replace(/[ \t]{2,}/g, " ").replace(/\n{3,}/g, "\n\n").trim();
  const seen = new Set();
  return clean
    .split(/(?<=[.!?…])\s+|\n{2,}/u)
    .map((part) => part.trim())
    .filter((part) => {
      const key = part.toLocaleLowerCase("vi").replace(/[^\p{L}\p{N}]+/gu, "");
      if (key.length >= 24 && seen.has(key)) return false;
      if (key) seen.add(key);
      return Boolean(part);
    })
    .join(" ")
    .trim();
}

function _contextSourceText(source) {
  const insights = (Array.isArray(source?.insights) ? source.insights : [])
    .map((item) => String(item?.content || item || "").trim())
    .filter(Boolean);
  return (
    insights.find((value) => textLooksVietnamese(value)) ||
    insights[0] ||
    String(source?.full_text || source?.content || "")
  );
}

/**
 * Deterministic, cited final node for the Chat flow. It is intentionally
 * extractive: when every LLM provider is unavailable, facts still come only
 * from the context already built for this turn.
 */
export function buildDeterministicGroundedAnswer(question, context) {
  const sources = Array.isArray(context?.sources) ? context.sources : [];
  const queryTerms = new Set(_chatTerms(question));
  const candidates = [];
  sources.forEach((source, sourceIndex) => {
    const id = normalizeNotebookSourceId(source?.id);
    if (!id) return;
    const title = cleanSourcePlainText(source?.title || "").split("\n")[0].trim();
    const raw = cleanSourcePlainText(_contextSourceText(source), {
      titleHint: title,
    });
    let sentences = extractClearSentences(raw, { min: 1, max: 18 });
    if (!sentences.length && raw.length >= 80) sentences = [raw.slice(0, 700)];
    const titleTerms = new Set(_chatTerms(title));
    sentences.forEach((sentence, sentenceIndex) => {
      if (NOTEBOOK_PROMPT_LEAK_RE.test(sentence)) return;
      const terms = new Set(_chatTerms(sentence));
      let score = Math.max(0, 12 - sentenceIndex);
      for (const term of queryTerms) {
        if (terms.has(term)) score += 12;
        if (titleTerms.has(term)) score += 4;
      }
      candidates.push({
        id,
        title,
        sentence: sentence.replace(/\s+/g, " ").trim().slice(0, 900),
        sourceIndex,
        sentenceIndex,
        score,
      });
    });
  });
  if (!candidates.length) return "";
  const selected = [];
  for (const item of candidates.sort(
    (a, b) =>
      b.score - a.score ||
      a.sourceIndex - b.sourceIndex ||
      a.sentenceIndex - b.sentenceIndex
  )) {
    if (
      selected.some(
        (hit) =>
          hit.id === item.id &&
          hit.sentence.toLocaleLowerCase("vi") ===
            item.sentence.toLocaleLowerCase("vi")
      )
    ) {
      continue;
    }
    selected.push(item);
    if (selected.length >= 4) break;
  }
  const titles = [...new Set(selected.map((item) => item.title).filter(Boolean))];
  const lead =
    titles.length === 1
      ? `Nguồn tin đề cập nội dung liên quan đến «${titles[0].slice(0, 180)}».`
      : "Kết quả rà soát các nguồn đã chọn cho thấy một số nội dung chính.";
  const connectors = ["Theo đó", "Đồng thời", "Bên cạnh đó", "Qua đó"];
  const facts = selected.map((item, index) => {
    const citation = `[${item.id}]`;
    const fact = item.sentence.replace(/^[•*\-\d.)\s]+/, "").trim();
    if (textLooksVietnamese(fact)) {
      return `${connectors[index]}, ${fact} ${citation}`;
    }
    return `${connectors[index]}, nguồn ghi nhận nguyên văn: “${fact}” ${citation}`;
  });
  return sanitizeNotebookAnswer([lead, ...facts].join(" "));
}

/** Latest AI message content from Open Notebook execute response. */
export function lastAiMessageContent(messages) {
  const list = Array.isArray(messages) ? messages : [];
  for (let i = list.length - 1; i >= 0; i -= 1) {
    const m = list[i];
    const role = m?.type || m?.role;
    if (role === "ai" || role === "assistant") {
      return String(m.content || "");
    }
  }
  return "";
}

export class NotebookApiError extends Error {
  constructor(message, status = 0, payload = null) {
    super(message);
    this.name = "NotebookApiError";
    this.status = status;
    this.payload = payload;
  }
}

async function nbFetch(path, { method = "GET", body, signal } = {}) {
  const url = `${NB_BASE}${path.startsWith("/") ? path : `/${path}`}`;
  const headers = {};
  let payload = body;
  if (body != null && !(body instanceof FormData)) {
    headers["Content-Type"] = "application/json";
    payload = JSON.stringify(body);
  }
  const res = await fetch(url, { method, headers, body: payload, signal });
  const text = await res.text();
  let data = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = text;
    }
  }
  if (!res.ok) {
    const msg =
      (data && (data.detail || data.message || data.error)) ||
      (typeof data === "string" ? data : `Notebook API ${res.status}`);
    throw new NotebookApiError(
      typeof msg === "string" ? msg : JSON.stringify(msg),
      res.status,
      data
    );
  }
  return data;
}

/**
 * Normalize Open Notebook source ids (`source:xxx` or bare id).
 * Matching must be exact on the wire; helpers compare both forms.
 */
export function normalizeNotebookSourceId(id) {
  const raw = String(id || "").trim();
  if (!raw) return "";
  return raw.startsWith("source:") ? raw : `source:${raw}`;
}

/**
 * Build Open Notebook context_config for chat.
 * - No selectedIds → whole notebook (all listed sources, short/insights).
 * - With selectedIds → ONLY those ids as "full content"; every other listed
 *   source is explicitly "not in context" (Open Notebook skips on substring
 *   "not in"). Omitting an id also excludes it — we still mark listed ones
 *   explicitly so a partial sources[] cannot accidentally widen scope.
 *
 * Status strings match Open Notebook protocol:
 * "not in context" | "insights" | "full content"
 */
export function buildNotebookContextConfig(
  sources,
  selectedIds = [],
  { fast = false } = {}
) {
  const selectedList = (Array.isArray(selectedIds) ? selectedIds : [])
    .map(normalizeNotebookSourceId)
    .filter(Boolean);
  const selected = new Set(selectedList);
  const scoped = selected.size > 0;
  const sourcesMap = {};
  // Scoped (1–2 checked): always request full article body — insights-only
  // caused off-topic answers from title/hallucination. Fast still truncates
  // via shrinkNotebookChatContext. Whole notebook stays insights/short.
  const scopedStatus = "full content";

  for (const s of sources || []) {
    const id = normalizeNotebookSourceId(s?.id);
    if (!id) continue;
    if (scoped) {
      sourcesMap[id] = selected.has(id) ? scopedStatus : "not in context";
    } else if (fast && (sources || []).length <= 2) {
      // Small whole-notebook: keep full body so crawl-backed text is used.
      sourcesMap[id] = "full content";
    } else {
      // Large whole notebook: short context so multi-source fits.
      sourcesMap[id] = "insights";
    }
  }

  // Ensure every explicitly selected id is present even if missing from the
  // current sources[] snapshot (stale list / pagination edge).
  if (scoped) {
    for (const id of selected) {
      if (!sourcesMap[id]) sourcesMap[id] = scopedStatus;
    }
  }

  return { sources: sourcesMap, notes: {} };
}

/**
 * Post-filter /chat/context payload so execute never sees unselected sources
 * when the user scoped the chat (defense in depth if the API widens).
 */
export function filterBuiltNotebookContext(context, selectedIds = []) {
  const selectedList = (Array.isArray(selectedIds) ? selectedIds : [])
    .map(normalizeNotebookSourceId)
    .filter(Boolean);
  if (!selectedList.length) {
    return context && typeof context === "object"
      ? context
      : { sources: [], notes: [] };
  }
  const selected = new Set(selectedList);
  const srcs = Array.isArray(context?.sources) ? context.sources : [];
  const notes = Array.isArray(context?.notes) ? context.notes : [];
  return {
    sources: srcs.filter((s) => selected.has(normalizeNotebookSourceId(s?.id))),
    notes, // notes stay as built; we never put notes in scoped config
  };
}

function _truncateChatField(text, maxChars) {
  const s = String(text || "");
  if (!maxChars || s.length <= maxChars) return s;
  return `${s.slice(0, maxChars)}\n…[đã cắt để chat nhanh hơn]`;
}

function _chatTerms(value) {
  const stop = new Set([
    "nhung", "nao", "duoc", "trong", "nguon", "thong", "tin", "bang",
    "chung", "theo", "ve", "va", "cua", "cho", "mot", "cac", "la", "gi",
    "what", "which", "from", "with", "that", "this", "the", "and", "are",
  ]);
  return (String(value || "")
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .replace(/đ/g, "d")
    .match(/[a-z0-9\u00c0-\u024f\u4e00-\u9fff]{3,}/g) || [])
    .filter((term) => !stop.has(term));
}

/**
 * Prefer question-relevant passages over blindly keeping the beginning of a
 * long article. The intro is retained for context, then high-overlap passages.
 */
function _extractRelevantChatText(text, question, maxChars) {
  const raw = String(text || "");
  if (!maxChars || raw.length <= maxChars) return raw;
  const terms = new Set(_chatTerms(question));
  if (!terms.size) return _truncateChatField(raw, maxChars);
  const chunks = raw
    .split(/\n{2,}|(?<=[.!?])\s+(?=[A-ZÀ-Ỹ0-9])/u)
    .map((value, index) => ({ value: value.trim(), index }))
    .filter((item) => item.value.length >= 40);
  if (!chunks.length) return _truncateChatField(raw, maxChars);
  const scored = chunks.map((item) => {
    const hay = new Set(_chatTerms(item.value));
    let overlap = 0;
    for (const term of terms) if (hay.has(term)) overlap += 1;
    return { ...item, score: overlap * 10 - item.index * 0.002 };
  });
  const picked = [chunks[0]];
  for (const item of scored.sort((a, b) => b.score - a.score)) {
    if (picked.some((hit) => hit.index === item.index)) continue;
    picked.push(item);
    if (picked.reduce((sum, hit) => sum + hit.value.length + 2, 0) >= maxChars) {
      break;
    }
  }
  return _truncateChatField(
    picked.sort((a, b) => a.index - b.index).map((item) => item.value).join("\n\n"),
    maxChars
  );
}

/**
 * Shrink built chat context before /chat/execute so TTFT is faster.
 * Scoped chats keep more of each selected source; whole-notebook stays lean.
 * Fast path uses tighter caps and prefers insights over full dump.
 * Does not change which source ids are included (filter first, then shrink).
 */
export function shrinkNotebookChatContext(
  context,
  {
    scoped = false,
    fast = false,
    question = "",
    maxSources = null,
  } = {}
) {
  const base =
    context && typeof context === "object"
      ? context
      : { sources: [], notes: [] };
  const cap = fast
    ? CHAT_FAST_SOURCE_CHAR_CAP
    : scoped
      ? CHAT_SCOPED_SOURCE_CHAR_CAP
      : CHAT_WHOLE_SOURCE_CHAR_CAP;
  const insightCap = fast ? CHAT_FAST_INSIGHT_CHAR_CAP : CHAT_INSIGHT_CHAR_CAP;
  const shrinkOne = (item) => {
    if (!item || typeof item !== "object") return item;
    const next = { ...item };
    if (next.full_text != null) {
      // Always keep truncated full_text when present — dropping it on the fast
      // path let models free-associate from titles/insights alone.
      next.full_text = _extractRelevantChatText(next.full_text, question, cap);
    }
    if (next.content != null && next.full_text == null) {
      next.content = _extractRelevantChatText(next.content, question, cap);
    }
    if (Array.isArray(next.insights)) {
      next.insights = next.insights.map((ins) =>
        ins && typeof ins === "object"
          ? {
              ...ins,
              content: _truncateChatField(ins.content, insightCap),
            }
          : ins
      );
    }
    return next;
  };
  let sources = (Array.isArray(base.sources) ? base.sources : []).map(
    shrinkOne
  );
  const limit = Number(maxSources) || (fast ? 6 : 10);
  if (!scoped && question && sources.length > limit) {
    const terms = new Set(_chatTerms(question));
    sources = sources
      .map((source, index) => {
        const title = String(source?.title || "");
        const body = String(
          source?.full_text ||
            source?.content ||
            (Array.isArray(source?.insights)
              ? source.insights.map((item) => item?.content || "").join(" ")
              : "")
        );
        const titleTerms = new Set(_chatTerms(title));
        const bodyTerms = new Set(_chatTerms(body.slice(0, 12_000)));
        let score = 0;
        for (const term of terms) {
          if (titleTerms.has(term)) score += 6;
          if (bodyTerms.has(term)) score += 2;
        }
        return { source, index, score };
      })
      .sort((a, b) => b.score - a.score || a.index - b.index)
      .slice(0, limit)
      .map((item) => item.source);
  }
  return {
    ...base,
    sources,
    notes: (Array.isArray(base.notes) ? base.notes : []).map((n) => {
      if (!n || typeof n !== "object") return n;
      return {
        ...n,
        content: _truncateChatField(n.content ?? n.full_text, insightCap),
      };
    }),
  };
}

/**
 * Validate explicit [source:ID] markers against the context actually sent.
 * Missing citations are reported but accepted; invented IDs are rejected.
 */
export function inspectNotebookCitations(answer, context) {
  const validIds = new Set(
    (Array.isArray(context?.sources) ? context.sources : [])
      .map((source) => normalizeNotebookSourceId(source?.id))
      .filter(Boolean)
  );
  const cited = [];
  const pattern = /\[source:([^\]\s]+)\]/gi;
  const text = String(answer || "");
  let match;
  while ((match = pattern.exec(text))) {
    const raw = String(match[1] || "").replace(/^source:/i, "");
    const normalized = normalizeNotebookSourceId(`source:${raw}`);
    if (normalized && !cited.includes(normalized)) cited.push(normalized);
  }
  const invalid = cited.filter((id) => !validIds.has(id));
  const valid = cited.filter((id) => validIds.has(id));
  return {
    status: invalid.length ? "invalid" : valid.length ? "ok" : "missing",
    cited_ids: cited,
    valid_ids: valid,
    invalid_ids: invalid,
    coverage:
      validIds.size > 0
        ? Math.round((valid.length / Math.min(validIds.size, 4)) * 100) / 100
        : 1,
  };
}

/** Convert internal [source:id] markers into readable numbered references. */
export function formatNotebookCitations(answer, context) {
  const sources = Array.isArray(context?.sources) ? context.sources : [];
  const byId = new Map(
    sources
      .map((source) => [normalizeNotebookSourceId(source?.id), source])
      .filter(([id]) => Boolean(id))
  );
  const cited = [];
  let text = String(answer || "").replace(
    /\[source:([^\]\s]+)\]/gi,
    (_, rawId) => {
      const id = normalizeNotebookSourceId(
        `source:${String(rawId || "").replace(/^source:/i, "")}`
      );
      const source = byId.get(id);
      if (!source) return "";
      let index = cited.findIndex((item) => item.id === id);
      if (index < 0) {
        cited.push({ id, source });
        index = cited.length - 1;
      }
      return `[${index + 1}]`;
    }
  );
  text = text.replace(/[ \t]+([.,;:!?])/g, "$1").replace(/[ \t]{2,}/g, " ").trim();
  if (!cited.length) return { text, references: [] };

  const references = cited.map(({ id, source }, index) => {
    const title = String(
      source?.title || source?.asset?.title || `Nguồn ${index + 1}`
    )
      .replace(/[\[\]]/g, "")
      .trim();
    const url = String(
      source?.asset?.url || source?.url || source?.metadata?.url || ""
    ).trim();
    return { id, number: index + 1, title, url };
  });
  const lines = references.map((ref) =>
    ref.url
      ? `[${ref.number}] [${ref.title}](${ref.url})`
      : `[${ref.number}] ${ref.title}`
  );
  return {
    text: `${text}\n\nNguồn tham khảo:\n${lines.join("\n")}`.trim(),
    references,
  };
}

/** Minimum chars to treat Open Notebook / cache body as usable for grounding. */
export const NOTEBOOK_BODY_MIN_CHARS = 400;

/**
 * Merge Redis crawl-cache / resolve bodies into chat context.
 * Prefer Transform-cleaned cache over dirty Open Notebook full_text.
 */
export function mergeCachedBodiesIntoContext(context, bodyItems = []) {
  const base =
    context && typeof context === "object"
      ? context
      : { sources: [], notes: [] };
  const byId = new Map();
  const byUrl = new Map();
  for (const item of Array.isArray(bodyItems) ? bodyItems : []) {
    if (!item?.ok || !item.text) continue;
    const text = String(item.text || "").trim();
    if (text.length < NOTEBOOK_BODY_MIN_CHARS) continue;
    const sid = normalizeNotebookSourceId(item.source_id || item.id || "");
    if (sid) byId.set(sid, item);
    const url = String(item.url || "").trim();
    if (url) byUrl.set(url, item);
  }
  if (!byId.size && !byUrl.size) return base;
  const sources = (Array.isArray(base.sources) ? base.sources : []).map((s) => {
    if (!s || typeof s !== "object") return s;
    const sid = normalizeNotebookSourceId(s.id);
    const url = String(s?.asset?.url || s?.url || "").trim();
    const hit = (sid && byId.get(sid)) || (url && byUrl.get(url));
    if (!hit) return s;
    const hitText = String(hit.text || "").trim();
    // Prefer cleaned resolve (cache/stored/crawl) — same body as Transformation.
    return {
      ...s,
      full_text: hitText,
      _body_from_cache: !!hit.cache_hit,
      _body_crawled: !!hit.crawled,
      _body_backend: String(hit.backend || ""),
    };
  });
  return { ...base, sources };
}

/**
 * Split try-order into: race top-2 non-Ollama cloud, then sequential remainder
 * (Ollama last so we do not load local weights when cloud wins quickly).
 * Fast path races only the first healthy cloud model (no parallel double-check).
 * Prefer distinct providers in the race window (avoids double-hitting one pool).
 */
export function splitChatRaceChain(chainIds, byId = {}, { fast = false } = {}) {
  const ids = Array.isArray(chainIds) ? chainIds.filter(Boolean) : [];
  const cloud = [];
  const local = [];
  for (const id of ids) {
    const p = notebookProviderOfModel(byId[id]);
    if (p === "ollama") local.push(id);
    else cloud.push(id);
  }
  if (fast) {
    return {
      raceIds: cloud.slice(0, 1),
      restIds: [...cloud.slice(1), ...local],
      cloudIds: cloud,
      localIds: local,
    };
  }
  // Prefer two different providers when available (e.g. openrouter + groq).
  const raceIds = [];
  const seenProviders = new Set();
  for (const id of cloud) {
    if (raceIds.length >= 2) break;
    const p = notebookProviderOfModel(byId[id]) || id;
    if (seenProviders.has(p) && raceIds.length === 1) continue;
    seenProviders.add(p);
    raceIds.push(id);
  }
  if (raceIds.length < 2 && cloud.length > raceIds.length) {
    for (const id of cloud) {
      if (raceIds.length >= 2) break;
      if (!raceIds.includes(id)) raceIds.push(id);
    }
  }
  const raced = new Set(raceIds);
  const restIds = [...cloud.filter((id) => !raced.has(id)), ...local];
  return { raceIds, restIds, cloudIds: cloud, localIds: local };
}

/** Alias — same race split for Transformation execute. */
export const splitTransformRaceChain = splitChatRaceChain;

/** Extra system hint when chatting against an explicit source selection. */
export function notebookScopedStyleHint(sources, selectedIds = []) {
  const selectedList = (Array.isArray(selectedIds) ? selectedIds : []).filter(
    Boolean
  );
  if (!selectedList.length) return "";
  const byId = new Map(
    (sources || [])
      .filter((s) => s?.id)
      .map((s) => [normalizeNotebookSourceId(s.id), s])
  );
  const labels = selectedList.map((id) => {
    const nid = normalizeNotebookSourceId(id);
    const s = byId.get(nid);
    const title = (s?.title || "").trim();
    return title ? `${nid} «${title}»` : nid;
  });
  return (
    `[Phạm vi BẮT BUỘC: chỉ ${selectedList.length} nguồn đã chọn — ${labels.join("; ")}. ` +
    `Không tóm tắt / không trích dẫn / không nhắc nguồn ngoài danh sách này.]\n\n`
  );
}

export const notebookApi = {
  listNotebooks: (archived = false) =>
    nbFetch(
      `/notebooks?archived=${archived ? "true" : "false"}&order_by=updated%20desc`
    ),

  createNotebook: ({ name, description = "" }) =>
    nbFetch("/notebooks", {
      method: "POST",
      body: { name, description },
    }),

  getNotebook: (id) => nbFetch(`/notebooks/${encodeURIComponent(id)}`),

  /**
   * Delete a notebook (Open Notebook cascade).
   * deleteExclusiveSources: also remove sources that only belong to this notebook.
   */
  deleteNotebook: (id, { deleteExclusiveSources = true } = {}) => {
    const q = new URLSearchParams({
      delete_exclusive_sources: deleteExclusiveSources ? "true" : "false",
    });
    return nbFetch(`/notebooks/${encodeURIComponent(id)}?${q}`, {
      method: "DELETE",
    });
  },

  listSources: ({ notebookId, limit = 50, offset = 0 } = {}) => {
    const q = new URLSearchParams({
      limit: String(limit),
      offset: String(offset),
      sort_by: "updated",
      sort_order: "desc",
    });
    if (notebookId) q.set("notebook_id", notebookId);
    return nbFetch(`/sources?${q}`);
  },

  getSource: (id, { signal } = {}) =>
    nbFetch(`/sources/${encodeURIComponent(id)}`, { signal }),

  /** Source insights (short summaries) — preferred for Vietnamese UI preview. */
  listSourceInsights: (id) =>
    nbFetch(`/sources/${encodeURIComponent(id)}/insights`),

  /** Add link or text source (JSON). Always pass notebooks: [id] for the active notebook. */
  createSourceJson: ({
    type,
    notebooks,
    url,
    content,
    title,
    embed = true,
    async_processing = true,
  }) =>
    nbFetch("/sources/json", {
      method: "POST",
      body: {
        type,
        notebooks: notebooks || [],
        ...(url ? { url } : {}),
        ...(content != null ? { content } : {}),
        ...(title ? { title } : {}),
        embed,
        async_processing,
      },
    }),

  /** Upload a document into a notebook (multipart). */
  createSourceUpload: async ({ file, notebooks, title, embed = true }) => {
    const fd = new FormData();
    fd.append("type", "upload");
    fd.append("notebooks", JSON.stringify(notebooks || []));
    fd.append("embed", embed ? "true" : "false");
    fd.append("async_processing", "true");
    if (title) fd.append("title", title);
    fd.append("file", file);
    return nbFetch("/sources", { method: "POST", body: fd });
  },

  // --- Notebook-level chat (whole notebook or source-scoped via context_config) ---

  listChatSessions: (notebookId) =>
    nbFetch(
      `/chat/sessions?notebook_id=${encodeURIComponent(notebookId)}`
    ),

  createChatSession: (
    { notebook_id, title, model_override } = {},
    { signal } = {}
  ) =>
    nbFetch("/chat/sessions", {
      method: "POST",
      body: {
        notebook_id,
        ...(title ? { title } : {}),
        ...(model_override ? { model_override } : {}),
      },
      signal,
    }),

  getChatSession: (sessionId) =>
    nbFetch(`/chat/sessions/${encodeURIComponent(sessionId)}`),

  buildChatContext: ({ notebook_id, context_config }, { signal } = {}) =>
    nbFetch("/chat/context", {
      method: "POST",
      body: { notebook_id, context_config },
      signal,
    }),

  /**
   * Execute notebook chat (synchronous; returns full message list).
   * No SSE on this route — SPA races fast providers + shrinks context instead.
   * Pass context from buildChatContext; omit restrictive filters for whole-notebook.
   */
  executeChat: (
    { session_id, message, context, model_override },
    { signal } = {}
  ) =>
    nbFetch("/chat/execute", {
      method: "POST",
      body: {
        session_id,
        message,
        context,
        ...(model_override ? { model_override } : {}),
      },
      signal,
    }),

  // --- Legacy single-source chat (SSE) — kept for compatibility ---

  listSourceChatSessions: (sourceId) =>
    nbFetch(`/sources/${encodeURIComponent(sourceId)}/chat/sessions`),

  createSourceChatSession: (sourceId, { title, model_override } = {}) =>
    nbFetch(`/sources/${encodeURIComponent(sourceId)}/chat/sessions`, {
      method: "POST",
      body: {
        source_id: sourceId,
        ...(title ? { title } : {}),
        ...(model_override ? { model_override } : {}),
      },
    }),

  getSourceChatSession: (sourceId, sessionId) =>
    nbFetch(
      `/sources/${encodeURIComponent(sourceId)}/chat/sessions/${encodeURIComponent(sessionId)}`
    ),

  /**
   * Send chat message; yields SSE event objects.
   * @param {(evt: object) => void} onEvent
   */
  async sendSourceChatMessage(
    sourceId,
    sessionId,
    message,
    { model_override, signal, onEvent } = {}
  ) {
    const url = `${NB_BASE}/sources/${encodeURIComponent(sourceId)}/chat/sessions/${encodeURIComponent(sessionId)}/messages`;
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
      body: JSON.stringify({
        message,
        ...(model_override ? { model_override } : {}),
      }),
      signal,
    });
    if (!res.ok) {
      const text = await res.text();
      throw new NotebookApiError(text || `Chat failed (${res.status})`, res.status);
    }
    const reader = res.body?.getReader();
    if (!reader) {
      throw new NotebookApiError("No stream body from chat API");
    }
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const chunks = buffer.split("\n");
      buffer = chunks.pop() || "";
      for (const line of chunks) {
        const trimmed = line.trim();
        if (!trimmed.startsWith("data:")) continue;
        const raw = trimmed.slice(5).trim();
        if (!raw || raw === "[DONE]") continue;
        try {
          const evt = JSON.parse(raw);
          onEvent?.(evt);
        } catch {
          // ignore malformed SSE lines
        }
      }
    }
  },

  listTransformations: () => nbFetch("/transformations"),

  getDefaultTransformationPrompt: () =>
    nbFetch("/transformations/default-prompt"),

  setDefaultTransformationPrompt: (transformation_instructions) =>
    nbFetch("/transformations/default-prompt", {
      method: "PUT",
      body: { transformation_instructions },
    }),

  updateTransformation: (id, patch) =>
    nbFetch(`/transformations/${encodeURIComponent(id)}`, {
      method: "PUT",
      body: patch,
    }),

  createTransformation: (payload) =>
    nbFetch("/transformations", { method: "POST", body: payload }),

  executeTransformation: ({
    transformation_id,
    input_text,
    model_id,
    max_tokens,
    signal,
  }) =>
    nbFetch("/transformations/execute", {
      method: "POST",
      body: {
        transformation_id,
        input_text,
        ...(model_id ? { model_id } : {}),
        ...(max_tokens != null && Number(max_tokens) > 0
          ? { max_tokens: Math.floor(Number(max_tokens)) }
          : {}),
      },
      signal,
    }),

  listModels: () => nbFetch("/models"),

  getDefaults: () => nbFetch("/models/defaults"),

  setDefaults: (defaults) =>
    nbFetch("/models/defaults", { method: "PUT", body: defaults }),
};

/**
 * NewsCrawler backend helpers for Notebook chat (auth via SPA token).
 * Polish uses Groq notebook pool only when quality is poor.
 * Healthy-models router prefers idle providers (Cerebras → OR → Groq → Ollama).
 */
export const notebookCloudApi = {
  /**
   * Fast social/chitchat reply (Groq GPT-OSS 20B) — no crawl, no notebook grounding.
   */
  socialChitchat: ({ message = "", question = "" } = {}, { signal } = {}) =>
    apiRequest("/api/v1/ai/notebook-chat/chitchat/", {
      method: "POST",
      body: { message: message || question, question: question || message },
      signal,
    }),

  polishAnswer: ({ question, draft, force = false }, { signal } = {}) =>
    apiRequest("/api/v1/ai/notebook-chat/polish/", {
      method: "POST",
      body: { question, draft, force: !!force },
      signal,
    }),

  unloadOllama: ({ signal } = {}) =>
    apiRequest("/api/v1/ai/notebook-chat/unload-ollama/", {
      method: "POST",
      body: {},
      signal,
    }),

  listHealthyModels: ({ purpose = "chat" } = {}, { signal } = {}) => {
    const q = new URLSearchParams({ purpose: String(purpose || "chat") });
    return apiRequest(`/api/v1/ai/notebook-chat/healthy-models/?${q}`, {
      method: "GET",
      signal,
    });
  },

  markProvider: (
    {
      provider,
      reason = "",
      seconds,
      success = false,
      latency_ms,
    } = {},
    { signal } = {}
  ) =>
    apiRequest("/api/v1/ai/notebook-chat/mark-provider/", {
      method: "POST",
      body: {
        provider,
        reason,
        success: !!success,
        ...(seconds != null ? { seconds } : {}),
        ...(latency_ms != null ? { latency_ms } : {}),
      },
      signal,
    }),

  recordChatMetrics: (
    {
      mode = "grounded",
      total_ms = 0,
      context_ms = 0,
      attempts = 0,
      source_count = 0,
      citation_status = "",
      citation_coverage = 0,
    } = {},
    { signal } = {}
  ) =>
    apiRequest("/api/v1/ai/notebook-chat/metrics/", {
      method: "POST",
      body: {
        mode,
        total_ms,
        context_ms,
        attempts,
        source_count,
        citation_status,
        citation_coverage,
      },
      signal,
    }),

  getChatMetrics: ({ signal } = {}) =>
    apiRequest("/api/v1/ai/notebook-chat/metrics/", {
      method: "GET",
      signal,
    }),

  /**
   * Fast digest: cache-first body (Redis TTL ~3h) or crawl + cloud summarize + VI.
   * Used for «nội dung chính» / summarize with 1–2 scoped sources.
   */
  articleDigest: (
    {
      url = "",
      title = "",
      body = "",
      question = "",
      allow_ollama = true,
      source_id = "",
      notebook_id = "",
      refresh = false,
    } = {},
    { signal } = {}
  ) =>
    apiRequest("/api/v1/ai/notebook-chat/article-digest/", {
      method: "POST",
      body: {
        url,
        title,
        body,
        question,
        allow_ollama: !!allow_ollama,
        source_id,
        notebook_id,
        refresh: !!refresh,
      },
      signal,
    }),

  /**
   * Resolve plain-text bodies from Redis crawl cache (optional crawl on miss).
   * Used to ground scoped/whole chat without re-crawling every question.
   */
  resolveArticleBodies: (
    {
      items = [],
      crawl_on_miss = false,
      refresh = false,
    } = {},
    { signal } = {}
  ) =>
    apiRequest("/api/v1/ai/notebook-chat/article-bodies/", {
      method: "POST",
      body: {
        items,
        crawl_on_miss: !!crawl_on_miss,
        refresh: !!refresh,
      },
      signal,
    }),
};
