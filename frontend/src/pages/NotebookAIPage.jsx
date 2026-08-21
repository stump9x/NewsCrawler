import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Divider,
  FormControl,
  IconButton,
  InputLabel,
  List,
  ListItemButton,
  ListItemText,
  MenuItem,
  Portal,
  Select,
  Stack,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import AddIcon from "@mui/icons-material/Add";
import DeleteOutlineIcon from "@mui/icons-material/DeleteOutline";
import FullscreenIcon from "@mui/icons-material/Fullscreen";
import FullscreenExitIcon from "@mui/icons-material/FullscreenExit";
import LinkIcon from "@mui/icons-material/Link";
import NoteAddIcon from "@mui/icons-material/NoteAdd";
import OpenInNewIcon from "@mui/icons-material/OpenInNew";
import SendIcon from "@mui/icons-material/Send";
import StopIcon from "@mui/icons-material/Stop";
import ExpandLessIcon from "@mui/icons-material/ExpandLess";
import ExpandMoreIcon from "@mui/icons-material/ExpandMore";
import UnfoldLessIcon from "@mui/icons-material/UnfoldLess";
import UnfoldMoreIcon from "@mui/icons-material/UnfoldMore";
import UploadFileIcon from "@mui/icons-material/UploadFile";
import useMediaQuery from "@mui/material/useMediaQuery";
import { useTheme } from "@mui/material/styles";
import { PageHeader } from "../components/PageHeader";
import {
  notebookApi,
  notebookCloudApi,
  NotebookApiError,
  NOTEBOOK_ANSWER_STYLE_HINT,
  NOTEBOOK_FAST_ANSWER_HINT,
  buildNotebookContextConfig,
  filterBuiltNotebookContext,
  shrinkNotebookChatContext,
  mergeCachedBodiesIntoContext,
  NOTEBOOK_BODY_MIN_CHARS,
  isUsableArticleBody,
  cleanSourcePlainText,
  textLooksLikeTitleOnly,
  splitChatRaceChain,
  NOTEBOOK_CHAT_V2_ENABLED,
  NOTEBOOK_CHAT_HEDGE_DELAY_MS,
  NOTEBOOK_CHAT_CLOUD_TIMEOUT_MS,
  NOTEBOOK_CHAT_LOCAL_TIMEOUT_MS,
  inspectNotebookCitations,
  formatNotebookCitations,
  notebookScopedStyleHint,
  normalizeNotebookSourceId,
  notebookAnswerQualityIssue,
  sanitizeNotebookAnswer,
  sanitizeStudioOutput,
  buildDeterministicGroundedAnswer,
  lastAiMessageContent,
  isNotebookStudioModel,
  notebookProviderOfModel,
  pickTransformFallbackChain,
  pickTransformPreferredModelId,
  buildTransformModelTryOrder,
  isTransformFailoverError,
  resolveNotebookModelTryOrder,
  reportNotebookProviderFailure,
  reportNotebookProviderSuccess,
  prepareTransformSourcePayload,
  transformMaxTokensForPreset,
  splitTransformRaceChain,
  isSimpleNotebookChatQuery,
  isMainContentDigestQuery,
  isSocialChitchatQuery,
  buildTextFragmentHref,
  CHAT_PROVIDER_ORDER,
  TRANSFORM_PROVIDER_ORDER,
  TRANSFORM_RACE_STAGGER_MS,
  CHAT_HANG_TIMEOUT_MS,
} from "../api/notebook";
import { InlineMarkdown } from "../components/InlineMarkdown";
import {
  loadNotebookChatHistory,
  saveNotebookChatHistory,
  trimChatToLastTurns,
  subscribeNotebookChatJob,
  upsertNotebookChatJob,
  getNotebookChatJob,
  abortNotebookChatJob,
  clearNotebookChatJob,
  notebookChatScopeKey,
  NOTEBOOK_CHAT_HANG_MS,
} from "../utils/notebookChatStore";
import { isSuppressedPipelineWarning } from "../utils/pipelineWarnings";

const SOURCES_W_MIN = 220;
const SOURCES_W_MAX = 640;
const SOURCES_W_DEFAULT = 300;
const SOURCES_H_MIN = 140;
const SOURCES_H_MAX = 560;
const SOURCES_H_DEFAULT = 260;

/** Provider-neutral pending status — never show ollama/model labels while waiting. */
const CHAT_THINKING_STATUS = "Đang suy nghĩ…";
/** Light status for social/chitchat (no crawl / no long thinking). */
const CHAT_SOCIAL_STATUS = "…";
/** Same copy for Transformation pending (no provider/model labels). */
const TRANSFORM_THINKING_STATUS = CHAT_THINKING_STATUS;

function errMsg(err) {
  if (err instanceof NotebookApiError) return err.message;
  return err?.message || String(err);
}

/** Prefer Open Notebook asset.url; fall back to top-level url when present. */
function sourceArticleUrl(source) {
  const raw = source?.asset?.url || source?.url || "";
  const u = typeof raw === "string" ? raw.trim() : "";
  return /^https?:\/\//i.test(u) ? u : "";
}

function clamp(n, min, max) {
  return Math.min(max, Math.max(min, n));
}

function waitForChatHedge(ms, signal) {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      const error = new Error("Aborted");
      error.name = "AbortError";
      reject(error);
      return;
    }
    const timer = setTimeout(resolve, ms);
    signal?.addEventListener(
      "abort",
      () => {
        clearTimeout(timer);
        const error = new Error("Aborted");
        error.name = "AbortError";
        reject(error);
      },
      { once: true }
    );
  });
}

function ModelsDialog({ open, onClose, onSaved }) {
  const [models, setModels] = useState([]);
  const [defaults, setDefaults] = useState({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [msg, setMsg] = useState("");

  const languageModels = useMemo(
    () => models.filter((m) => m.type === "language"),
    [models]
  );
  const embedModels = useMemo(
    () => models.filter((m) => m.type === "embedding"),
    [models]
  );

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    (async () => {
      try {
        setError("");
        const [m, d] = await Promise.all([
          notebookApi.listModels(),
          notebookApi.getDefaults(),
        ]);
        if (cancelled) return;
        setModels(Array.isArray(m) ? m : []);
        setDefaults(d || {});
      } catch (e) {
        if (!cancelled) setError(errMsg(e));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [open]);

  async function save() {
    setBusy(true);
    setError("");
    setMsg("");
    try {
      const next = await notebookApi.setDefaults({
        default_chat_model: defaults.default_chat_model || null,
        default_transformation_model:
          defaults.default_transformation_model || null,
        default_embedding_model: defaults.default_embedding_model || null,
        default_tools_model:
          defaults.default_tools_model ||
          defaults.default_transformation_model ||
          null,
        large_context_model:
          defaults.large_context_model || defaults.default_chat_model || null,
      });
      setDefaults(next || defaults);
      setMsg("Đã lưu cấu hình mô hình.");
      onSaved?.(next);
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setBusy(false);
    }
  }

  function field(key, label, options) {
    return (
      <FormControl fullWidth size="small" sx={{ mb: 2 }}>
        <InputLabel>{label}</InputLabel>
        <Select
          label={label}
          value={defaults[key] || ""}
          onChange={(e) =>
            setDefaults((d) => ({ ...d, [key]: e.target.value || null }))
          }
        >
          <MenuItem value="">
            <em>— chọn —</em>
          </MenuItem>
          {options.map((m) => (
            <MenuItem key={m.id} value={m.id}>
              {m.provider}/{m.name}
            </MenuItem>
          ))}
        </Select>
      </FormControl>
    );
  }

  return (
    <Dialog open={open} onClose={onClose} fullWidth maxWidth="sm">
      <DialogTitle>Cấu hình mô hình Notebook</DialogTitle>
      <DialogContent>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
          Chọn mô hình mặc định cho Studio.
        </Typography>
        {error ? (
          <Alert severity="error" sx={{ mb: 2 }}>
            {error}
          </Alert>
        ) : null}
        {msg ? (
          <Alert severity="success" sx={{ mb: 2 }}>
            {msg}
          </Alert>
        ) : null}
        {field(
          "default_transformation_model",
          "Studio",
          languageModels
        )}
        {field("default_embedding_model", "Embedding", embedModels)}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Đóng</Button>
        <Button variant="contained" disabled={busy} onClick={save}>
          {busy ? "Đang lưu…" : "Lưu"}
        </Button>
      </DialogActions>
    </Dialog>
  );
}

function CreateNotebookDialog({ open, onClose, onCreated }) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!open) return;
    setName("");
    setDescription("");
    setError("");
  }, [open]);

  async function submit() {
    const n = name.trim();
    if (!n) {
      setError("Nhập tên notebook");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const nb = await notebookApi.createNotebook({
        name: n,
        description: description.trim(),
      });
      onCreated?.(nb);
      onClose();
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={open} onClose={onClose} fullWidth maxWidth="sm">
      <DialogTitle>Tạo Notebook mới</DialogTitle>
      <DialogContent>
        {error ? (
          <Alert severity="error" sx={{ mb: 2 }}>
            {error}
          </Alert>
        ) : null}
        <TextField
          autoFocus
          fullWidth
          size="small"
          label="Tên"
          value={name}
          onChange={(e) => setName(e.target.value)}
          sx={{ mt: 1, mb: 2 }}
          onKeyDown={(e) => {
            if (e.key === "Enter") submit();
          }}
        />
        <TextField
          fullWidth
          size="small"
          label="Mô tả (tuỳ chọn)"
          multiline
          minRows={2}
          value={description}
          onChange={(e) => setDescription(e.target.value)}
        />
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Huỷ</Button>
        <Button variant="contained" disabled={busy} onClick={submit}>
          {busy ? "Đang tạo…" : "Tạo"}
        </Button>
      </DialogActions>
    </Dialog>
  );
}

function DeleteNotebookDialog({ open, onClose, notebook, onDeleted }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!open) return;
    setError("");
    setBusy(false);
  }, [open, notebook?.id]);

  async function submit() {
    if (!notebook?.id) return;
    setBusy(true);
    setError("");
    try {
      await notebookApi.deleteNotebook(notebook.id, {
        deleteExclusiveSources: true,
      });
      onDeleted?.(notebook);
      onClose();
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={open} onClose={busy ? undefined : onClose} fullWidth maxWidth="xs">
      <DialogTitle>Xóa notebook?</DialogTitle>
      <DialogContent>
        {error ? (
          <Alert severity="error" sx={{ mb: 2 }}>
            {error}
          </Alert>
        ) : null}
        <Typography variant="body2">
          Xóa «{notebook?.name || "notebook"}» và các ghi chú / phiên chat liên
          quan. Nguồn chỉ thuộc notebook này cũng sẽ bị xóa. Không hoàn tác được.
        </Typography>
      </DialogContent>
      <DialogActions sx={{ px: 2, pb: 2, gap: 1, flexWrap: "wrap" }}>
        <Button onClick={onClose} disabled={busy} sx={{ minHeight: 40 }}>
          Huỷ
        </Button>
        <Button
          color="error"
          variant="contained"
          disabled={busy || !notebook?.id}
          onClick={submit}
          sx={{ minHeight: 40 }}
        >
          {busy ? "Đang xóa…" : "Xóa notebook"}
        </Button>
      </DialogActions>
    </Dialog>
  );
}

function AddSourceDialog({ open, onClose, notebookId, onAdded }) {
  const [kind, setKind] = useState("link");
  const [title, setTitle] = useState("");
  const [url, setUrl] = useState("");
  const [content, setContent] = useState("");
  const [file, setFile] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!open) return;
    setKind("link");
    setTitle("");
    setUrl("");
    setContent("");
    setFile(null);
    setError("");
  }, [open]);

  async function submit() {
    if (!notebookId) {
      setError("Chọn hoặc tạo notebook trước");
      return;
    }
    setBusy(true);
    setError("");
    try {
      let src;
      if (kind === "link") {
        const u = url.trim();
        if (!/^https?:\/\//i.test(u)) {
          setError("URL phải bắt đầu bằng http:// hoặc https://");
          setBusy(false);
          return;
        }
        src = await notebookApi.createSourceJson({
          type: "link",
          url: u,
          title: title.trim() || u,
          notebooks: [notebookId],
        });
      } else if (kind === "text") {
        if (!content.trim()) {
          setError("Nhập nội dung văn bản");
          setBusy(false);
          return;
        }
        src = await notebookApi.createSourceJson({
          type: "text",
          content: content.slice(0, 120000),
          title: title.trim() || "Ghi chú / tài liệu",
          notebooks: [notebookId],
        });
      } else {
        if (!file) {
          setError("Chọn file tài liệu");
          setBusy(false);
          return;
        }
        src = await notebookApi.createSourceUpload({
          file,
          notebooks: [notebookId],
          title: title.trim() || file.name,
        });
      }
      onAdded?.(src);
      onClose();
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={open} onClose={onClose} fullWidth maxWidth="sm">
      <DialogTitle>Thêm nguồn vào Notebook</DialogTitle>
      <DialogContent>
        {error ? (
          <Alert severity="error" sx={{ mb: 2 }}>
            {error}
          </Alert>
        ) : null}
        <FormControl fullWidth size="small" sx={{ mt: 1, mb: 2 }}>
          <InputLabel>Loại nguồn</InputLabel>
          <Select
            label="Loại nguồn"
            value={kind}
            onChange={(e) => setKind(e.target.value)}
          >
            <MenuItem value="link">Link URL</MenuItem>
            <MenuItem value="text">Văn bản / ghi chú</MenuItem>
            <MenuItem value="upload">Tài liệu (file)</MenuItem>
          </Select>
        </FormControl>
        <TextField
          fullWidth
          size="small"
          label="Tiêu đề (tuỳ chọn)"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          sx={{ mb: 2 }}
        />
        {kind === "link" ? (
          <TextField
            fullWidth
            size="small"
            label="URL"
            placeholder="https://…"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
          />
        ) : null}
        {kind === "text" ? (
          <TextField
            fullWidth
            size="small"
            label="Nội dung"
            multiline
            minRows={6}
            value={content}
            onChange={(e) => setContent(e.target.value)}
          />
        ) : null}
        {kind === "upload" ? (
          <Button
            component="label"
            variant="outlined"
            startIcon={<UploadFileIcon />}
          >
            {file ? file.name : "Chọn file"}
            <input
              hidden
              type="file"
              onChange={(e) => setFile(e.target.files?.[0] || null)}
            />
          </Button>
        ) : null}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Huỷ</Button>
        <Button variant="contained" disabled={busy || !notebookId} onClick={submit}>
          {busy ? "Đang thêm…" : "Thêm nguồn"}
        </Button>
      </DialogActions>
    </Dialog>
  );
}

