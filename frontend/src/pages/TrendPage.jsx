import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Alert,
  Box,
  Button,
  Chip,
  Link,
  Stack,
  Typography,
} from "@mui/material";
import TravelExploreOutlinedIcon from "@mui/icons-material/TravelExploreOutlined";
import AccessTimeOutlinedIcon from "@mui/icons-material/AccessTimeOutlined";
import WhatshotOutlinedIcon from "@mui/icons-material/WhatshotOutlined";
import PublicOutlinedIcon from "@mui/icons-material/PublicOutlined";
import { api } from "../api/client";
import { PageHeader } from "../components/PageHeader";
import { displayWireTitle } from "../utils/wireTitle";

const LIST_POLL_MS = 2000;
const FINDINGS_POLL_MS = 1500;
const DEFAULT_TREND_TOPIC = "__newscrawler_scope__";
const TREND_API_PREFIX = "/api/v1/trend/researches";
const NEWSNOW_BASE_URL = "https://newsnow.busiyi.world";
const NEWSNOW_SOURCE_IDS = ["cls-hot", "weibo", "zhihu", "bilibili", "hupu", "v2ex"];
const TREND_SCOPE_RE = new RegExp(
  [
    "biển\\s*đông|south\\s*china\\s*sea|南海|黄岩岛|仁爱礁|西沙|南沙",
    "trung\\s*quốc|china|中国|philippines|菲律宾|đài\\s*loan|taiwan|台湾",
    "nhật\\s*bản|japan|日本|mỹ|hoa\\s*kỳ|united\\s*states|美国|guam",
    "indonesia|indonesian|印尼|malaysia|马来西亚|singapore|新加坡|campuchia|cambodia|柬埔寨",
    "lào|laos|老挝|thái\\s*lan|thailand|泰国|úc|australia|澳大利亚|hàn\\s*quốc|korea|韩国|triều\\s*tiên|朝鲜",
    "quốc\\s*phòng|quân\\s*sự|hải\\s*quân|tuần\\s*tra|diễn\\s*tập|tập\\s*trận|an\\s*ninh|国防|军事|海军|军演|演习|安全",
    "xuất\\s*khẩu|trừng\\s*phạt|đối\\s*phó|công\\s*nghệ\\s*lưỡng\\s*dụng|6g|trí\\s*tuệ\\s*nhân\\s*tạo|\\bai\\b|出口管制|制裁|人工智能",
  ].join("|"),
  "iu"
);
const SOURCE_LABELS = {
  reddit: "Reddit",
  x: "X",
  polymarket: "Polymarket",
  web: "Web chọn lọc",
  hackernews: "Hacker News",
};
const TREND_PROVIDER_LINKS = [
  {
    name: "NewsNow",
    description: "Bảng xếp hạng đa nền tảng · đã lọc theo phạm vi NewsCrawler",
    url: "https://newsnow.busiyi.world",
    accent: "#8fc7ff",
  },
  {
    name: "SoPilot · X",
    description: "Bài đăng nổi bật và thảo luận đang lan truyền trên X",
    url: "https://sopilot.net/zh/hot-tweets",
    accent: "#53d49a",
  },
  {
    name: "REBANG",
    description: "Bảng xếp hạng thịnh hành từ nhiều nền tảng",
    url: "https://rebang.open2hub.com",
    accent: "#ffb454",
  },
];

// Tone and icon are deliberately source-specific so the board is scannable at a glance,
// while remaining readable in the existing dark application theme.
const SOURCE_TONES = {
  reddit: { accent: "#f97352", surface: "rgba(249,115,82,.14)", border: "rgba(249,115,82,.34)" },
  x: { accent: "#8fc7ff", surface: "rgba(143,199,255,.14)", border: "rgba(143,199,255,.34)" },
  polymarket: { accent: "#53d49a", surface: "rgba(83,212,154,.14)", border: "rgba(83,212,154,.34)" },
  hackernews: { accent: "#ffb454", surface: "rgba(255,180,84,.14)", border: "rgba(255,180,84,.34)" },
  web: { accent: "#b49bff", surface: "rgba(180,155,255,.14)", border: "rgba(180,155,255,.34)" },
};

