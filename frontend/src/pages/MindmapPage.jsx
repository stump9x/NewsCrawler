import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Divider,
  FormControlLabel,
  IconButton,
  InputAdornment,
  MenuItem,
  Paper,
  Portal,
  Select,
  Stack,
  Switch,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import AccountTreeOutlinedIcon from "@mui/icons-material/AccountTreeOutlined";
import ArrowBackOutlinedIcon from "@mui/icons-material/ArrowBackOutlined";
import AutoAwesomeOutlinedIcon from "@mui/icons-material/AutoAwesomeOutlined";
import CenterFocusStrongOutlinedIcon from "@mui/icons-material/CenterFocusStrongOutlined";
import FullscreenOutlinedIcon from "@mui/icons-material/FullscreenOutlined";
import FullscreenExitOutlinedIcon from "@mui/icons-material/FullscreenExitOutlined";
import LaunchOutlinedIcon from "@mui/icons-material/LaunchOutlined";
import AddOutlinedIcon from "@mui/icons-material/AddOutlined";
import RemoveOutlinedIcon from "@mui/icons-material/RemoveOutlined";
import SearchOutlinedIcon from "@mui/icons-material/SearchOutlined";
import { analyzeThreatMindmap, getThreatMindmap } from "../api/client";

const WIDTH = 1800;
const HEIGHT = 1000;
const CENTER = { x: 900, y: 500 };

const TYPE_STYLE = {
  same_event: { color: "#64d8ff", label: "Cùng sự kiện" },
  same_country: { color: "#44c7a1", label: "Cùng quốc gia" },
  same_capability: { color: "#ffb74d", label: "Cùng năng lực/vũ khí" },
  same_topic: { color: "#7ca9ff", label: "Cùng chủ đề" },
  related_event: { color: "#b68cff", label: "Liên quan về nội dung" },
  cause_effect: { color: "#ff7f7f", label: "Nguyên nhân – hệ quả" },
  response: { color: "#ff6ea8", label: "Phản ứng/điều chỉnh" },
  cooperation: { color: "#4dd9d0", label: "Hợp tác/liên minh" },
  competition: { color: "#ff985e", label: "Cạnh tranh/đối trọng" },
  ai_related: { color: "#d28cff", label: "AI xác định" },
};

function shorten(text, max = 52) {
  const value = String(text || "").trim();
  return value.length > max ? `${value.slice(0, max - 1)}…` : value;
}

function graphLayout(nodes, edges, focusId) {
  if (!nodes.length) return { positions: {}, visibleNodes: [] };
  const focus = nodes.find((node) => node.id === focusId) || nodes[0];
  const scoreById = new Map();
  edges.forEach((edge) => {
    if (edge.source === focus.id) scoreById.set(edge.target, Math.max(scoreById.get(edge.target) || 0, edge.score));
    if (edge.target === focus.id) scoreById.set(edge.source, Math.max(scoreById.get(edge.source) || 0, edge.score));
  });
  const related = nodes
    .filter((node) => node.id !== focus.id)
    .sort((a, b) => (scoreById.get(b.id) || 0) - (scoreById.get(a.id) || 0));
  const positions = { [focus.id]: CENTER };
  const placeRing = (items, radiusX, radiusY, offset) => {
    items.forEach((node, index) => {
      const angle = offset + (Math.PI * 2 * index) / Math.max(1, items.length);
      positions[node.id] = {
        x: CENTER.x + Math.cos(angle) * radiusX,
        y: CENTER.y + Math.sin(angle) * radiusY,
      };
    });
  };
  let cursor = 0;
  let ring = 0;
  while (cursor < related.length) {
    const capacity = 12 + ring * 12;
    const items = related.slice(cursor, cursor + capacity);
    placeRing(items, 250 + ring * 140, 160 + ring * 70, -Math.PI / 2 + ring * 0.11);
    cursor += items.length;
    ring += 1;
  }
  return { positions, visibleNodes: [focus, ...related] };
}

