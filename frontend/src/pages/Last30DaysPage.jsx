import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Alert,
  Box,
  Button,
  Chip,
  FormControl,
  InputLabel,
  LinearProgress,
  Link,
  MenuItem,
  Select,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import TravelExploreOutlinedIcon from "@mui/icons-material/TravelExploreOutlined";
import { api } from "../api/client";
import { DataTable } from "../components/DataTable";
import { PageHeader } from "../components/PageHeader";
import { StatusChip } from "../components/StatusChips";
import { displayWireTitle } from "../utils/wireTitle";

const LIST_POLL_MS = 2000;
const FINDINGS_POLL_MS = 1500;

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

export default function Last30DaysPage() {
  const navigate = useNavigate();
  const [rows, setRows] = useState([]);
  const [selected, setSelected] = useState(null);
  const [findings, setFindings] = useState([]);
  const [topic, setTopic] = useState("Biển Đông Philippines");
  const [depth, setDepth] = useState("quick");
  const [days, setDays] = useState(30);
  const [busy, setBusy] = useState(false);
  const [notebookBusy, setNotebookBusy] = useState(false);
  const [error, setError] = useState("");
  const [msg, setMsg] = useState("");
  const [configured, setConfigured] = useState(true);
  const [xConfigured, setXConfigured] = useState(false);
  const [redditConfigured, setRedditConfigured] = useState(false);
  const [wigoloConfigured, setWigoloConfigured] = useState(false);
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
    const data = await api.get("/api/v1/last30days/researches/?page_size=30");
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
        `/api/v1/last30days/researches/${id}/findings/?page_size=100&after_id=${lastFindingIdRef.current}`
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
      `/api/v1/last30days/researches/${id}/findings/?page_size=100`
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
      const status = await api.get("/api/v1/last30days/researches/status/");
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
      const data = await api.post("/api/v1/last30days/researches/", {
        topic: t,
        depth,
        lookback_days: Number(days) || 30,
      });
      lastFindingIdRef.current = 0;
      setFindings([]);
      setSelected(data);
      setMsg(`Đã xếp hàng #${data.id} — kết quả và % sẽ cập nhật theo từng nguồn.`);
      await loadList();
    } catch (err) {
      setError(err.message || "Không thể bắt đầu nghiên cứu");
    } finally {
      setBusy(false);
    }
  }

  async function openResearch(row) {
    lastFindingIdRef.current = 0;
    setSelected(row);
    setFindings([]);
    try {
      await loadFindings(row.id);
    } catch (err) {
      setError(err.message || "Không thể tải kết quả");
    }
  }

  async function removeResearch(row) {
    if (!window.confirm(`Xóa nghiên cứu #${row.id}?`)) return;
    try {
      await api.delete(`/api/v1/last30days/researches/${row.id}/`);
      if (selected?.id === row.id) {
        setSelected(null);
        setFindings([]);
        lastFindingIdRef.current = 0;
      }
      await loadList();
    } catch (err) {
      setError(err.message || "Không thể xóa");
    }
  }

  async function addToNotebookAI() {
    if (!selectedLive?.id) return;
    setNotebookBusy(true);
    setError("");
    setMsg("");
    try {
      const data = await api.post(
        `/api/v1/last30days/researches/${selectedLive.id}/to-notebook/`,
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
        <Chip key={k} size="small" label={`${k}: ${v}`} color="primary" variant="outlined" />
      ))
    : null;
  const canAddNotebook =
    Boolean(selectedLive?.id) &&
    !selectedRunning &&
    (findings.length > 0 || Number(selectedLive?.item_count) > 0);

  return (
    <Stack spacing={2}>
      <PageHeader
        title="Xu hướng"
        subtitle="Reddit · X · Polymarket · Wigolo web — Groq hiểu chủ đề + dịch VI."
        action={
          <Button variant="outlined" onClick={refresh} startIcon={<TravelExploreOutlinedIcon />}>
            Làm mới
          </Button>
        }
      />
      {!configured ? (
        <Alert severity="warning">Module Last30Days chưa sẵn sàng.</Alert>
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

      <Stack direction={{ xs: "column", md: "row" }} spacing={1.5} alignItems={{ md: "center" }}>
        <TextField
          label="Chủ đề"
          value={topic}
          onChange={(e) => setTopic(e.target.value)}
          fullWidth
          size="small"
          disabled={busy || Boolean(active)}
        />
        <FormControl size="small" sx={{ minWidth: 140 }}>
          <InputLabel>Độ sâu</InputLabel>
          <Select
            label="Độ sâu"
            value={depth}
            onChange={(e) => setDepth(e.target.value)}
            disabled={busy || Boolean(active)}
          >
            <MenuItem value="quick">Nhanh</MenuItem>
            <MenuItem value="default">Mặc định</MenuItem>
            <MenuItem value="deep">Sâu</MenuItem>
          </Select>
        </FormControl>
        <TextField
          label="Số ngày"
          type="number"
          size="small"
          value={days}
          onChange={(e) => setDays(e.target.value)}
          inputProps={{ min: 1, max: 90 }}
          sx={{ width: 110 }}
          disabled={busy || Boolean(active)}
        />
        <Button
          variant="contained"
          onClick={startResearch}
          disabled={busy || Boolean(active) || !configured}
          sx={{ whiteSpace: "nowrap" }}
        >
          {active ? "Đang chạy…" : "Nghiên cứu"}
        </Button>
      </Stack>

      <DataTable
        rows={rows}
        columns={[
          { id: "id", label: "ID", width: 64 },
          { id: "topic", label: "Chủ đề" },
          {
            id: "status",
            label: "Trạng thái",
            render: (row) => <StatusChip value={row.status} />,
          },
          { id: "item_count", label: "Mục", width: 72 },
          {
            id: "progress_pct",
            label: "%",
            width: 72,
            render: (row) => `${row.progress_pct ?? 0}%`,
          },
          {
            id: "progress",
            label: "Tiến độ",
            render: (row) => row.progress || "—",
          },
          {
            id: "actions",
            label: "",
            render: (row) => (
              <Stack direction="row" spacing={1}>
                <Button size="small" onClick={() => openResearch(row)}>
                  Xem
                </Button>
                <Button
                  size="small"
                  color="inherit"
                  disabled={row.status === "queued" || row.status === "running"}
                  onClick={() => removeResearch(row)}
                >
                  Xóa
                </Button>
              </Stack>
            ),
          },
        ]}
      />

      {selectedLive ? (
        <Box sx={{ borderTop: 1, borderColor: "divider", pt: 2 }}>
          <Typography variant="h6" sx={{ mb: 1 }}>
            #{selectedLive.id} · {selectedLive.topic}
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

          <DataTable
            rows={findings}
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
                render: (row) => <Chip size="small" label={row.source} />,
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