function sourceLabel(source) {
  const key = String(source || "web").toLowerCase();
  if (key.startsWith("newsnow:")) {
    const newsnowId = key.slice("newsnow:".length);
    const labels = {
      cls: "NewsNow · Tài chính",
      "cls-hot": "NewsNow · Tài chính",
      weibo: "NewsNow · Weibo",
      zhihu: "NewsNow · Zhihu",
      bilibili: "NewsNow · Bilibili",
      hupu: "NewsNow · Hupu",
      v2ex: "NewsNow · V2EX",
    };
    return labels[newsnowId] || `NewsNow · ${newsnowId}`;
  }
  return SOURCE_LABELS[key] || key;
}

function sourceTone(source) {
  const key = String(source || "web").toLowerCase();
  return SOURCE_TONES[key.startsWith("newsnow:") ? "web" : key] || SOURCE_TONES.web;
}

function compactCount(value) {
  const number = Number(value);
  if (!Number.isFinite(number) || number <= 0) return "";
  if (number >= 1000000) return `${(number / 1000000).toFixed(1).replace(/\.0$/, "")}tr`;
  if (number >= 1000) return `${(number / 1000).toFixed(1).replace(/\.0$/, "")}k`;
  return String(Math.round(number));
}

function engagementLabel(finding) {
  const engagement = finding?.engagement && typeof finding.engagement === "object"
    ? finding.engagement
    : {};
  const parts = [];
  const comments = compactCount(engagement.comments ?? engagement.num_comments);
  const likes = compactCount(engagement.likes ?? engagement.favorites);
  const points = compactCount(engagement.points ?? engagement.upvotes ?? engagement.score);
  if (points) parts.push(`${points} điểm`);
  if (likes) parts.push(`${likes} thích`);
  if (comments) parts.push(`${comments} bình luận`);
  return parts.join(" · ");
}

function trendRequest(method, suffix = "", body) {
  const path = `${TREND_API_PREFIX}${suffix}`;
  return method === "post" ? api.post(path, body) : api.get(path);
}

function normalizeExternalFinding(item, sourceId, index) {
  if (!item || typeof item !== "object") return null;
  const extra = item.extra && typeof item.extra === "object" ? item.extra : {};
  const title = String(item.title || item.name || item.text || "").trim();
  if (!title || !TREND_SCOPE_RE.test(title)) return null;
  const url = String(item.url || item.mobileUrl || item.link || "").trim();
  return {
    id: `newsnow-${sourceId}-${item.id || index}`,
    source: `newsnow:${sourceId}`,
    title,
    title_vi: "",
    title_vi_status: "skipped",
    url,
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
    const response = await fetch(
      `${NEWSNOW_BASE_URL}/api/s?id=${encodeURIComponent(sourceId)}`,
      { headers: { Accept: "application/json" }, signal: controller.signal }
    );
    if (!response.ok) throw new Error(`NewsNow ${response.status}`);
    const payload = await response.json();
    const items = Array.isArray(payload) ? payload : payload?.items;
    if (!Array.isArray(items)) return [];
    return items
      .slice(0, 30)
      .map((item, index) => normalizeExternalFinding(item, sourceId, index))
      .filter(Boolean);
  } finally {
    clearTimeout(timeout);
  }
}

/** Drop findings whose published_at is older than lookback (FE safety net). */
function filterFindingsByLookback(rows, lookbackDays = 30) {
  const days = Math.max(1, Math.min(Number(lookbackDays) || 30, 90));
  const cutoff = Date.now() - days * 24 * 60 * 60 * 1000;
  return (Array.isArray(rows) ? rows : []).filter((row) => {
    if (!row?.published_at) return true;
    const ts = new Date(row.published_at).getTime();
    if (Number.isNaN(ts)) return true;
    return ts >= cutoff;
  });
}

function sortFindings(rows) {
  return [...rows].sort((a, b) => {
    const ta = a?.published_at ? new Date(a.published_at).getTime() : 0;
    const tb = b?.published_at ? new Date(b.published_at).getTime() : 0;
    const aOk = !Number.isNaN(ta) && ta > 0;
    const bOk = !Number.isNaN(tb) && tb > 0;
    if (aOk && bOk && tb !== ta) return tb - ta;
    return (b.id || 0) - (a.id || 0);
  });
}

