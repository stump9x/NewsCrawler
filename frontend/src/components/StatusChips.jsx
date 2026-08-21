import { Chip } from "@mui/material";

const SEVERITY = {
  info: "default",
  low: "info",
  medium: "warning",
  high: "error",
  critical: "error",
};

const STATUS = {
  found: "success",
  not_found: "default",
  error: "error",
  unknown: "warning",
  ok: "success",
  new: "info",
  pending: "info",
  queued: "info",
  running: "warning",
  completed: "success",
  partial: "warning",
  failed: "error",
};

const SEVERITY_LABELS = {
  info: "Thông tin",
  low: "Thấp",
  medium: "Trung bình",
  high: "Cao",
  critical: "Nghiêm trọng",
};

const STATUS_LABELS = {
  found: "Đã tìm thấy",
  not_found: "Không tìm thấy",
  error: "Lỗi",
  unknown: "Chưa xác định",
  ok: "Bình thường",
  new: "Mới",
  pending: "Đang chờ",
  queued: "Đang xếp hàng",
  running: "Đang chạy",
  completed: "Hoàn thành",
  partial: "Một phần",
  failed: "Thất bại",
};

export function SeverityChip({ value }) {
  const v = (value || "").toLowerCase();
  return <Chip size="small" label={SEVERITY_LABELS[v] || v || "—"} color={SEVERITY[v] || "default"} variant="outlined" />;
}

export function StatusChip({ value }) {
  const v = (value || "").toLowerCase();
  return <Chip size="small" label={STATUS_LABELS[v] || v || "—"} color={STATUS[v] || "default"} variant="outlined" />;
}
