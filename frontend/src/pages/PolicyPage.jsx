import { useCallback, useEffect, useState } from "react";
import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
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
  FormControlLabel,
  InputLabel,
  MenuItem,
  Pagination,
  Select,
  Stack,
  Switch,
  TextField,
  Typography,
} from "@mui/material";
import { api } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { PageHeader } from "../components/PageHeader";

function formatDate(value) {
  if (!value) return "Chưa có";
  return new Intl.DateTimeFormat("vi-VN", {
    dateStyle: "short",
    timeStyle: "short",
  }).format(new Date(value));
}

function PolicyMeta({ data }) {
  return (
    <Stack direction="row" spacing={1} useFlexGap flexWrap="wrap">
      <Chip size="small" label={`GIỮ: ${data?.keep_count || 0}`} />
      <Chip size="small" label={`LOẠI: ${data?.exclude_count || 0}`} />
      {data?.inherited_from_admin ? (
        <Chip size="small" color="info" label="Đang kế thừa bản quản trị" />
      ) : null}
    </Stack>
  );
}

export default function PolicyPage() {
  const { isSuperuser, username } = useAuth();
  const [prompt, setPrompt] = useState("");
  const [savedPrompt, setSavedPrompt] = useState("");
  const [recommendationsEnabled, setRecommendationsEnabled] = useState(true);
  const [savedRecommendationsEnabled, setSavedRecommendationsEnabled] = useState(true);
  const [mindmapPrompt, setMindmapPrompt] = useState("");
  const [savedMindmapPrompt, setSavedMindmapPrompt] = useState("");
  const [mindmapMeta, setMindmapMeta] = useState(null);
  const [mindmapLoading, setMindmapLoading] = useState(true);
  const [meta, setMeta] = useState(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  const [referenceOpen, setReferenceOpen] = useState(false);
  const [referenceLoading, setReferenceLoading] = useState(false);
  const [reference, setReference] = useState(null);
  const [mindmapReferenceOpen, setMindmapReferenceOpen] = useState(false);
  const [mindmapReferenceLoading, setMindmapReferenceLoading] = useState(false);
  const [mindmapReference, setMindmapReference] = useState(null);

  const [adminRows, setAdminRows] = useState([]);
  const [adminLoading, setAdminLoading] = useState(false);
  const [selectedUserId, setSelectedUserId] = useState("");
  const [selectedPolicy, setSelectedPolicy] = useState(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [audit, setAudit] = useState(null);
  const [auditLoading, setAuditLoading] = useState(false);
  const [auditUserId, setAuditUserId] = useState("");
  const [loginPage, setLoginPage] = useState(1);
  const [policyPage, setPolicyPage] = useState(1);
  const [revisionOpen, setRevisionOpen] = useState(false);
  const [revision, setRevision] = useState(null);

  const loadAdminPolicies = useCallback(async () => {
    if (!isSuperuser) return;
    setAdminLoading(true);
    try {
      const data = await api.get("/api/v1/auth/wire-filter-prompts/", { retries: 1 });
      setAdminRows(Array.isArray(data?.results) ? data.results : []);
    } catch (err) {
      setError(err.message || "Không thể tải chính sách của các tài khoản.");
    } finally {
      setAdminLoading(false);
    }
  }, [isSuperuser]);

  const loadAudit = useCallback(async (userId = "", loginPageArg = 1, policyPageArg = 1) => {
    if (!isSuperuser) return;
    setAuditLoading(true);
    try {
      const params = new URLSearchParams({
        page_size: "10",
        login_page: String(loginPageArg),
        policy_page: String(policyPageArg),
      });
      if (userId) params.set("user_id", String(userId));
      const data = await api.get(`/api/v1/auth/account-audit/?${params.toString()}`, { retries: 1 });
      setAudit(data || null);
    } catch (err) {
      setError(err.message || "Không thể tải nhật ký tài khoản.");
    } finally {
      setAuditLoading(false);
    }
  }, [isSuperuser]);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const data = await api.get("/api/v1/auth/wire-filter-prompt/", { retries: 1 });
      const current = data?.prompt || "";
      setPrompt(current);
      setSavedPrompt(current);
      setMeta(data || null);
      const recommendations = data?.favorite_recommendations_enabled !== false;
      setRecommendationsEnabled(recommendations);
      setSavedRecommendationsEnabled(recommendations);
    } catch (err) {
      setError(err.message || "Không thể tải chính sách lọc Trạm tin tức.");
    } finally {
      setLoading(false);
    }
  }, []);

  const loadMindmap = useCallback(async () => {
    setMindmapLoading(true);
    try {
      const data = await api.get("/api/v1/auth/mindmap-prompt/", { retries: 1 });
      setMindmapPrompt(data?.prompt || "");
      setSavedMindmapPrompt(data?.prompt || "");
      setMindmapMeta(data || null);
    } catch (err) {
      setError(err.message || "Không thể tải chính sách Mindmap.");
    } finally {
      setMindmapLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
    loadMindmap();
    loadAdminPolicies();
  }, [load, loadMindmap, loadAdminPolicies]);

  useEffect(() => {
    loadAudit(auditUserId, loginPage, policyPage);
  }, [loadAudit, auditUserId, loginPage, policyPage]);

  async function save() {
    setBusy(true);
    setError("");
    setMessage("");
    try {
      const data = await api.patch("/api/v1/auth/wire-filter-prompt/", { prompt, favorite_recommendations_enabled: recommendationsEnabled });
      setPrompt(data.prompt);
      setSavedPrompt(data.prompt);
      setMeta(data);
      const recommendations = data?.favorite_recommendations_enabled !== false;
      setRecommendationsEnabled(recommendations);
      setSavedRecommendationsEnabled(recommendations);
      setMessage(
        isSuperuser
          ? "Đã lưu chính sách quản trị. Worker sẽ áp dụng trong tối đa 30 giây."
          : "Đã lưu và áp dụng chính sách riêng cho tài khoản này."
      );
      if (isSuperuser) {
        await loadAdminPolicies();
        await loadAudit(auditUserId, loginPage, policyPage);
      }
    } catch (err) {
      setError(err.message || "Không thể lưu chính sách lọc.");
    } finally {
      setBusy(false);
    }
  }

  async function saveMindmap() {
    setBusy(true);
    setError("");
    setMessage("");
    try {
      const data = await api.patch("/api/v1/auth/mindmap-prompt/", { prompt: mindmapPrompt });
      setMindmapPrompt(data.prompt);
      setSavedMindmapPrompt(data.prompt);
      setMindmapMeta(data);
      setMessage(isSuperuser ? "Đã lưu chính sách Mindmap quản trị." : "Đã lưu chính sách Mindmap riêng cho tài khoản này.");
      if (isSuperuser) {
        await loadAdminPolicies();
        await loadAudit(auditUserId, loginPage, policyPage);
      }
    } catch (err) {
      setError(err.message || "Không thể lưu chính sách Mindmap.");
    } finally {
      setBusy(false);
    }
  }

  async function restoreMindmap() {
    const question = isSuperuser
      ? "Khôi phục chính sách Mindmap quản trị về bản mặc định?"
      : "Đặt lại chính sách Mindmap của bạn theo bản quản trị hiện tại?";
    if (!window.confirm(question)) return;
    setBusy(true);
    setError("");
    setMessage("");
    try {
      const data = await api.post("/api/v1/auth/mindmap-prompt/", {});
      setMindmapPrompt(data.prompt);
      setSavedMindmapPrompt(data.prompt);
      setMindmapMeta(data);
      setMessage(isSuperuser ? "Đã khôi phục chính sách Mindmap mặc định." : "Đã đặt lại chính sách Mindmap theo Quản trị viên.");
      if (isSuperuser) {
        await loadAdminPolicies();
        await loadAudit(auditUserId, loginPage, policyPage);
      }
    } catch (err) {
      setError(err.message || "Không thể đặt lại chính sách Mindmap.");
    } finally {
      setBusy(false);
    }
  }

  async function openMindmapReference() {
    setMindmapReferenceOpen(true);
    setMindmapReferenceLoading(true);
    setMindmapReference(null);
    try {
      const data = await api.get("/api/v1/auth/mindmap-prompt/admin-reference/", { retries: 1 });
      setMindmapReference(data);
    } catch (err) {
      setMindmapReferenceOpen(false);
      setError(err.message || "Không thể tải chính sách Mindmap quản trị.");
    } finally {
      setMindmapReferenceLoading(false);
    }
  }
  async function restorePolicy() {
    const question = isSuperuser
      ? "Khôi phục chính sách quản trị về bản mặc định đã kiểm duyệt?"
      : "Đặt lại chính sách của bạn theo bản quản trị hiện tại?";
    if (!window.confirm(question)) return;
    setBusy(true);
    setError("");
    setMessage("");
    try {
      const data = await api.post("/api/v1/auth/wire-filter-prompt/", {});
      setPrompt(data.prompt);
      setSavedPrompt(data.prompt);
      setMeta(data);
      setMessage(
        isSuperuser
          ? "Đã khôi phục chính sách quản trị mặc định."
          : "Đã đặt lại chính sách theo bản quản trị hiện tại."
      );
      if (isSuperuser) {
        await loadAdminPolicies();
        await loadAudit(auditUserId, loginPage, policyPage);
      }
    } catch (err) {
      setError(err.message || "Không thể đặt lại chính sách.");
    } finally {
      setBusy(false);
    }
  }

  async function openAdminReference() {
    setReferenceOpen(true);
    setReferenceLoading(true);
    setReference(null);
    try {
      const data = await api.get(
        "/api/v1/auth/wire-filter-prompt/admin-reference/",
        { retries: 1 }
      );
      setReference(data);
    } catch (err) {
      setReferenceOpen(false);
      setError(err.message || "Không thể tải chính sách quản trị để tham khảo.");
    } finally {
      setReferenceLoading(false);
    }
  }

  async function selectUserPolicy(userId) {
    setSelectedUserId(userId);
    setSelectedPolicy(null);
    if (!userId) return;
    setDetailLoading(true);
    try {
      const data = await api.get(`/api/v1/auth/wire-filter-prompts/${userId}/`, {
        retries: 1,
      });
      setSelectedPolicy(data);
    } catch (err) {
      setError(err.message || "Không thể tải chính sách của tài khoản đã chọn.");
    } finally {
      setDetailLoading(false);
    }
  }

  return (
    <Stack spacing={2.5}>
      <PageHeader
        title="Chính sách"
        subtitle={
          isSuperuser
            ? "Quản lý chính sách lọc đang áp dụng toàn hệ thống và xem chính sách riêng của các tài khoản."
            : "Chỉnh sửa chính sách riêng và đối chiếu với chính sách hiện hành của Quản trị viên."
        }
      />

      {error ? <Alert severity="error">{error}</Alert> : null}
      {message ? <Alert severity="success" onClose={() => setMessage("")}>{message}</Alert> : null}

      <Accordion defaultExpanded disableGutters sx={{ bgcolor: "background.paper", border: 1, borderColor: "divider", borderRadius: 1 }}>
        <AccordionSummary
          expandIcon={<Typography component="span" sx={{ fontSize: "1.35rem", fontWeight: 700, lineHeight: 1 }}>⌄</Typography>}
          sx={{ px: 2, minHeight: 58, "&:hover": { bgcolor: "action.hover" }, "& .MuiAccordionSummary-content": { my: 1.25 } }}
        >
          <Typography variant="h6">Chính sách Trạm tin tức</Typography>
        </AccordionSummary>
        <AccordionDetails>
          <Stack
            direction={{ xs: "column", md: "row" }}
            spacing={1.5}
            justifyContent="space-between"
            alignItems={{ xs: "stretch", md: "flex-start" }}
          >
            <Box>
              <Typography color="text.secondary" sx={{ mt: 0.5, mb: 2 }}>
                "Nhập hướng dẫn bằng tiếng Việt để xác định tin cần giữ hoặc loại. Dòng GIỮ: dùng để ưu tiên nội dung phù hợp; dòng LOẠI: dùng để loại nội dung không phù hợp. Có thể tham khảo chính sách hiện tại của Quản trị viên, thay đổi của bạn sẽ không làm ảnh hưởng tới người dùng khác."
              </Typography>
            </Box>
            {!isSuperuser ? (
              <Button variant="outlined" onClick={openAdminReference}>
                Tham khảo từ Quản trị viên
              </Button>
            ) : null}
          </Stack>

          {!loading ? (
            <Alert severity="info" sx={{ mb: 1.5 }}>
              Viết hoặc chỉnh sửa bằng tiếng Việt. Nếu muốn bộ lọc áp dụng chắc chắn, giữ nguyên hai dòng bắt đầu bằng <strong>GIỮ:</strong> và <strong>LOẠI:</strong>; các cụm từ trong hai dòng được ngăn cách bằng dấu chấm phẩy. Không cần thêm mã kỹ thuật hay câu lệnh tiếng Anh.
            </Alert>
          ) : null}
          {!loading ? (
            <Box sx={{ mb: 1.5, p: 1.25, border: 1, borderColor: "divider", borderRadius: 1 }}>
              <FormControlLabel
                control={<Switch checked={recommendationsEnabled} onChange={(event) => setRecommendationsEnabled(event.target.checked)} disabled={busy} />}
                label="Tự động nhận dạng tin và chủ đề yêu thích để tăng đề xuất tương tự"
              />
              <Typography variant="caption" color="text.secondary" sx={{ display: "block", ml: 6 }}>
                Đề xuất tin cùng phân nhóm cụ thể và quốc gia với một tin bạn đang theo dõi. Cùng nguồn hoặc thẻ chung chưa đủ. Chỉ áp dụng cho tài khoản này; giữ thứ tự theo thời gian xuất bản.
              </Typography>
            </Box>
          ) : null}

          {loading ? (
            <Stack direction="row" spacing={1.5} alignItems="center" sx={{ py: 3 }}>
              <CircularProgress size={22} />
              <Typography color="text.secondary">Đang tải chính sách…</Typography>
            </Stack>
          ) : (
            <>
              <TextField
                fullWidth
                multiline
                minRows={20}
                maxRows={34}
                label="Nội dung chính sách bằng tiếng Việt"
                value={prompt}
                onChange={(event) => setPrompt(event.target.value)}
                disabled={busy}
                inputProps={{ maxLength: 12000, spellCheck: false }}
                InputProps={{
                  sx: {
                    alignItems: "flex-start",
                    fontFamily: "ui-monospace, SFMono-Regular, Consolas, monospace",
                    fontSize: "0.9rem",
                    lineHeight: 1.55,
                  },
                }}
              />
              <Stack
                direction={{ xs: "column", sm: "row" }}
                spacing={1}
                alignItems={{ xs: "stretch", sm: "center" }}
                sx={{ mt: 1.5 }}
              >
                <Button
                  variant="contained"
                  onClick={save}
                  disabled={busy || (prompt.trim() === savedPrompt.trim() && recommendationsEnabled === savedRecommendationsEnabled)}
                >
                  {busy ? "Đang lưu…" : "Lưu chính sách"}
                </Button>
                <Button
                  variant="outlined"
                  color="secondary"
                  onClick={restorePolicy}
                  disabled={busy || (isSuperuser && Boolean(meta?.is_default))}
                >
                  {isSuperuser ? "Khôi phục mặc định" : "Đặt lại theo Quản trị viên"}
                </Button>
                <Box sx={{ flex: 1 }} />
                <PolicyMeta data={meta} />
              </Stack>
              <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 1 }}>
                {meta?.updated_at
                  ? `Cập nhật gần nhất: ${formatDate(meta.updated_at)}${
                      meta.updated_by ? ` bởi ${meta.updated_by}` : ""
                    }.`
                  : "Chưa có lịch sử cập nhật."}
              </Typography>
            </>
          )}
        </AccordionDetails>
      </Accordion>

      <Accordion defaultExpanded disableGutters sx={{ bgcolor: "background.paper", border: 1, borderColor: "divider", borderRadius: 1 }}>
        <AccordionSummary
          expandIcon={<Typography component="span" sx={{ fontSize: "1.35rem", fontWeight: 700, lineHeight: 1 }}>⌄</Typography>}
          sx={{ px: 2, minHeight: 58, "&:hover": { bgcolor: "action.hover" }, "& .MuiAccordionSummary-content": { my: 1.25 } }}
        >
          <Typography variant="h6">Chính sách Mindmap</Typography>
        </AccordionSummary>
        <AccordionDetails>
          <Stack direction={{ xs: "column", md: "row" }} spacing={1.5} justifyContent="space-between" alignItems={{ xs: "stretch", md: "flex-start" }}>
            <Box>
              <Typography color="text.secondary" sx={{ mt: 0.5, mb: 2 }}>
                "Nhập hướng dẫn bằng tiếng Việt để AI phân tích quan hệ giữa tin trung tâm và tin ứng viên. Nêu rõ điều kiện về quốc gia, chủ thể, năng lực, sự kiện và mức độ bằng chứng. Có thể tham khảo chính sách hiện tại của Quản trị viên, thay đổi của bạn sẽ không làm ảnh hưởng tới người dùng khác; các rào chắn chống dương tính giả vẫn được giữ cố định."
              </Typography>
            </Box>
            {!isSuperuser ? (
              <Button variant="outlined" onClick={openMindmapReference}>
                Tham khảo từ Quản trị viên
              </Button>
            ) : null}
          </Stack>
          {!mindmapLoading ? (
            <Alert severity="info" sx={{ mb: 1.5 }}>
              Có thể mô tả yêu cầu hoàn toàn bằng tiếng Việt: muốn siết hoặc nới điều kiện liên kết, hãy nêu rõ số lượng quốc gia, loại năng lực, chủ thể, sự kiện và mức độ bằng chứng. Hệ thống tự bổ sung định dạng kết quả; không cần tự viết câu lệnh tiếng Anh hay sửa các mã kỹ thuật trong ví dụ.
            </Alert>
          ) : null}

          {mindmapLoading ? (
            <Stack direction="row" spacing={1.5} alignItems="center" sx={{ py: 3 }}>
              <CircularProgress size={22} />
              <Typography color="text.secondary">Đang tải chính sách Mindmap…</Typography>
            </Stack>
          ) : (
            <>
              <TextField fullWidth multiline minRows={18} maxRows={32} label="Nội dung chính sách Mindmap bằng tiếng Việt" value={mindmapPrompt} onChange={(event) => setMindmapPrompt(event.target.value)} disabled={busy} inputProps={{ maxLength: 20000, spellCheck: false }} InputProps={{ sx: { alignItems: "flex-start", fontFamily: "ui-monospace, SFMono-Regular, Consolas, monospace", fontSize: "0.9rem", lineHeight: 1.55 } }} />
              <Stack direction={{ xs: "column", sm: "row" }} spacing={1} alignItems={{ xs: "stretch", sm: "center" }} sx={{ mt: 1.5 }}>
                <Button variant="contained" onClick={saveMindmap} disabled={busy || mindmapPrompt.trim() === savedMindmapPrompt.trim()}>
                  {busy ? "Đang lưu…" : "Lưu chính sách Mindmap"}
                </Button>
                <Button variant="outlined" color="secondary" onClick={restoreMindmap} disabled={busy || (isSuperuser && Boolean(mindmapMeta?.is_default))}>
                  {isSuperuser ? "Khôi phục mặc định" : "Đặt lại theo Quản trị viên"}
                </Button>
                <Box sx={{ flex: 1 }} />
                <Stack direction="row" spacing={1} useFlexGap flexWrap="wrap">
                  <Chip size="small" label={`Ký tự: ${mindmapPrompt.length}`} />
                  {mindmapMeta?.inherited_from_admin ? <Chip size="small" color="info" label="Đang kế thừa bản quản trị" /> : null}
                </Stack>
              </Stack>
              <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 1 }}>
                {mindmapMeta?.updated_at
                  ? `Cập nhật gần nhất: ${formatDate(mindmapMeta.updated_at)}${mindmapMeta.updated_by ? ` bởi ${mindmapMeta.updated_by}` : ""}.`
                  : "Chưa có thay đổi riêng; đang dùng bản quản trị."}
              </Typography>
            </>
          )}
        </AccordionDetails>
      </Accordion>
      {isSuperuser ? (
        <Accordion disableGutters sx={{ bgcolor: "background.paper", border: 1, borderColor: "divider", borderRadius: 1 }}>
          <AccordionSummary
          expandIcon={<Typography component="span" sx={{ fontSize: "1.35rem", fontWeight: 700, lineHeight: 1 }}>⌄</Typography>}
          sx={{ px: 2, minHeight: 58, "&:hover": { bgcolor: "action.hover" }, "& .MuiAccordionSummary-content": { my: 1.25 } }}
        >
            <Typography variant="h6">Chính sách của các tài khoản</Typography>
          </AccordionSummary>
          <AccordionDetails>
            <Typography color="text.secondary" sx={{ mt: 0.5, mb: 2 }}>
              Chọn một tài khoản để xem bản hiện tại. Nội dung tại đây chỉ đọc;
              mỗi user tự chịu trách nhiệm chỉnh sửa bản của mình.
            </Typography>
            {adminLoading ? (
              <CircularProgress size={22} />
            ) : adminRows.length ? (
              <>
                <FormControl fullWidth sx={{ maxWidth: 520 }}>
                  <InputLabel id="policy-user-label">Tài khoản</InputLabel>
                  <Select
                    labelId="policy-user-label"
                    label="Tài khoản"
                    value={selectedUserId}
                    onChange={(event) => selectUserPolicy(event.target.value)}
                  >
                    {adminRows.map((row) => (
                      <MenuItem key={row.owner_id} value={String(row.owner_id)}>
                        {row.owner_username}
                        {!row.is_active ? " — đã khóa" : ""}
                        {row.inherited_from_admin ? " — đang kế thừa" : ""}
                      </MenuItem>
                    ))}
                  </Select>
                </FormControl>
                {detailLoading ? (
                  <Stack direction="row" spacing={1.5} alignItems="center" sx={{ py: 3 }}>
                    <CircularProgress size={22} />
                    <Typography color="text.secondary">Đang tải chính sách tài khoản…</Typography>
                  </Stack>
                ) : selectedPolicy ? (
                  <Stack spacing={1.5} sx={{ mt: 2 }}>
                    <PolicyMeta data={selectedPolicy} />
                    <TextField
                      fullWidth
                      multiline
                      minRows={14}
                      maxRows={26}
                      label={`Chính sách của ${selectedPolicy.owner_username}`}
                      value={selectedPolicy.prompt || ""}
                      InputProps={{
                        readOnly: true,
                        sx: {
                          alignItems: "flex-start",
                          fontFamily: "ui-monospace, SFMono-Regular, Consolas, monospace",
                          fontSize: "0.9rem",
                          lineHeight: 1.55,
                        },
                      }}
                    />
                    <Typography variant="caption" color="text.secondary">
                      Cập nhật gần nhất: {formatDate(selectedPolicy.updated_at)}
                      {selectedPolicy.updated_by ? ` bởi ${selectedPolicy.updated_by}` : ""}.
                    </Typography>
                    {selectedPolicy.mindmap?.prompt ? (
                      <TextField fullWidth multiline minRows={12} maxRows={22} label={`Prompt Mindmap của ${selectedPolicy.owner_username}`} value={selectedPolicy.mindmap.prompt} InputProps={{ readOnly: true, sx: { alignItems: "flex-start", fontFamily: "ui-monospace, SFMono-Regular, Consolas, monospace", fontSize: "0.9rem", lineHeight: 1.55 } }} />
                    ) : null}
                  </Stack>
                ) : null}
              </>
            ) : (
              <Alert severity="info">Chưa có tài khoản người dùng nào.</Alert>
            )}
          </AccordionDetails>
        </Accordion>
      ) : null}

      {isSuperuser ? (
        <Accordion disableGutters sx={{ bgcolor: "background.paper", border: 1, borderColor: "divider", borderRadius: 1 }}>
          <AccordionSummary
          expandIcon={<Typography component="span" sx={{ fontSize: "1.35rem", fontWeight: 700, lineHeight: 1 }}>⌄</Typography>}
          sx={{ px: 2, minHeight: 58, "&:hover": { bgcolor: "action.hover" }, "& .MuiAccordionSummary-content": { my: 1.25 } }}
        >
            <Typography variant="h6">Nhật ký tài khoản</Typography>
          </AccordionSummary>
          <AccordionDetails>
            <Typography color="text.secondary" sx={{ mt: 0.5, mb: 2 }}>
              Theo dõi đăng nhập, đăng xuất, đăng nhập thất bại và mọi lần cập nhật
              hoặc đặt lại chính sách. Chỉ Quản trị viên được xem mục này.
            </Typography>
            <FormControl fullWidth sx={{ maxWidth: 520, mb: 2 }}>
              <InputLabel id="audit-user-label">Phạm vi tài khoản</InputLabel>
              <Select
                labelId="audit-user-label"
                label="Phạm vi tài khoản"
                value={auditUserId}
                onChange={(event) => {
                  setAuditUserId(event.target.value);
                  setLoginPage(1);
                  setPolicyPage(1);
                }}
              >
                <MenuItem value="">Tất cả tài khoản</MenuItem>
                {(audit?.accounts || []).map((account) => (
                  <MenuItem key={account.id} value={String(account.id)}>
                    {account.username}
                    {account.is_superuser ? " — Quản trị viên" : ""}
                    {!account.is_active ? " — đã khóa" : ""}
                  </MenuItem>
                ))}
              </Select>
            </FormControl>

            {auditLoading ? (
              <Stack direction="row" spacing={1.5} alignItems="center" sx={{ py: 3 }}>
                <CircularProgress size={22} />
                <Typography color="text.secondary">Đang tải nhật ký…</Typography>
              </Stack>
            ) : (
              <Stack spacing={2.5}>
                <Box>
                  <Typography variant="subtitle1" sx={{ fontWeight: 700, mb: 1 }}>
                    Lịch sử đăng nhập
                  </Typography>
                  {(audit?.login_events || []).length ? (
                    <Stack divider={<Divider flexItem />}>
                      {audit.login_events.map((event) => (
                        <Box key={event.id} sx={{ py: 1.15 }}>
                          <Stack
                            direction={{ xs: "column", md: "row" }}
                            spacing={{ xs: 0.25, md: 1.5 }}
                            justifyContent="space-between"
                          >
                            <Typography>
                              <strong>{event.username}</strong> — {event.event_label}
                            </Typography>
                            <Typography variant="body2" color="text.secondary">
                              {formatDate(event.occurred_at)}
                            </Typography>
                          </Stack>
                          <Typography variant="caption" color="text.secondary">
                            IP: {event.ip_address || "Không xác định"}
                            {event.user_agent ? ` · ${event.user_agent}` : ""}
                          </Typography>
                        </Box>
                      ))}
                    </Stack>
                  ) : (
                    <Alert severity="info">Chưa có lịch sử đăng nhập trong phạm vi đã chọn.</Alert>
                  )}
                  {(audit?.login_pagination?.total_pages || 0) > 1 ? (
                    <Stack spacing={0.75} alignItems="center" sx={{ mt: 1.5 }}>
                      <Pagination
                        size="small"
                        color="primary"
                        showFirstButton
                        showLastButton
                        page={audit.login_pagination.page}
                        count={audit.login_pagination.total_pages}
                        onChange={(_, value) => setLoginPage(value)}
                      />
                      <Typography variant="caption" color="text.secondary">
                        {`Trang ${audit.login_pagination.page}/${audit.login_pagination.total_pages} · ${audit.login_pagination.total} lần gần nhất`}
                      </Typography>
                    </Stack>
                  ) : null}
                </Box>

                <Box>
                  <Typography variant="subtitle1" sx={{ fontWeight: 700, mb: 1 }}>
                    Lịch sử thay đổi chính sách
                  </Typography>
                  {(audit?.policy_changes || []).length ? (
                    <Stack divider={<Divider flexItem />}>
                      {audit.policy_changes.map((change) => (
                        <Stack
                          key={change.id}
                          direction={{ xs: "column", md: "row" }}
                          spacing={1}
                          justifyContent="space-between"
                          alignItems={{ xs: "stretch", md: "center" }}
                          sx={{ py: 1.15 }}
                        >
                          <Box>
                            <Typography>
                              <strong>{change.owner_username}</strong> — {change.action_label}{change.policy_type_label ? ` · ${change.policy_type_label}` : ""}
                            </Typography>
                            <Typography variant="caption" color="text.secondary">
                              {formatDate(change.created_at)}
                              {change.actor_username ? ` · thực hiện bởi ${change.actor_username}` : ""}
                              {` · GIỮ ${change.keep_count} · LOẠI ${change.exclude_count}`}
                            </Typography>
                          </Box>
                          <Button
                            size="small"
                            variant="outlined"
                            onClick={() => {
                              setRevision(change);
                              setRevisionOpen(true);
                            }}
                          >
                            Xem bản
                          </Button>
                        </Stack>
                      ))}
                    </Stack>
                  ) : (
                    <Alert severity="info">Chưa có thay đổi chính sách trong phạm vi đã chọn.</Alert>
                  )}
                  {(audit?.policy_pagination?.total_pages || 0) > 1 ? (
                    <Stack spacing={0.75} alignItems="center" sx={{ mt: 1.5 }}>
                      <Pagination
                        size="small"
                        color="primary"
                        showFirstButton
                        showLastButton
                        page={audit.policy_pagination.page}
                        count={audit.policy_pagination.total_pages}
                        onChange={(_, value) => setPolicyPage(value)}
                      />
                      <Typography variant="caption" color="text.secondary">
                        {`Trang ${audit.policy_pagination.page}/${audit.policy_pagination.total_pages} · ${audit.policy_pagination.total} thay đổi gần nhất`}
                      </Typography>
                    </Stack>
                  ) : null}
                </Box>
              </Stack>
            )}
          </AccordionDetails>
        </Accordion>
      ) : null}

      <Dialog
        open={referenceOpen}
        onClose={() => setReferenceOpen(false)}
        fullWidth
        maxWidth="md"
      >
        <DialogTitle>Tham khảo từ Quản trị viên</DialogTitle>
        <DialogContent dividers>
          {referenceLoading ? (
            <Stack direction="row" spacing={1.5} alignItems="center" sx={{ py: 3 }}>
              <CircularProgress size={22} />
              <Typography color="text.secondary">Đang tải chính sách quản trị…</Typography>
            </Stack>
          ) : reference ? (
            <Stack spacing={1.5}>
              <Alert severity="info">
                Bản này chỉ để tham khảo và không bị thay đổi khi bạn chỉnh chính sách riêng.
              </Alert>
              <PolicyMeta data={reference} />
              <TextField
                fullWidth
                multiline
                minRows={18}
                maxRows={30}
                label="Chính sách quản trị hiện tại"
                value={reference.prompt || ""}
                InputProps={{
                  readOnly: true,
                  sx: {
                    alignItems: "flex-start",
                    fontFamily: "ui-monospace, SFMono-Regular, Consolas, monospace",
                    fontSize: "0.9rem",
                    lineHeight: 1.55,
                  },
                }}
              />
              <Typography variant="caption" color="text.secondary">
                Cập nhật gần nhất: {formatDate(reference.updated_at)}
                {reference.updated_by ? ` bởi ${reference.updated_by}` : ""}.
              </Typography>
            </Stack>
          ) : null}
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setReferenceOpen(false)}>Đóng</Button>
        </DialogActions>
      </Dialog>

      <Dialog
        open={mindmapReferenceOpen}
        onClose={() => setMindmapReferenceOpen(false)}
        fullWidth
        maxWidth="md"
      >
        <DialogTitle>Tham khảo chính sách Mindmap từ Quản trị viên</DialogTitle>
        <DialogContent dividers>
          {mindmapReferenceLoading ? (
            <Stack direction="row" spacing={1.5} alignItems="center" sx={{ py: 3 }}>
              <CircularProgress size={22} />
              <Typography color="text.secondary">Đang tải chính sách Mindmap quản trị…</Typography>
            </Stack>
          ) : mindmapReference ? (
            <Stack spacing={1.5}>
              <Alert severity="info">Bản này chỉ để tham khảo và không thay đổi chính sách riêng của bạn.</Alert>
              <TextField fullWidth multiline minRows={18} maxRows={30} label="Chính sách Mindmap quản trị hiện tại" value={mindmapReference.prompt || ""} InputProps={{ readOnly: true, sx: { alignItems: "flex-start", fontFamily: "ui-monospace, SFMono-Regular, Consolas, monospace", fontSize: "0.9rem", lineHeight: 1.55 } }} />
              <Typography variant="caption" color="text.secondary">
                {mindmapReference.updated_at ? `Cập nhật gần nhất: ${formatDate(mindmapReference.updated_at)}${mindmapReference.updated_by ? ` bởi ${mindmapReference.updated_by}` : ""}.` : "Chính sách quản trị hiện hành."}
              </Typography>
            </Stack>
          ) : null}
        </DialogContent>
        <DialogActions><Button onClick={() => setMindmapReferenceOpen(false)}>Đóng</Button></DialogActions>
      </Dialog>
      <Dialog
        open={revisionOpen}
        onClose={() => setRevisionOpen(false)}
        fullWidth
        maxWidth="md"
      >
        <DialogTitle>
          Bản chính sách lịch sử{revision?.owner_username ? ` — ${revision.owner_username}` : ""}
        </DialogTitle>
        <DialogContent dividers>
          {revision ? (
            <Stack spacing={1.5}>
              <Typography color="text.secondary">
                {revision.action_label} lúc {formatDate(revision.created_at)}
                {revision.actor_username ? ` bởi ${revision.actor_username}` : ""}.
              </Typography>
              <PolicyMeta data={revision} />
              <TextField
                fullWidth
                multiline
                minRows={18}
                maxRows={30}
                value={revision.prompt || ""}
                InputProps={{
                  readOnly: true,
                  sx: {
                    alignItems: "flex-start",
                    fontFamily: "ui-monospace, SFMono-Regular, Consolas, monospace",
                    fontSize: "0.9rem",
                    lineHeight: 1.55,
                  },
                }}
              />
            </Stack>
          ) : null}
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setRevisionOpen(false)}>Đóng</Button>
        </DialogActions>
      </Dialog>
    </Stack>
  );
}