/** Expand / collapse control shared by Sources, Chat, Studio panels. */
function PanelExpandButton({ expanded, onToggle, expandLabel, collapseLabel }) {
  return (
    <Portal disablePortal={!expanded}>
      <Tooltip title={expanded ? collapseLabel : expandLabel}>
        <IconButton
        size="small"
        color={expanded ? "primary" : "default"}
        aria-label={expanded ? collapseLabel : expandLabel}
        onClick={(e) => {
          e.stopPropagation();
          onToggle();
        }}
          sx={{
          flexShrink: 0,
          minHeight: 44,
          minWidth: expanded ? 112 : 44,
          maxWidth: expanded ? "calc(100vw - 32px)" : 44,
          gap: expanded ? 0.5 : 0,
          whiteSpace: "nowrap",
          overflow: "visible",
          fontWeight: 700,
          px: expanded ? 1.25 : 0.5,
          border: 1,
          borderColor: expanded ? "primary.main" : "divider",
          borderRadius: 1,
          bgcolor: expanded ? "rgba(76,154,255,0.22)" : "background.paper",
          color: expanded ? "primary.light" : "text.primary",
          zIndex: 8,
          ...(expanded
            ? {
                position: "fixed",
                top: "max(20px, env(safe-area-inset-top, 0px))",
                right: "max(20px, env(safe-area-inset-right, 0px))",
                zIndex: (theme) => theme.zIndex.modal + 5,
              }
            : {}),
          boxShadow: expanded
            ? "0 0 0 1px rgba(76,154,255,0.45)"
            : "0 1px 2px rgba(0,0,0,0.35)",
          "&:hover": {
            bgcolor: expanded ? "rgba(76,154,255,0.34)" : "action.hover",
          },
        }}
        >
        {expanded ? (
          <FullscreenExitIcon fontSize="small" />
        ) : (
          <FullscreenIcon fontSize="small" />
        )}
        {expanded ? (
          <Typography
            component="span"
            variant="caption"
            sx={{
              fontWeight: 800,
              letterSpacing: 0.3,
              fontSize: "0.8125rem",
              display: "inline",
              whiteSpace: "nowrap",
              overflow: "visible",
            }}
          >
            Thu nhỏ
          </Typography>
        ) : null}
        </IconButton>
      </Tooltip>
    </Portal>
  );
}

/** Fixed fullscreen shell so one panel fills the viewport (mobile + desktop). */
const PANEL_EXPAND_SX = {
  position: "fixed",
  inset: 0,
  zIndex: (theme) => theme.zIndex.modal + 2,
  bgcolor: "background.paper",
  borderRadius: 0,
  border: "none",
  height: "100dvh !important",
  maxHeight: "100dvh !important",
  minHeight: "100dvh !important",
  width: "100% !important",
  maxWidth: "100%",
  display: "flex",
  flexDirection: "column",
  // visible so portaled Select menus / sticky controls aren’t clipped
  overflowX: "hidden",
  overflowY: "auto",
  p: { xs: 1.25, sm: 1.5 },
  pl: { xs: "max(12px, env(safe-area-inset-left, 0px))", sm: 1.5 },
  pr: { xs: "max(12px, env(safe-area-inset-right, 0px))", sm: 1.5 },
  pt: { xs: "max(12px, env(safe-area-inset-top, 0px))", sm: 1.5 },
  pb: {
    xs: "max(12px, env(safe-area-inset-bottom, 0px))",
    sm: 1.5,
  },
};

function isRateLimitOrDownMessage(msg) {
  const s = String(msg || "").toLowerCase();
  return (
    s.includes("rate limit") ||
    s.includes("429") ||
    s.includes("402") ||
    s.includes("payment required") ||
    s.includes("too many") ||
    s.includes("ollama") ||
    s.includes("connection refused") ||
    s.includes("timeout") ||
    s.includes("timed out") ||
    s.includes("unavailable") ||
    s.includes("503") ||
    s.includes("502") ||
    s.includes("quota")
  );
}

/** Normalize registered model metadata to a logical provider. */
function providerOfModel(model) {
  return notebookProviderOfModel(model);
}

/** Cloud drafts eligible for optional Groq polish when quality is poor. */
function canPolishProvider(provider) {
  return provider === "cerebras" || provider === "openrouter";
}

const CHAT_HANG_ERROR_VI =
  "Yêu cầu đã được dừng ở giây thứ 14 để giao diện không bị treo. Hãy thử lại hoặc thu hẹp phạm vi nguồn.";

function linkAbortSignals(parent, child) {
  if (!parent || !child) return () => {};
  if (parent.aborted) {
    child.abort(parent.reason);
    return () => {};
  }
  const onAbort = () => {
    try {
      child.abort(parent.reason);
    } catch {
      child.abort();
    }
  };
  parent.addEventListener("abort", onAbort);
  return () => parent.removeEventListener("abort", onAbort);
}

async function runBoundedChatStep(work, parentSignal, timeoutMs) {
  const child = new AbortController();
  const unlink = linkAbortSignals(parentSignal, child);
  const timer = setTimeout(() => {
    try {
      child.abort("step_timeout");
    } catch {
      child.abort();
    }
  }, timeoutMs);
  try {
    return await work(child.signal);
  } finally {
    clearTimeout(timer);
    unlink();
  }
}

function isAbortError(e) {
  return (
    e?.name === "AbortError" ||
    e?.code === 20 ||
    /aborted|abort/i.test(String(e?.message || ""))
  );
}

/**
 * Drop empty-fetch pipeline warning lines from assistant text so they never
 * appear in the chat transcript (server still logs them elsewhere).
 */