function MindmapGraph({ graph, selectedId, selectedEdge, onSelectNode, onSelectEdge, zoom = 1 }) {
  const { positions, visibleNodes } = useMemo(
    () => graphLayout(graph.nodes || [], graph.edges || [], graph.focus_id),
    [graph]
  );
  const visibleIds = useMemo(() => new Set(visibleNodes.map((node) => node.id)), [visibleNodes]);
  const visibleEdges = (graph.edges || []).filter(
    (edge) => visibleIds.has(edge.source) && visibleIds.has(edge.target)
  );

  return (
    <Box sx={{ width: `calc(${zoom * 100}% + 320px)`, minWidth: 1450 * zoom + 320, height: 800 * zoom + 160, display: "flex", alignItems: "center", justifyContent: "center", transition: "width 160ms ease, height 160ms ease, min-width 160ms ease" }}>
      <Box sx={{ width: "calc(100% - 320px)", minWidth: 1450 * zoom, height: 800 * zoom }}>
        <svg viewBox={`0 0 ${WIDTH} ${HEIGHT}`} width="100%" height="100%" role="img" aria-label="Bản đồ quan hệ tin tức">
        <defs>
          <filter id="nodeGlow" x="-60%" y="-60%" width="220%" height="220%">
            <feGaussianBlur stdDeviation="5" result="blur" />
            <feMerge><feMergeNode in="blur" /><feMergeNode in="SourceGraphic" /></feMerge>
          </filter>
        </defs>
        {visibleEdges.map((edge) => {
          const start = positions[edge.source];
          const end = positions[edge.target];
          if (!start || !end) return null;
          const style = TYPE_STYLE[edge.type] || TYPE_STYLE.related_event;
          const active = selectedEdge === edge;
          return (
            <g data-mindmap-interactive="true" key={`${edge.source}-${edge.target}`} onClick={() => onSelectEdge(edge)} style={{ cursor: "pointer" }}>
              <title>{`${edge.label}: ${edge.reason}`}</title>
              <line x1={start.x} y1={start.y} x2={end.x} y2={end.y} stroke="transparent" strokeWidth="15" />
              <line
                x1={start.x} y1={start.y} x2={end.x} y2={end.y}
                stroke={style.color}
                strokeWidth={active ? 3.8 : 1.2 + edge.score * 1.6}
                strokeOpacity={active ? 1 : edge.ai_verified ? 0.9 : 0.48}
                strokeDasharray={edge.ai_verified ? "0" : "5 4"}
              />
            </g>
          );
        })}
        {visibleNodes.map((node) => {
          const point = positions[node.id];
          const focus = node.id === graph.focus_id;
          const selected = node.id === selectedId;
          const radius = focus ? 50 : selected ? 34 : 27;
          const bridge = Number(node.bridge_score || 0) >= 0.45;
          return (
            <g
              key={node.id}
              data-mindmap-interactive="true"
              transform={`translate(${point.x} ${point.y})`}
              onClick={() => onSelectNode(node)}
              style={{ cursor: "pointer" }}
              filter={focus || selected ? "url(#nodeGlow)" : undefined}
            >
              <title>{`${node.title}${node.contexts?.length ? `\nNgữ cảnh: ${node.contexts.join(", ")}` : ""}`}</title>
              {bridge ? (
                <circle
                  r={radius + 8}
                  fill="none"
                  stroke="#ffd166"
                  strokeWidth="2"
                  strokeDasharray="4 4"
                  opacity="0.9"
                />
              ) : null}
              <circle
                r={radius}
                fill={focus ? "#1c5ce5" : selected ? "#274f91" : "#14243b"}
                stroke={focus ? "#75c7ff" : selected ? "#8bb7ff" : "#4f6f9e"}
                strokeWidth={focus ? 3 : selected ? 2.5 : 1.3}
              />
              {node.countries?.length ? (
                <circle cx={radius - 6} cy={-radius + 6} r="7" fill="#44c7a1" stroke="#08111f" strokeWidth="2" />
              ) : null}
              {node.event_cluster_size > 1 ? (
                <g transform={`translate(${-radius + 3} ${-radius + 4})`}>
                  <circle r="10" fill="#64d8ff" stroke="#08111f" strokeWidth="2" />
                  <text x="0" y="4" textAnchor="middle" fill="#06101e" fontSize="9" fontWeight="800">
                    {node.event_cluster_size}
                  </text>
                </g>
              ) : null}
              <text x="0" y={focus ? -5 : 3} textAnchor="middle" fill="#fff" fontSize={focus ? 13 : 11} fontWeight="700">
                {focus ? "TIN TRUNG TÂM" : `#${node.wire_rank ?? "—"}`}
              </text>
              <text x="0" y={radius + 18} textAnchor="middle" fill="#dce8ff" fontSize="11.5" fontWeight="600">
                {shorten(node.title, focus ? 68 : 42)}
              </text>
            </g>
          );
        })}
        </svg>
      </Box>
    </Box>
  );
}

