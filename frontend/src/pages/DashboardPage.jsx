import { useEffect, useState } from "react";
import { Alert, Avatar, Chip, Stack, Typography } from "@mui/material";
import { api } from "../api/client";
import { KpiStrip } from "../components/KpiStrip";
import { PageHeader } from "../components/PageHeader";
import { DataTable } from "../components/DataTable";
import { wireDisplayInstant } from "../utils/dateTime";
import { ExternalTitleLink, resolveThreatHref } from "../components/ExternalTitleLink";
import { displayWireTitle } from "../utils/wireTitle";
import {
  geographyFlagUrl,
  geographyTagLabel,
  isGeographyTag,
  preferCountryGeography,
} from "../utils/wireTags";

function CountryTags({ row }) {
  const countries = preferCountryGeography(
    (row.tags || []).filter(isGeographyTag)
  ).slice(0, 2);
  if (!countries.length) return null;

  return (
    <Stack direction="row" spacing={0.5} flexWrap="wrap" useFlexGap>
      {countries.map((tag) => {
        const flagSrc = geographyFlagUrl(tag, 20);
        return (
          <Chip
            key={tag.id || tag.slug || tag.name}
            size="small"
            variant="outlined"
            color="warning"
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
            label={geographyTagLabel(tag)}
          />
        );
      })}
    </Stack>
  );
}

function formatOverviewDate(row, now = Date.now()) {
  const when = wireDisplayInstant(row, now);
  if (!when) return "—";
  const elapsedMinutes = Math.max(
    0,
    Math.floor((Number(now) - when.getTime()) / 60_000)
  );
  let relative = "vừa xong";
  if (elapsedMinutes >= 24 * 60) {
    relative = `${Math.floor(elapsedMinutes / (24 * 60))} ngày trước`;
  } else if (elapsedMinutes >= 60) {
    relative = `${Math.floor(elapsedMinutes / 60)} giờ trước`;
  } else if (elapsedMinutes >= 1) {
    relative = `${elapsedMinutes} phút trước`;
  }
  return `${when.toLocaleDateString("vi-VN")} · ${relative}`;
}

export default function DashboardPage() {
  const [stats, setStats] = useState({ threats: 0, sources: 0, briefings: 0 });
  const [recent, setRecent] = useState([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    Promise.all([
      api.get("/api/v1/threats/?wire_feed=true&page_size=10&ordering=-published_at,-id"),
      api.get("/api/v1/feed-sources/?page_size=1"),
      api.get("/api/v1/ai/briefings/?page_size=1"),
    ]).then(([wire, feeds, briefs]) => {
      setStats({ threats: wire.count || 0, sources: feeds.count || 0, briefings: briefs.count || 0 });
      // The API is the single canonical order: newest publication time first,
      // then id as a deterministic tie-breaker. Ranks are oldest-first and are
      // kept by the API; this page must not sort the rows again.
      setRecent([...(wire.results || [])]);
    }).catch((err) => setError(err.message || "Không thể tải trang tổng quan"))
      .finally(() => setLoading(false));
  }, []);

  return (
    <Stack spacing={2}>
      <PageHeader
        title="Tổng quan"
        subtitle="Thông tin tình báo nguồn mở về quân sự và quốc phòng khu vực Ấn Độ Dương - Thái Bình Dương: tin tức mới nhất, thông cáo chính thức, diễn tập, bố trí lực lượng, mua sắm quốc phòng, hoạt động hàng hải và tác chiến mạng."
      />
      {error ? <Alert severity="error">{error}</Alert> : null}
      <KpiStrip items={[
        { label: "Tổng số bản tin", value: loading ? "…" : stats.threats },
        { label: "Nguồn đang theo dõi", value: loading ? "…" : stats.sources, accent: "secondary.main" },
        { label: "Bản tóm tắt AI", value: loading ? "…" : stats.briefings, accent: "warning.main" },
      ]} />
      <Typography variant="h6">Tin mới nhất trên Trạm tin tức</Typography>
      <DataTable loading={loading} rows={recent} empty="Chưa có bản tin — hãy chạy quét nguồn RSS."
        columns={[
          {
            id: "title",
            label: "Tiêu đề",
            render: (row) => (
              <Stack spacing={0.5}>
                <ExternalTitleLink
                  title={displayWireTitle(row)}
                  href={resolveThreatHref(row)}
                />
                <CountryTags row={row} />
              </Stack>
            ),
          },
          {
            id: "published_at",
            label: "Ngày đăng",
            render: (row) => formatOverviewDate(row),
          },
        ]} />
    </Stack>
  );
}