function scrubPipelineWarningText(text) {
  const raw = String(text || "");
  if (!raw) return raw;
  if (!/cảnh báo pipeline|không đọc được|empty response/i.test(raw)) {
    return raw;
  }
  const lines = raw.split("\n");
  const kept = lines.filter((line) => !isSuppressedPipelineWarning(line));
  // Drop a lone "Cảnh báo pipeline …" header if its body was fully removed.
  const cleaned = kept
    .filter((line, i, arr) => {
      if (!/cảnh báo pipeline/i.test(line)) return true;
      const next = arr[i + 1] || "";
      return next.trim().length > 0 && !/cảnh báo pipeline/i.test(next);
    })
    .join("\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
  return cleaned;
}

function scrubPipelineWarningsFromMessages(apiMessages) {
  return (apiMessages || []).map((m) => {
    const role = m?.type || m?.role;
    if (role === "human" || role === "user") return m;
    const content = scrubPipelineWarningText(m?.content);
    return content === m?.content ? m : { ...m, content };
  });
}

/**
 * Notebook-level chat: default = all sources; scoped only when selectedSourceIds is non-empty.
 * In-flight requests survive tab/module unmount; AbortController only on cancel / 3‑min hang.
 */
function NotebookChatPanel({ notebookId, sources, selectedSourceIds }) {
  const theme = useTheme();
  const isNarrow = useMediaQuery(theme.breakpoints.down("md"));
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [fallbackModelId, setFallbackModelId] = useState("");
  const [fallbackChain, setFallbackChain] = useState([]);
  const [fallbackById, setFallbackById] = useState({});
  const [modelsCatalog, setModelsCatalog] = useState([]);
  const [usedModelLabel, setUsedModelLabel] = useState("");
  const [contextMeta, setContextMeta] = useState(null);
  const [pendingStatus, setPendingStatus] = useState("");
  /** Extra bottom pad when mobile keyboard overlaps the viewport. */
  const [keyboardPad, setKeyboardPad] = useState(0);
  const bottomRef = useRef(null);
  const listRef = useRef(null);
  const messagesRef = useRef(messages);
  messagesRef.current = messages;
  // Selection snapshot helpers — send() freezes scope so unmount/tab switch cannot mutate mid-flight.
  const scopeRef = useRef({ sources, selectedSourceIds });
  scopeRef.current = { sources, selectedSourceIds };
  const scopeKey = useMemo(
    () => notebookChatScopeKey(selectedSourceIds),
    [selectedSourceIds]
  );
  const scopeKeyRef = useRef(scopeKey);
  scopeKeyRef.current = scopeKey;
  const catalogRef = useRef({
    fallbackChain,
    fallbackById,
    fallbackModelId,
    modelsCatalog,
  });
  catalogRef.current = {
    fallbackChain,
    fallbackById,
    fallbackModelId,
    modelsCatalog,
  };

  const scoped = (selectedSourceIds || []).length > 0;

  // Restore history for this notebook + source scope; clear visible thread on scope change.
  useEffect(() => {
    if (!notebookId) {
      setMessages([]);
      setBusy(false);
      setError("");
      setPendingStatus("");
      setUsedModelLabel("");
      setContextMeta(null);
      return undefined;
    }
    const hist = loadNotebookChatHistory(notebookId, scopeKey);
    const job = getNotebookChatJob(notebookId);
    const jobMatchesScope =
      job?.busy &&
      (job.scopeKey == null || String(job.scopeKey) === String(scopeKey));
    if (jobMatchesScope && Array.isArray(job.messages) && job.messages.length) {
      setMessages(job.messages);
      setBusy(true);
      setError(job.error || "");
      setPendingStatus(job.status || CHAT_THINKING_STATUS);
      setUsedModelLabel(job.usedModelLabel || "");
      setContextMeta(job.contextMeta || null);
    } else {
      // New source focus/scope → show that scope's history only (often empty).
      setMessages(hist);
      setBusy(false);
      setError("");
      setPendingStatus("");
      setUsedModelLabel("");
      setContextMeta(null);
    }
    setInput("");
    return subscribeNotebookChatJob(notebookId, (j) => {
      if (!j) return;
      const curScope = scopeKeyRef.current;
      if (j.scopeKey != null && String(j.scopeKey) !== String(curScope)) {
        return;
      }
      if (Array.isArray(j.messages)) setMessages(j.messages);
      setBusy(Boolean(j.busy));
      setError(j.error || "");
      setPendingStatus(j.status || "");
      if (j.usedModelLabel) setUsedModelLabel(j.usedModelLabel);
      if (j.contextMeta) setContextMeta(j.contextMeta);
    });
  }, [notebookId, scopeKey]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const models = await notebookApi.listModels();
        if (cancelled) return;
        const list = Array.isArray(models) ? models : [];
        setModelsCatalog(list);
        const resolved = await resolveNotebookModelTryOrder(list, {
          purpose: "chat",
        });
        if (cancelled) return;
        setFallbackChain(resolved.ids);
        setFallbackById(resolved.byId);
        setFallbackModelId(resolved.ids[0] || "");
      } catch {
        // ignore — chat still uses server defaults
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // Keep composer above the on-screen keyboard (iOS/Android visualViewport).
  useEffect(() => {
    if (!isNarrow || typeof window === "undefined" || !window.visualViewport) {
      setKeyboardPad(0);
      return undefined;
    }
    const vv = window.visualViewport;
    const update = () => {
      const overlap = Math.max(0, window.innerHeight - vv.height - vv.offsetTop);
      setKeyboardPad(overlap > 12 ? overlap : 0);
    };
    vv.addEventListener("resize", update);
    vv.addEventListener("scroll", update);
    update();
    return () => {
      vv.removeEventListener("resize", update);
      vv.removeEventListener("scroll", update);
    };
  }, [isNarrow]);

  // Scroll only the transcript pane (avoid yanking the whole page on mobile).
  useEffect(() => {
    const list = listRef.current;
    if (list) {
      list.scrollTop = list.scrollHeight;
      return;
    }
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [messages, busy, pendingStatus]);

  function persistUi(patch) {
    if (!notebookId) return;
    upsertNotebookChatJob(notebookId, patch);
  }

  function setPendingBubble(status, content = "") {
    setPendingStatus(status || "");
    setMessages((prev) => {
      const next = [...prev];
      for (let i = next.length - 1; i >= 0; i -= 1) {
        if (next[i].type === "ai" && next[i]._pending) {
          next[i] = {
            ...next[i],
            content: content || next[i].content || "",
            _status: status || "",
            _pending: true,
          };
          break;
        }
      }
      persistUi({ messages: next, status: status || "" });
      return next;
    });
  }

  async function prepareChatExecution(
    text,
    signal,
    scopeSnap,
    onContextMeta,
    { fast = false } = {}
  ) {
    const contextStartedAt = performance.now();
    const srcs = scopeSnap.sources || [];
    const selected = (scopeSnap.selectedSourceIds || [])
      .map(normalizeNotebookSourceId)
      .filter(Boolean);
    const isScoped = selected.length > 0;
    const context_config = buildNotebookContextConfig(srcs, selected, {
      fast,
    });
    const built = await notebookApi.buildChatContext(
      {
        notebook_id: notebookId,
        context_config,
      },
      { signal }
    );
    let context = filterBuiltNotebookContext(built?.context, selected);
    if (isScoped) {
      const got = Array.isArray(context?.sources) ? context.sources : [];
      if (!got.length) {
        throw new Error(
          "Context rỗng sau khi lọc nguồn đã chọn — kiểm tra nguồn còn trong notebook / đã xử lý xong."
        );
      }
      const allow = new Set(selected);
      context = {
        ...context,
        sources: got.filter((s) =>
          allow.has(normalizeNotebookSourceId(s?.id))
        ),
        notes: [],
      };
    }

    // Shared crawl pipeline with Transformation: cache → clean stored → crawl.
    // Always resolve so Chat hits Transform's Redis 3h cache (no second full crawl).
    try {
      const ctxSources = Array.isArray(context?.sources) ? context.sources : [];
      const byId = new Map(
        (srcs || [])
          .filter((s) => s?.id)
          .map((s) => [normalizeNotebookSourceId(s.id), s])
      );
      const need = [];
      for (const s of ctxSources) {
        const existing = String(s?.full_text || s?.content || "").trim();
        const sid = normalizeNotebookSourceId(s?.id);
        const meta = byId.get(sid) || s;
        const url = sourceArticleUrl(meta) || sourceArticleUrl(s);
        if (!url && !sid) continue;
        // The Open Notebook context already carries usable article text. Do not
        // add a cache-resolution HTTP round trip merely because the source also
        // has a URL; interactive Chat can answer directly from this body.
        if (isUsableArticleBody(existing)) continue;
        need.push({
          source_id: sid,
          url,
          notebook_id: notebookId || "",
          title: String(meta?.title || s?.title || "").trim(),
          existing_text: existing,
        });
      }
      // Fast whole-notebook questions already use ranked Open Notebook context.
      // Skip a second cache-resolution request; scoped/deep turns still resolve
      // full bodies for stronger grounding.
      const limited = fast && !isScoped ? [] : need.slice(0, isScoped ? 4 : 8);
      if (limited.length) {
        const resolved = await notebookCloudApi.resolveArticleBodies(
          {
            items: limited,
            // Backend still prefers cache → stored before crawling.
            // Interactive Chat never waits for a live crawl. Dedicated digest
            // owns the bounded crawl path; normal Chat uses cache/stored text.
            crawl_on_miss: false,
            refresh: false,
          },
          { signal }
        );
        context = mergeCachedBodiesIntoContext(context, resolved?.items || []);
        const hits = Number(resolved?.cache_hits || 0);
        const crawled = Number(resolved?.crawled || 0);
        if (hits > 0 && crawled === 0) {
          setPendingBubble("Đã dùng bản đã đọc");
        } else if (crawled > 0) {
          setPendingBubble("Đang đọc bài…");
        }
      }
    } catch (e) {
      if (isAbortError(e)) throw e;
      // Soft-fail: chat continues with whatever Open Notebook provided.
    }

    context = shrinkNotebookChatContext(context, {
      scoped: isScoped,
      fast,
      question: text,
      maxSources: isScoped ? selected.length : fast ? 6 : 10,
    });
    // Drop FE-only flags before sending to Open Notebook.
    if (Array.isArray(context?.sources)) {
      context = {
        ...context,
        sources: context.sources.map((s) => {
          if (!s || typeof s !== "object") return s;
          const {
            _body_from_cache,
            _body_crawled,
            _body_backend,
            ...rest
          } = s;
          return rest;
        }),
      };
    }
    const meta = {
      token_count: built?.token_count,
      char_count: built?.char_count,
      source_count: Array.isArray(context?.sources) ? context.sources.length : 0,
      scoped: isScoped,
      selected_ids: selected,
      fast,
    };
    onContextMeta?.(meta);
    const payloadMessage = `${NOTEBOOK_ANSWER_STYLE_HINT}${
      fast ? NOTEBOOK_FAST_ANSWER_HINT : ""
    }${notebookScopedStyleHint(srcs, selected)}${text}`;
    return {
      context: context || { sources: [], notes: [] },
      payloadMessage,
      meta,
      contextMs: Math.round(performance.now() - contextStartedAt),
    };
  }

  async function executeOnce(
    sid,
    text,
    modelOverride,
    signal,
    scopeSnap,
    onContextMeta,
    { fast = false, prepared = null } = {}
  ) {
    const execution =
      prepared ||
      (await prepareChatExecution(
        text,
        signal,
        scopeSnap,
        onContextMeta,
        { fast }
      ));
    const executeStartedAt = performance.now();
    try {
      const res = await notebookApi.executeChat(
        {
          session_id: sid,
          message: execution.payloadMessage,
          context: execution.context,
          model_override: modelOverride || undefined,
        },
        { signal }
      );
      const msgs = scrubPipelineWarningsFromMessages(res?.messages || []);
      let replaced = false;
      const sanitizedReversed = [...msgs].reverse().map((message) => {
        const role = message?.type || message?.role;
        if (!replaced && (role === "ai" || role === "assistant")) {
          replaced = true;
          return {
            ...message,
            content: sanitizeNotebookAnswer(message?.content || ""),
          };
        }
        return message;
      });
      const sanitizedMessages = sanitizedReversed.reverse();
      const aiText = lastAiMessageContent(sanitizedMessages);
      const quality = notebookAnswerQualityIssue(aiText);
      if (
        quality === "empty" ||
        quality === "too_short" ||
        quality === "not_vietnamese" ||
        quality === "prompt_leakage" ||
        quality === "numbered_dump"
      ) {
        return {
          ok: false,
          messages: sanitizedMessages,
          rateLimited: true,
          quality,
          error: `answer_${quality}`,
          latencyMs: Math.round(performance.now() - executeStartedAt),
        };
      }
      const grounding = inspectNotebookCitations(aiText, execution.context);
      if (grounding.status === "invalid") {
        return {
          ok: false,
          messages: sanitizedMessages,
          rateLimited: true,
          quality: "invalid_citations",
          grounding,
          error: `invalid_source_ids:${grounding.invalid_ids.join(",")}`,
          latencyMs: Math.round(performance.now() - executeStartedAt),
        };
      }
      const readable = formatNotebookCitations(aiText, execution.context);
      const renderedMessages = sanitizedMessages.map((message) => {
        const role = message?.type || message?.role;
        if (role !== "ai" && role !== "assistant") return message;
        if (String(message?.content || "") !== aiText) return message;
        return { ...message, content: readable.text };
      });
      return {
        ok: true,
        messages: renderedMessages,
        rateLimited: false,
        quality,
        aiText: readable.text,
        grounding,
        latencyMs: Math.round(performance.now() - executeStartedAt),
      };
    } catch (e) {
      if (isAbortError(e)) throw e;
      const msg = errMsg(e);
      if (isRateLimitOrDownMessage(msg) || isTransformFailoverError(e)) {
        return {
          ok: false,
          messages: [],
          rateLimited: true,
          error: msg,
          latencyMs: Math.round(performance.now() - executeStartedAt),
        };
      }
      throw e;
    }
  }

  function mapApiMessages(apiMessages) {
    return scrubPipelineWarningsFromMessages(apiMessages || []).map((m) => ({
      type: m.type === "human" || m.role === "user" ? "human" : "ai",
      content: String(m.content || ""),
      source_url: m.source_url || "",
      source_title: m.source_title || "",
      quotes: Array.isArray(m.quotes) ? m.quotes : [],
    }));
  }

  /** Strip our style hint from displayed human messages. */
  function displayContent(m) {
    if (m.type !== "human") {
      return formatNotebookCitations(m.content, {
        sources: scopeRef.current?.sources || [],
      }).text;
    }
    let c = m.content || "";
    if (c.startsWith(NOTEBOOK_ANSWER_STYLE_HINT)) {
      c = c.slice(NOTEBOOK_ANSWER_STYLE_HINT.length);
    }
    if (c.startsWith(NOTEBOOK_FAST_ANSWER_HINT)) {
      c = c.slice(NOTEBOOK_FAST_ANSWER_HINT.length);
    }
    if (c.startsWith("[Phạm vi BẮT BUỘC:")) {
      const end = c.indexOf("]\n\n");
      if (end >= 0) c = c.slice(end + 3);
    }
    if (c.startsWith("[Trả lời ngắn gọn")) {
      const end = c.indexOf("]\n\n");
      if (end >= 0) c = c.slice(end + 3);
    }
    return c;
  }

  async function maybePolishMessages(
    apiMessages,
    question,
    provider,
    signal,
    { fast = false } = {}
  ) {
    // Interactive output is already constrained by the generation prompt and
    // local sanitizer. A second model call added up to seven seconds and could
    // push an otherwise successful turn beyond 20 seconds.
    return apiMessages;
  }

  async function attemptProvider(
    modelId,
    byId,
    text,
    scopeSnap,
    signal,
    onContextMeta,
    onStatus,
    { fast = false, prepared = null, sessionId = "" } = {}
  ) {
    const primary = modelId || undefined;
    const modelMeta = primary ? byId[primary] : null;
    const provider = providerOfModel(modelMeta);
    const selected = scopeSnap.selectedSourceIds || [];
    const scopedNow = selected.length > 0;
    const label = modelMeta
      ? `${provider || modelMeta.provider}/${modelMeta.name}`
      : primary || "mặc định";
    // Never surface provider/model (esp. ollama) in the pending bubble.
    onStatus?.(CHAT_THINKING_STATUS);
    const sess = sessionId
      ? { id: sessionId }
      : await notebookApi.createChatSession(
          {
            notebook_id: notebookId,
            title: scopedNow
              ? `Chat ${selected.length} nguồn`
              : "Chat toàn notebook",
            model_override: primary,
          },
          { signal }
        );
    if (signal.aborted) {
      const err = new Error("Aborted");
      err.name = "AbortError";
      throw err;
    }
    const attemptAc = new AbortController();
    const unlinkAttempt = linkAbortSignals(signal, attemptAc);
    const timeoutMs =
      provider === "ollama"
        ? NOTEBOOK_CHAT_LOCAL_TIMEOUT_MS
        : Math.min(NOTEBOOK_CHAT_CLOUD_TIMEOUT_MS, fast ? 6_500 : 11_000);
    const attemptTimer = setTimeout(() => {
      try {
        attemptAc.abort("provider_timeout");
      } catch {
        attemptAc.abort();
      }
    }, timeoutMs);
    let result;
    try {
      result = await executeOnce(
        sess.id,
        text,
        primary,
        attemptAc.signal,
        scopeSnap,
        onContextMeta,
        { fast, prepared }
      );
    } catch (error) {
      if (
        attemptAc.signal.reason === "provider_timeout" &&
        !signal.aborted
      ) {
        result = {
          ok: false,
          messages: [],
          rateLimited: true,
          quality: "timeout",
          error: `provider_timeout_${Math.round(timeoutMs / 1000)}s`,
          latencyMs: timeoutMs,
        };
      } else {
        throw error;
      }
    } finally {
      clearTimeout(attemptTimer);
      unlinkAttempt();
    }
    return { result, provider, label, usedOllama: provider === "ollama" };
  }

  /**
   * Race top-2 cloud providers; first good answer wins. If the first finisher
   * is only "too_short", wait briefly for the sibling before accepting short.
   * Fast path races a single healthy model (no parallel double-check).
   */
  async function raceCloudProviders(
    raceIds,
    byId,
    text,
    scopeSnap,
    parentSignal,
    onContextMeta,
    onStatus,
    {
      fast = false,
      prepared = null,
      primarySessionId = "",
      hedgeDelayMs = 0,
    } = {}
  ) {
    if (!raceIds.length) {
      return { ok: false, messages: [], rateLimited: true, error: "", tried: 0 };
    }
    if (raceIds.length === 1) {
      const child = new AbortController();
      const unlink = linkAbortSignals(parentSignal, child);
      try {
        const { result, provider, label, usedOllama } = await attemptProvider(
          raceIds[0],
          byId,
          text,
          scopeSnap,
          child.signal,
          onContextMeta,
          onStatus,
          { fast, prepared, sessionId: primarySessionId }
        );
        return { ...result, provider, label, usedOllama, tried: 1 };
      } finally {
        unlink();
      }
    }

    onStatus?.(CHAT_THINKING_STATUS);
    const controllers = raceIds.map(() => new AbortController());
    const unlinks = controllers.map((c) => linkAbortSignals(parentSignal, c));
    let settled = false;
    let shortHold = null;
    let failures = 0;
    let lastFail = { ok: false, messages: [], rateLimited: true, error: "" };

    const outcome = await new Promise((resolve) => {
      let remaining = raceIds.length;
      raceIds.forEach((modelId, idx) => {
        (async () => {
          try {
            if (idx > 0 && hedgeDelayMs > 0) {
              await waitForChatHedge(hedgeDelayMs, controllers[idx].signal);
              if (settled) return;
            }
            const { result, provider, label, usedOllama } =
              await attemptProvider(
                modelId,
                byId,
                text,
                scopeSnap,
                controllers[idx].signal,
                onContextMeta,
                onStatus,
                {
                  fast,
                  prepared,
                  sessionId: idx === 0 ? primarySessionId : "",
                }
              );
            if (settled) return;
            if (result.ok) {
              settled = true;
              controllers.forEach((c, j) => {
                if (j !== idx) {
                  try {
                    c.abort("race_lost");
                  } catch {
                    /* ignore */
                  }
                }
              });
              resolve({
                ...result,
                provider,
                label,
                usedOllama,
                tried: raceIds.length,
              });
              return;
            }
            if (result.quality === "too_short" || result.quality === "empty") {
              if (
                !shortHold ||
                (result.aiText || "").length > (shortHold.aiText || "").length
              ) {
                shortHold = {
                  ...result,
                  provider,
                  label,
                  usedOllama,
                  tried: raceIds.length,
                };
              }
              if (provider) {
                reportNotebookProviderFailure(
                  provider,
                  result.error || result.quality || "",
                  {
                    signal: parentSignal,
                    latencyMs: result.latencyMs,
                  }
                ).catch(() => {});
              }
            } else if (result.rateLimited) {
              failures += 1;
              lastFail = {
                ...result,
                provider,
                label,
                usedOllama,
                tried: raceIds.length,
              };
              if (provider) {
                reportNotebookProviderFailure(provider, result.error || "", {
                  signal: parentSignal,
                  latencyMs: result.latencyMs,
                }).catch(() => {});
              }
            } else {
              failures += 1;
              lastFail = {
                ...result,
                provider,
                label,
                usedOllama,
                tried: raceIds.length,
              };
            }
          } catch (e) {
            if (!isAbortError(e) && !settled) {
              failures += 1;
              lastFail = {
                ok: false,
                messages: [],
                rateLimited: true,
                error: errMsg(e),
                tried: raceIds.length,
              };
            }
          } finally {
            remaining -= 1;
            if (remaining <= 0 && !settled) {
              resolve(
                shortHold || {
                  ...lastFail,
                  tried: raceIds.length,
                  rateLimited: true,
                }
              );
            }
          }
        })();
      });
    });

    unlinks.forEach((u) => u());
    return outcome;
  }

  async function runChatTurn(text, scopeSnap, hangAc) {
    const turnStartedAt = performance.now();
    const signal = hangAc.signal;
    let usedOllama = false;
    let contextMetaLocal = null;
    const onContextMeta = (meta) => {
      contextMetaLocal = meta;
      setContextMeta(meta);
      persistUi({ contextMeta: meta });
    };
    const onStatus = (status) =>
      setPendingBubble(status || CHAT_THINKING_STATUS);

    // Social/chitchat → single fast Groq reply (no crawl, digest, or cascade).
    if (isSocialChitchatQuery(text)) {
      onStatus(CHAT_SOCIAL_STATUS);
      try {
        const out = await notebookCloudApi.socialChitchat(
          { message: text },
          { signal }
        );
        const answer = String(out?.text || "").trim();
        if (out?.ok && answer) {
          const provider = String(out.provider || "groq");
          const meta = {
            mode: "social_chitchat",
            provider,
            model: out.model || "",
          };
          setContextMeta(meta);
          persistUi({ contextMeta: meta });
          reportNotebookProviderSuccess(provider, { signal }).catch(() => {});
          notebookCloudApi
            .recordChatMetrics({
              mode: "social",
              total_ms: Math.round(performance.now() - turnStartedAt),
              attempts: 1,
              source_count: 0,
            })
            .catch(() => {});
          return {
            result: {
              ok: true,
              messages: [
                { type: "human", content: text },
                { type: "ai", content: answer },
              ],
              aiText: answer,
              provider,
              label: `chitchat/${provider}`,
              usedOllama: false,
              quality: "ok",
              rateLimited: false,
              error: "",
            },
            tried: 1,
            usedProvider: provider,
            usedLabel: `chitchat/${provider}`,
            usedOllama: false,
            contextMetaLocal: meta,
            fast: true,
            social: true,
          };
        }
      } catch (e) {
        if (isAbortError(e)) throw e;
        // Fall through to normal chat if Groq social path fails.
      }
      onStatus(CHAT_THINKING_STATUS);
    }

    // Dedicated crawl+cloud digest for «nội dung chính» / summarize (1–2 sources).
    const digestHit = await tryMainContentDigest(
      text,
      scopeSnap,
      signal,
      onStatus
    );
    if (digestHit?.ok) {
      const meta = {
        mode: "article_digest",
        provider: digestHit.provider,
        fetch: digestHit.fetch || null,
      };
      setContextMeta(meta);
      persistUi({ contextMeta: meta });
      reportNotebookProviderSuccess(
        String(digestHit.provider || "").split("+")[0],
        { signal }
      ).catch(() => {});
      notebookCloudApi
        .recordChatMetrics({
          mode: "digest",
          total_ms: Math.round(performance.now() - turnStartedAt),
          attempts: 1,
          source_count: Number(digestHit?.quotes?.length || 1),
        })
        .catch(() => {});
      return {
        result: digestHit,
        tried: 1,
        usedProvider: digestHit.provider || "digest",
        usedLabel: digestHit.label || "",
        usedOllama: false,
        contextMetaLocal: meta,
        fast: true,
      };
    }

    const cat = catalogRef.current;
    let chain = cat.fallbackChain;
    let byId = cat.fallbackById;
    const fast = isSimpleNotebookChatQuery(text, {
      selectedSourceIds: scopeSnap.selectedSourceIds || [],
    });
    try {
      onStatus(CHAT_THINKING_STATUS);
      const catalog =
        cat.modelsCatalog.length > 0
          ? cat.modelsCatalog
          : Object.values(cat.fallbackById);
      const resolved = await resolveNotebookModelTryOrder(catalog, {
        purpose: "chat",
        profile: fast ? "fast" : "deep",
        // Query complexity, not a previously saved model, decides paid tier.
        preferredId: "",
        signal,
      });
      if (resolved.ids.length) {
        chain = resolved.ids;
        byId = resolved.byId;
        setFallbackChain(chain);
        setFallbackById(byId);
        if (resolved.ids[0]) setFallbackModelId(resolved.ids[0]);
      }
    } catch {
      // keep cached chain
    }
    if (!chain.length) {
      chain = cat.fallbackModelId ? [cat.fallbackModelId] : [undefined];
    }

    onStatus(CHAT_THINKING_STATUS);
    if (fast) {
      onStatus(CHAT_THINKING_STATUS);
    }
    const { raceIds, restIds } = splitChatRaceChain(
      chain.filter(Boolean),
      byId,
      { fast }
    );
    // If chain was [undefined] only, raceIds empty — sequential fallback below.
    let result = { ok: false, messages: [], rateLimited: true, error: "" };
    let tried = 0;
    let usedProvider = "";
    let usedLabel = "";
    let prepared = null;
    let primarySessionId = "";

    if (NOTEBOOK_CHAT_V2_ENABLED) {
      const selected = scopeSnap.selectedSourceIds || [];
      const scopedNow = selected.length > 0;
      const [preparedResult, session] = await Promise.all([
        prepareChatExecution(
          text,
          signal,
          scopeSnap,
          onContextMeta,
          { fast }
        ),
        notebookApi.createChatSession(
          {
            notebook_id: notebookId,
            title: scopedNow
              ? `Chat ${selected.length} nguồn`
              : "Chat toàn notebook",
            model_override: raceIds[0] || chain[0] || undefined,
          },
          { signal }
        ),
      ]);
      prepared = preparedResult;
      primarySessionId = session?.id || "";
    }

    if (raceIds.length) {
      result = await raceCloudProviders(
        raceIds,
        byId,
        text,
        scopeSnap,
        signal,
        onContextMeta,
        onStatus,
        {
          fast,
          prepared,
          primarySessionId,
          hedgeDelayMs:
            NOTEBOOK_CHAT_V2_ENABLED && raceIds.length > 1
              ? NOTEBOOK_CHAT_HEDGE_DELAY_MS
              : 0,
        }
      );
      tried += result.tried || raceIds.length;
      if (result.usedOllama) usedOllama = true;
      if (result.ok) {
        usedProvider = result.provider || "";
        usedLabel = result.label || "";
      }
    }

    // The paid primary plus one hedged fallback are the complete interactive
    // budget. Never append another series of 8-second provider attempts.
    const sequential = result.ok || raceIds.length ? [] : chain.slice(0, 1);

    for (const modelId of sequential) {
      if (signal.aborted) break;
      if (result.ok) break;
      try {
        const attempt = await attemptProvider(
          modelId,
          byId,
          text,
          scopeSnap,
          signal,
          onContextMeta,
          onStatus,
          { fast, prepared, sessionId: primarySessionId }
        );
        tried += 1;
        if (attempt.usedOllama) usedOllama = true;
        result = attempt.result;
        if (result.ok) {
          usedProvider = attempt.provider;
          usedLabel = attempt.label;
          break;
        }
        if (!result.rateLimited) {
          throw new Error(result.error || "chat failed");
        }
        if (attempt.provider) {
          await reportNotebookProviderFailure(
            attempt.provider,
            result.error || "",
            { signal, latencyMs: result.latencyMs }
          );
        }
      } catch (e) {
        if (isAbortError(e)) throw e;
        throw e;
      }
    }

    // PocketFlow-style deterministic final node: once generate/repair providers
    // are exhausted, answer strictly from the already-ranked context.
    if (!result.ok && prepared?.context) {
      let fallbackText = buildDeterministicGroundedAnswer(
        text,
        prepared.context
      );
      if (notebookAnswerQualityIssue(fallbackText) === "not_vietnamese") {
        fallbackText =
          "Hiện chưa thể biên tập nội dung nguồn sang tiếng Việt do các mô hình xử lý đang tạm thời không khả dụng. Vui lòng thử lại sau ít phút.";
      }
      const grounding = inspectNotebookCitations(
        fallbackText,
        prepared.context
      );
      fallbackText = formatNotebookCitations(
        fallbackText,
        prepared.context
      ).text;
      if (fallbackText) {
        result = {
          ok: true,
          messages: [
            { type: "human", content: text },
            { type: "ai", content: fallbackText },
          ],
          aiText: fallbackText,
          provider: "extractive",
          label: "Trích xuất có dẫn nguồn",
          usedOllama: false,
          quality: null,
          rateLimited: false,
          grounding,
          deterministicFallback: true,
          upstreamError: result.error || "",
        };
        usedProvider = "extractive";
        usedLabel = "Trích xuất có dẫn nguồn";
      }
    }

    if (result.ok) {
      if (!fast) onStatus(CHAT_THINKING_STATUS);
      result.messages = await maybePolishMessages(
        result.messages,
        text,
        usedProvider,
        signal,
        { fast }
      );
      if (usedProvider && usedProvider !== "extractive") {
        reportNotebookProviderSuccess(usedProvider, {
          signal,
          latencyMs: result.latencyMs,
        }).catch(() => {});
      }
    }

    notebookCloudApi
      .recordChatMetrics({
        mode: fast ? "fast_grounded" : "deep_grounded",
        total_ms: Math.round(performance.now() - turnStartedAt),
        context_ms: Number(prepared?.contextMs || 0),
        attempts: tried,
        source_count: Number(prepared?.meta?.source_count || 0),
        citation_status: String(result?.grounding?.status || ""),
        citation_coverage: Number(result?.grounding?.coverage || 0),
      })
      .catch(() => {});

    return {
      result,
      tried,
      usedProvider,
      usedLabel,
      usedOllama,
      contextMetaLocal,
      fast,
    };
  }

  /**
   * Crawl selected source URL(s) + cloud summarize via Django (skip Open Notebook
   * first). Returns a chat-shaped result on success / true unreadable.
   * On recoverable cloud/network errors returns null so runChatTurn falls through
   * to grounded normal chat — never leave the user on «Digest cloud lỗi».
   */
  async function tryMainContentDigest(text, scopeSnap, signal, onStatus) {
    const allSources = scopeSnap.sources || [];
    const selected = (scopeSnap.selectedSourceIds || [])
      .map(normalizeNotebookSourceId)
      .filter(Boolean);
    const mainContentIntent = isMainContentDigestQuery(text, {
      selectedSourceIds: selected,
      sourceCount: allSources.length,
    });
    const singleSourceQuestion =
      selected.length === 1 || (selected.length === 0 && allSources.length === 1);
    // A one-source turn never needs the slower Open Notebook session/context
    // orchestration: the digest endpoint accepts the actual question and body,
    // and applies its own basic/deep budget.
    const directSourceAnswer = singleSourceQuestion;
    if (!mainContentIntent && !directSourceAnswer) return null;
    const byId = new Map(
      allSources
        .filter((s) => s?.id)
        .map((s) => [normalizeNotebookSourceId(s.id), s])
    );
    const pool =
      selected.length > 0
        ? selected.map((id) => byId.get(id)).filter(Boolean)
        : allSources;
    const targets = pool
      .map((s) => ({
        id: normalizeNotebookSourceId(s.id),
        title: String(s.title || "").trim(),
        url: sourceArticleUrl(s),
        body: String(s.full_text || s.content || "").trim(),
      }))
      .filter((t) => t.url || t.id || t.body)
      .slice(0, 2);
    if (!targets.length) {
      // Digest intent but no crawlable URL — tell user clearly, do not invent.
      const msg =
        "Nguồn chưa có nội dung có thể đọc. Hãy nạp lại nội dung bài hoặc chọn nguồn khác.";
      return {
        ok: true,
        messages: [
          { type: "human", content: text },
          { type: "ai", content: msg },
        ],
        aiText: msg,
        provider: "digest",
        label: "digest/unreadable",
        usedOllama: false,
        quality: "ok",
        unreadable: true,
        fetch: null,
        source_url: "",
        quotes: [],
        rateLimited: false,
        error: "",
      };
    }

    const primary = targets[0];
    const bodyHint = String(primary.body || "").slice(0, 30000);
    const directFallback = (message = "") => {
      let fallback = String(message || "").trim();
      if (!fallback && isUsableArticleBody(bodyHint)) {
        const fallbackContext = {
          sources: [
            {
              id: primary.id,
              title: primary.title,
              url: primary.url,
              full_text: bodyHint,
            },
          ],
          notes: [],
        };
        const draft = buildDeterministicGroundedAnswer(text, fallbackContext);
        if (notebookAnswerQualityIssue(draft) !== "not_vietnamese") {
          fallback = formatNotebookCitations(draft, fallbackContext).text;
        }
      }
      fallback =
        fallback ||
        "Nguồn đã được chọn nhưng chưa thể hoàn tất phần biên tập. Hãy gửi lại câu hỏi sau vài giây.";
      return {
        ok: true,
        messages: [
          { type: "human", content: text },
          {
            type: "ai",
            content: fallback,
            source_url: primary.url,
            source_title: primary.title,
            quotes: [],
          },
        ],
        aiText: fallback,
        provider: "extractive",
        label: "Trích xuất từ nguồn đã chọn",
        usedOllama: false,
        quality: "ok",
        source_url: primary.url,
        source_title: primary.title,
        quotes: [],
        rateLimited: false,
        error: "",
        deterministicFallback: true,
      };
    };

    // articleDigest checks the same Redis cache before using this stored body
    // or crawling. A separate cache-peek duplicated work on every summary.
    onStatus("Đang đọc bài…");
    if (signal.aborted) {
      const err = new Error("Aborted");
      err.name = "AbortError";
      throw err;
    }
    try {
      const out = await runBoundedChatStep(
        (stepSignal) =>
          notebookCloudApi.articleDigest(
            {
              url: primary.url,
              title: primary.title,
              body: bodyHint,
              question: text,
              // Cloud first; Ollama only as backend last resort after race+sequential.
              allow_ollama: false,
              source_id: primary.id,
              notebook_id: notebookId || "",
              refresh: false,
            },
            { signal: stepSignal }
          ),
        signal,
        10_500
      );
      const answer = String(out?.text || "").trim();
      const unreadable =
        !!out?.unreadable || out?.error === "article_unreadable";
      const cacheHit =
        !!out?.fetch?.cache_hit || out?.fetch?.source_of_body === "cache";
      if (unreadable) {
        const msg =
          answer ||
          "Không đọc được bài gốc từ URL nguồn. Không suy đoán chủ đề từ tiêu đề.";
        onStatus("Không đọc được bài gốc");
        if (directSourceAnswer) return directFallback(msg);
        return {
          ok: true,
          messages: [
            { type: "human", content: text },
            {
              type: "ai",
              content: msg,
              source_url: primary.url,
              source_title: primary.title,
              quotes: [],
            },
          ],
          aiText: msg,
          provider: "digest",
          label: "digest/unreadable",
          usedOllama: false,
          quality: "ok",
          unreadable: true,
          fetch: out?.fetch || null,
          source_url: primary.url,
          source_title: primary.title,
          quotes: [],
          rateLimited: false,
          error: "",
        };
      }
      if (!out?.ok || answer.length < 40) {
        // A one-source question is terminal on this direct path. Never append
        // the slower Open Notebook session pipeline after a digest response.
        if (directSourceAnswer) return directFallback(answer);
        onStatus("Đang dùng chat thường…");
        return null;
      }
      const provider = String(out.provider || "openrouter");
      const label = `digest/${provider}`;
      const quotes = Array.isArray(out.quotes) ? out.quotes : [];
      onStatus(
        cacheHit
          ? "Đã dùng bản đã đọc"
          : out?.extractive
            ? CHAT_THINKING_STATUS
            : CHAT_THINKING_STATUS
      );
      return {
        ok: true,
        messages: [
          { type: "human", content: text },
          {
            type: "ai",
            content: answer,
            source_url: out.source_url || primary.url,
            source_title: primary.title,
            quotes,
          },
        ],
        aiText: answer,
        provider,
        label,
        usedOllama: provider.includes("ollama"),
        quality: "ok",
        fetch: out.fetch || null,
        source_url: out.source_url || primary.url,
        source_title: primary.title,
        quotes,
        rateLimited: false,
        error: "",
      };
    } catch (e) {
      if (signal.aborted) throw e;
      if (directSourceAnswer) return directFallback();
      if (isAbortError(e)) throw e;
      onStatus("Đang dùng chat thường…");
      return null;
    }
  }
  async function send() {
    const text = input.trim();
    if (!text || !notebookId || busy) return;
    const socialTurn = isSocialChitchatQuery(text);
    if (!(sources || []).length && !socialTurn) {
      setError("Notebook chưa có nguồn — thêm nguồn trước khi hỏi.");
      return;
    }
    const existing = getNotebookChatJob(notebookId);
    if (existing?.busy) return;

    const scopeSnap = {
      sources: [...(sources || [])],
      selectedSourceIds: [...(selectedSourceIds || [])],
    };
    const turnScopeKey = notebookChatScopeKey(scopeSnap.selectedSourceIds);
    const pendingStatus = socialTurn ? CHAT_SOCIAL_STATUS : CHAT_THINKING_STATUS;
    const baseMessages = trimChatToLastTurns([
      ...messagesRef.current.filter((m) => !m._pending),
      { type: "human", content: text },
      {
        type: "ai",
        content: "",
        _pending: true,
        _status: pendingStatus,
      },
    ]);
    setError("");
    setUsedModelLabel("");
    setInput("");
    setMessages(baseMessages);
    setBusy(true);
    setPendingStatus(pendingStatus);

    const hangAc = new AbortController();
    const hangTimer = setTimeout(() => {
      try {
        hangAc.abort("hang_timeout");
      } catch {
        hangAc.abort();
      }
    }, CHAT_HANG_TIMEOUT_MS || NOTEBOOK_CHAT_HANG_MS);

    persistUi({
      busy: true,
      messages: baseMessages,
      error: "",
      status: pendingStatus,
      question: text,
      startedAt: Date.now(),
      abortController: hangAc,
      usedModelLabel: "",
      scopeKey: turnScopeKey,
    });

    let usedOllama = false;
    let settledMessages = null;
    try {
      const {
        result,
        tried,
        usedLabel,
        usedOllama: ollamaHit,
      } = await runChatTurn(text, scopeSnap, hangAc);
      usedOllama = ollamaHit;

      if (hangAc.signal.aborted && hangAc.signal.reason === "hang_timeout") {
        throw Object.assign(new Error(CHAT_HANG_ERROR_VI), {
          name: "HangTimeoutError",
        });
      }

      let nextMessages;
      let nextError = "";
      if (result.ok) {
        // Prefer mapped API transcript when it includes the user turn; else stitch locally.
        const mapped = mapApiMessages(result.messages);
        const hasHuman = mapped.some((m) => m.type === "human");
        if (hasHuman && mapped.length >= 2) {
          nextMessages = trimChatToLastTurns(mapped);
        } else {
          const aiText = lastAiMessageContent(result.messages) || result.aiText || "";
          nextMessages = trimChatToLastTurns([
            ...baseMessages.filter((m) => !m._pending),
            {
              type: "ai",
              content: aiText,
              source_url: result.source_url || "",
              source_title: result.source_title || "",
              quotes: Array.isArray(result.quotes) ? result.quotes : [],
            },
          ]);
        }
        // Preserve citation metadata from digest path onto the last AI bubble.
        if (result.source_url || (result.quotes && result.quotes.length)) {
          nextMessages = nextMessages.map((m, idx, arr) => {
            if (idx !== arr.length - 1 || m.type !== "ai") return m;
            return {
              ...m,
              source_url: m.source_url || result.source_url || "",
              source_title: m.source_title || result.source_title || "",
              quotes:
                Array.isArray(m.quotes) && m.quotes.length
                  ? m.quotes
                  : Array.isArray(result.quotes)
                    ? result.quotes
                    : [],
            };
          });
        }
        if (usedLabel) setUsedModelLabel(usedLabel);
        setError("");
      } else {
        nextError =
          hangAc.signal.reason === "hang_timeout"
            ? CHAT_HANG_ERROR_VI
            : tried > 1
              ? `Không có provider khả dụng sau ${tried} lần thử (${CHAT_PROVIDER_ORDER.join(" → ")}). Đợi cooldown hoặc chọn model khác trong Studio.`
              : result.error ||
                "Model không khả dụng. Thử lại sau hoặc chọn model khác trong Studio.";
        nextMessages = baseMessages.filter(
          (m) => !(m.type === "ai" && m._pending && !m.content)
        );
        setError(nextError);
      }
      settledMessages = nextMessages;
      setMessages(nextMessages);
      saveNotebookChatHistory(notebookId, nextMessages, turnScopeKey);
      persistUi({
        busy: false,
        messages: nextMessages,
        error: nextError,
        status: "",
        abortController: null,
        usedModelLabel: usedLabel || "",
      });
    } catch (e) {
      const hung =
        e?.name === "HangTimeoutError" ||
        hangAc.signal.reason === "hang_timeout";
      const cancelled =
        !hung &&
        (hangAc.signal.reason === "cancel" ||
          String(hangAc.signal.reason || "") === "cancel");
      let nextError = "";
      if (hung) {
        nextError = CHAT_HANG_ERROR_VI;
        // Clear congestion: brief cooldown + unload local weights if any.
        reportNotebookProviderFailure("cerebras", "hang_timeout_3m", {
          seconds: 45,
        }).catch(() => {});
        reportNotebookProviderFailure("openrouter", "hang_timeout_3m", {
          seconds: 45,
        }).catch(() => {});
        notebookCloudApi.unloadOllama().catch(() => {});
        usedOllama = false;
      } else if (cancelled) {
        nextError = "Đã hủy yêu cầu chat.";
      } else if (!isAbortError(e)) {
        nextError = errMsg(e);
      }
      let nextMessages = (
        messagesRef.current.length ? messagesRef.current : baseMessages
      ).filter((m) => !(m.type === "ai" && m._pending && !m.content));
      if (hung) {
        const timeoutSources = (scopeSnap.sources || []).filter((source) =>
          isUsableArticleBody(
            String(source?.full_text || source?.content || "").trim()
          )
        );
        let timeoutReply = "";
        if (timeoutSources.length) {
          const timeoutContext = { sources: timeoutSources, notes: [] };
          const draft = buildDeterministicGroundedAnswer(text, timeoutContext);
          const issue = notebookAnswerQualityIssue(draft);
          if (
            draft &&
            issue !== "empty" &&
            issue !== "not_vietnamese" &&
            issue !== "prompt_leakage"
          ) {
            timeoutReply = formatNotebookCitations(draft, timeoutContext).text;
          }
        }
        timeoutReply = timeoutReply || CHAT_HANG_ERROR_VI;
        nextMessages = trimChatToLastTurns([
          ...nextMessages,
          { type: "ai", content: timeoutReply },
        ]);
        // The assistant bubble is the visible watchdog response; avoid leaving
        // a separate red error banner while the transcript is already settled.
        nextError = "";
      }
      settledMessages = nextMessages;
      setMessages(nextMessages);
      if (nextError) setError(nextError);
      saveNotebookChatHistory(notebookId, nextMessages, turnScopeKey);
      persistUi({
        busy: false,
        messages: nextMessages,
        error: nextError,
        status: "",
        abortController: null,
      });
      clearNotebookChatJob(notebookId);
    } finally {
      clearTimeout(hangTimer);
      setBusy(false);
      setPendingStatus("");
      setMessages((prev) => {
        const cleaned = (
          settledMessages ||
          prev.map((m) =>
            m._pending ? { type: "ai", content: m.content || "" } : m
          )
        ).filter(
          (m) =>
            !(
              m.type === "ai" &&
              !String(m.content || "").trim() &&
              !m.source_url &&
              !(Array.isArray(m.quotes) && m.quotes.length)
            )
        );
        saveNotebookChatHistory(notebookId, cleaned, turnScopeKey);
        return cleaned;
      });
      if (usedOllama) {
        notebookCloudApi.unloadOllama().catch(() => {});
      }
      const job = getNotebookChatJob(notebookId);
      if (job) {
        upsertNotebookChatJob(notebookId, {
          busy: false,
          status: "",
          abortController: null,
        });
      }
    }
  }

  function cancelChat() {
    if (!notebookId) return;
    abortNotebookChatJob(notebookId, "cancel");
  }

  if (!notebookId) {
    return (
      <Typography color="text.secondary">
        Chọn hoặc tạo notebook để bắt đầu chat.
      </Typography>
    );
  }

  const scopeLabel = scoped
    ? `Đang hỏi ${selectedSourceIds.length} nguồn đã chọn`
    : `Đang hỏi toàn notebook (${sources?.length || 0} nguồn)`;
  const selectedTitles = scoped
    ? (selectedSourceIds || [])
        .map((id) => {
          const s = (sources || []).find(
            (x) =>
              normalizeNotebookSourceId(x.id) === normalizeNotebookSourceId(id)
          );
          return (s?.title || id || "").trim();
        })
        .filter(Boolean)
    : [];

  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        height: "100%",
        minHeight: 0,
        maxWidth: "100%",
        overflow: "hidden",
      }}
    >
      <Box sx={{ flexShrink: 0, px: { xs: 0.25, sm: 0 } }}>
        <Stack
          direction="row"
          spacing={1}
          alignItems="center"
          flexWrap="wrap"
          sx={{ mb: 1, gap: 0.75 }}
        >
          <Chip
            size={isNarrow ? "medium" : "small"}
            color={scoped ? "warning" : "success"}
            label={scopeLabel}
            variant={scoped ? "filled" : "outlined"}
            sx={{
              height: "auto",
              py: 0.75,
              maxWidth: "100%",
              fontWeight: scoped ? 600 : 400,
              "& .MuiChip-label": {
                whiteSpace: "normal",
                fontSize: { xs: "0.8125rem", sm: "0.75rem" },
                lineHeight: 1.35,
                px: 1,
              },
            }}
          />
          {contextMeta?.source_count != null ? (
            <Typography
              variant="caption"
              color="text.secondary"
              sx={{ fontSize: { xs: "0.75rem", sm: "0.7rem" } }}
            >
              {contextMeta.token_count != null
                ? `~${contextMeta.token_count} token · `
                : ""}
              {contextMeta.source_count} nguồn trong context
              {contextMeta.scoped ? " (đã lọc)" : ""}
            </Typography>
          ) : null}
          {usedModelLabel ? (
            <Typography
              variant="caption"
              color="text.secondary"
              sx={{ fontSize: { xs: "0.75rem", sm: "0.7rem" } }}
            >
              · {usedModelLabel}
            </Typography>
          ) : null}
        </Stack>
        {scoped && selectedTitles.length ? (
          <Typography
            variant="caption"
            color="warning.main"
            sx={{
              mb: 1,
              display: "block",
              lineHeight: 1.45,
              fontSize: { xs: "0.8125rem", sm: "0.75rem" },
            }}
          >
            Phạm vi: {selectedTitles.slice(0, 4).join(" · ")}
            {selectedTitles.length > 4
              ? ` · +${selectedTitles.length - 4} nữa`
              : ""}
          </Typography>
        ) : null}
        <Typography
          variant="caption"
          color="text.secondary"
          sx={{
            mb: 1,
            display: { xs: "none", sm: "block" },
            lineHeight: 1.45,
          }}
        >
          Tích nguồn để hỏi theo phạm vi chọn; bỏ chọn để hỏi toàn notebook.
        </Typography>
        {error ? (
          <Alert severity="error" sx={{ mb: 1 }} onClose={() => setError("")}>
            {error}
          </Alert>
        ) : null}
      </Box>

      {/* Scrollable transcript — flex + minHeight:0 so overflow-y works in nested layout */}
      <Box
        ref={listRef}
        sx={{
          flex: "1 1 auto",
          minHeight: 0,
          overflowY: "auto",
          overflowX: "hidden",
          border: "1px solid",
          borderColor: "divider",
          borderRadius: 1,
          p: { xs: 1.25, sm: 1.5 },
          bgcolor: "background.default",
          WebkitOverflowScrolling: "touch",
          overscrollBehavior: "contain",
          touchAction: "pan-y",
        }}
      >
        {messages.length === 0 ? (
          <Typography
            color="text.secondary"
            variant="body2"
            sx={{ fontSize: { xs: "0.9375rem", sm: "0.875rem" }, lineHeight: 1.5 }}
          >
            {scoped
              ? "Hỏi về (các) nguồn đã chọn — tóm tắt, so sánh, rủi ro…"
              : "Hỏi về toàn bộ notebook — tổng hợp điểm chính từ mọi nguồn…"}
          </Typography>
        ) : (
          messages.map((m, i) => (
            <Box
              key={i}
              sx={{
                mb: 1.5,
                p: { xs: 1.25, sm: 1 },
                borderRadius: 1.5,
                bgcolor:
                  m.type === "human"
                    ? "rgba(76,154,255,0.12)"
                    : "rgba(255,255,255,0.04)",
              }}
            >
              <Typography
                variant="caption"
                color="text.secondary"
                sx={{ display: "block", mb: 0.5, fontSize: { xs: "0.75rem", sm: "0.7rem" } }}
              >
                {m.type === "human" ? "Bạn" : "Trợ lý ảo"}
                {m._pending ? "…" : ""}
              </Typography>
              <Typography
                variant="body2"
                sx={{
                  whiteSpace: "pre-wrap",
                  wordBreak: "break-word",
                  fontSize: { xs: "0.9375rem", sm: "0.875rem" },
                  lineHeight: 1.55,
                }}
              >
                {m._pending && !m.content ? (
                  <Box
                    component="span"
                    sx={{
                      display: "inline-flex",
                      alignItems: "center",
                      gap: 1,
                    }}
                  >
                    <CircularProgress size={14} />{" "}
                    {m._status || pendingStatus || CHAT_THINKING_STATUS}
                  </Box>
                ) : (
                  <>
                    <InlineMarkdown text={displayContent(m)} />
                    {m.type === "ai" &&
                    !m._pending &&
                    (m.source_url || (m.quotes && m.quotes.length)) ? (
                      <Box
                        sx={{
                          mt: 1.25,
                          pt: 1,
                          borderTop: "1px dashed",
                          borderColor: "divider",
                        }}
                      >
                        {m.source_url ? (
                          <Typography
                            variant="caption"
                            component="a"
                            href={m.source_url}
                            target="_blank"
                            rel="noopener noreferrer"
                            sx={{
                              display: "inline-flex",
                              alignItems: "center",
                              gap: 0.5,
                              color: "primary.main",
                              textDecoration: "none",
                              fontWeight: 600,
                              mb: m.quotes?.length ? 0.75 : 0,
                              "&:hover": { textDecoration: "underline" },
                            }}
                          >
                            <OpenInNewIcon sx={{ fontSize: 14 }} />
                            {m.source_title || "Nguồn bài gốc"}
                          </Typography>
                        ) : null}
                        {(m.quotes || []).slice(0, 3).map((q, qi) => {
                          const excerpt = String(q?.text || "").trim();
                          if (!excerpt) return null;
                          const href =
                            q?.href ||
                            buildTextFragmentHref(m.source_url, excerpt) ||
                            m.source_url ||
                            "";
                          return (
                            <Typography
                              key={qi}
                              variant="caption"
                              component="div"
                              sx={{
                                display: "block",
                                mb: 0.5,
                                pl: 1,
                                borderLeft: "2px solid",
                                borderColor: "primary.light",
                                color: "text.secondary",
                                lineHeight: 1.45,
                                fontStyle: "italic",
                              }}
                            >
                              {href ? (
                                <Box
                                  component="a"
                                  href={href}
                                  target="_blank"
                                  rel="noopener noreferrer"
                                  sx={{
                                    color: "inherit",
                                    textDecoration: "none",
                                    "&:hover": { color: "primary.main" },
                                  }}
                                >
                                  “{excerpt}”
                                </Box>
                              ) : (
                                <>“{excerpt}”</>
                              )}
                            </Typography>
                          );
                        })}
                      </Box>
                    ) : null}
                  </>
                )}
              </Typography>
            </Box>
          ))
        )}
        <div ref={bottomRef} />
      </Box>

      {/* Sticky composer at bottom of chat panel; keyboardPad lifts above soft keyboard */}
      <Box
        sx={{
          flexShrink: 0,
          position: "sticky",
          bottom: 0,
          zIndex: 2,
          mt: 1,
          pt: 1,
          bgcolor: "background.paper",
          borderTop: "1px solid",
          borderColor: "divider",
          pb: `max(${keyboardPad}px, env(safe-area-inset-bottom, 0px))`,
        }}
      >
        <Stack
          direction={{ xs: "column", sm: "row" }}
          spacing={1}
          alignItems={{ xs: "stretch", sm: "flex-end" }}
        >
          <TextField
            size="small"
            fullWidth
            multiline
            maxRows={isNarrow ? 5 : 4}
            placeholder={
              scoped
                ? "Câu hỏi về nguồn đã chọn…"
                : "Câu hỏi về toàn notebook…"
            }
            value={input}
            disabled={busy}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                send();
              }
            }}
            inputProps={{
              enterKeyHint: "send",
              "aria-label": "Nội dung câu hỏi",
            }}
            sx={{
              "& .MuiInputBase-root": {
                minHeight: 48,
                fontSize: { xs: "1rem", sm: "0.875rem" },
                alignItems: "flex-start",
              },
            }}
          />
          {busy ? (
            <Button
              variant="outlined"
              color="warning"
              startIcon={<StopIcon />}
              onClick={cancelChat}
              sx={{
                minHeight: 48,
                minWidth: { xs: "100%", sm: 104 },
                flexShrink: 0,
                fontSize: { xs: "1rem", sm: "0.875rem" },
                py: 1.25,
              }}
            >
              Hủy
            </Button>
          ) : (
            <Button
              variant="contained"
              endIcon={<SendIcon />}
              disabled={!input.trim() || !(sources || []).length}
              onClick={send}
              sx={{
                minHeight: 48,
                minWidth: { xs: "100%", sm: 104 },
                flexShrink: 0,
                fontSize: { xs: "1rem", sm: "0.875rem" },
                py: 1.25,
              }}
            >
              Gửi
            </Button>
          )}
        </Stack>
      </Box>
    </Box>
  );
}

