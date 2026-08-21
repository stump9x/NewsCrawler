import { useState } from "react";
import {
  Alert,
  Box,
  Button,
  Container,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import ShieldOutlinedIcon from "@mui/icons-material/ShieldOutlined";
import { useAuth } from "../auth/AuthContext";
import TacticalBackdrop from "../components/TacticalBackdrop";

export default function LoginPage() {
  const { login, error, clearError, sessionNotice, clearSessionNotice } = useAuth();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);

  async function onSubmit(event) {
    event.preventDefault();
    setBusy(true);
    clearError();
    clearSessionNotice();
    try {
      await login(username.trim(), password);
    } catch {
      // error surfaced via context
    } finally {
      setBusy(false);
      setPassword("");
    }
  }

  return (
    <Box
      sx={{
        minHeight: "100vh",
        display: "flex",
        alignItems: "center",
        py: 6,
        position: "relative",
      }}
    >
      <TacticalBackdrop />
      <Container maxWidth="sm" sx={{ position: "relative", zIndex: 1 }}>
        <Stack spacing={3} component="form" onSubmit={onSubmit}>
          <Stack direction="row" spacing={1.5} alignItems="center">
            <ShieldOutlinedIcon sx={{ color: "primary.main", fontSize: 42 }} />
            <Typography variant="h3" sx={{ color: "primary.main" }}>
              NewsCrawler
            </Typography>
          </Stack>
          <Typography color="text.secondary">
            Đăng nhập để theo dõi tin tức quân sự và quốc phòng khu vực Ấn Độ Dương - Thái Bình Dương.
          </Typography>
          {sessionNotice ? (
            <Alert severity="info" onClose={clearSessionNotice}>
              {sessionNotice}
            </Alert>
          ) : null}
          {error ? <Alert severity="error">{error}</Alert> : null}
          <TextField
            label="Tên đăng nhập"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            autoComplete="username"
            required
            fullWidth
            autoFocus
          />
          <TextField
            label="Mật khẩu"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="current-password"
            required
            fullWidth
          />
          <Button type="submit" variant="contained" disabled={busy || !username || !password}>
            {busy ? "Đang xác thực…" : "Đăng nhập"}
          </Button>
        </Stack>
      </Container>
    </Box>
  );
}
