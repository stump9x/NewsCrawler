import { useCallback, useEffect, useMemo, useState } from "react";
import { Alert, Box, Button, Chip, Link, Stack, Typography } from "@mui/material";
import TravelExploreOutlinedIcon from "@mui/icons-material/TravelExploreOutlined";
import AccessTimeOutlinedIcon from "@mui/icons-material/AccessTimeOutlined";
import WhatshotOutlinedIcon from "@mui/icons-material/WhatshotOutlined";
import PublicOutlinedIcon from "@mui/icons-material/PublicOutlined";
import { PageHeader } from "../components/PageHeader";
import { displayWireTitle } from "../utils/wireTitle";

const NEWSNOW_BASE_URL = "https://newsnow.busiyi.world";
const NEWSNOW_SOURCE_IDS = ["cls-hot", "weibo", "zhihu", "bilibili", "hupu", "v2ex"];
const TREND_SCOPE_RE = new RegExp([
  "biển\\s*đông|south\\s*china\\s*sea|南海|黄岩岛|仁爱礁|西沙|南沙",
  "trung\\s*quốc|china|中国|philippines|菲律宾|đài\\s*loan|taiwan|台湾",
  "nhật\\s*bản|japan|日本|mỹ|hoa\\s*kỳ|united\\s*states|美国|guam",
  "indonesia|indonesian|印尼|malaysia|马来西亚|singapore|新加坡|campuchia|cambodia|柬埔寨",
  "lào|laos|老挝|thái\\s*lan|thailand|泰国|úc|australia|澳大利亚|hàn\\s*quốc|korea|韩国|triều\\s*tiên|朝鲜",
  "quốc\\s*phòng|quân\\s*sự|hải\\s*quân|tuần\\s*tra|diễn\\s*tập|tập\\s*trận|an\\s*ninh|国防|军事|海军|军演|演习|安全",
  "xuất\\s*khẩu|trừng\\s*phạt|đối\\s*phó|công\\s*nghệ\\s*lưỡng\\s*dụng|6g|trí\\s*tuệ\\s*nhân\\s*tạo|\\bai\\b|出口管制|制裁|人工智能",
].join("|"), "iu");
const SOURCE_LABELS = {
  "newsnow:cls-hot": "NewsNow · Tài chính",
  "newsnow:weibo": "NewsNow · Weibo",
  "newsnow:zhihu": "NewsNow · Zhihu",
  "newsnow:bilibili": "NewsNow · Bilibili",
  "newsnow:hupu": "NewsNow · Hupu",
  "newsnow:v2ex": "NewsNow · V2EX",
};
const SOURCE_TONES = {
  "newsnow:cls-hot": { accent: "#8fc7ff", surface: "rgba(143,199,255,.14)", border: "rgba(143,199,255,.34)" },
  "newsnow:weibo": { accent: "#ff7d91", surface: "rgba(255,125,145,.14)", border: "rgba(255,125,145,.34)" },
  "newsnow:zhihu": { accent: "#53b7ff", surface: "rgba(83,183,255,.14)", border: "rgba(83,183,255,.34)" },
  "newsnow:bilibili": { accent: "#f58dcb", surface: "rgba(245,141,203,.14)", border: "rgba(245,141,203,.34)" },
  "newsnow:hupu": { accent: "#ffb454", surface: "rgba(255,180,84,.14)", border: "rgba(255,180,84,.34)" },
  "newsnow:v2ex": { accent: "#53d49a", surface: "rgba(83,212,154,.14)", border: "rgba(83,212,154,.34)" },
};
const TREND_PROVIDER_LINKS = [
  { name: "NewsNow", description: "Bảng xếp hạng đa nền tảng, đã lọc theo phạm vi NewsCrawler", url: "https://newsnow.busiyi.world", accent: "#8fc7ff" },
  { name: "SoPilot · X", description: "Bài đăng nổi bật và thảo luận đang lan truyền trên X", url: "https://sopilot.net/zh/hot-tweets", accent: "#53d49a" },
  { name: "REBANG", description: "Bảng xếp hạng thịnh hành từ nhiều nền tảng", url: "https://rebang.open2hub.com", accent: "#ffb454" },
];