/** Session-light expand prefs for Transformation collapsible sections. */
const TRANSFORM_SECTION_STORAGE = {
  input: "nc_nb_ai_tf_input_open",
  result: "nc_nb_ai_tf_result_open",
};

function readTransformSectionOpen(key, defaultOpen = false) {
  try {
    if (typeof sessionStorage === "undefined") return defaultOpen;
    const v = sessionStorage.getItem(key);
    if (v === "1") return true;
    if (v === "0") return false;
  } catch {
    // ignore
  }
  return defaultOpen;
}

function writeTransformSectionOpen(key, open) {
  try {
    if (typeof sessionStorage === "undefined") return;
    sessionStorage.setItem(key, open ? "1" : "0");
  } catch {
    // ignore
  }
}

/**
 * Collapsible block for Transformation tab (preview / input / result).
 * Distinct from panel fullscreen expand (PanelExpandButton).
 */
function TransformCollapsible({
  title,
  open,
  onToggle,
  summary = null,
  children,
  sx = {},
}) {
  return (
    <Box
      sx={{
        border: 1,
        borderColor: "divider",
        borderRadius: 1.5,
        overflow: "hidden",
        flexShrink: 0,
        position: "relative",
        zIndex: 0,
        bgcolor: "background.paper",
        boxShadow: open ? "0 0 0 1px rgba(76,154,255,0.18)" : "none",
        ...sx,
      }}
    >
      <Stack
        direction="row"
        spacing={1}
        alignItems="center"
        justifyContent="space-between"
        onClick={onToggle}
        role="button"
        tabIndex={0}
        aria-expanded={open}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            onToggle();
          }
        }}
        sx={{
          cursor: "pointer",
          px: { xs: 1.25, sm: 1.5 },
          py: { xs: 1.1, sm: 1.15 },
          minHeight: 48,
          bgcolor: open ? "rgba(76,154,255,0.08)" : "rgba(10,18,32,0.55)",
          "&:hover": { bgcolor: "rgba(76,154,255,0.12)" },
          userSelect: "none",
          gap: 1,
        }}
      >
        <Typography
          variant="caption"
          color="text.primary"
          sx={{
            fontWeight: 700,
            lineHeight: 1.45,
            pr: 1,
            flex: 1,
            minWidth: 0,
            fontSize: { xs: "0.875rem", sm: "0.8125rem" },
            WebkitFontSmoothing: "antialiased",
            textRendering: "optimizeLegibility",
          }}
        >
          {title}
        </Typography>
        <Button
          size="small"
          variant={open ? "outlined" : "text"}
          onClick={(e) => {
            e.stopPropagation();
            onToggle();
          }}
          endIcon={open ? <ExpandLessIcon /> : <ExpandMoreIcon />}
          sx={{
            flexShrink: 0,
            minHeight: 40,
            minWidth: 108,
            whiteSpace: "nowrap",
            fontWeight: 700,
            px: 1.25,
          }}
        >
          {open ? "Thu gọn" : "Mở rộng"}
        </Button>
      </Stack>
      {open ? (
        <Box
          sx={{
            px: { xs: 1.25, sm: 1.5 },
            pb: 1.5,
            pt: 0.75,
            borderTop: 1,
            borderColor: "divider",
            maxHeight: { xs: "55vh", sm: "48vh" },
            overflowY: "auto",
            WebkitOverflowScrolling: "touch",
          }}
        >
          {children}
        </Box>
      ) : summary ? (
        <Box sx={{ px: { xs: 1.25, sm: 1.5 }, pb: 1.25 }}>{summary}</Box>
      ) : null}
    </Box>
  );
}

