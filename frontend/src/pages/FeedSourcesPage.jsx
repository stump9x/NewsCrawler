import { useCallback, useEffect, useState } from "react";
import {
  Alert,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  MenuItem,
  Stack,
  Switch,
  FormControlLabel,
  TextField,
  Typography,
} from "@mui/material";
import { api } from "../api/client";
import { DataTable } from "../components/DataTable";
import { PageHeader } from "../components/PageHeader";

const EMPTY = {
  name: "",
  url: "",
  category: "news",
  confidence: 2,
  country: "",
  country_code: "",
  is_active: true,
  notes: "",
};

export default function FeedSourcesPage() {
  const [rows, setRows] = useState([]);
  const [totalCount, setTotalCount] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [msg, setMsg] = useState("");
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState(EMPTY);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      // Pull full Watcher-scale catalog (API max_page_size=500)
      const data = await api.get(
        "/api/v1/feed-sources/?page_size=500&ordering=confidence,name"
      );
      setRows(data.results || []);
      setTotalCount(data.count ?? (data.results || []).length);
    } catch (err) {
      setError(err.message || "Không thể tải danh sách nguồn");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  async function createSource(event) {
    event.preventDefault();
    setError("");
    try {
      await api.post("/api/v1/feed-sources/", {
        ...form,
        confidence: Number(form.confidence) || 2,
      });
      setOpen(false);
      setForm(EMPTY);
      setMsg("Đã thêm nguồn RSS — nguồn sẽ được đưa vào lần quét tiếp theo");
      await load();
    } catch (err) {
      setError(err.message || "Không thể tạo nguồn");
    }
  }

  async function toggleActive(row) {
    try {
      await api.patch(`/api/v1/feed-sources/${row.id}/`, { is_active: !row.is_active });
      await load();
    } catch (err) {
      setError(err.message || "Không thể cập nhật nguồn");
    }
  }

  async function queueIngest() {
    setBusy(true);
    setError("");
    setMsg("");
    try {
      const data = await api.post("/api/v1/workers/ingest-feeds/", {
        feeds: ["cert"],
        limit: 30,
        async_mode: true,
      });
      setMsg(`Đã xếp hàng thu thập RSS: ${JSON.stringify(data.tasks || {})}`);
    } catch (err) {
      setError(err.message || "Thu thập RSS thất bại");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Stack spacing={2}>
      <PageHeader
        title="Nguồn RSS"
        subtitle="Danh mục nguồn chính thức, báo chí chuyên ngành và viện nghiên cứu phục vụ theo dõi tin tức quân sự, quốc phòng khu vực Ấn Độ Dương - Thái Bình Dương. Hệ thống tự động quét mỗi 15 phút."
        action={
          <Stack direction="row" spacing={1}>
            <Button variant="outlined" disabled={busy} onClick={queueIngest}>
              Quét ngay
            </Button>
            <Button variant="contained" onClick={() => setOpen(true)}>
              Thêm nguồn
            </Button>
          </Stack>
        }
      />
      {error ? <Alert severity="error">{error}</Alert> : null}
      {msg ? (
        <Alert severity="success" onClose={() => setMsg("")}>
          {msg}
        </Alert>
      ) : null}

      <Typography variant="body2" color="text.secondary">
        Đang hoạt động: {rows.filter((r) => r.is_active).length} / {totalCount || rows.length}
      </Typography>

      <DataTable
        loading={loading}
        rows={rows}
        columns={[
          { id: "name", label: "Tên nguồn" },
          {
            id: "url",
            label: "URL",
            render: (row) => (
              <Typography
                component="a"
                href={row.url}
                target="_blank"
                rel="noopener noreferrer"
                variant="body2"
                sx={{ color: "primary.main", wordBreak: "break-all" }}
              >
                {row.url}
              </Typography>
            ),
          },
          {
            id: "category",
            label: "Danh mục",
            render: (row) => row.category === "news" ? "Tin tức" : "Khác",
          },
          { id: "confidence", label: "Độ tin cậy" },
          { id: "country_code", label: "Mã quốc gia" },
          {
            id: "last_status",
            label: "Lần quét gần nhất",
            render: (row) =>
              row.last_status
                ? `${row.last_status}${row.last_item_count ? ` (${row.last_item_count})` : ""}`
                : "—",
          },
          {
            id: "is_active",
            label: "Hoạt động",
            render: (row) => (
              <Switch
                size="small"
                checked={!!row.is_active}
                onChange={() => toggleActive(row)}
              />
            ),
          },
        ]}
      />

      <Dialog open={open} onClose={() => setOpen(false)} fullWidth maxWidth="sm">
        <form onSubmit={createSource}>
          <DialogTitle>Thêm nguồn RSS / Atom</DialogTitle>
          <DialogContent>
            <Stack spacing={2} sx={{ mt: 1 }}>
              <TextField
                label="Tên nguồn"
                required
                value={form.name}
                onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
              />
              <TextField
                label="URL nguồn cấp"
                required
                value={form.url}
                onChange={(e) => setForm((f) => ({ ...f, url: e.target.value }))}
              />
              <TextField
                select
                label="Danh mục"
                value={form.category}
                onChange={(e) => setForm((f) => ({ ...f, category: e.target.value }))}
              >
                <MenuItem value="news">Tin tức</MenuItem>
                <MenuItem value="other">Khác</MenuItem>
              </TextField>
              <TextField
                type="number"
                label="Độ tin cậy (1–5)"
                value={form.confidence}
                onChange={(e) => setForm((f) => ({ ...f, confidence: e.target.value }))}
                inputProps={{ min: 1, max: 5 }}
              />
              <TextField
                label="Mã quốc gia"
                value={form.country_code}
                onChange={(e) => setForm((f) => ({ ...f, country_code: e.target.value }))}
              />
              <FormControlLabel
                control={
                  <Switch
                    checked={form.is_active}
                    onChange={(e) => setForm((f) => ({ ...f, is_active: e.target.checked }))}
                  />
                }
                label="Hoạt động"
              />
            </Stack>
          </DialogContent>
          <DialogActions>
            <Button onClick={() => setOpen(false)}>Hủy</Button>
            <Button type="submit" variant="contained">
              Lưu
            </Button>
          </DialogActions>
        </form>
      </Dialog>
    </Stack>
  );
}
