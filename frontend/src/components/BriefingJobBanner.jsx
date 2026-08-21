import { useEffect, useState } from "react";
import { Link as RouterLink } from "react-router-dom";
import {
  Alert,
  Button,
  LinearProgress,
  Paper,
  Stack,
  Typography,
} from "@mui/material";
import { api } from "../api/client";
import {
  clearActiveBriefingJob,
  patchActiveBriefingJob,
  readActiveBriefingJob,
  subscribeActiveBriefingJob,
} from "../utils/briefingJobStore";

const TERMINAL = new Set(["ready", "failed"]);

/**
 * Global banner: keeps polling an in-flight briefing even when user leaves
 * the Báo cáo nhanh page (nav / reload-safe via localStorage).
 */
export default function BriefingJobBanner() {
  const [job, setJob] = useState(() => readActiveBriefingJob());
  const [live, setLive] = useState(null);

  useEffect(() => subscribeActiveBriefingJob(setJob), []);

  useEffect(() => {
    if (!job?.id) {
      setLive(null);
      return undefined;
    }
    let cancelled = false;
    let timer;

    async function tick() {
      try {
        const row = await api.get(`/api/v1/ai/briefings/${job.id}/`);
        if (cancelled) return;
        setLive(row);
        const status = String(row?.status || "").toLowerCase();
        patchActiveBriefingJob({
          progress_pct: row.progress_pct,
          progress: row.progress,
          title: row.title,
        });
        if (TERMINAL.has(status)) {
          // Keep job record briefly so Intelligence page can pick up result,
          // then clear so banner hides after user sees completion.
          setTimeout(() => {
            if (!cancelled) clearActiveBriefingJob();
          }, 8000);
          return;
        }
      } catch {
        /* network blip — keep trying */
      }
      if (!cancelled) {
        timer = setTimeout(tick, 2500);
      }
    }

    tick();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [job?.id]);

  if (!job?.id) return null;

  const status = String(live?.status || "pending").toLowerCase();
  const pct = Math.max(
    0,
    Math.min(
      100,
      Number(live?.progress_pct ?? job.progress_pct) || 0
    )
  );
  const msg =
    live?.progress ||
    job.progress ||
    (status === "ready"
      ? "Báo cáo đã xong"
      : status === "failed"
        ? "Báo cáo thất bại"
        : "Đang tạo báo cáo…");
  const severity =
    status === "failed" ? "error" : status === "ready" ? "success" : "info";

  return (
    <Paper
      elevation={6}
      sx={{
        position: "fixed",
        left: { xs: 12, md: 260 },
        right: 12,
        bottom: 12,
        zIndex: (t) => t.zIndex.snackbar,
        p: 1.5,
        maxWidth: 560,
        ml: "auto",
      }}
    >
      <Alert
        severity={severity}
        sx={{ bgcolor: "transparent", p: 0, "& .MuiAlert-message": { width: "100%" } }}
        action={
          <Stack direction="row" spacing={0.5} alignItems="center">
            <Button
              color="inherit"
              size="small"
              component={RouterLink}
              to="/intelligence"
            >
              Xem
            </Button>
            {TERMINAL.has(status) ? (
              <Button color="inherit" size="small" onClick={() => clearActiveBriefingJob()}>
                Đóng
              </Button>
            ) : null}
          </Stack>
        }
      >
        <Stack spacing={0.75} sx={{ width: "100%", minWidth: 200 }}>
          <Typography variant="body2" fontWeight={600}>
            {live?.title || job.title || "Báo cáo nhanh đang chạy"}
          </Typography>
          <Stack direction="row" justifyContent="space-between" gap={1}>
            <Typography variant="caption" color="text.secondary">
              {msg}
            </Typography>
            <Typography variant="caption" fontWeight={700}>
              {status === "ready" ? "100%" : `${Math.round(pct)}%`}
            </Typography>
          </Stack>
          {!TERMINAL.has(status) ? (
            <LinearProgress
              variant="determinate"
              value={pct}
              sx={{ height: 8, borderRadius: 1 }}
            />
          ) : null}
        </Stack>
      </Alert>
    </Paper>
  );
}