/** Preferred display order for NewsCrawler Vietnamese transform presets. */
const TRANSFORM_NAME_ORDER = [
  "Simple Summary",
  "Key Insights",
  "Table of Contents",
  "Analyze Paper",
  "Reflections",
  "Translate Formal VN",
];

/** Hide retired presets (e.g. Trích yếu hành chính / Dense Summary). */
function isRetiredTransform(t) {
  const name = String(t?.name || "");
  const title = String(t?.title || "");
  return (
    name === "Dense Summary" ||
    /trích yếu hành chính/i.test(title) ||
    /^dense summary$/i.test(title)
  );
}

function sortTransforms(list) {
  const filtered = (list || []).filter((t) => !isRetiredTransform(t));
  const rank = new Map(TRANSFORM_NAME_ORDER.map((n, i) => [n, i]));
  return [...filtered].sort((a, b) => {
    const ra = rank.has(a?.name) ? rank.get(a.name) : 100;
    const rb = rank.has(b?.name) ? rank.get(b.name) : 100;
    if (ra !== rb) return ra - rb;
    return String(a?.title || a?.name || "").localeCompare(
      String(b?.title || b?.name || ""),
      "vi"
    );
  });
}

function TransformPanel({ source, notebookId }) {
  const [transforms, setTransforms] = useState([]);
  const [selected, setSelected] = useState("");
  const [inputText, setInputText] = useState("");
  /** Collapsible sections — default collapsed; prefs in sessionStorage. */
  const [inputOpen, setInputOpen] = useState(() =>
    readTransformSectionOpen(TRANSFORM_SECTION_STORAGE.input, false)
  );
  const [resultOpen, setResultOpen] = useState(() =>
    readTransformSectionOpen(TRANSFORM_SECTION_STORAGE.result, false)
  );
  const [output, setOutput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [modelId, setModelId] = useState("");
  const [languageModels, setLanguageModels] = useState([]);
  const [fallbackChain, setFallbackChain] = useState([]);
  const [fallbackById, setFallbackById] = useState({});
  const [usedModelLabel, setUsedModelLabel] = useState("");
  const [loadingSource, setLoadingSource] = useState(false);
  /** Provider-neutral pending status — never show ollama labels while waiting. */
  const [runStatus, setRunStatus] = useState("");

  const toggleInputOpen = useCallback(() => {
    setInputOpen((v) => {
      const next = !v;
      writeTransformSectionOpen(TRANSFORM_SECTION_STORAGE.input, next);
      return next;
    });
  }, []);
  const toggleResultOpen = useCallback(() => {
    setResultOpen((v) => {
      const next = !v;
      writeTransformSectionOpen(TRANSFORM_SECTION_STORAGE.result, next);
      return next;
    });
  }, []);
  const expandResult = useCallback(() => {
    setResultOpen(true);
    writeTransformSectionOpen(TRANSFORM_SECTION_STORAGE.result, true);
  }, []);

  const selectedTransform = useMemo(
    () => transforms.find((t) => t.id === selected) || null,
    [transforms, selected]
  );

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [t, models, defaults] = await Promise.all([
          notebookApi.listTransformations(),
          notebookApi.listModels(),
          notebookApi.getDefaults(),
        ]);
        if (cancelled) return;
        const list = sortTransforms(Array.isArray(t) ? t : []);
        setTransforms(list);
        if (list[0]) setSelected(list[0].id);
        const lang = (Array.isArray(models) ? models : []).filter(
          isNotebookStudioModel
        );
        setLanguageModels(lang);
        // Prefer healthy/idle fast cloud: OpenRouter → Groq → Cerebras → Ollama
        const resolved = await resolveNotebookModelTryOrder(lang, {
          purpose: "transform",
        });
        if (cancelled) return;
        const ids = resolved.ids.length
          ? resolved.ids
          : pickTransformFallbackChain(lang).ids;
        const byId = Object.keys(resolved.byId || {}).length
          ? resolved.byId
          : pickTransformFallbackChain(lang).byId;
        setFallbackChain(ids);
        setFallbackById(byId);
        // Auto-pick fastest healthy cloud; never park on Ollama/qwen 1.5b
        // when OpenRouter / Groq / Cerebras are in the chain.
        const preferred = pickTransformPreferredModelId(
          ids,
          byId,
          defaults?.default_transformation_model || ""
        );
        setModelId(preferred);
      } catch (e) {
        if (!cancelled) setError(errMsg(e));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      if (!source?.id) {
        setInputText("");
        setLoadingSource(false);
        return;
      }
      setLoadingSource(true);
      setError("");
      try {
        const [full, insights] = await Promise.all([
          notebookApi.getSource(source.id),
          notebookApi.listSourceInsights(source.id).catch(() => []),
        ]);
        if (cancelled) return;
        const title =
          full?.title || source.title || "";
        const url = sourceArticleUrl(full) || sourceArticleUrl(source);
        let bodyText = String(full?.full_text || "").trim();
        // Cache-first: Redis 3h → clean stored. Crawl ONLY when body unusable.
        // Run() sends the input-box text straight to the model (no second crawl).
        try {
          const cleanedExisting = cleanSourcePlainText(bodyText, {
            titleHint: title,
          });
          const titleLen = String(title || "").trim().length;
          // Title-only / title+dek must NOT count as usable — force crawl.
          const looksTitleOnly = textLooksLikeTitleOnly(
            cleanedExisting || bodyText,
            title
          );
          const bodyAlreadyGood =
            !looksTitleOnly &&
            isUsableArticleBody(cleanedExisting || bodyText, {
              minChars: NOTEBOOK_BODY_MIN_CHARS,
            });
          let resolved = await notebookCloudApi.resolveArticleBodies(
            {
              items: [
                {
                  source_id: normalizeNotebookSourceId(source.id),
                  url,
                  notebook_id: notebookId || "",
                  title,
                  existing_text: bodyText,
                },
              ],
              crawl_on_miss: Boolean(url) && !bodyAlreadyGood,
              refresh: false,
            }
          );
          if (cancelled) return;
          let hit = Array.isArray(resolved?.items) ? resolved.items[0] : null;
          let hitText = String(hit?.text || "").trim();
          // Accept crawl/cache hit only when it is more than headline+dek.
          const hitUsable =
            hit?.ok &&
            !textLooksLikeTitleOnly(hitText, title) &&
            hitText.length >= Math.max(NOTEBOOK_BODY_MIN_CHARS, titleLen + 400);
          if (hitUsable) {
            bodyText = hitText;
          } else if (Boolean(url) && !bodyAlreadyGood) {
            // Thin/title-only cache or miss: force a fresh crawl once.
            resolved = await notebookCloudApi.resolveArticleBodies(
              {
                items: [
                  {
                    source_id: normalizeNotebookSourceId(source.id),
                    url,
                    notebook_id: notebookId || "",
                    title,
                    existing_text: bodyText,
                  },
                ],
                crawl_on_miss: true,
                refresh: true,
              }
            );
            if (cancelled) return;
            hit = Array.isArray(resolved?.items) ? resolved.items[0] : null;
            hitText = String(hit?.text || "").trim();
            if (
              hit?.ok &&
              !textLooksLikeTitleOnly(hitText, title) &&
              hitText.length >= Math.max(NOTEBOOK_BODY_MIN_CHARS, titleLen + 400)
            ) {
              bodyText = hitText;
            }
          }
        } catch {
          // Soft-fail: fall back to Open Notebook full_text + FE cleaner.
        }
        const prepared = prepareTransformSourcePayload(
          { ...full, title, full_text: bodyText || title },
          {
            insights: Array.isArray(insights) ? insights : [],
            fallbackTitle: source.title || "",
          }
        );
        if (cancelled) return;
        setInputText(prepared.inputText);
        // After load: keep long input collapsed (declutter).
        setInputOpen(false);
        writeTransformSectionOpen(TRANSFORM_SECTION_STORAGE.input, false);
        setOutput("");
        setUsedModelLabel("");
        setResultOpen(false);
        writeTransformSectionOpen(TRANSFORM_SECTION_STORAGE.result, false);
      } catch (e) {
        if (!cancelled) setError(errMsg(e));
      } finally {
        if (!cancelled) setLoadingSource(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [source?.id, notebookId]);

  async function run() {
    if (!selected || !inputText.trim()) return;
    // Ground only on the cleaned text already in the input box —
    // never re-crawl on Transform run.
    const groundedText = String(inputText || "").trim();
    if (!groundedText) return;
    setBusy(true);
    setError("");
    setOutput("");
    setUsedModelLabel("");
    setRunStatus(TRANSFORM_THINKING_STATUS);
    let chain = fallbackChain;
    let byId = fallbackById;
    try {
      const resolved = await resolveNotebookModelTryOrder(languageModels, {
        purpose: "transform",
        // Prefer cloud even if saved default is Ollama.
        preferredId: pickTransformPreferredModelId(
          fallbackChain,
          fallbackById,
          modelId || ""
        ),
        forceHealth: true,
      });
      if (resolved.ids.length) {
        chain = resolved.ids;
        byId = resolved.byId;
        setFallbackChain(chain);
        setFallbackById(byId);
      }
    } catch {
      // keep cached chain
    }
    const autoPreferred = pickTransformPreferredModelId(
      chain,
      byId,
      modelId || ""
    );
    const tryOrder = buildTransformModelTryOrder(
      chain,
      autoPreferred || "",
      byId
    );
    const maxTokens = transformMaxTokensForPreset(selectedTransform);
    const { raceIds, restIds } = splitTransformRaceChain(tryOrder, byId);
    let lastErr = null;
    let tried = 0;

    const applyWin = (mid, res) => {
      const meta = mid
        ? byId[mid] || languageModels.find((m) => m.id === mid) || null
        : null;
      const provider = providerOfModel(meta);
      const label = meta
        ? `${provider || meta.provider}/${meta.name}`
        : mid || "mặc định hệ thống";
      setOutput(sanitizeStudioOutput(res?.output || ""));
      setUsedModelLabel(label);
      expandResult();
      if (mid && mid !== modelId) setModelId(mid);
      reportNotebookProviderSuccess(provider).catch(() => {});
    };

    const attemptOne = async (mid, signal) =>
      notebookApi.executeTransformation({
        transformation_id: selected,
        input_text: groundedText,
        model_id: mid || undefined,
        max_tokens: maxTokens,
        signal,
      });

    try {
      // Race top-2 distinct cloud providers (staggered) — first good wins.
      if (raceIds.length) {
        setRunStatus(TRANSFORM_THINKING_STATUS);
        const raceOutcome = await new Promise((resolve) => {
          if (raceIds.length === 1) {
            (async () => {
              tried += 1;
              try {
                const res = await attemptOne(raceIds[0]);
                resolve({ ok: true, mid: raceIds[0], res });
              } catch (e) {
                lastErr = e;
                if (!isTransformFailoverError(e)) {
                  resolve({ ok: false, fatal: e });
                  return;
                }
                const meta =
                  byId[raceIds[0]] ||
                  languageModels.find((m) => m.id === raceIds[0]) ||
                  null;
                const provider = providerOfModel(meta);
                if (provider) {
                  reportNotebookProviderFailure(provider, errMsg(e)).catch(
                    () => {}
                  );
                }
                resolve({ ok: false });
              }
            })();
            return;
          }

          const controllers = raceIds.map(() => new AbortController());
          let settled = false;
          let remaining = raceIds.length;

          raceIds.forEach((mid, idx) => {
            const start = () => {
              if (settled) return;
              tried += 1;
              (async () => {
                try {
                  const res = await attemptOne(mid, controllers[idx].signal);
                  if (settled) return;
                  const out = String(res?.output || "").trim();
                  if (out.length >= 24) {
                    settled = true;
                    controllers.forEach((c, j) => {
                      if (j !== idx) {
                        try {
                          c.abort("race_lost");
                        } catch {
                          /* ignore */
                        }
                      }
                    });
                    resolve({ ok: true, mid, res });
                    return;
                  }
                  lastErr = new Error("empty_transform");
                } catch (e) {
                  if (isAbortError(e) || settled) return;
                  lastErr = e;
                  if (!isTransformFailoverError(e)) {
                    settled = true;
                    controllers.forEach((c) => {
                      try {
                        c.abort("fatal");
                      } catch {
                        /* ignore */
                      }
                    });
                    resolve({ ok: false, fatal: e });
                    return;
                  }
                  const meta =
                    byId[mid] ||
                    languageModels.find((m) => m.id === mid) ||
                    null;
                  const provider = providerOfModel(meta);
                  if (provider) {
                    reportNotebookProviderFailure(provider, errMsg(e)).catch(
                      () => {}
                    );
                  }
                } finally {
                  remaining -= 1;
                  if (remaining <= 0 && !settled) {
                    resolve({ ok: false });
                  }
                }
              })();
            };
            // Stagger 2nd starter to avoid same-pool 429 stampede.
            if (idx === 0) start();
            else setTimeout(start, idx * TRANSFORM_RACE_STAGGER_MS);
          });
        });

        if (raceOutcome?.fatal) throw raceOutcome.fatal;
        if (raceOutcome?.ok) {
          applyWin(raceOutcome.mid, raceOutcome.res);
          return;
        }
      }

      // Sequential remainder (other cloud, then Ollama last).
      for (const mid of restIds) {
        tried += 1;
        setRunStatus(TRANSFORM_THINKING_STATUS);
        try {
          const res = await attemptOne(mid);
          applyWin(mid, res);
          return;
        } catch (e) {
          lastErr = e;
          if (!isTransformFailoverError(e)) throw e;
          const meta =
            byId[mid] || languageModels.find((m) => m.id === mid) || null;
          const provider = providerOfModel(meta);
          if (provider) {
            await reportNotebookProviderFailure(provider, errMsg(e));
          }
        }
      }

      // Edge: empty race/rest — try leftover tryOrder sequentially.
      if (!raceIds.length && !restIds.length) {
        for (const mid of tryOrder) {
          tried += 1;
          try {
            const res = await attemptOne(mid);
            applyWin(mid, res);
            return;
          } catch (e) {
            lastErr = e;
            if (!isTransformFailoverError(e)) throw e;
          }
        }
      }

      setError(
        tried > 1
          ? `Không có model khả dụng sau ${tried} lần thử. Đợi cooldown hoặc chọn model khác.`
          : errMsg(lastErr) ||
              "Không chạy được phép biến đổi. Thử lại hoặc đổi model."
      );
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setBusy(false);
      setRunStatus("");
    }
  }

  /**
   * Fullscreen Studio uses a fixed panel at modal+2. Menu must portal to
   * document.body with a higher Modal z-index, and skip scroll-lock (body is
   * already locked while expanded) so dropdowns stay clickable.
   */
  const selectMenuProps = {
    disablePortal: false,
    disableScrollLock: true,
    keepMounted: false,
    slotProps: {
      root: {
        sx: { zIndex: 10000 },
      },
      paper: {
        sx: { maxHeight: 360, zIndex: 10000 },
      },
    },
    PaperProps: {
      sx: { maxHeight: 360 },
    },
    style: { zIndex: 10000 },
  };

  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        gap: { xs: 1.75, sm: 2 },
        // Let parent scroll on Transform tab (incl. fullscreen) so sections don't overlap.
        height: "auto",
        minHeight: "100%",
        overflow: "visible",
        WebkitFontSmoothing: "antialiased",
        textRendering: "optimizeLegibility",
        pb: { xs: 2.5, sm: 2 },
      }}
    >
      {error ? <Alert severity="error">{error}</Alert> : null}
      {busy && runStatus ? (
        <Alert severity="info" icon={<CircularProgress size={16} />}>
          {runStatus}
        </Alert>
      ) : null}
      {!source?.id ? (
        <Alert severity="warning">
          Bấm chọn một nguồn bên trái trước, rồi chọn phép biến đổi + model và
          bấm Chạy.
        </Alert>
      ) : loadingSource ? (
        <Alert severity="info" icon={<CircularProgress size={16} />}>
          Đang tải nguồn…
        </Alert>
      ) : null}
      <Stack
        direction={{ xs: "column", sm: "row" }}
        spacing={1}
        alignItems={{ xs: "stretch", sm: "flex-start" }}
        sx={{
          position: "sticky",
          top: 0,
          zIndex: 20,
          py: 1,
          px: 0.25,
          mx: -0.25,
          bgcolor: "background.paper",
          borderBottom: 1,
          borderColor: "divider",
          flexShrink: 0,
          overflow: "visible",
          pointerEvents: "auto",
        }}
      >
        <FormControl size="small" fullWidth sx={{ overflow: "visible" }}>
          <InputLabel id="studio-transform-label">Phép biến đổi</InputLabel>
          <Select
            labelId="studio-transform-label"
            label="Phép biến đổi"
            value={selected}
            onChange={(e) => setSelected(e.target.value)}
            MenuProps={selectMenuProps}
            sx={{ minHeight: 44 }}
          >
            {transforms.map((t) => (
              <MenuItem key={t.id} value={t.id}>
                {t.title || t.name}
              </MenuItem>
            ))}
          </Select>
        </FormControl>
        <FormControl size="small" fullWidth sx={{ overflow: "visible" }}>
          <InputLabel id="studio-model-label">Model</InputLabel>
          <Select
            labelId="studio-model-label"
            label="Model"
            value={modelId}
            onChange={(e) => setModelId(e.target.value)}
            MenuProps={selectMenuProps}
            sx={{ minHeight: 44 }}
          >
            <MenuItem value="">
              <em>Tự chọn</em>
            </MenuItem>
            {languageModels.map((m) => (
              <MenuItem key={m.id} value={m.id}>
                {notebookProviderOfModel(m)}/{m.name}
              </MenuItem>
            ))}
          </Select>
        </FormControl>
        <Button
          variant="contained"
          disabled={busy || !selected || !inputText.trim()}
          onClick={run}
          sx={{
            whiteSpace: "nowrap",
            minWidth: { xs: "100%", sm: 120 },
            minHeight: 48,
            flexShrink: 0,
          }}
        >
          {busy ? runStatus || TRANSFORM_THINKING_STATUS : "Chạy"}
        </Button>
      </Stack>
      {selectedTransform?.description ? (
        <Typography
          variant="caption"
          color="text.secondary"
          sx={{ lineHeight: 1.45, display: "block" }}
        >
          {selectedTransform.description}
        </Typography>
      ) : null}
      <TransformCollapsible
        title="Nội dung gửi biến đổi — văn bản sạch (có thể chỉnh trước khi chạy)"
        open={inputOpen}
        onToggle={toggleInputOpen}
        summary={
          <Typography variant="body2" color="text.secondary" sx={{ fontSize: "0.8125rem" }}>
            {inputText.trim()
              ? `${inputText.trim().length.toLocaleString("vi-VN")} ký tự · bấm «Mở rộng» để chỉnh`
              : "Chưa có nội dung"}
          </Typography>
        }
      >
        <TextField
          label={
            source?.title
              ? `Văn bản sạch gửi biến đổi — ${source.title}`
              : "Văn bản sạch gửi biến đổi (focus hoặc dán tay)"
          }
          multiline
          minRows={4}
          maxRows={12}
          value={inputText}
          onChange={(e) => setInputText(e.target.value)}
          fullWidth
          helperText="Kết quả chỉ dựa trên ô này (đã bỏ HTML/markdown). Output bắt buộc tiếng Việt hành chính–quân sự."
          sx={{
            "& .MuiInputBase-root": { fontSize: { xs: "1rem", sm: "0.875rem" } },
          }}
        />
      </TransformCollapsible>
      <TransformCollapsible
        title={
          usedModelLabel
            ? `Kết quả tiếng Việt (${usedModelLabel})`
            : "Kết quả biến đổi (tiếng Việt hành chính–quân sự)"
        }
        open={resultOpen}
        onToggle={toggleResultOpen}
        summary={
          <Typography variant="body2" color="text.secondary" sx={{ fontSize: "0.8125rem" }}>
            {busy
              ? `${runStatus || TRANSFORM_THINKING_STATUS} kết quả sẽ mở khi xong`
              : output.trim()
                ? `${output.trim().length.toLocaleString("vi-VN")} ký tự · bấm «Mở rộng» để xem`
                : "Chưa có kết quả — bấm Chạy rồi xem tại đây"}
          </Typography>
        }
      >
        <TextField
          label={
            usedModelLabel
              ? `Kết quả tiếng Việt (${usedModelLabel})`
              : "Kết quả biến đổi (tiếng Việt hành chính–quân sự)"
          }
          multiline
          minRows={4}
          maxRows={14}
          value={output}
          InputProps={{ readOnly: true }}
          fullWidth
          placeholder="Kết quả có cấu trúc sẽ hiện ở đây — luôn tiếng Việt, bám sát nguồn."
          sx={{
            "& .MuiInputBase-root": { fontSize: { xs: "1rem", sm: "0.875rem" } },
          }}
        />
      </TransformCollapsible>
    </Box>
  );
}

