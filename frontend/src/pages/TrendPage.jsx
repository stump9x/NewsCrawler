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
const SOURCE_LABELS = {
  reddit: "Reddit",
  x: "X",
  polymarket: "Polymarket",
  web: "Web chọn lọc",
  hackernews: "Hacker News",
};

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
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [configured, setConfigured] = useState(null);
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
    const data = await api.get("/api/v1/trend/researches/?page_size=30");
    setRows(data.results || []);
  }, []);

  const loadFindings = useCallback(async (id, { incremental = false, lookbackDays = 30 } = {}) => {
    if (!id) {
      setFindings([]);
      lastFindingIdRef.current = 0;
      return;
    }
    if (incremental && lastFindingIdRef.current > 0) {
      const data = await api.get(
        `/api/v1/trend/researches/${id}/findings/?page_size=100&after_id=${lastFindingIdRef.current}`
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
    const data = await api.get(
      `/api/v1/trend/researches/${id}/findings/?page_size=100`
    );
    const all = sortFindings(
      filterFindingsByLookback(data.results || data || [], lookbackDays)
    );
    setFindings(all);
    lastFindingIdRef.current = all.reduce((m, f) => Math.max(m, f.id || 0), 0);
  }, []);

  const refresh = useCallback(async () => {
    setError("");
    try {
      const status = await api.get("/api/v1/trend/researches/status/");
      setConfigured(Boolean(status.configured));
      await loadList();
      setStatusReady(true);
    } catch (err) {
      setError(err.message || "Không thể tải nghiên cứu");
      setStatusReady(true);
    }
  }, [loadList]);

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
    api.post("/api/v1/trend/researches/", {
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
    for (const finding of findings) {
      const source = String(finding.source || "web");
      counts[source] = (counts[source] || 0) + 1;
    }
    return counts;
  }, [findings]);
  const visibleFindings = useMemo(
    () => sourceFilter === "all"
      ? findings
      : findings.filter((finding) => String(finding.source || "web") === sourceFilter),
    [findings, sourceFilter]
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

      {selectedLive ? (
        <Box sx={{ pt: 0.5 }}>
          {findings.length ? (
            <Stack direction="row" spacing={0.75} flexWrap="wrap" useFlexGap sx={{ mb: 1.5 }}>
              <Chip
                size="small"
                label={`Tất cả (${findings.length})`}
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

          {selectedLive.error_message ? (
            <Alert severity="warning" sx={{ mb: 1.5 }}>
              {selectedLive.error_message}
            </Alert>
          ) : null}
        </Box>
      ) : null}
    </Stack>
  );
}
