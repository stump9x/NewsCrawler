import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Alert,
  Box,
  Button,
  Chip,
  LinearProgress,
  Link,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import TravelExploreOutlinedIcon from "@mui/icons-material/TravelExploreOutlined";
import AccessTimeOutlinedIcon from "@mui/icons-material/AccessTimeOutlined";
import WhatshotOutlinedIcon from "@mui/icons-material/WhatshotOutlined";
import PublicOutlinedIcon from "@mui/icons-material/PublicOutlined";
import { api } from "../api/client";
import { DataTable } from "../components/DataTable";
import { PageHeader } from "../components/PageHeader";
import { StatusChip } from "../components/StatusChips";
import { displayWireTitle } from "../utils/wireTitle";

const LIST_POLL_MS = 2000;
const FINDINGS_POLL_MS = 1500;
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
  const navigate = useNavigate();
  const [rows, setRows] = useState([]);
  const [selected, setSelected] = useState(null);
  const [findings, setFindings] = useState([]);
  const [topic, setTopic] = useState("Biển Đông Philippines");
  const [busy, setBusy] = useState(false);
  const [notebookBusy, setNotebookBusy] = useState(false);
  const [error, setError] = useState("");
  const [msg, setMsg] = useState("");
  const [configured, setConfigured] = useState(true);
  const [xConfigured, setXConfigured] = useState(false);
  const [redditConfigured, setRedditConfigured] = useState(false);
  const [wigoloConfigured, setWigoloConfigured] = useState(false);
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

  const selectedRunning =
    selectedLive &&
    (selectedLive.status === "queued" || selectedLive.status === "running");

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
      setXConfigured(Boolean(status.x_configured));
      setRedditConfigured(Boolean(status.reddit_configured));
      setWigoloConfigured(Boolean(status.wigolo_configured));
      await loadList();
    } catch (err) {
      setError(err.message || "Không thể tải nghiên cứu");
    }
  }, [loadList]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  useEffect(() => {
    if (!selected && rows.length) setSelected(rows[0]);
  }, [rows, selected]);

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

  async function startResearch() {
    const t = topic.trim();
    if (t.length < 2) {
      setError("Nhập chủ đề (ít nhất 2 ký tự).");
      return;
    }
    setBusy(true);
    setError("");
    setMsg("");
    try {
      const data = await api.post("/api/v1/trend/researches/", {
        topic: t,
        depth: "quick",
        lookback_days: 30,
      });
      lastFindingIdRef.current = 0;
      setFindings([]);
      setSourceFilter("all");
      setSelected(data);
      setMsg(`Đã xếp hàng #${data.id} — kết quả và % sẽ cập nhật theo từng nguồn.`);
      await loadList();
    } catch (err) {
      setError(err.message || "Không thể bắt đầu nghiên cứu");
    } finally {
      setBusy(false);
    }
  }

  async function addToNotebookAI() {
    if (!selectedLive?.id) return;
    setNotebookBusy(true);
    setError("");
    setMsg("");
    try {
      const data = await api.post(
        `/api/v1/trend/researches/${selectedLive.id}/to-notebook/`,
        {}
      );
      const name = data.notebook_name || "Notebook mới";
      const n = data.sources_queued || 0;
      setMsg(`Đã tạo notebook mới «${name}» trong Phân tích sâu — xếp hàng ${n} nguồn.`);
      if (data.notebook_id) {
        navigate(`/notebook-ai?notebook=${encodeURIComponent(data.notebook_id)}`);
      } else if (data.open_url) {
        window.open(data.open_url, "_blank", "noopener,noreferrer");
      }
    } catch (err) {
      setError(err.message || "Không thêm được vào Phân tích sâu");
    } finally {
      setNotebookBusy(false);
    }
  }

  const pct = Math.max(0, Math.min(100, Number(selectedLive?.progress_pct) || 0));
  const sourceChips = selectedLive?.source_counts
    ? Object.entries(selectedLive.source_counts).map(([k, v]) => (
        <Chip key={k} size="small" label={`${sourceLabel(k)}: ${v}`} color="primary" variant="outlined" />
      ))
    : null;
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
  const canAddNotebook =
    Boolean(selectedLive?.id) &&
    !selectedRunning &&
    (findings.length > 0 || Number(selectedLive?.item_count) > 0);

  return (
    <Stack spacing={2}>
      <PageHeader
        title="Xu hướng"
        subtitle="Tổng hợp đa nền tảng · xếp hạng nổi bật · hiển thị tiếng Việt"
        action={
          <Button variant="outlined" onClick={refresh} startIcon={<TravelExploreOutlinedIcon />}>
            Làm mới
          </Button>
        }
      />
      {!configured ? (
        <Alert severity="warning">Module Xu hướng chưa sẵn sàng.</Alert>
      ) : null}
      <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
        <Chip
          size="small"
          color={redditConfigured ? "success" : "default"}
          label={redditConfigured ? "Reddit: có cookie" : "Reddit: chưa cookie"}
        />
        <Chip
          size="small"
          color={xConfigured ? "success" : "default"}
          label={xConfigured ? "X: có credential" : "X: chưa credential"}
        />
        <Chip
          size="small"
          color={wigoloConfigured ? "success" : "default"}
          label={wigoloConfigured ? "Wigolo: sẵn sàng" : "Wigolo: chưa cấu hình"}
        />
      </Stack>
      {error ? <Alert severity="error">{error}</Alert> : null}
      {msg ? <Alert severity="success">{msg}</Alert> : null}

      <Box sx={{ border: 1, borderColor: "divider", borderRadius: 1.5, p: 1.5 }}>
        <Typography variant="subtitle1" fontWeight={700}>
          Nghiên cứu tích hợp đa nguồn
        </Typography>
        <Typography variant="body2" color="text.secondary">
          Gom các mục nổi bật từ X, Reddit, Web chọn lọc, Polymarket và Hacker News;
          chuẩn hóa tiếng Việt, xếp hạng theo điểm và loại bản sao trùng trang hoặc tiêu đề.
        </Typography>
      </Box>

      <Stack direction={{ xs: "column", md: "row" }} spacing={1.5} alignItems={{ md: "center" }}>
        <TextField
          label="Tìm xu hướng"
          placeholder="vd. Biển Đông, diễn tập SEACAT, công nghệ quốc phòng…"
          value={topic}
          onChange={(e) => setTopic(e.target.value)}
          fullWidth
          size="small"
          disabled={busy || Boolean(active)}
        />
        <Button
          variant="contained"
          onClick={startResearch}
          disabled={busy || Boolean(active) || !configured}
          sx={{ whiteSpace: "nowrap", minWidth: 170 }}
        >
          {active ? "Đang cập nhật…" : "Cập nhật xu hướng"}
        </Button>
      </Stack>

      {!selectedLive ? (
        <Alert severity="info">
          Chưa có bảng xu hướng. Nhập một chủ đề để tạo nghiên cứu tích hợp đầu tiên.
        </Alert>
      ) : null}

      {selectedLive ? (
        <Box sx={{ borderTop: 1, borderColor: "divider", pt: 2 }}>
          <Typography variant="h6" sx={{ mb: 1 }}>
            Bảng xu hướng · {selectedLive.topic}
          </Typography>
          <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap sx={{ mb: 1.5 }}>
            <StatusChip value={selectedLive.status} />
            <Chip size="small" label={`${selectedLive.lookback_days || 30} ngày`} />
            <Chip size="small" label={`${pct}%`} color="primary" />
            <Chip size="small" label={`${findings.length || selectedLive.item_count || 0} mục`} />
            {selectedLive.duration_ms ? (
              <Chip size="small" label={`${Math.round(selectedLive.duration_ms / 1000)}s`} />
            ) : null}
            {sourceChips}
            {canAddNotebook ? (
              <Button
                size="small"
                variant="outlined"
                disabled={notebookBusy}
                onClick={addToNotebookAI}
              >
                {notebookBusy ? "Đang thêm…" : "Thêm vào Phân tích sâu"}
              </Button>
            ) : null}
          </Stack>

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

          {(selectedRunning || pct > 0) && (
            <Box sx={{ mb: 2 }}>
              <Stack direction="row" justifyContent="space-between" sx={{ mb: 0.5 }}>
                <Typography variant="body2" color="text.secondary">
                  {selectedLive.progress ||
                    (selectedRunning
                      ? "Đang chạy…"
                      : selectedLive.status === "partial"
                        ? "Hoàn thành một phần"
                        : selectedLive.status === "failed"
                          ? "Thất bại"
                          : "Hoàn thành")}
                </Typography>
                <Typography variant="body2" fontWeight={600}>
                  {pct}%
                </Typography>
              </Stack>
              <LinearProgress
                variant="determinate"
                value={pct}
                sx={{ height: 8, borderRadius: 1 }}
              />
            </Box>
          )}

          {selectedLive.error_message ? (
            <Alert severity="warning" sx={{ mb: 1.5 }}>
              {selectedLive.error_message}
            </Alert>
          ) : null}

          <Typography variant="subtitle1" fontWeight={700} sx={{ mb: 1 }}>
            Kết quả nghiên cứu tích hợp
          </Typography>
          <DataTable
            rows={visibleFindings}
            empty={
              selectedRunning
                ? "Đang thu thập — kết quả sẽ xuất hiện dần theo từng nguồn…"
                : "Chưa có kết quả."
            }
            columns={[
              {
                id: "source",
                label: "Nguồn",
                width: 110,
                render: (row) => <Chip size="small" label={sourceLabel(row.source)} />,
              },
              {
                id: "title",
                label: "Tiêu đề",
                render: (row) => {
                  const vi = displayWireTitle(row);
                  const original = (row.title || "").trim();
                  const showOriginal =
                    original &&
                    vi &&
                    vi !== original &&
                    row.title_vi_status !== "pending";
                  const body = row.url ? (
                    <Link href={row.url} target="_blank" rel="noreferrer">
                      {vi}
                    </Link>
                  ) : (
                    vi
                  );
                  return (
                    <Stack spacing={0.25}>
                      {body}
                      {showOriginal ? (
                        <Typography variant="caption" color="text.secondary">
                          {original}
                        </Typography>
                      ) : null}
                      {row.snippet_vi || row.snippet ? (
                        <Typography
                          variant="caption"
                          color="text.secondary"
                          sx={{
                            display: "-webkit-box",
                            WebkitLineClamp: 2,
                            WebkitBoxOrient: "vertical",
                            overflow: "hidden",
                          }}
                        >
                          {row.snippet_vi || row.snippet}
                        </Typography>
                      ) : null}
                    </Stack>
                  );
                },
              },
              {
                id: "score",
                label: "Điểm",
                width: 80,
                render: (row) => Number(row.score || 0).toFixed(0),
              },
              {
                id: "published_at",
                label: "Ngày",
                width: 120,
                render: (row) =>
                  row.published_at
                    ? new Date(row.published_at).toLocaleDateString("vi-VN")
                    : "—",
              },
            ]}
          />
        </Box>
      ) : null}
    </Stack>
  );
}