export default function NotebookAIPage() {
  const theme = useTheme();
  const isMobile = useMediaQuery(theme.breakpoints.down("sm"));
  const isMdUp = useMediaQuery(theme.breakpoints.up("md"));
  const [searchParams, setSearchParams] = useSearchParams();
  const notebookParam = searchParams.get("notebook") || "";

  const [notebooks, setNotebooks] = useState([]);
  const [notebookId, setNotebookId] = useState(notebookParam);
  const [sources, setSources] = useState([]);
  /** Focus source for Studio (click a source row). */
  const [focusSourceId, setFocusSourceId] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [info, setInfo] = useState("");
  const [createOpen, setCreateOpen] = useState(false);
  const [addSourceOpen, setAddSourceOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  /** Resizable sources panel: width (md+) / height (mobile). */
  const [sourcesPanelWidth, setSourcesPanelWidth] = useState(SOURCES_W_DEFAULT);
  const [sourcesPanelHeight, setSourcesPanelHeight] = useState(SOURCES_H_DEFAULT);
  const [titlesExpanded, setTitlesExpanded] = useState(false);
  /** null | 'sources' | 'transform' — one panel fills the viewport. */
  const [expandedPanel, setExpandedPanel] = useState(null);
  const resizeDragRef = useRef(null);

  const focusSource = useMemo(() => {
    const fid = normalizeNotebookSourceId(focusSourceId);
    if (!fid) return null;
    return (
      sources.find(
        (s) => normalizeNotebookSourceId(s.id) === fid
      ) || null
    );
  }, [sources, focusSourceId]);
  const selectedNotebook = useMemo(
    () => notebooks.find((n) => n.id === notebookId) || null,
    [notebooks, notebookId]
  );

  const loadNotebooks = useCallback(async () => {
    const list = await notebookApi.listNotebooks(false);
    setNotebooks(Array.isArray(list) ? list : []);
    return Array.isArray(list) ? list : [];
  }, []);

  const loadSources = useCallback(async (nbId) => {
    const list = await notebookApi.listSources({
      notebookId: nbId || undefined,
      limit: 80,
    });
    const items = Array.isArray(list) ? list : [];
    setSources(items);
    return items;
  }, []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      setError("");
      try {
        const list = await loadNotebooks();
        if (cancelled) return;
        let nb = notebookParam;
        if (!nb && list[0]) nb = list[0].id;
        if (nb) {
          setNotebookId(nb);
          const srcs = await loadSources(nb);
          // Focus the first source for Studio.
          if (!cancelled) {
            setFocusSourceId(srcs[0]?.id || "");
          }
        } else {
          setSources([]);
          setFocusSourceId("");
        }
      } catch (e) {
        if (!cancelled) setError(errMsg(e));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [loadNotebooks, loadSources, notebookParam]);

  async function onNotebookChange(id) {
    setNotebookId(id);
    setSearchParams(id ? { notebook: id } : {});
    setFocusSourceId("");
    setError("");
    setInfo("");
    try {
      const srcs = await loadSources(id || undefined);
      setFocusSourceId(srcs[0]?.id || "");
    } catch (e) {
      setError(errMsg(e));
    }
  }

  async function handleCreated(nb) {
    setInfo(`Đã tạo notebook «${nb.name}».`);
    await loadNotebooks();
    await onNotebookChange(nb.id);
  }

  async function handleDeleted(nb) {
    setInfo(`Đã xóa notebook «${nb?.name || ""}».`);
    setDeleteOpen(false);
    const list = await loadNotebooks();
    const next = list[0]?.id || "";
    await onNotebookChange(next);
  }

  async function handleSourceAdded(src) {
    setInfo(`Đã thêm nguồn «${src.title || src.id}» (đang xử lý nền).`);
    const srcs = await loadSources(notebookId);
    if (src?.id) setFocusSourceId(src.id);
    else if (srcs[0]) setFocusSourceId(srcs[0].id);
  }

  const onSourcesResizeStart = useCallback(
    (axis) => (e) => {
      e.preventDefault();
      e.stopPropagation();
      const pointerId = e.pointerId;
      const target = e.currentTarget;
      try {
        target.setPointerCapture(pointerId);
      } catch {
        /* ignore */
      }
      resizeDragRef.current = {
        axis,
        startX: e.clientX,
        startY: e.clientY,
        startW: sourcesPanelWidth,
        startH: sourcesPanelHeight,
      };
      const onMove = (ev) => {
        const drag = resizeDragRef.current;
        if (!drag) return;
        if (drag.axis === "x") {
          setSourcesPanelWidth(
            clamp(drag.startW + (ev.clientX - drag.startX), SOURCES_W_MIN, SOURCES_W_MAX)
          );
        } else {
          setSourcesPanelHeight(
            clamp(drag.startH + (ev.clientY - drag.startY), SOURCES_H_MIN, SOURCES_H_MAX)
          );
        }
      };
      const onUp = () => {
        resizeDragRef.current = null;
        window.removeEventListener("pointermove", onMove);
        window.removeEventListener("pointerup", onUp);
        window.removeEventListener("pointercancel", onUp);
        try {
          target.releasePointerCapture(pointerId);
        } catch {
          /* ignore */
        }
      };
      window.addEventListener("pointermove", onMove);
      window.addEventListener("pointerup", onUp);
      window.addEventListener("pointercancel", onUp);
    },
    [sourcesPanelWidth, sourcesPanelHeight]
  );

  const anyExpanded = Boolean(expandedPanel);

  const toggleExpand = useCallback((panel) => {
    setExpandedPanel((cur) => (cur === panel ? null : panel));
  }, []);

  // Esc exits fullscreen panel; lock body scroll while expanded.
  // Ignore Esc while a Select/Menu listbox is open so dropdowns close first.
  useEffect(() => {
    if (!expandedPanel) return undefined;
    const onKey = (e) => {
      if (e.key !== "Escape") return;
      if (
        document.querySelector(
          '.MuiModal-root[aria-hidden="false"], .MuiMenu-root, [role="listbox"]'
        )
      ) {
        return;
      }
      e.preventDefault();
      setExpandedPanel(null);
    };
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    window.addEventListener("keydown", onKey);
    return () => {
      document.body.style.overflow = prevOverflow;
      window.removeEventListener("keydown", onKey);
    };
  }, [expandedPanel]);

  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        // Laptop: fill viewport under AppBar; mobile: page scrolls, chat panel has its own height.
        height: { xs: "auto", md: "calc(100dvh - 72px)" },
        minHeight: { xs: "calc(100dvh - 56px)", md: 0 },
        maxWidth: "100%",
        overflowX: "hidden",
        overflowY: { xs: "visible", md: "hidden" },
        px: { xs: 0, sm: 0 },
        pb: {
          xs: "max(12px, env(safe-area-inset-bottom, 0px))",
          md: 0,
        },
        // When a panel is expanded fullscreen, hide page chrome underneath.
        ...(anyExpanded
          ? {
              height: "100dvh",
              minHeight: "100dvh",
              overflow: "hidden",
              pb: 0,
            }
          : {}),
      }}
    >
      <Box
        sx={{
          flexShrink: 0,
          display: anyExpanded ? "none" : "block",
        }}
      >
        <PageHeader
          title="Phân tích sâu"
          subtitle={
            isMobile
              ? "Notebook · Nguồn · Studio."
              : "Tạo notebook, quản lý nguồn và biến đổi nội dung trong Studio."
          }
        />
      </Box>

      <Box
        sx={{
          flexShrink: 0,
          display: anyExpanded ? "none" : "block",
        }}
      >
        {error ? (
          <Alert severity="error" sx={{ mb: 1.5 }}>
            {error}
          </Alert>
        ) : null}
        {info ? (
          <Alert severity="success" sx={{ mb: 1.5 }} onClose={() => setInfo("")}>
            {info}
          </Alert>
        ) : null}

        <Stack
          direction={{ xs: "column", sm: "row" }}
          spacing={1}
          sx={{ mb: 1.5, flexShrink: 0 }}
          alignItems={{ xs: "stretch", sm: "center" }}
        >
          <FormControl size="small" sx={{ minWidth: 0, flex: 1, width: "100%" }}>
            <InputLabel>Notebook</InputLabel>
            <Select
              label="Notebook"
              value={notebookId}
              onChange={(e) => onNotebookChange(e.target.value)}
              disabled={loading}
              sx={{ minHeight: 48 }}
            >
              {notebooks.map((nb) => (
                <MenuItem key={nb.id} value={nb.id}>
                  {nb.name} ({nb.source_count ?? 0})
                </MenuItem>
              ))}
            </Select>
          </FormControl>
          <Stack
            direction="row"
            spacing={1}
            flexWrap="wrap"
            useFlexGap
            sx={{ width: { xs: "100%", sm: "auto" } }}
          >
            <Button
              size="small"
              variant="contained"
              startIcon={<NoteAddIcon />}
              onClick={() => setCreateOpen(true)}
              sx={{ minHeight: 48, flex: { xs: 1, sm: "none" }, px: 1.5 }}
            >
              Notebook mới
            </Button>
            <Button
              size="small"
              variant="outlined"
              startIcon={<AddIcon />}
              disabled={!notebookId}
              onClick={() => setAddSourceOpen(true)}
              sx={{ minHeight: 48, flex: { xs: 1, sm: "none" }, px: 1.5 }}
            >
              Thêm nguồn
            </Button>
            <Button
              size="small"
              variant="outlined"
              color="error"
              startIcon={<DeleteOutlineIcon />}
              disabled={!notebookId}
              onClick={() => setDeleteOpen(true)}
              sx={{ minHeight: 48, flex: { xs: 1, sm: "none" }, px: 1.5 }}
            >
              Xóa
            </Button>
          </Stack>
        </Stack>

      </Box>

      {loading ? (
        <Box sx={{ display: "flex", justifyContent: "center", py: 6 }}>
          <CircularProgress />
        </Box>
      ) : (
        <Box
          sx={{
            flex: { xs: anyExpanded ? 1 : "none", md: 1 },
            minHeight: { xs: "auto", md: 0 },
            display: "grid",
            // Stack sources → chat on phone; sources | resize | chat from md up.
            // When expanded, single full-bleed cell.
            gridTemplateColumns: anyExpanded
              ? "minmax(0, 1fr)"
              : {
                  xs: "minmax(0, 1fr)",
                  md: `${sourcesPanelWidth}px 6px minmax(0, 1fr)`,
                },
            gridTemplateRows: anyExpanded
              ? "minmax(0, 1fr)"
              : { xs: "auto auto", md: "minmax(0, 1fr)" },
            gap: { xs: 1.25, md: 0 },
            width: "100%",
            maxWidth: "100%",
            ...(anyExpanded
              ? { height: "100%", minHeight: 0, flex: 1 }
              : {}),
          }}
        >
          <Box
            sx={{
              border: "1px solid",
              borderColor: "divider",
              borderRadius: { xs: 1, md: "4px 0 0 4px" },
              overflow: "hidden",
              height: { xs: sourcesPanelHeight, md: "100%" },
              maxHeight: { xs: sourcesPanelHeight, md: "100%" },
              minHeight: { xs: SOURCES_H_MIN, md: 0 },
              minWidth: 0,
              display:
                expandedPanel && expandedPanel !== "sources" ? "none" : "flex",
              flexDirection: "column",
              WebkitOverflowScrolling: "touch",
              overscrollBehavior: "contain",
              ...(expandedPanel === "sources" ? PANEL_EXPAND_SX : {}),
            }}
          >
            <Stack
              direction="row"
              alignItems="center"
              justifyContent="space-between"
              sx={{
                px: 1.5,
                pt: 1.25,
                pb: 0.75,
                gap: 1,
                flexWrap: "wrap",
                flexShrink: 0,
                position: "sticky",
                top: 0,
                zIndex: 10,
                bgcolor: "background.paper",
                borderBottom: 1,
                borderColor: "divider",
                minHeight: 56,
                overflow: "visible",
              }}
            >
              <Typography
                variant="caption"
                color="text.primary"
                sx={{
                  fontSize: { xs: "0.875rem", sm: "0.8125rem" },
                  fontWeight: 700,
                  WebkitFontSmoothing: "antialiased",
                  minWidth: 0,
                }}
              >
                Nguồn ({sources.length})
                {expandedPanel === "sources" ? " — toàn màn hình" : ""}
              </Typography>
              <Stack
                direction="row"
                spacing={0.5}
                flexWrap="wrap"
                useFlexGap
                alignItems="center"
                sx={{ flexShrink: 0, overflow: "visible" }}
              >
                <PanelExpandButton
                  expanded={expandedPanel === "sources"}
                  onToggle={() => toggleExpand("sources")}
                  expandLabel="Mở rộng danh sách nguồn"
                  collapseLabel="Thu nhỏ"
                />
                <Tooltip
                  title={
                    titlesExpanded
                      ? "Thu gọn tiêu đề"
                      : "Hiện đủ tiêu đề (không cắt)"
                  }
                >
                  <IconButton
                    size="small"
                    aria-label={
                      titlesExpanded ? "Thu gọn tiêu đề" : "Hiện đủ tiêu đề"
                    }
                    onClick={() => setTitlesExpanded((v) => !v)}
                    sx={{ minWidth: 40, minHeight: 40 }}
                  >
                    {titlesExpanded ? (
                      <UnfoldLessIcon fontSize="small" />
                    ) : (
                      <UnfoldMoreIcon fontSize="small" />
                    )}
                  </IconButton>
                </Tooltip>
                <Button
                  size="small"
                  startIcon={<LinkIcon />}
                  disabled={!notebookId}
                  onClick={() => setAddSourceOpen(true)}
                  sx={{ minHeight: 40 }}
                >
                  Thêm
                </Button>
              </Stack>
            </Stack>
            <Typography
              variant="caption"
              color="text.secondary"
              sx={{
                px: 1.5,
                display: "block",
                mb: 0.5,
                lineHeight: 1.45,
                fontSize: { xs: "0.8125rem", sm: "0.75rem" },
                flexShrink: 0,
                fontWeight: 400,
              }}
            >
              Chọn một nguồn để xem và xử lý trong Studio
              {!anyExpanded ? (
                <>
                  {" · "}
                  {isMdUp ? "kéo mép phải để co giãn" : "kéo thanh dưới để co giãn"}
                </>
              ) : (
                " · Esc để thu nhỏ"
              )}
            </Typography>
            <List
              dense
              disablePadding
              sx={{
                flex: 1,
                minHeight: 0,
                overflowY: "auto",
                overflowX: "hidden",
                touchAction: "pan-y",
                pb: { xs: 0.5, md: 0 },
              }}
            >
              {sources.map((s) => {
                const focused =
                  normalizeNotebookSourceId(s.id) ===
                  normalizeNotebookSourceId(focusSourceId);
                const articleUrl = sourceArticleUrl(s);
                const titleText = s.title || s.id;
                return (
                  <ListItemButton
                    key={s.id}
                    selected={focused}
                    onClick={() => setFocusSourceId(s.id)}
                    sx={{
                      alignItems: "flex-start",
                      py: 1.25,
                      minHeight: 52,
                      px: 1,
                      pr: articleUrl ? 0.5 : 1,
                      ...(!titlesExpanded
                        ? {
                            "&:not(:hover):not(:focus-within) .nb-source-title": {
                              display: "-webkit-box",
                              WebkitLineClamp: 2,
                              WebkitBoxOrient: "vertical",
                              overflow: "hidden",
                            },
                          }
                        : {}),
                    }}
                  >
                    <ListItemText
                      primary={
                        articleUrl ? (
                          <Box
                            component="a"
                            href={articleUrl}
                            target="_blank"
                            rel="noopener noreferrer"
                            title="Mở bài viết"
                            onClick={(e) => e.stopPropagation()}
                            sx={{
                              color: "inherit",
                              textDecoration: "none",
                              "&:hover": {
                                color: "primary.main",
                                textDecoration: "underline",
                              },
                              "&:focus-visible": {
                                outline: "2px solid",
                                outlineColor: "primary.main",
                                outlineOffset: 2,
                              },
                            }}
                          >
                            {titleText}
                          </Box>
                        ) : (
                          titleText
                        )
                      }
                      secondary={s.status || undefined}
                      primaryTypographyProps={{
                        variant: "body2",
                        component: "div",
                        className: "nb-source-title",
                        title: titleText,
                        sx: {
                          wordBreak: "break-word",
                          whiteSpace: "normal",
                          lineHeight: 1.4,
                          fontSize: { xs: "0.9375rem", sm: "0.875rem" },
                        },
                      }}
                      secondaryTypographyProps={{ variant: "caption" }}
                    />
                    {articleUrl ? (
                      <Tooltip title="Mở bài viết">
                        <IconButton
                          component="a"
                          href={articleUrl}
                          target="_blank"
                          rel="noopener noreferrer"
                          size="small"
                          aria-label={`Mở bài viết: ${titleText}`}
                          onClick={(e) => e.stopPropagation()}
                          sx={{
                            mt: 0.1,
                            flexShrink: 0,
                            minWidth: 40,
                            minHeight: 40,
                          }}
                        >
                          <OpenInNewIcon fontSize="small" />
                        </IconButton>
                      </Tooltip>
                    ) : null}
                  </ListItemButton>
                );
              })}
              {sources.length === 0 ? (
                <Typography
                  variant="body2"
                  color="text.secondary"
                  sx={{ p: 2, fontSize: { xs: "0.9375rem", sm: "0.875rem" } }}
                >
                  Chưa có nguồn. Bấm «Thêm nguồn» hoặc xuất từ Báo cáo nhanh.
                </Typography>
              ) : null}
            </List>
            {!isMdUp && expandedPanel !== "sources" ? (
              <Box
                role="separator"
                aria-orientation="horizontal"
                aria-label="Co giãn khung nguồn"
                onPointerDown={onSourcesResizeStart("y")}
                sx={{
                  flexShrink: 0,
                  height: 12,
                  cursor: "row-resize",
                  touchAction: "none",
                  bgcolor: "action.hover",
                  borderTop: "1px solid",
                  borderColor: "divider",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  "&:hover": { bgcolor: "action.selected" },
                  "&::after": {
                    content: '""',
                    width: 40,
                    height: 3,
                    borderRadius: 1,
                    bgcolor: "text.disabled",
                  },
                }}
              />
            ) : null}
          </Box>

          {isMdUp && !anyExpanded ? (
            <Box
              role="separator"
              aria-orientation="vertical"
              aria-label="Co giãn khung nguồn"
              onPointerDown={onSourcesResizeStart("x")}
              sx={{
                cursor: "col-resize",
                touchAction: "none",
                bgcolor: "divider",
                alignSelf: "stretch",
                position: "relative",
                zIndex: 1,
                "&:hover": { bgcolor: "primary.main", opacity: 0.45 },
                "&:active": { bgcolor: "primary.main", opacity: 0.7 },
              }}
            />
          ) : null}

          <Box
            sx={{
              border: "1px solid",
              borderColor: "divider",
              borderRadius: { xs: 1, md: "0 4px 4px 0" },
              borderLeft: { md: anyExpanded ? undefined : "none" },
              p: { xs: 1.25, sm: 1.5 },
              // Explicit height on mobile so nested flex + overflow-y:auto works.
              height: {
                xs: "min(72dvh, calc(100dvh - 160px))",
                md: "100%",
              },
              minHeight: { xs: 360, md: 0 },
              minWidth: 0,
              maxWidth: "100%",
              overflow: "hidden",
              display:
                expandedPanel === "sources"
                  ? "none"
                  : "flex",
              flexDirection: "column",
              ...(expandedPanel === "transform" ? PANEL_EXPAND_SX : {}),
            }}
          >
            <Stack
              direction="row"
              alignItems="center"
              justifyContent="space-between"
              sx={{
                mb: 1,
                flexShrink: 0,
                gap: 1,
                position: "sticky",
                top: 0,
                zIndex: 10,
                py: 0.75,
                px: 0.5,
                bgcolor: "background.paper",
                borderBottom: 1,
                borderColor: "divider",
                minHeight: 56,
                overflow: "visible",
              }}
            >
              <Typography
                variant="caption"
                color="text.primary"
                sx={{
                  fontSize: { xs: "0.875rem", sm: "0.8125rem" },
                  fontWeight: 700,
                  WebkitFontSmoothing: "antialiased",
                  minWidth: 0,
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                }}
              >
                Studio
                {expandedPanel === "transform" ? " — toàn màn hình" : ""}
              </Typography>
              <Box sx={{ flexShrink: 0, overflow: "visible" }}>
                <PanelExpandButton
                  expanded={expandedPanel === "transform"}
                  onToggle={() => toggleExpand("transform")}
                  expandLabel="Phóng to Studio"
                  collapseLabel="Thu nhỏ"
                />
              </Box>
            </Stack>
            <Box
              sx={{
                flex: 1,
                minHeight: 0,
                display: "flex",
                flexDirection: "column",
                overflow: "auto",
                WebkitOverflowScrolling: "touch",
              }}
            >
              <TransformPanel
                key={focusSourceId || "no-focus"}
                source={focusSource}
                notebookId={notebookId}
              />
            </Box>
          </Box>
        </Box>
      )}

      <Divider
        sx={{
          my: 1.5,
          flexShrink: 0,
          display: { xs: "none", md: anyExpanded ? "none" : "block" },
        }}
      />

      <CreateNotebookDialog
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onCreated={handleCreated}
      />
      <DeleteNotebookDialog
        open={deleteOpen}
        onClose={() => setDeleteOpen(false)}
        notebook={selectedNotebook}
        onDeleted={handleDeleted}
      />
      <AddSourceDialog
        open={addSourceOpen}
        onClose={() => setAddSourceOpen(false)}
        notebookId={notebookId}
        onAdded={handleSourceAdded}
      />
    </Box>
  );
}
