import { useEffect, useState } from "react";
import {
  Alert,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Stack,
  TextField,
} from "@mui/material";
import { ApiError, changePassword } from "../api/client";

export default function ChangePasswordDialog({ open, onClose }) {
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");

  useEffect(() => {
    if (!open) return;
    setCurrentPassword("");
    setNewPassword("");
    setConfirmPassword("");
    setError("");
    setSuccess("");
    setBusy(false);
  }, [open]);

  async function onSubmit(event) {
    event.preventDefault();
    setError("");
    setSuccess("");
    if (newPassword.length < 8) {
      setError("Mật khẩu mới cần ít nhất 8 ký tự.");
      return;
    }
    if (newPassword !== confirmPassword) {
      setError("Mật khẩu xác nhận không khớp.");
      return;
    }
    setBusy(true);
    try {
      await changePassword({
        currentPassword,
        newPassword,
        confirmPassword,
      });
      setSuccess("Đã đổi mật khẩu thành công.");
      setCurrentPassword("");
      setNewPassword("");
      setConfirmPassword("");
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.message
          : "Không đổi được mật khẩu. Vui lòng thử lại."
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={open} onClose={busy ? undefined : onClose} fullWidth maxWidth="xs">
      <DialogTitle>Đổi mật khẩu</DialogTitle>
      <DialogContent>
        <Stack
          component="form"
          id="change-password-form"
          spacing={2}
          sx={{ mt: 0.5 }}
          onSubmit={onSubmit}
          autoComplete="on"
        >
          {error ? <Alert severity="error">{error}</Alert> : null}
          {success ? <Alert severity="success">{success}</Alert> : null}
          <TextField
            label="Mật khẩu hiện tại"
            type="password"
            value={currentPassword}
            onChange={(e) => setCurrentPassword(e.target.value)}
            autoComplete="current-password"
            required
            fullWidth
            autoFocus
            inputProps={{ inputMode: "text", spellCheck: false }}
          />
          <TextField
            label="Mật khẩu mới"
            type="password"
            value={newPassword}
            onChange={(e) => setNewPassword(e.target.value)}
            autoComplete="new-password"
            required
            fullWidth
            helperText="Ít nhất 8 ký tự, gõ bằng bàn phím."
            inputProps={{ inputMode: "text", minLength: 8, spellCheck: false }}
          />
          <TextField
            label="Xác nhận mật khẩu mới"
            type="password"
            value={confirmPassword}
            onChange={(e) => setConfirmPassword(e.target.value)}
            autoComplete="new-password"
            required
            fullWidth
            inputProps={{ inputMode: "text", minLength: 8, spellCheck: false }}
          />
        </Stack>
      </DialogContent>
      <DialogActions sx={{ px: 3, pb: 2 }}>
        <Button onClick={onClose} disabled={busy}>
          Đóng
        </Button>
        <Button
          type="submit"
          form="change-password-form"
          variant="contained"
          disabled={
            busy || !currentPassword || !newPassword || !confirmPassword
          }
        >
          {busy ? "Đang lưu…" : "Lưu mật khẩu"}
        </Button>
      </DialogActions>
    </Dialog>
  );
}