export default function MindmapPage() {
  const [graph, setGraph] = useState({ nodes: [], edges: [], meta: {} });
  const [loading, setLoading] = useState(true);
  const [aiLoading, setAiLoading] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [days, setDays] = useState(14);
  const [limit, setLimit] = useState(100);
  const [zoom, setZoom] = useState(1);
  const [isPanning, setIsPanning] = useState(false);
  const [graphExpanded, setGraphExpanded] = useState(false);
  const [search, setSearch] = useState("");
  const [wireRankSearch, setWireRankSearch] = useState("");
  const [selectedId, setSelectedId] = useState(null);
  const [selectedEdge, setSelectedEdge] = useState(null);
  const [activeContext, setActiveContext] = useState("all");
  const [collapseEvents, setCollapseEvents] = useState(false);
  const historyRef = useRef([]);
  const viewportRef = useRef(null);
  const zoomRef = useRef(1);
  const panRef = useRef(null);
  const suppressClickRef = useRef(false);

  useEffect(() => {
    if (!graphExpanded) return undefined;
    const previousOverflow = document.body.style.overflow;
    const closeOnEscape = (event) => {
      if (event.key === "Escape") setGraphExpanded(false);
    };
    document.body.style.overflow = "hidden";
    window.addEventListener("keydown", closeOnEscape);
    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener("keydown", closeOnEscape);
    };
  }, [graphExpanded]);

  useEffect(() => {
    if (!graphExpanded && graph.focus_id && selectedId == null) {
      setSelectedId(graph.focus_id);
      setSelectedEdge(null);
    }
  }, [graphExpanded, graph.focus_id, selectedId]);

  const applyZoom = useCallback((requestedZoom, clientX = null, clientY = null) => {
    const viewport = viewportRef.current;
    const current = zoomRef.current;
    const next = Math.max(0.75, Math.min(1.5, Number(requestedZoom.toFixed(2))));
    if (next === current) return;

    let anchorX = 0;
    let anchorY = 0;
    let offsetX = 0;
    let offsetY = 0;
    if (viewport) {
      const rect = viewport.getBoundingClientRect();
      offsetX = clientX == null ? viewport.clientWidth / 2 : clientX - rect.left;
      offsetY = clientY == null ? viewport.clientHeight / 2 : clientY - rect.top;
      anchorX = viewport.scrollLeft + offsetX;
      anchorY = viewport.scrollTop + offsetY;
    }

    zoomRef.current = next;
    setZoom(next);
    if (viewport) {
      requestAnimationFrame(() => {
        const ratio = next / current;
        viewport.scrollLeft = anchorX * ratio - offsetX;
        viewport.scrollTop = anchorY * ratio - offsetY;
      });
    }
  }, []);

  const handleWheelZoom = useCallback((event) => {
    event.preventDefault();
    const direction = event.deltaY < 0 ? 1 : -1;
    applyZoom(zoomRef.current + direction * 0.1, event.clientX, event.clientY);
  }, [applyZoom]);

  const handlePanStart = useCallback((event) => {
    if (event.button !== 0 || event.target.closest("button, a")) return;
    const viewport = viewportRef.current;
    if (!viewport) return;
    panRef.current = {
      x: event.clientX,
      y: event.clientY,
      scrollLeft: viewport.scrollLeft,
      scrollTop: viewport.scrollTop,
      pointerId: event.pointerId,
      moved: false,
    };
    suppressClickRef.current = false;
  }, []);

  const handlePanMove = useCallback((event) => {
    const start = panRef.current;
    const viewport = viewportRef.current;
    if (!start || !viewport) return;
    const dx = event.clientX - start.x;
    const dy = event.clientY - start.y;
    if (!start.moved && Math.hypot(dx, dy) > 4) {
      start.moved = true;
      suppressClickRef.current = true;
      setIsPanning(true);
      viewport.setPointerCapture?.(event.pointerId);
    }
    if (start.moved) {
      viewport.scrollLeft = start.scrollLeft - dx;
      viewport.scrollTop = start.scrollTop - dy;
    }
  }, []);

  const handlePanEnd = useCallback((event) => {
    const viewport = viewportRef.current;
    if (!panRef.current) return;
    const wasMoved = panRef.current.moved;
    if (viewport?.hasPointerCapture?.(event.pointerId)) {
      viewport.releasePointerCapture(event.pointerId);
    }
    panRef.current = null;
    setIsPanning(false);
    if (wasMoved) {
      setTimeout(() => { suppressClickRef.current = false; }, 0);
    }
  }, []);

  const loadGraph = useCallback(async ({ focusId = null, focusRank = null, query = search, remember = true } = {}) => {
    setLoading(true);
    setError("");
    setSelectedEdge(null);
    try {
      const data = await getThreatMindmap({ focusId, focusRank, days, limit, search: query });
      if (data.meta?.focus_rank_not_found) {
        throw new Error(
          `Không tìm thấy tin số ${data.meta.focus_rank_not_found}. Trạm hiện có ${data.meta.wire_total || 0} tin.`
        );
      }
      if (remember && graph.focus_id && graph.focus_id !== data.focus_id) historyRef.current.push(graph.focus_id);
      setGraph(data);
      setActiveContext("all");
      setSelectedId(data.focus_id || null);
    } catch (err) {
      setError(err.message || "Không tải được bản đồ quan hệ.");
    } finally {
      setLoading(false);
    }
  }, [days, limit, search, graph.focus_id]);

  useEffect(() => {
    loadGraph({ remember: false });
    // only refresh when the time window changes
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [days, limit]);

  const displayGraph = useMemo(() => {
    let nodes = graph.nodes || [];
    if (activeContext !== "all") {
      nodes = nodes.filter(
        (node) => node.id === graph.focus_id || (node.contexts || []).includes(activeContext)
      );
    }
    if (collapseEvents) {
      const seenClusters = new Set();
      nodes = nodes.filter((node) => {
        const cluster = node.event_cluster_id;
        if (!cluster) return true;
        if (node.id === graph.focus_id) {
          seenClusters.add(cluster);
          return true;
        }
        if (seenClusters.has(cluster)) return false;
        seenClusters.add(cluster);
        return true;
      });
    }
    const ids = new Set(nodes.map((node) => node.id));
    return {
      ...graph,
      nodes,
      edges: (graph.edges || []).filter((edge) => ids.has(edge.source) && ids.has(edge.target)),
    };
  }, [graph, activeContext, collapseEvents]);

  const nodeById = useMemo(
    () => new Map((displayGraph.nodes || []).map((node) => [node.id, node])),
    [displayGraph.nodes]
  );
  const selectedNode = selectedId == null
    ? null
    : nodeById.get(selectedId) || nodeById.get(graph.focus_id);
  const focusNode = nodeById.get(graph.focus_id);
  const selectedRelations = useMemo(
    () => (displayGraph.edges || [])
      .filter((edge) => edge.source === selectedNode?.id || edge.target === selectedNode?.id)
      .sort((a, b) => b.score - a.score),
    [displayGraph.edges, selectedNode]
  );

  const analyzeAi = async () => {
    if (!graph.focus_id) return;
    setAiLoading(true);
    setError("");
    setNotice("");
    try {
      const data = await analyzeThreatMindmap({ focusId: graph.focus_id, days, limit });
      setGraph(data);
      if (data.meta?.ai_error) {
        setError(data.meta.ai_error);
      } else {
        setNotice(`AI đã kiểm chứng ${data.meta?.ai_edge_count || 0} liên kết; kết quả được lưu đệm 24 giờ.`);
      }
    } catch (err) {
      setError(err.message || "AI chưa khả dụng; bản đồ quy tắc vẫn dùng bình thường.");
    } finally {
      setAiLoading(false);
    }
  };

  const goBack = () => {
    const previous = historyRef.current.pop();
    if (previous) loadGraph({ focusId: previous, remember: false });
  };

  const focusByWireRank = () => {
    const rank = Number.parseInt(wireRankSearch, 10);
    if (!Number.isInteger(rank) || rank < 1) {
      setError("Nhập số thứ tự hợp lệ của tin trong Trạm tin tức.");
      return;
    }
    setSearch("");
    loadGraph({ focusRank: rank, query: "" });
  };

  return (
    <Stack spacing={2.2}>
      <Stack direction={{ xs: "column", md: "row" }} justifyContent="space-between" gap={1.5}>
        <Box>
          <Stack direction="row" spacing={1} alignItems="center">
            <AccountTreeOutlinedIcon color="primary" />
            <Typography variant="h4">Mindmap tin tức</Typography>
          </Stack>
          <Typography color="text.secondary" sx={{ mt: 0.6 }}>
            Đồ thị liên kết động giữa các tin trong Trạm tin tức. Bấm một nút để xem quan hệ hoặc đặt làm trung tâm.
          </Typography>
        </Box>
        <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap>
          <Tooltip title="Quay lại tin trung tâm trước">
            <span><IconButton onClick={goBack} disabled={!historyRef.current.length}><ArrowBackOutlinedIcon /></IconButton></span>
          </Tooltip>
          <Select size="small" value={days} onChange={(event) => setDays(Number(event.target.value))} sx={{ minWidth: 125 }}>
            <MenuItem value={1}>1 ngày</MenuItem>
            <MenuItem value={7}>7 ngày</MenuItem>
            <MenuItem value={14}>14 ngày</MenuItem>
            <MenuItem value={30}>30 ngày</MenuItem>
          </Select>
          <Select size="small" value={limit} onChange={(event) => setLimit(Number(event.target.value))} sx={{ minWidth: 115 }}>
            <MenuItem value={50}>50 tin</MenuItem>
            <MenuItem value={100}>100 tin</MenuItem>
            <MenuItem value={150}>150 tin</MenuItem>
          </Select>
          <Button variant="contained" startIcon={aiLoading ? <CircularProgress size={16} color="inherit" /> : <AutoAwesomeOutlinedIcon />} onClick={analyzeAi} disabled={aiLoading || !graph.focus_id}>
            Phân tích AI
          </Button>
        </Stack>
      </Stack>

      <Box
        sx={{
          display: "grid",
          gridTemplateColumns: {
            xs: "minmax(0, 1fr)",
            sm: "minmax(180px, 220px) auto",
            md: "minmax(180px, 210px) auto minmax(220px, 1fr) auto",
          },
          gap: 1,
          alignItems: "center",
        }}
      >
        <TextField
          size="small"
          type="number"
          value={wireRankSearch}
          onChange={(event) => setWireRankSearch(event.target.value)}
          onKeyDown={(event) => event.key === "Enter" && focusByWireRank()}
          label="Số thứ tự trong Trạm"
          placeholder="Ví dụ: 1250"
          inputProps={{ min: 1, step: 1 }}
          sx={{ minWidth: 0 }}
          InputProps={{ startAdornment: <InputAdornment position="start">#</InputAdornment> }}
        />
        <Button
          size="small"
          variant="contained"
          startIcon={<CenterFocusStrongOutlinedIcon />}
          onClick={focusByWireRank}
          sx={{
            whiteSpace: "nowrap",
            minWidth: 0,
            minHeight: 40,
            px: 1.25,
            fontSize: "0.82rem",
            justifySelf: { xs: "stretch", sm: "start" },
          }}
        >
          Đặt làm trung tâm
        </Button>
        <TextField
          size="small"
          fullWidth
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          onKeyDown={(event) => event.key === "Enter" && loadGraph({ query: search, remember: false })}
          placeholder="Lọc theo quốc gia, vũ khí, chiến dịch, tổ chức…"
          InputProps={{ startAdornment: <InputAdornment position="start"><SearchOutlinedIcon fontSize="small" /></InputAdornment> }}
        />
        <Button
          size="small"
          variant="outlined"
          onClick={() => loadGraph({ query: search, remember: false })}
          sx={{ minHeight: 40, whiteSpace: "nowrap", px: 1.25 }}
        >
          Lọc bản đồ
        </Button>
      </Box>

      {error ? <Alert severity="warning">{error}</Alert> : null}
      {notice ? <Alert severity="success">{notice}</Alert> : null}

      <Typography variant="caption" color="text.secondary">
        Đã nạp {graph.nodes?.length || 0}/{graph.meta?.window_total || 0} tin trong {days} ngày · toàn Trạm hiện có {graph.meta?.wire_total || 0} tin
        {displayGraph.nodes?.length !== graph.nodes?.length ? ` · đang hiển thị ${displayGraph.nodes?.length || 0} sau khi thu gọn/lọc` : ""}.
      </Typography>

      {graph.meta?.period_counts?.length ? (
        <Stack direction="row" flexWrap="wrap" gap={0.8} alignItems="center">
          <Typography variant="caption" color="text.secondary">Phân bố thời gian:</Typography>
          {graph.meta.period_counts.map((period) => (
            <Chip key={period.label} size="small" variant="outlined" label={`${period.label}: đang vẽ ${period.shown}/${period.available} tin`} />
          ))}
        </Stack>
      ) : null}

      <Stack direction="row" flexWrap="wrap" gap={0.8}>
        {Object.entries(TYPE_STYLE).slice(0, 9).map(([key, style]) => (
          <Chip key={key} size="small" label={style.label} sx={{ borderColor: style.color, color: style.color, borderWidth: 1, borderStyle: "solid", bgcolor: "transparent" }} />
        ))}
      </Stack>

      <Paper variant="outlined" sx={{ p: 1.4 }}>
        <Stack direction={{ xs: "column", lg: "row" }} gap={1.2} justifyContent="space-between" alignItems={{ lg: "center" }}>
          <Stack direction="row" gap={0.7} flexWrap="wrap" useFlexGap>
            <Chip
              size="small"
              label="Tất cả ngữ cảnh"
              color={activeContext === "all" ? "primary" : "default"}
              variant={activeContext === "all" ? "filled" : "outlined"}
              onClick={() => setActiveContext("all")}
            />
            {(graph.meta?.contexts || []).slice(0, 10).map((context) => (
              <Chip
                key={context.name}
                size="small"
                label={`${context.name} · ${context.count}`}
                color={activeContext === context.name ? "primary" : "default"}
                variant={activeContext === context.name ? "filled" : "outlined"}
                onClick={() => setActiveContext(context.name)}
              />
            ))}
          </Stack>
          <FormControlLabel
            control={<Switch size="small" checked={collapseEvents} onChange={(event) => setCollapseEvents(event.target.checked)} />}
            label="Thu gọn tin cùng sự kiện"
            sx={{ m: 0, whiteSpace: "nowrap" }}
          />
        </Stack>
        <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 0.8 }}>
          Vòng vàng nét đứt là nút cầu nối nhiều nhóm; huy hiệu số cho biết số bài đang cùng một cụm sự kiện.
        </Typography>
      </Paper>

      {graph.meta?.ai_insight?.overview ? (
        <Paper variant="outlined" sx={{ p: 1.7, borderColor: "secondary.main", bgcolor: "rgba(174, 116, 255, 0.06)" }}>
          <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 0.8 }}>
            <AutoAwesomeOutlinedIcon color="secondary" fontSize="small" />
            <Typography variant="subtitle1">Nhận định trên đồ thị</Typography>
          </Stack>
          <Typography variant="body2">{graph.meta.ai_insight.overview}</Typography>
          {graph.meta.ai_insight.patterns?.length ? (
            <Stack spacing={0.4} sx={{ mt: 1 }}>
              {graph.meta.ai_insight.patterns.map((item) => <Typography key={item} variant="body2">• {item}</Typography>)}
            </Stack>
          ) : null}
          {graph.meta.ai_insight.cautions?.length ? (
            <Alert severity="info" sx={{ mt: 1.2 }}>
              {graph.meta.ai_insight.cautions.join(" • ")}
            </Alert>
          ) : null}
        </Paper>
      ) : null}

      <Box sx={{ display: "grid", gridTemplateColumns: { xs: "1fr", xl: "minmax(0, 1fr) 355px" }, gap: 2, alignItems: "stretch" }}>
        <Portal disablePortal={!graphExpanded}>
        <Box
          sx={{
            position: graphExpanded ? "fixed" : "relative",
            inset: graphExpanded ? 0 : "auto",
            zIndex: graphExpanded ? (theme) => theme.zIndex.modal + 3 : "auto",
            minWidth: 0,
            display: graphExpanded ? "flex" : "block",
            flexDirection: "column",
            overflow: graphExpanded ? "hidden" : "visible",
            boxSizing: "border-box",
            p: graphExpanded ? 1.5 : 0,
            bgcolor: graphExpanded ? "background.default" : "transparent",
          }}
        >
          {graphExpanded ? (
            <Box
              sx={{
                flexShrink: 0,
                pt: 7,
                pb: 1,
                px: 0.5,
                maxHeight: "34dvh",
                overflowY: "auto",
                scrollbarGutter: "stable",
              }}
            >
              <Stack direction="row" flexWrap="wrap" gap={0.7} sx={{ mb: 1 }}>
                {Object.entries(TYPE_STYLE).slice(0, 9).map(([key, style]) => (
                  <Chip
                    key={key}
                    size="small"
                    label={style.label}
                    sx={{
                      borderColor: style.color,
                      color: style.color,
                      borderWidth: 1,
                      borderStyle: "solid",
                      bgcolor: "rgba(5, 13, 25, 0.86)",
                    }}
                  />
                ))}
              </Stack>
              <Paper variant="outlined" sx={{ p: 1, bgcolor: "rgba(7, 20, 40, 0.94)" }}>
                <Stack
                  direction={{ xs: "column", md: "row" }}
                  gap={1}
                  justifyContent="space-between"
                  alignItems={{ md: "center" }}
                >
                  <Stack direction="row" gap={0.6} flexWrap="wrap" useFlexGap>
                    <Chip
                      size="small"
                      label="Tất cả ngữ cảnh"
                      color={activeContext === "all" ? "primary" : "default"}
                      variant={activeContext === "all" ? "filled" : "outlined"}
                      onClick={() => setActiveContext("all")}
                    />
                    {(graph.meta?.contexts || []).slice(0, 10).map((context) => (
                      <Chip
                        key={context.name}
                        size="small"
                        label={`${context.name} · ${context.count}`}
                        color={activeContext === context.name ? "primary" : "default"}
                        variant={activeContext === context.name ? "filled" : "outlined"}
                        onClick={() => setActiveContext(context.name)}
                      />
                    ))}
                  </Stack>
                  <FormControlLabel
                    control={<Switch size="small" checked={collapseEvents} onChange={(event) => setCollapseEvents(event.target.checked)} />}
                    label="Thu gọn tin cùng sự kiện"
                    sx={{ m: 0, whiteSpace: "nowrap", flexShrink: 0 }}
                  />
                </Stack>
                <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 0.7 }}>
                  Vòng vàng nét đứt là nút cầu nối nhiều nhóm; huy hiệu số cho biết số bài trong cùng một cụm sự kiện.
                </Typography>
              </Paper>
            </Box>
          ) : null}
          <Stack
            direction="row"
            spacing={0.6}
            alignItems="center"
            sx={{
              position: "absolute",
              top: graphExpanded ? "max(20px, env(safe-area-inset-top, 0px))" : 16,
              right: graphExpanded ? "max(20px, env(safe-area-inset-right, 0px))" : 16,
              zIndex: 20,
              maxWidth: "calc(100vw - 32px)",
              p: 0.7,
              borderRadius: 2.5,
              bgcolor: "rgba(7, 20, 40, 0.97)",
              border: "1px solid",
              borderColor: "primary.main",
              boxShadow: "0 8px 28px rgba(0, 0, 0, 0.48)",
            }}
          >
            <Tooltip title="Giảm tỷ lệ">
              <span><IconButton aria-label="Giảm tỷ lệ" onClick={() => applyZoom(zoomRef.current - 0.15)} disabled={zoom <= 0.75} sx={{ bgcolor: "rgba(255,255,255,.08)", "&:hover": { bgcolor: "rgba(255,255,255,.16)" } }}><RemoveOutlinedIcon /></IconButton></span>
            </Tooltip>
            <Chip label={`${Math.round(zoom * 100)}%`} color="primary" variant="outlined" onClick={() => applyZoom(1)} title="Bấm để về 100%" sx={{ minWidth: 68, fontWeight: 700 }} />
            <Tooltip title="Tăng tỷ lệ">
              <span><IconButton aria-label="Tăng tỷ lệ" onClick={() => applyZoom(zoomRef.current + 0.15)} disabled={zoom >= 1.5} sx={{ bgcolor: "primary.main", color: "primary.contrastText", "&:hover": { bgcolor: "primary.dark" }, "&.Mui-disabled": { bgcolor: "action.disabledBackground" } }}><AddOutlinedIcon /></IconButton></span>
            </Tooltip>
            <Tooltip title={graphExpanded ? "Thu nhỏ về trang" : "Xem sơ đồ toàn màn hình"}>
              <IconButton
                aria-label={graphExpanded ? "Thu nhỏ về trang" : "Xem sơ đồ toàn màn hình"}
                onClick={() => setGraphExpanded((value) => !value)}
                sx={{
                  flexShrink: 0,
                  bgcolor: graphExpanded ? "secondary.main" : "rgba(255,255,255,.08)",
                  color: graphExpanded ? "secondary.contrastText" : "text.primary",
                  "&:hover": { bgcolor: graphExpanded ? "secondary.dark" : "rgba(255,255,255,.16)" },
                }}
              >
                {graphExpanded ? <FullscreenExitOutlinedIcon /> : <FullscreenOutlinedIcon />}
              </IconButton>
            </Tooltip>
          </Stack>
          {graphExpanded && selectedNode && selectedNode.id !== graph.focus_id ? (
            <Paper
              variant="outlined"
              sx={{
                position: "absolute",
                left: "max(20px, env(safe-area-inset-left, 0px))",
                bottom: "max(20px, env(safe-area-inset-bottom, 0px))",
                zIndex: 20,
                width: { xs: "calc(100vw - 40px)", sm: 460 },
                maxWidth: "calc(100vw - 40px)",
                p: 1.4,
                bgcolor: "rgba(7, 20, 40, 0.97)",
                borderColor: "primary.main",
                boxShadow: "0 10px 32px rgba(0, 0, 0, 0.52)",
              }}
            >
              <Stack direction="row" justifyContent="space-between" alignItems="flex-start" gap={1.2}>
                <Box sx={{ minWidth: 0 }}>
                  <Typography variant="overline" color="primary.main">
                    TIN #{selectedNode.wire_rank ?? "—"} TRÊN TRẠM TIN TỨC
                  </Typography>
                  <Typography
                    variant="subtitle2"
                    sx={{
                      lineHeight: 1.35,
                      display: "-webkit-box",
                      WebkitLineClamp: 2,
                      WebkitBoxOrient: "vertical",
                      overflow: "hidden",
                    }}
                  >
                    {selectedNode.title}
                  </Typography>
                </Box>
                {selectedNode.source_url ? (
                  <Tooltip title="Mở bài gốc">
                    <IconButton
                      size="small"
                      component="a"
                      href={selectedNode.source_url}
                      target="_blank"
                      rel="noopener noreferrer"
                      sx={{ flexShrink: 0 }}
                    >
                      <LaunchOutlinedIcon fontSize="small" />
                    </IconButton>
                  </Tooltip>
                ) : null}
              </Stack>
              <Button
                fullWidth
                size="small"
                startIcon={<CenterFocusStrongOutlinedIcon />}
                variant="contained"
                onClick={() => loadGraph({ focusId: selectedNode.id })}
                sx={{ mt: 1.2 }}
              >
                Đặt làm trung tâm
              </Button>
            </Paper>
          ) : null}
          <Paper
            ref={viewportRef}
            variant="outlined"
            onWheel={handleWheelZoom}
            onPointerDown={handlePanStart}
            onPointerMove={handlePanMove}
            onPointerUp={handlePanEnd}
            onPointerCancel={handlePanEnd}
            onClickCapture={(event) => {
              if (suppressClickRef.current) {
                event.preventDefault();
                event.stopPropagation();
                suppressClickRef.current = false;
              }
            }}
            onClick={(event) => {
              if (event.target.closest?.('[data-mindmap-interactive="true"], button, a')) return;
              setSelectedId(null);
              setSelectedEdge(null);
            }}
            sx={{ minHeight: graphExpanded ? 0 : 800, maxHeight: graphExpanded ? "none" : 800, height: graphExpanded ? "100%" : "auto", overflow: "auto", cursor: isPanning ? "grabbing" : "grab", userSelect: "none", touchAction: "none", background: "radial-gradient(circle at 46% 48%, rgba(35,78,145,.24), rgba(5,13,25,.88) 62%)" }}
          >
          {loading ? (
            <Stack alignItems="center" justifyContent="center" sx={{ minHeight: 700 }} spacing={1.5}>
              <CircularProgress />
              <Typography color="text.secondary">Đang dựng mạng quan hệ…</Typography>
            </Stack>
          ) : displayGraph.nodes?.length ? (
            <MindmapGraph
              graph={displayGraph}
              selectedId={selectedId}
              selectedEdge={selectedEdge}
              onSelectNode={(node) => { setSelectedId(node.id); setSelectedEdge(null); }}
              onSelectEdge={(edge) => { setSelectedEdge(edge); setSelectedId(edge.target === graph.focus_id ? edge.source : edge.target); }}
              zoom={zoom}
            />
          ) : (
            <Stack alignItems="center" justifyContent="center" sx={{ minHeight: 700 }}><Typography>Không có tin phù hợp trong cửa sổ đã chọn.</Typography></Stack>
          )}
          </Paper>
        </Box>
        </Portal>

        <Paper variant="outlined" sx={{ p: 2, minHeight: 700 }}>
          {selectedNode ? (
            <Stack spacing={1.6}>
              <Stack direction="row" justifyContent="space-between" gap={1}>
                <Box>
                  <Typography variant="overline" color="primary.main">TIN #{selectedNode.wire_rank ?? "—"} TRÊN TRẠM TIN TỨC</Typography>
                  <Typography variant="h6" sx={{ lineHeight: 1.35 }}>{selectedNode.title}</Typography>
                </Box>
                {selectedNode.source_url ? (
                  <Tooltip title="Mở bài gốc"><IconButton component="a" href={selectedNode.source_url} target="_blank" rel="noopener noreferrer"><LaunchOutlinedIcon /></IconButton></Tooltip>
                ) : null}
              </Stack>
              <Stack direction="row" gap={0.7} flexWrap="wrap">
                {(selectedNode.countries || []).map((country) => <Chip key={country} label={country} size="small" color="success" variant="outlined" />)}
                <Chip label={selectedNode.severity || "info"} size="small" variant="outlined" />
                {selectedNode.bridge_score >= 0.45 ? <Chip label="Nút cầu" size="small" color="warning" variant="outlined" /> : null}
                {selectedNode.event_cluster_size > 1 ? <Chip label={`${selectedNode.event_cluster_size} tin cùng sự kiện`} size="small" color="info" variant="outlined" /> : null}
              </Stack>
              {selectedNode.contexts?.length ? (
                <Stack direction="row" gap={0.5} flexWrap="wrap">
                  {selectedNode.contexts.slice(0, 8).map((context) => (
                    <Chip key={context} label={context} size="small" onClick={() => setActiveContext(context)} />
                  ))}
                </Stack>
              ) : null}
              <Typography variant="body2" color="text.secondary">
                {selectedNode.summary || "Chưa có tóm tắt; mở bài gốc để xem đầy đủ."}
              </Typography>
              {selectedNode.id !== graph.focus_id ? (
                <Button startIcon={<CenterFocusStrongOutlinedIcon />} variant="outlined" onClick={() => loadGraph({ focusId: selectedNode.id })}>
                  Đặt làm trung tâm
                </Button>
              ) : null}
              <Divider />
              <Typography variant="subtitle1">Các mối liên hệ ({selectedRelations.length})</Typography>
              <Stack spacing={1} sx={{ maxHeight: 330, overflowY: "auto", pr: 0.5 }}>
                {selectedRelations.length ? selectedRelations.map((edge) => {
                  const otherId = edge.source === selectedNode.id ? edge.target : edge.source;
                  const other = nodeById.get(otherId);
                  const style = TYPE_STYLE[edge.type] || TYPE_STYLE.related_event;
                  return (
                    <Box key={`${edge.source}-${edge.target}`} onClick={() => { setSelectedEdge(edge); if (other) setSelectedId(other.id); }} sx={{ p: 1.2, border: "1px solid", borderColor: selectedEdge === edge ? style.color : "divider", borderRadius: 1.5, cursor: "pointer", bgcolor: selectedEdge === edge ? `${style.color}12` : "transparent" }}>
                      <Stack direction="row" justifyContent="space-between" gap={1}>
                        <Typography variant="caption" sx={{ color: style.color, fontWeight: 700 }}>{edge.label}</Typography>
                        <Typography variant="caption" color="text.secondary">{Math.round(edge.score * 100)}%</Typography>
                      </Stack>
                      <Typography variant="body2" sx={{ mt: 0.35, fontWeight: 600 }}>{shorten(other?.title || "Tin liên quan", 72)}</Typography>
                      <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 0.45 }}>{edge.reason}</Typography>
                      {edge.ai_verified ? <Chip size="small" label="AI đã kiểm chứng" color="secondary" variant="outlined" sx={{ mt: 0.8 }} /> : null}
                    </Box>
                  );
                }) : <Typography variant="body2" color="text.secondary">Chưa tìm thấy liên kết đủ mạnh.</Typography>}
              </Stack>
              {focusNode && selectedNode.id !== focusNode.id ? <Typography variant="caption" color="text.secondary">Tin trung tâm hiện tại: {shorten(focusNode.title, 80)}</Typography> : null}
            </Stack>
          ) : null}
        </Paper>
      </Box>
    </Stack>
  );
}
