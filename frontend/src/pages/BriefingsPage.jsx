import { useCallback, useEffect, useState } from "react";
import { Link as RouterLink, useNavigate } from "react-router-dom";
import FullscreenIcon from "@mui/icons-material/Fullscreen";
import FullscreenExitIcon from "@mui/icons-material/FullscreenExit";
import MenuBookOutlinedIcon from "@mui/icons-material/MenuBookOutlined";
import OpenInNewIcon from "@mui/icons-material/OpenInNew";
import {
  Alert,
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  IconButton,
  LinearProgress,
  Link,
  Paper,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { api } from "../api/client";
import { DataTable } from "../components/DataTable";
import { InlineMarkdown } from "../components/InlineMarkdown";
import { PageHeader } from "../components/PageHeader";
import { StatusChip } from "../components/StatusChips";
import {
  clearActiveBriefingJob,
  patchActiveBriefingJob,
  readActiveBriefingJob,
  subscribeActiveBriefingJob,
  writeActiveBriefingJob,
} from "../utils/briefingJobStore";
import { filterUserFacingPipelineWarnings } from "../utils/pipelineWarnings";

const TERMINAL = new Set(["ready", "failed"]);
const URL_SPLIT_RE = /(https?:\/\/[^\s<>"'）】\]}>]+)/gi;
const MD_LINK_RE = /\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/gi;

/**
 * Soft-clean Wire metadata noise. Keep paired **bold** / *italic* for
 * InlineMarkdown — do not strip emphasis into broken plain text.
 */
function displayProse(text) {
  return String(text || "")
    .replace(/^#{1,6}\s*/gm, "")
    .replace(/^\s*[\*\-]\s+/gm, "• ")
    .replace(/\(source=[^)]*\)/gi, "")
    .replace(/\bkev\s*=\s*(True|False)\b/gi, "")
    .replace(/\bcvss\s*=\s*\S+/gi, "")
    .replace(/\[(critical|high|medium|low|info)\]\s*/gi, "")
    .replace(/[ \t]{2,}/g, " ")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

function trimUrlTrailingPunct(url) {
  return String(url || "").replace(/[.,;:!?)]+$/g, "");
}

/** Split text into markdown-links, bare URLs, and plain runs. */
function splitProseWithLinks(text) {
  const clean = displayProse(text);
  if (!clean) return [];
  const chunks = [];
  let last = 0;
  MD_LINK_RE.lastIndex = 0;
  let m = MD_LINK_RE.exec(clean);
  while (m) {
    if (m.index > last) {
      chunks.push({ type: "text", value: clean.slice(last, m.index) });
    }
    chunks.push({ type: "mdlink", label: m[1], href: trimUrlTrailingPunct(m[2]) });
    last = m.index + m[0].length;
    m = MD_LINK_RE.exec(clean);
  }
  if (last < clean.length) {
    chunks.push({ type: "text", value: clean.slice(last) });
  }
  // Expand bare URLs inside plain text runs.
  const out = [];
  for (const chunk of chunks) {
    if (chunk.type !== "text") {
      out.push(chunk);
      continue;
    }
    const parts = chunk.value.split(URL_SPLIT_RE);
    for (const part of parts) {
      if (!part) continue;
      if (/^https?:\/\//i.test(part)) {
        const href = trimUrlTrailingPunct(part);
        out.push({ type: "url", href, trailing: part.slice(href.length) });
      } else {
        out.push({ type: "text", value: part });
      }
    }
  }
  return out;
}

function LinkifiedProse({ text }) {
  const parts = splitProseWithLinks(text);
  if (!parts.length) {
    return (
      <Typography color="text.secondary" sx={{ py: 2 }}>
        (trống)
      </Typography>
    );
  }
  return (
    <Box
      component="pre"
      sx={{
        m: 0,
        whiteSpace: "pre-wrap",
        wordBreak: "break-word",
        fontFamily: "inherit",
        fontSize: 14,
        lineHeight: 1.65,
        color: "text.primary",
      }}
    >
      {parts.map((part, i) => {
        if (part.type === "mdlink") {
          return (
            <Link
              key={`ml-${i}`}
              href={part.href}
              target="_blank"
              rel="noopener noreferrer"
              underline="hover"
              sx={{ wordBreak: "break-word" }}
            >
              {part.label}
            </Link>
          );
        }
        if (part.type === "url") {
          return (
            <span key={`u-${i}`}>
              <Link
                href={part.href}
                target="_blank"
                rel="noopener noreferrer"
                underline="hover"
                sx={{ wordBreak: "break-all" }}
              >
                {part.href}
              </Link>
              {part.trailing}
            </span>
          );
        }
        return (
          <span key={`t-${i}`}>
            <InlineMarkdown text={part.value} />
          </span>
        );
      })}
    </Box>
  );
}

function BriefingProse({ text, minRows = 12, maxHeight }) {
  const clean = displayProse(text);
  if (!clean) {
    return (
      <Typography color="text.secondary" sx={{ py: 2 }}>
        (trống)
      </Typography>
    );
  }
  return (
    <Paper
      variant="outlined"
      sx={{
        p: 2.5,
        bgcolor: "rgba(255,255,255,0.02)",
        maxHeight: maxHeight ?? minRows * 28,
        overflow: "auto",
      }}
    >
      <LinkifiedProse text={text} />
    </Paper>
  );
}

export default function BriefingsPage() {
  const navigate = useNavigate();
  const [briefings, setBriefings] = useState([]);
  const [latest, setLatest] = useState(null);
  const [viewing, setViewing] = useState(null);
  const [digest, setDigest] = useState("");
  const [keyword, setKeyword] = useState("");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [msg, setMsg] = useState("");
  const [progressPct, setProgressPct] = useState(0);
  const [progressMsg, setProgressMsg] = useState("");
  const [pipelineWarnings, setPipelineWarnings] = useState([]);
  const [reportFullscreen, setReportFullscreen] = useState(false);
  const [digestFullscreen, setDigestFullscreen] = useState(false);
  const [activeJobId, setActiveJobId] = useState(() => readActiveBriefingJob()?.id || null);

  const load = useCallback(
    () =>
      api
        .get("/api/v1/ai/briefings/?status=ready&page_size=50")
        .then((data) => setBriefings(data.results || []))
        .catch((err) => setError(err.message || "Không thể tải lịch sử báo cáo")),
    []
  );

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    return subscribeActiveBriefingJob((job) => {
      setActiveJobId(job?.id || null);
      if (job?.progress_pct != null) {
        setProgressPct((prev) => Math.max(prev, Number(job.progress_pct) || 0));
      }
      if (job?.progress) setProgressMsg(job.progress);
    });
  }, []);

  // Resume / keep watching in-flight job when entering the page (reload-safe).
  useEffect(() => {
    const stored = readActiveBriefingJob();
    if (!stored?.id) return undefined;
    let cancelled = false;
    setBusy(stored.kind || "resume");
    setProgressPct(Number(stored.progress_pct) || 5);
    setProgressMsg(stored.progress || "Đang tiếp tục báo cáo đã gửi…");
    setMsg("Đang theo dõi báo cáo đang chạy (không bị mất khi chuyển trang / tải lại).");

    async function poll() {
      while (!cancelled) {
        try {
          const row = await api.get(`/api/v1/ai/briefings/${stored.id}/`);
          if (cancelled) return;
          const status = String(row?.status || "").toLowerCase();
          const pct = Math.max(0, Math.min(100, Number(row?.progress_pct) || 0));
          setProgressPct((prev) => Math.max(prev, pct));
          if (row?.progress) setProgressMsg(row.progress);
          if (Array.isArray(row?.warnings) && row.warnings.length) {
            setPipelineWarnings(filterUserFacingPipelineWarnings(row.warnings));
          }
          patchActiveBriefingJob({
            progress_pct: row.progress_pct,
            progress: row.progress,
            title: row.title,
          });
          if (TERMINAL.has(status)) {
            if (status === "failed") {
              setError(row.error_message || "Tạo báo cáo thất bại");
              clearActiveBriefingJob();
            } else {
              setProgressPct(100);
              setProgressMsg(row.progress || "Hoàn thành");
              if (Array.isArray(row?.warnings)) {
                setPipelineWarnings(filterUserFacingPipelineWarnings(row.warnings));
              }
              setLatest(row);
              setMsg(`Đã tạo báo cáo chi tiết qua ${row.provider || "AI"}`);
              clearActiveBriefingJob();
              await load();
            }
            if (!cancelled) setBusy("");
            return;
          }
        } catch (err) {
          if (!cancelled) {
            setProgressMsg("Mất kết nối tạm thời — vẫn giữ lượt trên máy chủ…");
          }
        }
        await new Promise((r) => setTimeout(r, 2500));
      }
    }
    poll();
    return () => {
      cancelled = true;
    };
  }, [activeJobId, load]);

  function openViewing(row) {
    setDigest("");
    setDigestFullscreen(false);
    setReportFullscreen(false);
    setViewing(row);
    if (Array.isArray(row?.warnings) && row.warnings.length) {
      setPipelineWarnings(filterUserFacingPipelineWarnings(row.warnings));
    }
  }

  function closeViewing() {
    setViewing(null);
    setDigest("");
    setReportFullscreen(false);
    setDigestFullscreen(false);
  }

  async function run(kind) {
    const existing = readActiveBriefingJob();
    if (existing?.id) {
      const ok = window.confirm(
        "Đang có báo cáo chạy trên máy chủ. Tạo thêm bản mới (bản cũ vẫn chạy)?\nBấm Hủy để chỉ theo dõi bản đang chạy."
      );
      if (!ok) {
        setActiveJobId(existing.id);
        setBusy(existing.kind || "resume");
        setMsg("Tiếp tục theo dõi báo cáo đang chạy.");
        return;
      }
    }
    setBusy(kind);
    setError("");
    setMsg("");
    setDigest("");
    setProgressPct(3);
    setProgressMsg("Đang gửi yêu cầu…");
    setPipelineWarnings([]);
    try {
      let queued;
      if (kind === "daily") {
        queued = await api.post("/api/v1/ai/briefings/generate/", {
          window_hours: 24,
          async_mode: true,
        });
      } else if (kind === "weekly") {
        queued = await api.post("/api/v1/ai/weekly-digest/", { async_mode: true });
      } else if (kind === "keyword") {
        queued = await api.post("/api/v1/ai/keyword-summary/", {
          keyword: keyword.trim(),
          window_hours: 168,
          async_mode: true,
        });
      } else {
        throw new Error("Loại báo cáo không hợp lệ");
      }

      const cleaned = queued?.cleaned || {};
      const cleanedBits = [];
      if (cleaned.failed_deleted) {
        cleanedBits.push(`${cleaned.failed_deleted} bản lỗi`);
      }
      if (cleaned.pending_deleted) {
        cleanedBits.push(`${cleaned.pending_deleted} hàng đợi kẹt (cũ)`);
      }

      if (queued?.id && String(queued.status).toLowerCase() === "pending") {
        writeActiveBriefingJob({
          id: queued.id,
          kind,
          title: queued.title || "",
          startedAt: Date.now(),
          progress_pct: Number(queued.progress_pct) || 5,
          progress: queued.progress || "Đã xếp hàng — chờ xử lý",
        });
        setActiveJobId(queued.id);
        setProgressPct(Number(queued.progress_pct) || 5);
        setProgressMsg(queued.progress || "Đã xếp hàng — chờ xử lý");
        setMsg(
          `Đã gửi báo cáo #${queued.id} — có thể chuyển trang / tải lại, tiến trình vẫn chạy.` +
            (cleanedBits.length ? ` (đã dọn ${cleanedBits.join(", ")})` : "")
        );
        // Polling continues via useEffect(activeJobId) — do not block here.
        return;
      }

      // Sync / already ready
      clearActiveBriefingJob();
      setProgressPct(100);
      setProgressMsg("Hoàn thành");
      setLatest(queued);
      openViewing(queued);
      setMsg(`Đã tạo báo cáo chi tiết qua ${queued?.provider || "AI"}`);
      setBusy("");
      await load();
    } catch (err) {
      setError(err.message || "Không thể tạo báo cáo");
      setBusy("");
      await load();
    }
  }

  async function summarizeViewing() {
    if (!viewing?.id) return;
    setBusy("summarize");
    setError("");
    try {
      const data = await api.post(`/api/v1/ai/briefings/${viewing.id}/summarize/`, {});
      setDigest(data.summary || "");
      setDigestFullscreen(false);
      setMsg(`Đã tóm tắt nội dung chính (${data.provider || "AI"})`);
    } catch (err) {
      setError(err.message || "Không thể tóm tắt báo cáo");
    } finally {
      setBusy("");
    }
  }

  async function addToNotebookAI() {
    if (!viewing?.id) return;
    setBusy("notebook");
    setError("");
    try {
      const data = await api.post(`/api/v1/ai/briefings/${viewing.id}/to-notebook/`, {});
      const name = data.notebook_name || "Notebook mới";
      const n = data.sources_queued || 0;
      setMsg(`Đã tạo notebook mới «${name}» trong Phân tích sâu — xếp hàng ${n} nguồn.`);
      setViewing((prev) =>
        prev
          ? {
              ...prev,
              last_notebook_export: {
                notebook_id: data.notebook_id,
                notebook_name: name,
                open_url: data.open_url,
                sources_added: n,
              },
              sources: prev.sources,
            }
          : prev
      );
      if (data.notebook_id) {
        navigate(`/notebook-ai?notebook=${encodeURIComponent(data.notebook_id)}`);
      } else if (data.open_url) {
        window.open(data.open_url, "_blank", "noopener,noreferrer");
      }
    } catch (err) {
      setError(err.message || "Không thêm được vào Phân tích sâu");
    } finally {
      setBusy("");
    }
  }

  async function purgeStale() {
    setError("");
    try {
      const data = await api.post("/api/v1/ai/briefings/purge-stale/", {});
      setMsg(
        `Đã dọn ${data.failed_deleted || 0} bản lỗi và ${data.pending_deleted || 0} hàng đợi kẹt`
      );
      await load();
    } catch (err) {
      setError(err.message || "Không thể dọn hàng đợi");
    }
  }

  async function deleteReady(row) {
    if (!window.confirm(`Xóa báo cáo “${row.title}”?`)) return;
    setError("");
    try {
      await api.delete(`/api/v1/ai/briefings/${row.id}/`);
      if (latest?.id === row.id) setLatest(null);
      if (viewing?.id === row.id) closeViewing();
      setMsg("Đã xóa báo cáo khỏi lịch sử");
      await load();
    } catch (err) {
      setError(err.message || "Không thể xóa");
    }
  }

  const showProgress = (!!busy && busy !== "summarize" && busy !== "notebook") || !!activeJobId;

  return (
    <Stack spacing={2}>
      <PageHeader
        title="Báo cáo nhanh"
        subtitle="Tạo báo cáo ngắn từ tin tức đã thu thập."
        action={
          <Stack direction="row" spacing={1}>
            <Button variant="outlined" color="warning" onClick={purgeStale} disabled={!!busy}>
              Dọn lỗi / kẹt
            </Button>
            <Button variant="outlined" onClick={load}>
              Làm mới
            </Button>
          </Stack>
        }
      />
      {error ? <Alert severity="error">{error}</Alert> : null}
      {msg ? <Alert severity="success">{msg}</Alert> : null}
      {pipelineWarnings.length ? (
        <Alert severity="warning">
          <Typography variant="subtitle2" sx={{ mb: 0.5 }}>
            Cảnh báo pipeline ({pipelineWarnings.length}) — hướng xử lý:
          </Typography>
          <Box component="ul" sx={{ m: 0, pl: 2 }}>
            {pipelineWarnings.slice(0, 8).map((w, i) => (
              <li key={`w-${i}`}>
                <Typography variant="body2">{w}</Typography>
              </li>
            ))}
          </Box>
        </Alert>
      ) : null}

      {showProgress ? (
        <Paper variant="outlined" sx={{ p: 2 }}>
          <Stack spacing={1}>
            <Stack direction="row" justifyContent="space-between" alignItems="center">
              <Typography variant="body2">{progressMsg || "Đang xử lý…"}</Typography>
              <Typography variant="subtitle2" fontWeight={700}>
                {Math.round(progressPct)}%
              </Typography>
            </Stack>
            <LinearProgress
              variant="determinate"
              value={Math.max(0, Math.min(100, progressPct))}
              sx={{ height: 10, borderRadius: 1 }}
            />
          </Stack>
        </Paper>
      ) : null}

      <Typography variant="subtitle2" color="text.secondary">
        Tổng hợp chung (không cần từ khóa)
      </Typography>
      <Stack direction={{ xs: "column", sm: "row" }} spacing={1.5}>
        <Button variant="contained" disabled={!!busy} onClick={() => run("daily")}>
          {busy === "daily" ? "Đang tạo…" : "Xu hướng ngày — 24 giờ"}
        </Button>
        <Button variant="outlined" disabled={!!busy} onClick={() => run("weekly")}>
          {busy === "weekly" ? "Đang tạo…" : "Chủ đề nổi bật trong tuần"}
        </Button>
      </Stack>
      <Typography variant="subtitle2" color="text.secondary">
        Báo cáo theo từ khóa
      </Typography>
      <Stack direction={{ xs: "column", sm: "row" }} spacing={1.5}>
        <TextField
          label="Từ khóa (vd: PLA Biển Đông, Starlink, AUKUS…)"
          placeholder="Nhập từ khóa rồi bấm Tạo báo cáo"
          value={keyword}
          onChange={(e) => setKeyword(e.target.value)}
          fullWidth
          helperText="Để trống nếu chỉ muốn tổng hợp chung 24h / tuần ở phía trên."
        />
        <Button
          variant="contained"
          color="secondary"
          disabled={!!busy || !keyword.trim()}
          onClick={() => run("keyword")}
          sx={{ whiteSpace: "nowrap", alignSelf: { sm: "flex-start" }, mt: { sm: 0.5 } }}
        >
          {busy === "keyword" ? "Đang tạo…" : "Tạo báo cáo"}
        </Button>
      </Stack>
      {latest?.content ? (
        <Stack spacing={1}>
          <Typography variant="subtitle1">Báo cáo mới nhất</Typography>
          <BriefingProse text={latest.content} minRows={16} />
        </Stack>
      ) : null}
      <Typography variant="h6">Lịch sử báo cáo thành công</Typography>
      <DataTable
        rows={briefings}
        empty="Chưa có báo cáo thành công"
        columns={[
          {
            id: "title",
            label: "Tiêu đề",
            render: (row) => (
              <Button
                size="small"
                onClick={() => openViewing(row)}
                sx={{ textTransform: "none", justifyContent: "flex-start", px: 0 }}
              >
                {row.title}
              </Button>
            ),
          },
          {
            id: "status",
            label: "Trạng thái",
            render: (row) => <StatusChip value={row.status} />,
          },
          { id: "threat_count", label: "Số bản tin" },
          {
            id: "created_at",
            label: "Ngày tạo",
            render: (row) =>
              row.created_at ? new Date(row.created_at).toLocaleString("vi-VN") : "—",
          },
          {
            id: "actions",
            label: "",
            render: (row) => (
              <Stack direction="row" spacing={1}>
                <Button size="small" onClick={() => openViewing(row)}>
                  Xem
                </Button>
                <Button size="small" color="error" onClick={() => deleteReady(row)}>
                  Xóa
                </Button>
              </Stack>
            ),
          },
        ]}
      />

      {/* Main viewer: report OR digest preview (not stacked fullscreen together). */}
      <Dialog
        open={!!viewing && !reportFullscreen && !digestFullscreen}
        onClose={closeViewing}
        fullWidth
        maxWidth="xl"
        scroll="paper"
        PaperProps={{
          sx: {
            height: { xs: "96vh", sm: "90vh" },
            maxHeight: { xs: "96vh", sm: "90vh" },
            width: { xs: "100%", sm: "94%" },
            m: { xs: 0.5, sm: 2 },
          },
        }}
      >
        <DialogTitle sx={{ display: "flex", alignItems: "center", gap: 1, pr: 1 }}>
          <Box sx={{ flex: 1, minWidth: 0 }}>{viewing?.title || "Chi tiết báo cáo"}</Box>
        </DialogTitle>
        <DialogContent dividers sx={{ display: "flex", flexDirection: "column", gap: 2.5 }}>
          <Typography variant="body2" color="text.secondary">
            {viewing?.created_at
              ? new Date(viewing.created_at).toLocaleString("vi-VN")
              : ""}
          </Typography>

          <Stack direction={{ xs: "column", sm: "row" }} spacing={1} flexWrap="wrap">
            <Button
              size="small"
              variant="contained"
              color="primary"
              startIcon={<MenuBookOutlinedIcon />}
              disabled={!!busy || !viewing?.id || viewing?.status !== "ready"}
              onClick={addToNotebookAI}
            >
              {busy === "notebook" ? "Đang thêm…" : "Thêm vào Phân tích sâu"}
            </Button>
            {viewing?.last_notebook_export?.notebook_id ? (
              <Button
                size="small"
                variant="outlined"
                startIcon={<OpenInNewIcon />}
                component={RouterLink}
                to={`/notebook-ai?notebook=${encodeURIComponent(viewing.last_notebook_export.notebook_id)}`}
              >
                Mở notebook vừa tạo
              </Button>
            ) : viewing?.last_notebook_export?.open_url ? (
              <Button
                size="small"
                variant="outlined"
                startIcon={<OpenInNewIcon />}
                href={viewing.last_notebook_export.open_url}
                target="_blank"
                rel="noopener noreferrer"
              >
                Mở notebook vừa tạo
              </Button>
            ) : null}
          </Stack>
          <Typography variant="caption" color="text.secondary">
            Mỗi lần tạo một notebook mới kèm nguồn + nội dung báo cáo. Chat / Studio trong Phân tích sâu.
          </Typography>
          <Stack spacing={1}>
            <Stack direction="row" alignItems="center" justifyContent="space-between">
              <Typography variant="subtitle2">Báo cáo chi tiết</Typography>
              <Button
                size="small"
                startIcon={<FullscreenIcon />}
                onClick={() => setReportFullscreen(true)}
                disabled={!viewing?.content}
              >
                Phóng to
              </Button>
            </Stack>
            <BriefingProse text={viewing?.content} maxHeight="min(42vh, 420px)" minRows={14} />
          </Stack>

          <Stack spacing={1}>
            <Stack direction="row" alignItems="center" justifyContent="space-between" flexWrap="wrap" gap={1}>
              <Typography variant="subtitle2">Tóm tắt nội dung chính</Typography>
              <Stack direction="row" spacing={1}>
                {digest ? (
                  <Button
                    size="small"
                    startIcon={<FullscreenIcon />}
                    onClick={() => setDigestFullscreen(true)}
                  >
                    Phóng to
                  </Button>
                ) : null}
                <Button
                  size="small"
                  variant="outlined"
                  color="secondary"
                  disabled={!!busy || !viewing?.id || !viewing?.content}
                  onClick={summarizeViewing}
                >
                  {busy === "summarize" ? "Đang tóm tắt…" : digest ? "Tóm tắt lại" : "Tóm tắt"}
                </Button>
              </Stack>
            </Stack>
            {digest ? (
              <BriefingProse text={digest} maxHeight={220} minRows={6} />
            ) : (
              <Typography variant="body2" color="text.secondary">
                Bấm Tóm tắt khi cần bản ngắn — không chồng lên báo cáo chi tiết.
              </Typography>
            )}
          </Stack>
        </DialogContent>
        <DialogActions sx={{ px: 2, py: 1.5, gap: 1, flexWrap: "wrap" }}>
          <Button
            variant="contained"
            startIcon={<MenuBookOutlinedIcon />}
            disabled={!!busy || !viewing?.id}
            onClick={addToNotebookAI}
          >
            {busy === "notebook" ? "Đang thêm…" : "Thêm vào Phân tích sâu"}
          </Button>
          <Box sx={{ flex: 1 }} />
          <Button onClick={closeViewing}>Đóng</Button>
        </DialogActions>
      </Dialog>

      {/* Fullscreen report */}
      <Dialog
        open={!!viewing && reportFullscreen}
        onClose={() => setReportFullscreen(false)}
        fullScreen
      >
        <DialogTitle sx={{ display: "flex", alignItems: "center", gap: 1, pr: "max(16px, env(safe-area-inset-right, 0px))" }}>
          <Box sx={{ flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{viewing?.title || "Báo cáo chi tiết"}</Box>
          <IconButton sx={{ flexShrink: 0 }} aria-label="Thu nhỏ" onClick={() => setReportFullscreen(false)}>
            <FullscreenExitIcon />
          </IconButton>
        </DialogTitle>
        <DialogContent dividers>
          <BriefingProse text={viewing?.content} maxHeight="calc(100vh - 160px)" minRows={40} />
        </DialogContent>
        <DialogActions>
          <Button
            variant="contained"
            startIcon={<MenuBookOutlinedIcon />}
            disabled={!!busy || !viewing?.id}
            onClick={addToNotebookAI}
          >
            {busy === "notebook" ? "Đang thêm…" : "Thêm vào Phân tích sâu"}
          </Button>
          <Box sx={{ flex: 1 }} />
          <Button startIcon={<FullscreenExitIcon />} onClick={() => setReportFullscreen(false)}>
            Thu nhỏ
          </Button>
        </DialogActions>
      </Dialog>

      {/* Fullscreen digest */}
      <Dialog
        open={!!viewing && digestFullscreen && !!digest}
        onClose={() => setDigestFullscreen(false)}
        fullScreen
      >
        <DialogTitle sx={{ display: "flex", alignItems: "center", gap: 1, pr: "max(16px, env(safe-area-inset-right, 0px))" }}>
          <Box sx={{ flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>Tóm tắt nội dung chính</Box>
          <IconButton sx={{ flexShrink: 0 }} aria-label="Thu nhỏ" onClick={() => setDigestFullscreen(false)}>
            <FullscreenExitIcon />
          </IconButton>
        </DialogTitle>
        <DialogContent dividers>
          <BriefingProse text={digest} maxHeight="calc(100vh - 160px)" minRows={30} />
        </DialogContent>
        <DialogActions>
          <Button startIcon={<FullscreenExitIcon />} onClick={() => setDigestFullscreen(false)}>
            Thu nhỏ
          </Button>
        </DialogActions>
      </Dialog>
    </Stack>
  );
}