export default function TrendPage() {
  const [rows, setRows] = useState([]);
  const [selected, setSelected] = useState(null);
  const [findings, setFindings] = useState([]);
  const [fallbackFindings, setFallbackFindings] = useState([]);
  const [fallbackLoading, setFallbackLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [configured, setConfigured] = useState(true);
  const [statusReady, setStatusReady] = useState(false);
  const [sourceFilter, setSourceFilter] = useState("all");
  const [trendSort, setTrendSort] = useState("hot");
  const lastFindingIdRef = useRef(0);

  const active = useMemo(
    () => rows.find((r) => r.status === "queued" || r.status === "running"),
    [rows]
  );

  const selectedLive = useMemo(() => {
    if (!selected?.id) return selected;
    return rows.find((r) => r.id === selected.id) || selected;
  }, [rows, selected]);

  const loadList = useCallback(async () => {
    const data = await trendRequest("get", "/?page_size=30");
    const results = Array.isArray(data) ? data : data?.results;
    const normalized = Array.isArray(results) ? results : [];
    setRows(normalized);
    return normalized;
  }, []);

  // A public-feed fallback keeps the board useful while the authenticated
  // research worker is starting, disabled, or temporarily unavailable.
  const loadExternalTrends = useCallback(async () => {
    setFallbackLoading(true);
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
    setFallbackFindings(sortFindings(merged));
    setFallbackLoading(false);
    return merged;
  }, []);

  const loadFindings = useCallback(async (id, { incremental = false, lookbackDays = 30 } = {}) => {
    if (!id) {
      setFindings([]);
      lastFindingIdRef.current = 0;
      return;
    }
    if (incremental && lastFindingIdRef.current > 0) {
      const data = await trendRequest(
        "get",
        `/${id}/findings/?page_size=100&after_id=${lastFindingIdRef.current}`
      );
      const batch = filterFindingsByLookback(data.results || data || [], lookbackDays);
      if (batch.length) {
        setFindings((prev) => {
          const seen = new Set(prev.map((f) => f.id));
          const merged = filterFindingsByLookback([...prev], lookbackDays);
          for (const row of batch) {
            if (!seen.has(row.id)) merged.push(row);
          }
          return sortFindings(merged);
        });
        lastFindingIdRef.current = Math.max(
          lastFindingIdRef.current,
          ...batch.map((f) => f.id)
        );
      }
      return;
    }
    const data = await trendRequest("get", `/${id}/findings/?page_size=100`);
    const all = sortFindings(
      filterFindingsByLookback(data.results || data || [], lookbackDays)
    );
    setFindings(all);
    lastFindingIdRef.current = all.reduce((m, f) => Math.max(m, f.id || 0), 0);
  }, []);

  const refresh = useCallback(async () => {
    setError("");
    const [statusResult, listResult, fallbackResult] = await Promise.allSettled([
      trendRequest("get", "/status/"),
      loadList(),
      loadExternalTrends(),
    ]);
    if (statusResult.status === "fulfilled") {
      setConfigured(Boolean(statusResult.value?.configured));
    } else {
      // Status is informational; keep the board usable if a proxy serves a
      // stale route while the research endpoints are healthy.
      setConfigured(true);
    }
    if (listResult.status === "rejected") {
      setError(listResult.reason?.message || "Không thể tải xu hướng");
    }
    if (fallbackResult.status === "rejected" && listResult.status === "rejected") {
      setError(fallbackResult.reason?.message || "Không thể tải xu hướng");
    }
    setStatusReady(true);
  }, [loadExternalTrends, loadList]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  useEffect(() => {
    if (!selected && rows.length) setSelected(rows[0]);
  }, [rows, selected]);

  // The board is intentionally search-free. Start one scoped sweep on first
  // visit (or upgrade an old topic run) so the cards always have data.
  useEffect(() => {
    const hasScopedRun = rows.some((row) => row.topic === DEFAULT_TREND_TOPIC);
    if (!statusReady || configured !== true || busy || active || hasScopedRun) return;
    setBusy(true);
    setError("");
    trendRequest("post", "/", {
      topic: DEFAULT_TREND_TOPIC,
      depth: "quick",
      lookback_days: 30,
    }).then((data) => {
      lastFindingIdRef.current = 0;
      setFindings([]);
      setSourceFilter("all");
      setSelected(data);
      return loadList();
    }).catch((err) => {
      setError(err.message || "Không thể tải xu hướng");
    }).finally(() => setBusy(false));
  }, [active, busy, configured, loadList, rows, statusReady]);

  useEffect(() => {
    if (!active) return undefined;
    const t = setInterval(() => {
      loadList().catch(() => {});
    }, LIST_POLL_MS);
    return () => clearInterval(t);
  }, [active, loadList]);

  const pendingTitleVi = useMemo(
    () =>
      findings.some(
        (f) =>
          f.title_vi_status === "pending" ||
          f.title_vi_status === "failed" ||
          (!f.title_vi && f.title_vi_status !== "skipped")
      ),
    [findings]
  );

  useEffect(() => {
    if (!selectedLive?.id) return undefined;
    const id = selectedLive.id;
    const running =
      selectedLive.status === "queued" || selectedLive.status === "running";

    // Full refresh so title_vi updates land after Celery translation.
    loadFindings(id, {
      incremental: false,
      lookbackDays: selectedLive.lookback_days || 30,
    }).catch(() => {});

    if (!running && !pendingTitleVi) return undefined;
    const t = setInterval(() => {
      loadList().catch(() => {});
      loadFindings(id, {
        incremental: false,
        lookbackDays: selectedLive.lookback_days || 30,
      }).catch(() => {});
    }, FINDINGS_POLL_MS);
    return () => clearInterval(t);
  }, [
    selectedLive?.id,
    selectedLive?.status,
    selectedLive?.lookback_days,
    pendingTitleVi,
    loadFindings,
    loadList,
  ]);

  const findingSourceCounts = useMemo(() => {
    const counts = {};
    const rowsForBoard = findings.length ? findings : fallbackFindings;
    for (const finding of rowsForBoard) {
      const source = String(finding.source || "web");
      counts[source] = (counts[source] || 0) + 1;
    }
    return counts;
  }, [fallbackFindings, findings]);
  const visibleFindings = useMemo(
    () => {
      const rowsForBoard = findings.length ? findings : fallbackFindings;
      return sourceFilter === "all"
        ? rowsForBoard
        : rowsForBoard.filter((finding) => String(finding.source || "web") === sourceFilter);
    },
    [fallbackFindings, findings, sourceFilter]
  );
  const trendHighlights = useMemo(() => {
    const grouped = new Map();
    const ranked = [...visibleFindings].sort(
      (a, b) => trendSort === "latest"
        ? (new Date(b.published_at || 0).getTime() - new Date(a.published_at || 0).getTime())
        : Number(b.score || 0) - Number(a.score || 0)
    );
    for (const finding of ranked) {
      const source = String(finding.source || "web");
      const rowsForSource = grouped.get(source) || [];
      if (rowsForSource.length < 8) rowsForSource.push(finding);
      grouped.set(source, rowsForSource);
    }
    return [...grouped.entries()];
  }, [visibleFindings, trendSort]);
  return (
    <Stack spacing={2}>
      <PageHeader
        title="Xu hướng"
        subtitle="Tổng hợp đa nền tảng · tự động lọc theo phạm vi NewsCrawler · tiếng Việt"
        action={
          <Button variant="outlined" onClick={refresh} startIcon={<TravelExploreOutlinedIcon />}>
            Làm mới
          </Button>
        }
      />
      {!configured ? (
        <Alert severity="warning">Module Xu hướng chưa sẵn sàng.</Alert>
      ) : null}
      {error ? <Alert severity="error">{error}</Alert> : null}

      <Box sx={{ pt: 0.5 }}>
          {!selectedLive && (busy || !statusReady || fallbackLoading) ? (
            <Box
              sx={{
                mb: 1.5,
                p: 2,
                borderRadius: 2,
                border: "1px dashed rgba(143,199,255,.24)",
                color: "text.secondary",
                textAlign: "center",
              }}
            >
              Đang tải bảng xu hướng…
            </Box>
          ) : null}

          {!selectedLive && !busy && !fallbackLoading && !fallbackFindings.length && statusReady ? (
            <Alert severity="info" sx={{ mb: 1.5 }}>
              Chưa có dữ liệu phù hợp. Bấm “Làm mới” để quét lại các nguồn xu hướng.
            </Alert>
          ) : null}

          {findings.length || fallbackFindings.length ? (
            <Stack direction="row" spacing={0.75} flexWrap="wrap" useFlexGap sx={{ mb: 1.5 }}>
              <Chip
                size="small"
                label={`Tất cả (${visibleFindings.length})`}
                color={sourceFilter === "all" ? "primary" : "default"}
                variant={sourceFilter === "all" ? "filled" : "outlined"}
                onClick={() => setSourceFilter("all")}
              />
              {Object.entries(findingSourceCounts).sort(([a], [b]) => a.localeCompare(b)).map(([source, count]) => (
                <Chip
                  key={source}
                  size="small"
                  label={`${sourceLabel(source)} (${count})`}
                  color={sourceFilter === source ? "primary" : "default"}
                  variant={sourceFilter === source ? "filled" : "outlined"}
                  onClick={() => setSourceFilter(source)}
                />
              ))}
            </Stack>
          ) : null}

          {trendHighlights.length ? (
            <Box
              sx={{
                mb: 2,
                p: { xs: 1.25, md: 2 },
                borderRadius: 2.5,
                border: "1px solid rgba(143,199,255,.16)",
                background:
                  "linear-gradient(145deg, rgba(22,36,59,.96), rgba(8,17,31,.96))",
                boxShadow: "0 18px 44px rgba(0,0,0,.2)",
              }}
            >
              <Stack
                direction={{ xs: "column", sm: "row" }}
                alignItems={{ sm: "center" }}
                justifyContent="space-between"
                spacing={1}
                sx={{ mb: 1.5 }}
              >
                <Stack direction="row" spacing={1} alignItems="center">
                  <Box
                    sx={{
                      width: 34,
                      height: 34,
                      display: "grid",
                      placeItems: "center",
                      borderRadius: 1.25,
                      color: "#8fc7ff",
                      background: "rgba(76,154,255,.16)",
                    }}
                  >
                    <PublicOutlinedIcon fontSize="small" />
                  </Box>
                  <Box>
                    <Typography variant="h6" sx={{ lineHeight: 1.1 }}>
                      Xu hướng đa nền tảng
                    </Typography>
                    <Typography variant="caption" color="text.secondary">
                      {trendSort === "hot" ? "Xếp hạng nóng nhất" : "Tin mới nhất"} · tối đa 8 mục mỗi nguồn
                    </Typography>
                  </Box>
                </Stack>
                <Stack
                  direction="row"
                  sx={{
                    p: 0.35,
                    borderRadius: 1.5,
                    border: "1px solid rgba(143,199,255,.18)",
                    background: "rgba(6,13,25,.58)",
                  }}
                >
                  <Button
                    size="small"
                    startIcon={<WhatshotOutlinedIcon fontSize="small" />}
                    onClick={() => setTrendSort("hot")}
                    variant={trendSort === "hot" ? "contained" : "text"}
                    sx={{ minWidth: 0, px: 1.2, borderRadius: 1.1 }}
                  >
                    Nóng nhất
                  </Button>
                  <Button
                    size="small"
                    startIcon={<AccessTimeOutlinedIcon fontSize="small" />}
                    onClick={() => setTrendSort("latest")}
                    variant={trendSort === "latest" ? "contained" : "text"}
                    sx={{ minWidth: 0, px: 1.2, borderRadius: 1.1 }}
                  >
                    Mới nhất
                  </Button>
                </Stack>
              </Stack>
              <Box
                sx={{
                  display: "grid",
                  gridTemplateColumns: "repeat(auto-fit, minmax(290px, 1fr))",
                  gap: 1.25,
                  alignItems: "stretch",
                }}
              >
                {trendHighlights.map(([source, sourceRows]) => {
                  const tone = sourceTone(source);
                  return (
                    <Box
                      key={source}
                      sx={{
                        minWidth: 0,
                        display: "flex",
                        flexDirection: "column",
                        border: `1px solid ${tone.border}`,
                        borderRadius: 2,
                        overflow: "hidden",
                        background: "rgba(9,19,34,.88)",
                      }}
                    >
                      <Stack
                        direction="row"
                        alignItems="center"
                        justifyContent="space-between"
                        sx={{ px: 1.35, py: 1, background: tone.surface }}
                      >
                        <Stack direction="row" spacing={0.9} alignItems="center" minWidth={0}>
                          <Box
                            sx={{
                              width: 29,
                              height: 29,
                              display: "grid",
                              placeItems: "center",
                              borderRadius: 1,
                              color: tone.accent,
                              border: `1px solid ${tone.border}`,
                              fontWeight: 800,
                              fontSize: 12,
                            }}
                          >
                            {sourceLabel(source).slice(0, 2).toUpperCase()}
                          </Box>
                          <Typography noWrap fontWeight={700} sx={{ color: tone.accent }}>
                            {sourceLabel(source)}
                          </Typography>
                        </Stack>
                        <Typography variant="caption" color="text.secondary">
                          {sourceRows.length} mục
                        </Typography>
                      </Stack>
                      <Stack spacing={0} sx={{ p: 0.8, flex: 1 }}>
                        {sourceRows.map((finding, index) => {
                          const title = displayWireTitle(finding);
                          const engagement = engagementLabel(finding);
                          const content = (
                            <Stack
                              direction="row"
                              spacing={1}
                              alignItems="flex-start"
                              sx={{
                                p: 0.75,
                                borderRadius: 1.25,
                                transition: "background .15s ease",
                                "&:hover": { background: "rgba(255,255,255,.055)" },
                              }}
                            >
                              <Box
                                sx={{
                                  flex: "0 0 25px",
                                  width: 25,
                                  height: 25,
                                  display: "grid",
                                  placeItems: "center",
                                  borderRadius: 0.8,
                                  color: index === 0 ? tone.accent : "text.secondary",
                                  background: index === 0 ? tone.surface : "rgba(255,255,255,.06)",
                                  fontSize: 12,
                                  fontWeight: 700,
                                }}
                              >
                                {index + 1}
                              </Box>
                              <Box sx={{ minWidth: 0, flex: 1 }}>
                                <Typography
                                  variant="body2"
                                  sx={{
                                    fontWeight: 600,
                                    lineHeight: 1.45,
                                    display: "-webkit-box",
                                    WebkitLineClamp: 3,
                                    WebkitBoxOrient: "vertical",
                                    overflow: "hidden",
                                  }}
                                >
                                  {title}
                                </Typography>
                                <Typography variant="caption" color="text.secondary" noWrap>
                                  Điểm {Number(finding.score || 0).toFixed(0)}
                                  {engagement ? ` · ${engagement}` : ""}
                                </Typography>
                              </Box>
                            </Stack>
                          );
                          return finding.url ? (
                            <Link
                              key={finding.id}
                              href={finding.url}
                              target="_blank"
                              rel="noreferrer"
                              underline="none"
                              sx={{ color: "inherit" }}
                            >
                              {content}
                            </Link>
                          ) : (
                            <Box key={finding.id}>{content}</Box>
                          );
                        })}
                      </Stack>
                    </Box>
                  );
                })}
              </Box>
            </Box>
          ) : null}

          {!trendHighlights.length && !fallbackLoading ? (
            <Box
              sx={{
                mb: 2,
                p: { xs: 1.25, md: 2 },
                borderRadius: 2.5,
                border: "1px solid rgba(143,199,255,.16)",
                background: "rgba(9,19,34,.72)",
              }}
            >
              <Typography variant="subtitle1" fontWeight={700} sx={{ mb: 1 }}>
                Nguồn xu hướng
              </Typography>
              <Box
                sx={{
                  display: "grid",
                  gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))",
                  gap: 1,
                }}
              >
                {TREND_PROVIDER_LINKS.map((provider) => (
                  <Link
                    key={provider.name}
                    href={provider.url}
                    target="_blank"
                    rel="noreferrer"
                    underline="none"
                    sx={{
                      p: 1.25,
                      borderRadius: 1.5,
                      border: `1px solid ${provider.accent}55`,
                      background: `${provider.accent}12`,
                      color: "inherit",
                      transition: "background .15s ease",
                      "&:hover": { background: `${provider.accent}22` },
                    }}
                  >
                    <Typography fontWeight={700} sx={{ color: provider.accent }}>
                      {provider.name}
                    </Typography>
                    <Typography variant="caption" color="text.secondary">
                      {provider.description}
                    </Typography>
                  </Link>
                ))}
              </Box>
            </Box>
          ) : null}

          {!trendHighlights.length && selectedLive ? (
            <Box
              sx={{
                p: 2.5,
                borderRadius: 2,
                border: "1px dashed rgba(143,199,255,.24)",
                color: "text.secondary",
                textAlign: "center",
              }}
            >
              Nguồn đang được cập nhật. Bảng sẽ tự hiển thị khi có mục phù hợp.
            </Box>
          ) : null}

          {selectedLive?.error_message ? (
            <Alert severity="warning" sx={{ mb: 1.5 }}>
              {selectedLive.error_message}
            </Alert>
          ) : null}
        </Box>
    </Stack>
  );
}