function sourceLabel(source) { return SOURCE_LABELS[source] || source || "Nguồn khác"; }
function sourceTone(source) { return SOURCE_TONES[source] || { accent: "#b49bff", surface: "rgba(180,155,255,.14)", border: "rgba(180,155,255,.34)" }; }
function compactCount(value) {
  const number = Number(value);
  if (!Number.isFinite(number) || number <= 0) return "";
  if (number >= 1000000) return `${(number / 1000000).toFixed(1).replace(/\.0$/, "")}tr`;
  if (number >= 1000) return `${(number / 1000).toFixed(1).replace(/\.0$/, "")}k`;
  return String(Math.round(number));
}
function engagementLabel(finding) {
  const engagement = finding?.engagement && typeof finding.engagement === "object" ? finding.engagement : {};
  const parts = [];
  const points = compactCount(engagement.points ?? engagement.upvotes ?? engagement.score);
  const likes = compactCount(engagement.likes ?? engagement.favorites);
  const comments = compactCount(engagement.comments ?? engagement.num_comments);
  if (points) parts.push(`${points} điểm`);
  if (likes) parts.push(`${likes} thích`);
  if (comments) parts.push(`${comments} bình luận`);
  return parts.join(" · ");
}
function normalizeExternalFinding(item, sourceId, index) {
  if (!item || typeof item !== "object") return null;
  const extra = item.extra && typeof item.extra === "object" ? item.extra : {};
  const title = String(item.title || item.name || item.text || "").trim();
  if (!title || !TREND_SCOPE_RE.test(title)) return null;
  const translated = String(item.title_vi || item.titleVi || "").trim();
  return {
    id: `newsnow-${sourceId}-${item.id || index}`,
    source: `newsnow:${sourceId}`,
    title,
    title_vi: translated,
    title_vi_status: translated ? "ok" : "skipped",
    url: String(item.url || item.mobileUrl || item.link || "").trim(),
    host: "newsnow.busiyi.world",
    snippet: String(item.description || item.content || "").slice(0, 500),
    published_at: item.pubDate || extra.date || item.date || null,
    score: Number(item.score || item.hot || extra.hot || extra.score || 0) || 0,
    engagement: extra,
  };
}
async function fetchNewsNowSource(sourceId) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 8000);
  try {
    const response = await fetch(`${NEWSNOW_BASE_URL}/api/s?id=${encodeURIComponent(sourceId)}`, { headers: { Accept: "application/json" }, signal: controller.signal });
    if (!response.ok) throw new Error(`NewsNow ${response.status}`);
    const payload = await response.json();
    const items = Array.isArray(payload) ? payload : payload?.items || payload?.data?.items || payload?.data;
    if (!Array.isArray(items)) return [];
    return items.slice(0, 30).map((item, index) => normalizeExternalFinding(item, sourceId, index)).filter(Boolean);
  } finally {
    clearTimeout(timeout);
  }
}
function sortFindings(rows, sort) {
  return [...rows].sort((a, b) => sort === "latest"
    ? new Date(b.published_at || 0).getTime() - new Date(a.published_at || 0).getTime()
    : Number(b.score || 0) - Number(a.score || 0));
}

