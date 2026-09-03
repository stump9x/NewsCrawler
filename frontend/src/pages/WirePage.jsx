import { useCallback, useEffect, useRef, useState } from "react";
import {
  Alert,
  Avatar,
  Box,
  Button,
  Card,
  CardContent,
  Chip,
  Divider,
  FormControlLabel,
  IconButton,
  MenuItem,
  Pagination,
  Stack,
  Switch,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from "@mui/material";
import ArticleOutlinedIcon from "@mui/icons-material/ArticleOutlined";
import FavoriteBorderOutlinedIcon from "@mui/icons-material/FavoriteBorderOutlined";
import FavoriteOutlinedIcon from "@mui/icons-material/FavoriteOutlined";
import CalendarTodayOutlinedIcon from "@mui/icons-material/CalendarTodayOutlined";
import LanguageOutlinedIcon from "@mui/icons-material/LanguageOutlined";
import TableRowsOutlinedIcon from "@mui/icons-material/TableRowsOutlined";
import ViewModuleOutlinedIcon from "@mui/icons-material/ViewModuleOutlined";
import Tooltip from "@mui/material/Tooltip";
import { Link as RouterLink } from "react-router-dom";
import { api, buildQuery } from "../api/client";
import { DataTable } from "../components/DataTable";
import { PageHeader } from "../components/PageHeader";
import {
  ExternalTitleLink,
  resolveThreatHref,
} from "../components/ExternalTitleLink";
import { formatDateWithRelative } from "../utils/dateTime";
import { displayWireTitle } from "../utils/wireTitle";
import {
  geographyFlagUrl,
  geographyTagLabel,
  orderedWireTags,
  wireCountryTags,
  WIRE_COUNTRY_FILTER_OPTIONS,
} from "../utils/wireTags";

const POLL_MS = 10000;
const WIRE_MAX_AGE_DAYS = 30;
const PAGE_SIZE = 50;
const SCOPE_TOPICS = {
  "wire-topic-1a": "Biển Đông: thực địa",
  "wire-topic-1b": "Biển Đông: chủ quyền",
  "wire-topic-2a": "TQ: biên giới, cửa khẩu",
  "wire-topic-2b": "TQ: quốc phòng, an ninh",
  "wire-topic-2c": "TQ: công nghệ, hạ tầng",
  "wire-topic-3a": "Tổ chức, chiến lược QP",
  "wire-topic-3b": "Hội nghị, quyết sách",
  "wire-topic-3c": "Công nghệ, hiện đại hóa",
  "wire-topic-4a": "Diễn tập, hoạt động QS",
  "wire-topic-4b": "Hợp tác, chuyển giao QP",
  "wire-topic-4c": "Học thuyết, năng lực mới",
  "wire-topic-5": "Trừng phạt, kiểm soát XK",
};
const TOPIC_LABELS = {
  ...SCOPE_TOPICS,
  exercises: "Diễn tập",
  maritime: "Hàng hải",
  procurement: "Mua sắm QP",
  "force-posture": "Bố trí lực lượng",
  "combat-trends": "Xu hướng QS",
  "national-strategy": "Chiến lược QS",
  "cyber-operations": "Tác chiến mạng",
  "security-cooperation": "Hợp tác AN",
  "defense-policy": "Chính sách QP",
  analysis: "Phân tích",
};
const SOURCE_LABELS = {
  manual: "Thủ công",
  news: "Tin tức",
  osint: "OSINT",
};

function formatWebsiteTag(tag) {
  const slug = String(tag?.slug || tag?.name || "");
  const site = slug.replace(/^site-/, "");
  return site.replace(/-(com|org|net|io|me|nz|st|fi|is|news)$/, ".$1");
}

function wireListQuery({ source, tag, country, publisher, page }) {
  return buildQuery({
    source: source || undefined,
    tag: tag || undefined,
    country: country || undefined,
    publisher: publisher || undefined,
    wire_feed: true,
    page,
    page_size: PAGE_SIZE,
    // Publication timeline: newest first (matches ThreatViewSet default).
    ordering: "-published_at,-id",
  });
}

function WireTagChips({ row, maxTags = 8 }) {
  const displayTags = orderedWireTags(row, maxTags);
  return (
    <Stack direction="row" spacing={0.5} flexWrap="wrap" useFlexGap>
      {displayTags.map((item) => {
        if (item.kind === "kev") {
          return <Chip key={item.key} size="small" color="error" label="KEV" variant="outlined" />;
        }
        const itemTag = item.tag;
        if (item.kind === "geography") {
          const flagSrc = geographyFlagUrl(itemTag, 20);
          return (
            <Chip
              key={itemTag.id || itemTag.slug || itemTag.name}
              size="small"
              avatar={
                flagSrc ? (
                  <Avatar
                    alt=""
                    src={flagSrc}
                    variant="square"
                    imgProps={{ loading: "lazy", referrerPolicy: "no-referrer" }}
                    sx={{
                      width: 18,
                      height: 13,
                      borderRadius: "2px",
                      bgcolor: "transparent",
                      "& img": { objectFit: "cover", width: 18, height: 13 },
                    }}
                  />
                ) : undefined
              }
              label={geographyTagLabel(itemTag)}
              variant="outlined"
              color="warning"
            />
          );
        }
        return (
          <Chip
            key={itemTag.id || itemTag.slug || itemTag.name}
            size="small"
            label={
              item.kind === "website"
                ? formatWebsiteTag(itemTag)
                : TOPIC_LABELS[itemTag.slug] || itemTag.name || itemTag.slug
            }
            variant={item.kind === "website" ? "filled" : "outlined"}
            color={item.kind === "website" ? "info" : "default"}
          />
        );
      })}
    </Stack>
  );
}

function resolveFeedImageUrl(row) {
  const payload = row?.raw_payload || {};
  for (const key of ["image_url", "image", "thumbnail", "enclosure_url"]) {
    const value = payload[key];
    if (typeof value === "string" && /^https?:\/\//i.test(value.trim())) {
      return value.trim();
    }
  }
  return "";
}

function wireEndpoint(favoritesOnly = false) {
  return favoritesOnly ? "/api/v1/threats/favorites/" : "/api/v1/threats/";
}

function FavoriteButton({ row, onToggle, busy = false }) {
  const active = Boolean(row.is_favorite);
  return (
    <Tooltip title={active ? "Bỏ theo dõi" : "Theo dõi"} arrow>
      <span>
        <IconButton
          size="small"
          aria-label={active ? "Bỏ theo dõi" : "Theo dõi"}
          onClick={() => onToggle(row)}
          disabled={busy}
          sx={{
            width: 30,
            height: 30,
            color: active ? "error.light" : "text.secondary",
            border: "1px solid",
            borderColor: active ? "rgba(248,113,113,.45)" : "rgba(148,163,184,.28)",
            bgcolor: active ? "rgba(248,113,113,.10)" : "rgba(148,163,184,.06)",
          }}
        >
          {active ? (
            <FavoriteOutlinedIcon sx={{ fontSize: 17 }} />
          ) : (
            <FavoriteBorderOutlinedIcon sx={{ fontSize: 17 }} />
          )}
        </IconButton>
      </span>
    </Tooltip>
  );
}

function WireCard({ row, onFavoriteToggle, favoriteBusy }) {
  const [imageFailed, setImageFailed] = useState(false);
  const countries = wireCountryTags(row, 6);
  const sourceName =
    row.raw_payload?.feed || row.raw_payload?.country || row.source || "RSS";
  const summary = row.summary || row.raw_payload?.description || "";
  const imageUrl = resolveFeedImageUrl(row);
  const showImage = Boolean(imageUrl) && !imageFailed;

  useEffect(() => {
    setImageFailed(false);
  }, [imageUrl, row.id]);

  return (
    <Card
      variant="outlined"
      sx={{
        height: "100%",
        overflow: "hidden",
        borderColor: "rgba(103, 232, 249, 0.16)",
        background:
          "linear-gradient(145deg, rgba(12,20,34,.98), rgba(5,12,24,.98))",
        transition: "transform 160ms ease, border-color 160ms ease, box-shadow 160ms ease",
        "&:hover": {
          transform: "translateY(-3px)",
          borderColor: "rgba(103, 232, 249, 0.48)",
          boxShadow: "0 12px 30px rgba(0,0,0,.32)",
        },
      }}
    >
      <CardContent sx={{ p: 2, "&:last-child": { pb: 2 } }}>
        <Stack spacing={1.25} height="100%">
          <Stack direction="row" alignItems="center" justifyContent="space-between" gap={1}>
            <Stack direction="row" alignItems="center" spacing={0.75}>
              <Chip
                label="BẢN TIN"
                size="small"
                sx={{
                  height: 22,
                  color: "secondary.light",
                  border: "1px solid rgba(103,232,249,.25)",
                  bgcolor: "rgba(103,232,249,.08)",
                  fontWeight: 700,
                  fontSize: 10,
                }}
              />
              <Typography variant="caption" color="text.secondary" fontWeight={700}>
                #{row._wireRank}
              </Typography>
            </Stack>
            {countries.length ? (
              <Stack
                direction="row"
                spacing={0.5}
                flexWrap="wrap"
                justifyContent="flex-end"
                useFlexGap
                aria-label="Các quốc gia được nhắc đến"
              >
                {countries.map((country) => (
                  <Avatar
                    key={country.id || country.slug || country.name}
                    src={geographyFlagUrl(country, 40)}
                    alt={geographyTagLabel(country)}
                    title={geographyTagLabel(country)}
                    variant="square"
                    imgProps={{ loading: "lazy", referrerPolicy: "no-referrer" }}
                    sx={{ width: 25, height: 17, borderRadius: "2px" }}
                  />
                ))}
              </Stack>
            ) : null}
            <FavoriteButton
              row={row}
              onToggle={onFavoriteToggle}
              busy={favoriteBusy}
            />
          </Stack>

          <Box minWidth={0}>
            <ExternalTitleLink
              title={displayWireTitle(row)}
              href={resolveThreatHref(row)}
            />
            <Typography
              variant="caption"
              color="text.secondary"
              sx={{ display: "block", mt: 0.5, textTransform: "uppercase", letterSpacing: ".06em" }}
            >
              {sourceName}
            </Typography>
          </Box>

          {showImage ? (
            <Box
              sx={{
                width: "100%",
                aspectRatio: "16 / 9",
                borderRadius: 1.5,
                overflow: "hidden",
                border: "1px solid rgba(255,255,255,.1)",
                bgcolor: "rgba(0,0,0,.28)",
              }}
            >
              <Box
                component="img"
                src={imageUrl}
                alt=""
                loading="lazy"
                decoding="async"
                referrerPolicy="no-referrer"
                onError={() => setImageFailed(true)}
                sx={{
                  display: "block",
                  width: "100%",
                  height: "100%",
                  objectFit: "cover",
                }}
              />
            </Box>
          ) : (
            <Stack direction="row" spacing={1.25} alignItems="flex-start">
              <Box
                sx={{
                  width: 54,
                  height: 54,
                  flex: "0 0 54px",
                  display: "grid",
                  placeItems: "center",
                  borderRadius: 1.5,
                  color: "secondary.light",
                  border: "1px solid rgba(103,232,249,.18)",
                  bgcolor: "rgba(103,232,249,.06)",
                }}
              >
                <ArticleOutlinedIcon />
              </Box>
              {summary ? (
                <Typography
                  variant="body2"
                  color="text.secondary"
                  sx={{
                    display: "-webkit-box",
                    WebkitLineClamp: 3,
                    WebkitBoxOrient: "vertical",
                    overflow: "hidden",
                    minHeight: 54,
                  }}
                >
                  {summary}
                </Typography>
              ) : (
                <Box sx={{ minHeight: 54 }} />
              )}
            </Stack>
          )}

          <Divider sx={{ borderColor: "rgba(255,255,255,.07)" }} />
          <Stack spacing={0.75}>
            <Stack direction="row" spacing={0.75} alignItems="center">
              <LanguageOutlinedIcon sx={{ fontSize: 15, color: "secondary.main" }} />
              <Typography variant="caption" color="text.secondary">
                Nguồn: {sourceName}
              </Typography>
            </Stack>
            <Stack direction="row" spacing={0.75} alignItems="center">
              <CalendarTodayOutlinedIcon sx={{ fontSize: 14, color: "secondary.main" }} />
              <Typography
                variant="caption"
                color="text.secondary"
                title={`Đã đăng ${row.published_at || "—"}`}
              >
                {formatDateWithRelative(row.published_at)}
              </Typography>
            </Stack>
          </Stack>
          <Box sx={{ mt: "auto !important", pt: 0.5 }}>
            <WireTagChips row={row} maxTags={8} />
          </Box>
        </Stack>
      </CardContent>
    </Card>
  );
}

export default function WirePage() {
  const [rows, setRows] = useState([]);
  const [totalCount, setTotalCount] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [source, setSource] = useState("");
  const [tag, setTag] = useState("");
  const [country, setCountry] = useState("");
  const [publisherInput, setPublisherInput] = useState("");
  const [publisher, setPublisher] = useState("");
  const [favoritesOnly, setFavoritesOnly] = useState(false);
  const [favoriteBusyId, setFavoriteBusyId] = useState(null);
  const [live, setLive] = useState(true);
  const [newCount, setNewCount] = useState(0);
  const [lastRefresh, setLastRefresh] = useState(null);
  const [view, setView] = useState("cards");
  const knownIds = useRef(new Set());
  const firstLoad = useRef(true);

  const pageCount = Math.max(1, Math.ceil(totalCount / PAGE_SIZE));

  // Debounce free-text publisher search so typing does not spam the API.
  useEffect(() => {
    const handle = setTimeout(() => {
      const next = publisherInput.trim();
      setPublisher((prev) => {
        if (prev === next) return prev;
        firstLoad.current = true;
        knownIds.current = new Set();
        setNewCount(0);
        setPage(1);
        return next;
      });
    }, 350);
    return () => clearTimeout(handle);
  }, [publisherInput]);

  const load = useCallback(
    async ({ silent = false, pageOverride } = {}) => {
      const pageNum = pageOverride ?? page;
      if (!silent) setLoading(true);
      setError("");
      try {
        const qs = wireListQuery({ source, tag, country, publisher, page: pageNum });
        const data = await api.get(`${wireEndpoint(favoritesOnly)}${qs}`);
        // Favorites is a strict scope: a stale response must never repopulate
        // this view with the main Wire list.
        const rawResults = data.results || [];
        const scopedResults = favoritesOnly
          ? rawResults.filter((row) => row.is_favorite === true)
          : rawResults;
        const count = data.count ?? scopedResults.length;
        // Keep the exact server order. It is also the canonical order used by
        // Mindmap after each account's policy is applied; re-sorting here
        // The server rank is canonical; this fallback preserves oldest-first
        // numbering when an older API response has no wire_rank field.
        const results = scopedResults.map((row, index) => ({
            ...row,
            _wireRank:
              Number(row.wire_rank) ||
              Math.max(1, count - (pageNum - 1) * PAGE_SIZE - index),
          }));
        setTotalCount(count);

        if (favoritesOnly) {
          setNewCount(0);
        } else if (!firstLoad.current && pageNum === 1) {
          const fresh = results.filter((r) => !knownIds.current.has(r.id));
          if (fresh.length) setNewCount((c) => c + fresh.length);
        }
        firstLoad.current = false;
        if (pageNum === 1) {
          for (const r of results) knownIds.current.add(r.id);
        }
        setRows(results);
        setLastRefresh(new Date());
      } catch (err) {
        setError(err.message || "Không thể tải bản tin");
      } finally {
        if (!silent) setLoading(false);
      }
    },
    [source, tag, country, favoritesOnly, publisher, page]
  );

  const toggleFavorite = useCallback(
    async (row) => {
      setFavoriteBusyId(row.id);
      setError("");
      try {
        const path = `/api/v1/threats/${row.id}/favorite/`;
        if (row.is_favorite) {
          await api.delete(path, { retries: 0 });
        } else {
          await api.post(path, {}, { retries: 0 });
        }
        if (favoritesOnly && row.is_favorite) {
          setRows((current) => current.filter((item) => item.id !== row.id));
          setTotalCount((current) => Math.max(0, current - 1));
        } else {
          setRows((current) =>
            current.map((item) =>
              item.id === row.id
                ? { ...item, is_favorite: !row.is_favorite }
                : item
            )
          );
        }
      } catch (err) {
        setError(err.message || "Không thể cập nhật danh sách yêu thích");
      } finally {
        setFavoriteBusyId(null);
      }
    },
    [favoritesOnly]
  );

  useEffect(() => {
    load();
  }, [load]);

  // Live: keep pulling page 1 so newest headlines appear continuously.
  useEffect(() => {
    if (!live || favoritesOnly) return undefined;
    const id = setInterval(() => {
      if (page === 1) {
        load({ silent: true });
      } else {
        // Check page 1 while browsing older pages. If new stories arrive,
        // return to the live timeline automatically instead of waiting for
        // the user to click a notification.
        const qs = wireListQuery({ source, tag, country, publisher, page: 1 });
        api
          .get(`${wireEndpoint(false)}${qs}`)
          .then((data) => {
            const results = data.results || [];
            const fresh = results.filter((r) => !knownIds.current.has(r.id));
            if (fresh.length) {
              setNewCount((c) => c + fresh.length);
              setPage(1);
            }
            setLastRefresh(new Date());
          })
          .catch(() => {});
      }
    }, POLL_MS);
    return () => clearInterval(id);
  }, [live, load, page, source, tag, country, publisher, favoritesOnly]);

  // Refresh immediately when the user returns to this browser tab; do not
  // wait for the next polling interval.
  useEffect(() => {
    if (!live || favoritesOnly) return undefined;
    const refreshWhenVisible = () => {
      if (document.visibilityState === "visible") {
        if (page === 1) {
          load({ silent: true });
        } else {
          const qs = wireListQuery({ source, tag, country, publisher, page: 1 });
          api.get(`${wireEndpoint(favoritesOnly)}${qs}`)
            .then((data) => {
              const fresh = (data.results || []).filter(
                (row) => !knownIds.current.has(row.id)
              );
              if (fresh.length) setPage(1);
              setLastRefresh(new Date());
            })
            .catch(() => {});
        }
      }
    };
    document.addEventListener("visibilitychange", refreshWhenVisible);
    window.addEventListener("focus", refreshWhenVisible);
    return () => {
      document.removeEventListener("visibilitychange", refreshWhenVisible);
      window.removeEventListener("focus", refreshWhenVisible);
    };
  }, [live, load, page, source, tag, country, publisher, favoritesOnly]);

  const resetFiltersToPage1 = (updater) => {
    firstLoad.current = true;
    knownIds.current = new Set();
    setNewCount(0);
    setPage(1);
    updater();
  };

  return (
    <Stack spacing={2}>
      <PageHeader
        title="Trạm tin tức"
        subtitle={
          favoritesOnly
            ? `Chỉ các tin đã theo dõi trong ${WIRE_MAX_AGE_DAYS} ngày gần nhất · ${totalCount} bản tin.`
            : `Tin quân sự và quốc phòng Ấn Độ Dương - Thái Bình Dương trong ${WIRE_MAX_AGE_DAYS} ngày gần nhất · ${totalCount} bản tin.`
        }
        action={
          <Stack direction="row" spacing={1} alignItems="center">
            <Button component={RouterLink} to="/sources" variant="outlined" size="small">
              Nguồn RSS
            </Button>
            <Button
              variant={favoritesOnly ? "contained" : "outlined"}
              size="small"
              startIcon={<FavoriteOutlinedIcon sx={{ fontSize: 17 }} />}
              onClick={() => {
                firstLoad.current = true;
                knownIds.current = new Set();
                setNewCount(0);
                setPage(1);
                setRows([]);
                setTotalCount(0);
                setFavoritesOnly((value) => !value);
              }}
            >
              Yêu thích
            </Button>
            <Button
              variant="outlined"
              onClick={() => {
                setNewCount(0);
                load();
              }}
            >
              Làm mới
            </Button>
          </Stack>
        }
      />
      {error ? <Alert severity="error">{error}</Alert> : null}
      {!favoritesOnly && newCount > 0 ? (
        <Alert
          severity="info"
          action={
            <Stack direction="row" spacing={1}>
              {page !== 1 ? (
                <Button
                  color="inherit"
                  size="small"
                  onClick={() => {
                    setNewCount(0);
                    setPage(1);
                  }}
                >
                  Xem tin mới nhất
                </Button>
              ) : null}

            </Stack>
          }
        >
          Có {newCount} bản tin mới.
        </Alert>
      ) : null}
      <Stack
        direction={{ xs: "column", sm: "row" }}
        spacing={1.5}
        alignItems={{ sm: "center" }}
      >
        <FormControlLabel
          control={<Switch checked={live} onChange={(e) => setLive(e.target.checked)} />}
          label="Tự động làm mới (10 giây)"
        />
        <Typography variant="caption" color="text.secondary">
          {lastRefresh
            ? `Cập nhật ${lastRefresh.toLocaleTimeString("vi-VN")} · ${WIRE_MAX_AGE_DAYS} ngày · ${totalCount} bản tin`
            : ""}
        </Typography>
      </Stack>
      <Stack direction={{ xs: "column", sm: "row" }} spacing={1.5} flexWrap="wrap" useFlexGap>
        <TextField
          select
          size="small"
          label="Loại tin"
          value={source}
          onChange={(e) => resetFiltersToPage1(() => setSource(e.target.value))}
          sx={{ minWidth: 160 }}
        >
          <MenuItem value="">Tất cả</MenuItem>
          <MenuItem value="manual">Thủ công</MenuItem>
          <MenuItem value="news">Tin tức</MenuItem>
          <MenuItem value="osint">OSINT</MenuItem>
        </TextField>
        <TextField
          select
          size="small"
          label="Chủ đề"
          value={tag}
          onChange={(e) => resetFiltersToPage1(() => setTag(e.target.value))}
          sx={{ minWidth: 180 }}
        >
          <MenuItem value="">Tất cả</MenuItem>
          {Object.entries(SCOPE_TOPICS).map(([value, label]) => (
            <MenuItem key={value} value={value}>{label}</MenuItem>
          ))}
          <MenuItem value="exercises">Diễn tập</MenuItem>
          <MenuItem value="maritime">Hàng hải</MenuItem>
          <MenuItem value="procurement">Mua sắm quốc phòng</MenuItem>
          <MenuItem value="force-posture">Bố trí lực lượng</MenuItem>
          <MenuItem value="combat-trends">Xu hướng tác chiến</MenuItem>
          <MenuItem value="national-strategy">Chiến lược quân sự</MenuItem>
          <MenuItem value="cyber-operations">Hoạt động mạng</MenuItem>
          <MenuItem value="security-cooperation">Hợp tác an ninh</MenuItem>
          <MenuItem value="defense-policy">Chính sách quốc phòng</MenuItem>
          <MenuItem value="analysis">Phân tích</MenuItem>
        </TextField>
        <TextField
          select
          size="small"
          label="Tìm theo quốc gia"
          value={country}
          onChange={(e) =>
            resetFiltersToPage1(() => {
              firstLoad.current = true;
              knownIds.current = new Set();
              setNewCount(0);
              setCountry(e.target.value);
            })
          }
          sx={{ minWidth: 200 }}
        >
          <MenuItem value="">Tất cả</MenuItem>
          {WIRE_COUNTRY_FILTER_OPTIONS.map((opt) => (
            <MenuItem key={opt.value} value={opt.value}>
              {opt.label}
            </MenuItem>
          ))}
        </TextField>
        <TextField
          size="small"
          label="Tìm theo nguồn"
          placeholder="vd. secrss, japan-mod, mod.go.jp"
          value={publisherInput}
          onChange={(e) => setPublisherInput(e.target.value)}
          sx={{ minWidth: 240, flexGrow: { xs: 1, md: 0 } }}
        />
        <Box sx={{ flexGrow: 1 }} />
        <ToggleButtonGroup
          exclusive
          size="small"
          value={view}
          onChange={(_event, nextView) => nextView && setView(nextView)}
          aria-label="Kiểu hiển thị dòng tin"
        >
          <ToggleButton value="cards" aria-label="Dạng thẻ">
            <ViewModuleOutlinedIcon sx={{ mr: 0.75, fontSize: 18 }} />
            Thẻ
          </ToggleButton>
          <ToggleButton value="table" aria-label="Dạng bảng">
            <TableRowsOutlinedIcon sx={{ mr: 0.75, fontSize: 18 }} />
            Bảng
          </ToggleButton>
        </ToggleButtonGroup>
      </Stack>
      {view === "cards" ? (
        loading ? (
          <Typography color="text.secondary">Đang tải bản tin…</Typography>
        ) : (
          <Box
            sx={{
              display: "grid",
              gridTemplateColumns: {
                xs: "1fr",
                sm: "repeat(2, minmax(0, 1fr))",
                xl: "repeat(4, minmax(0, 1fr))",
              },
              gap: 1.5,
            }}
          >
            {rows.map((row) => (
              <WireCard
                key={row.id}
                row={row}
                onFavoriteToggle={toggleFavorite}
                favoriteBusy={favoriteBusyId === row.id}
              />
            ))}
          </Box>
        )
      ) : (
        <DataTable
          loading={loading}
          rows={rows}
          columns={[
            {
              id: "id",
              label: "Mã bản tin",
              render: (row) => `#${row._wireRank}`,
            },
            {
              id: "title",
              label: "Tiêu đề",
              render: (row) => (
                <Stack spacing={0.5}>
                  <ExternalTitleLink
                    title={displayWireTitle(row)}
                    href={resolveThreatHref(row)}
                  />
                  <WireTagChips row={row} maxTags={8} />
                </Stack>
              ),
            },
            {
              id: "favorite",
              label: "Theo dõi",
              render: (row) => (
                <FavoriteButton
                  row={row}
                  onToggle={toggleFavorite}
                  busy={favoriteBusyId === row.id}
                />
              ),
            },
            {
              id: "source",
              label: "Nguồn",
              render: (row) => SOURCE_LABELS[row.source] || row.source,
            },
            {
              id: "published_at",
              label: "Ngày đăng",
              render: (row) => (
                <Typography
                  variant="body2"
                  title={`Đã đăng ${row.published_at || "—"} · Lên dòng tin ${row.created_at || "—"}`}
                >
                  {formatDateWithRelative(row.published_at)}
                </Typography>
              ),
            },
          ]}
        />
      )}
      <Stack direction="row" justifyContent="space-between" alignItems="center" flexWrap="wrap" gap={1}>
        <Typography variant="body2" color="text.secondary">
          Trang {page} / {pageCount} · {PAGE_SIZE} bản tin mỗi trang
        </Typography>
        <Pagination
          color="primary"
          count={pageCount}
          page={page}
          onChange={(_e, value) => setPage(value)}
          getItemAriaLabel={(type, value) => {
            if (type === "first") return "Đến trang đầu";
            if (type === "last") return "Đến trang cuối";
            if (type === "next") return "Đến trang tiếp theo";
            if (type === "previous") return "Về trang trước";
            return `Đến trang ${value}`;
          }}
          showFirstButton
          showLastButton
        />
      </Stack>
    </Stack>
  );
}
