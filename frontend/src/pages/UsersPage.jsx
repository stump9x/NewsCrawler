import { useCallback, useEffect, useState } from "react";
import {
  Alert,
  Box,
  Button,
  Card,
  CardContent,
  Chip,
  CircularProgress,
  Stack,
  Switch,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  TextField,
  Typography,
} from "@mui/material";
import { api } from "../api/client";
import { PageHeader } from "../components/PageHeader";

const EMPTY = { username: "", password: "", confirm_password: "" };

function formatDate(value) {
  if (!value) return "Chưa đăng nhập";
  return new Intl.DateTimeFormat("vi-VN", {
    dateStyle: "short",
    timeStyle: "short",
  }).format(new Date(value));
}

export default function UsersPage() {
  const [users, setUsers] = useState([]);
  const [form, setForm] = useState(EMPTY);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const data = await api.get("/api/v1/auth/users/", { retries: 1 });
      setUsers(data?.results || []);
    } catch (err) {
      setError(err.message || "Không thể tải danh sách tài khoản.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  async function createUser(event) {
    event.preventDefault();
    setBusy(true);
    setError("");
    setMessage("");
    try {
      await api.post("/api/v1/auth/users/", form);
      setForm(EMPTY);
      setMessage("Đã tạo người dùng. Tài khoản này không có quyền quản lý người dùng.");
      await load();
    } catch (err) {
      setError(err.message || "Không thể tạo tài khoản.");
    } finally {
      setBusy(false);
    }
  }

  async function toggleActive(user) {
    setError("");
    setMessage("");
    try {
      await api.patch(`/api/v1/auth/users/${user.id}/`, {
        is_active: !user.is_active,
      });
      setMessage(
        user.is_active
          ? `Đã khóa tài khoản ${user.username} và thu hồi phiên đăng nhập.`
          : `Đã mở lại tài khoản ${user.username}.`
      );
      await load();
    } catch (err) {
      setError(err.message || "Không thể cập nhật tài khoản.");
    }
  }

  const canSubmit =
    form.username.trim().length >= 3 &&
    form.password.length >= 8 &&
    form.password === form.confirm_password;

  return (
    <Stack spacing={2.5}>
      <PageHeader
        title="Quản lý người dùng"
        subtitle="Chỉ quản trị viên hệ thống truy cập được mục này. Tài khoản mới có đầy đủ chức năng vận hành NewsCrawler nhưng không thể xem hoặc quản lý tài khoản khác."
      />

      {error ? <Alert severity="error">{error}</Alert> : null}
      {message ? <Alert severity="success" onClose={() => setMessage("")}>{message}</Alert> : null}

      <Card variant="outlined">
        <CardContent component="form" onSubmit={createUser}>
          <Typography variant="h6" sx={{ mb: 2 }}>Tạo người dùng</Typography>
          <Box
            sx={{
              display: "grid",
              gridTemplateColumns: { xs: "1fr", md: "1fr 1fr" },
              gap: 2,
            }}
          >
            <TextField
              label="Tên đăng nhập"
              value={form.username}
              onChange={(event) => setForm({ ...form, username: event.target.value })}
              autoComplete="off"
              required
              inputProps={{ minLength: 3, maxLength: 150 }}
            />
            <TextField
              label="Mật khẩu ban đầu"
              type="password"
              value={form.password}
              onChange={(event) => setForm({ ...form, password: event.target.value })}
              autoComplete="new-password"
              required
              helperText="Tối thiểu 8 ký tự và phải đạt chính sách mật khẩu của hệ thống."
            />
            <TextField
              label="Xác nhận mật khẩu"
              type="password"
              value={form.confirm_password}
              onChange={(event) => setForm({ ...form, confirm_password: event.target.value })}
              autoComplete="new-password"
              required
              error={Boolean(form.confirm_password && form.password !== form.confirm_password)}
              helperText={
                form.confirm_password && form.password !== form.confirm_password
                  ? "Mật khẩu xác nhận không khớp."
                  : " "
              }
            />
          </Box>
          <Button type="submit" variant="contained" disabled={busy || !canSubmit} sx={{ mt: 2 }}>
            {busy ? "Đang tạo…" : "Tạo người dùng"}
          </Button>
        </CardContent>
      </Card>

      <Card variant="outlined">
        <CardContent>
          <Typography variant="h6" sx={{ mb: 1.5 }}>Danh sách tài khoản</Typography>
          {loading ? (
            <Stack direction="row" spacing={1.5} alignItems="center" sx={{ py: 3 }}>
              <CircularProgress size={22} />
              <Typography color="text.secondary">Đang tải…</Typography>
            </Stack>
          ) : (
            <TableContainer>
              <Table size="small">
                <TableHead>
                  <TableRow>
                    <TableCell>Tài khoản</TableCell>
                    <TableCell>Vai trò</TableCell>
                    <TableCell>Lần đăng nhập gần nhất</TableCell>
                    <TableCell align="right">Hoạt động</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {users.map((user) => (
                    <TableRow key={user.id} hover>
                      <TableCell>
                        <Typography fontWeight={600}>{user.username}</Typography>
                      </TableCell>
                      <TableCell>
                        <Chip
                          size="small"
                          color={user.is_superuser ? "primary" : "default"}
                          label={user.is_superuser ? "Quản trị viên" : "Người dùng"}
                        />
                      </TableCell>
                      <TableCell>{formatDate(user.last_login)}</TableCell>
                      <TableCell align="right">
                        <Switch
                          checked={Boolean(user.is_active)}
                          disabled={Boolean(user.is_superuser)}
                          onChange={() => toggleActive(user)}
                          inputProps={{ "aria-label": `Trạng thái ${user.username}` }}
                        />
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </TableContainer>
          )}
        </CardContent>
      </Card>

    </Stack>
  );
}