export default function TrendPage() {
  const [findings, setFindings] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [sourceFilter, setSourceFilter] = useState("all");
  const [trendSort, setTrendSort] = useState("hot");
  const refresh = useCallback(async () => {
    setLoading(true);
    setError("");
    const settled = await Promise.allSettled(NEWSNOW_SOURCE_IDS.map(fetchNewsNowSource));
    const merged = [];
    const seen = new Set();
    for (const result of settled) {
      if (result.status !== "fulfilled") continue;
      for (const item of result.value) {
        const key = item.url || item.title.toLocaleLowerCase();
        if (seen.has(key)) continue;
        seen.add(key);
        merged.push(item);
      }
    }
    setFindings(merged);
    if (!merged.length && settled.every((result) => result.status === "rejected")) setError("Không kết nối được các nguồn xu hướng. Bạn vẫn có thể mở bảng gốc bên dưới.");
    setLoading(false);
  }, []);
  useEffect(() => { refresh(); }, [refresh]);
  const findingSourceCounts = useMemo(() => findings.reduce((counts, finding) => ({ ...counts, [finding.source]: (counts[finding.source] || 0) + 1 }), {}), [findings]);
  const visibleFindings = useMemo(() => sourceFilter === "all" ? findings : findings.filter((finding) => finding.source === sourceFilter), [findings, sourceFilter]);
  const trendHighlights = useMemo(() => {
    const grouped = new Map();
    for (const finding of sortFindings(visibleFindings, trendSort)) {
      const sourceRows = grouped.get(finding.source) || [];
      if (sourceRows.length < 8) sourceRows.push(finding);
      grouped.set(finding.source, sourceRows);
    }
    return [...grouped.entries()];
  }, [trendSort, visibleFindings]);
  return (
    <Stack spacing={2}>
      <PageHeader title="Xu hướng" subtitle="NewsNow · SoPilot · REBANG · lọc theo phạm vi NewsCrawler · tiếng Việt" action={<Button variant="outlined" onClick={refresh} disabled={loading} startIcon={<TravelExploreOutlinedIcon />}>Làm mới</Button>} />
      {error ? <Alert severity="warning">{error}</Alert> : null}
      {loading ? <Box sx={{ p: 2.5, borderRadius: 2, border: "1px dashed rgba(143,199,255,.24)", color: "text.secondary", textAlign: "center" }}>Đang tải bảng xu hướng…</Box> : null}
      {!loading && !trendHighlights.length ? <Alert severity="info">Chưa có mục phù hợp từ các nguồn đã chọn. Bảng nguồn vẫn sẵn sàng bên dưới.</Alert> : null}
      {!loading && findings.length ? (
        <Box sx={{ pt: 0.5 }}>
          <Stack direction="row" spacing={0.75} flexWrap="wrap" useFlexGap sx={{ mb: 1.5 }}>
            <Chip size="small" label={`Tất cả (${visibleFindings.length})`} color={sourceFilter === "all" ? "primary" : "default"} variant={sourceFilter === "all" ? "filled" : "outlined"} onClick={() => setSourceFilter("all")} />
            {Object.entries(findingSourceCounts).map(([source, count]) => <Chip key={source} size="small" label={`${sourceLabel(source)} (${count})`} color={sourceFilter === source ? "primary" : "default"} variant={sourceFilter === source ? "filled" : "outlined"} onClick={() => setSourceFilter(source)} />)}
          </Stack>
          {trendHighlights.length ? (
            <Box sx={{ mb: 2, p: { xs: 1.25, md: 2 }, borderRadius: 2.5, border: "1px solid rgba(143,199,255,.16)", background: "linear-gradient(145deg, rgba(22,36,59,.96), rgba(8,17,31,.96))", boxShadow: "0 18px 44px rgba(0,0,0,.2)" }}>
              <Stack direction={{ xs: "column", sm: "row" }} alignItems={{ sm: "center" }} justifyContent="space-between" spacing={1} sx={{ mb: 1.5 }}>
                <Stack direction="row" spacing={1} alignItems="center"><Box sx={{ width: 34, height: 34, display: "grid", placeItems: "center", borderRadius: 1.25, color: "#8fc7ff", background: "rgba(76,154,255,.16)" }}><PublicOutlinedIcon fontSize="small" /></Box><Box><Typography variant="h6" sx={{ lineHeight: 1.1 }}>Xu hướng đa nền tảng</Typography><Typography variant="caption" color="text.secondary">{trendSort === "hot" ? "Xếp hạng nóng nhất" : "Tin mới nhất"} · tối đa 8 mục mỗi nguồn</Typography></Box></Stack>
                <Stack direction="row" sx={{ p: 0.35, borderRadius: 1.5, border: "1px solid rgba(143,199,255,.18)", background: "rgba(6,13,25,.58)" }}><Button size="small" startIcon={<WhatshotOutlinedIcon fontSize="small" />} onClick={() => setTrendSort("hot")} variant={trendSort === "hot" ? "contained" : "text"} sx={{ minWidth: 0, px: 1.2, borderRadius: 1.1 }}>Nóng nhất</Button><Button size="small" startIcon={<AccessTimeOutlinedIcon fontSize="small" />} onClick={() => setTrendSort("latest")} variant={trendSort === "latest" ? "contained" : "text"} sx={{ minWidth: 0, px: 1.2, borderRadius: 1.1 }}>Mới nhất</Button></Stack>
              </Stack>
              <Box sx={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(290px, 1fr))", gap: 1.25, alignItems: "stretch" }}>
                {trendHighlights.map(([source, sourceRows]) => { const tone = sourceTone(source); return <Box key={source} sx={{ minWidth: 0, display: "flex", flexDirection: "column", border: `1px solid ${tone.border}`, borderRadius: 2, overflow: "hidden", background: "rgba(9,19,34,.88)" }}><Stack direction="row" alignItems="center" justifyContent="space-between" sx={{ px: 1.35, py: 1, background: tone.surface }}><Stack direction="row" spacing={0.9} alignItems="center" minWidth={0}><Box sx={{ width: 29, height: 29, display: "grid", placeItems: "center", borderRadius: 1, color: tone.accent, border: `1px solid ${tone.border}`, fontWeight: 800, fontSize: 12 }}>{sourceLabel(source).slice(0, 2).toUpperCase()}</Box><Typography noWrap fontWeight={700} sx={{ color: tone.accent }}>{sourceLabel(source)}</Typography></Stack><Typography variant="caption" color="text.secondary">{sourceRows.length} mục</Typography></Stack><Stack spacing={0} sx={{ p: 0.8, flex: 1 }}>{sourceRows.map((finding, index) => { const content = <Stack direction="row" spacing={1} alignItems="flex-start" sx={{ p: 0.75, borderRadius: 1.25, transition: "background .15s ease", "&:hover": { background: "rgba(255,255,255,.055)" } }}><Box sx={{ flex: "0 0 25px", width: 25, height: 25, display: "grid", placeItems: "center", borderRadius: 0.8, color: index === 0 ? tone.accent : "text.secondary", background: index === 0 ? tone.surface : "rgba(255,255,255,.06)", fontSize: 12, fontWeight: 700 }}>{index + 1}</Box><Box sx={{ minWidth: 0, flex: 1 }}><Typography variant="body2" sx={{ fontWeight: 600, lineHeight: 1.45, display: "-webkit-box", WebkitLineClamp: 3, WebkitBoxOrient: "vertical", overflow: "hidden" }}>{displayWireTitle(finding)}</Typography><Typography variant="caption" color="text.secondary" noWrap>Điểm {Number(finding.score || 0).toFixed(0)}{engagementLabel(finding) ? ` · ${engagementLabel(finding)}` : ""}</Typography></Box></Stack>; return finding.url ? <Link key={finding.id} href={finding.url} target="_blank" rel="noreferrer" underline="none" sx={{ color: "inherit" }}>{content}</Link> : <Box key={finding.id}>{content}</Box>; })}</Stack></Box>; })}
              </Box>
            </Box>
          ) : null}
        </Box>
      ) : null}
      <Box sx={{ p: { xs: 1.25, md: 2 }, borderRadius: 2.5, border: "1px solid rgba(143,199,255,.16)", background: "rgba(9,19,34,.72)" }}><Typography variant="subtitle1" fontWeight={700} sx={{ mb: 1 }}>Nguồn xu hướng</Typography><Box sx={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: 1 }}>{TREND_PROVIDER_LINKS.map((provider) => <Link key={provider.name} href={provider.url} target="_blank" rel="noreferrer" underline="none" sx={{ p: 1.25, borderRadius: 1.5, border: `1px solid ${provider.accent}55`, background: `${provider.accent}12`, color: "inherit", transition: "background .15s ease", "&:hover": { background: `${provider.accent}22` } }}><Typography fontWeight={700} sx={{ color: provider.accent }}>{provider.name}</Typography><Typography variant="caption" color="text.secondary">{provider.description}</Typography></Link>)}</Box></Box>
    </Stack>
  );
}
