/**
 * Browser API client for NewsCrawler DRF backend.
 * Auth: short-lived DRF Token via /api/v1/auth/login/ (localStorage, tab-synced).
 * Never stores passwords. Never logs secrets.
 *
 * credentials: "omit" — do not attach Django session cookies to API calls.
 */

const TOKEN_KEY = "nc_api_token";
const SESSION_KEY = "nc_api_session"; // username hint for UI only
const LEGACY_SESSION_KEY = "nc_api_token"; // sessionStorage migration

export function getApiBase() {
  const base = import.meta.env.VITE_API_BASE_URL;
  return typeof base === "string" ? base.replace(/\/$/, "") : "";
}

function readStorage(storage, key) {
  try {
    return storage.getItem(key) || "";
  } catch {
    return "";
  }
}

function writeStorage(storage, key, value) {
  try {
    if (value) storage.setItem(key, value);
    else storage.removeItem(key);
  } catch {
    // private mode / quota — ignore
  }
}

/** Migrate old sessionStorage token → localStorage (multi-tab safe). */
function migrateLegacyToken() {
  try {
    const legacy = sessionStorage.getItem(LEGACY_SESSION_KEY) || "";
    if (legacy && !localStorage.getItem(TOKEN_KEY)) {
      localStorage.setItem(TOKEN_KEY, legacy);
    }
    if (legacy) sessionStorage.removeItem(LEGACY_SESSION_KEY);
  } catch {
    // ignore
  }
}

export function loadStoredAuth() {
  migrateLegacyToken();
  return readStorage(localStorage, TOKEN_KEY);
}

export function storeAuth(token, meta = {}) {
  writeStorage(localStorage, TOKEN_KEY, token || "");
  // Keep sessionStorage in sync for older code paths / same-tab reads.
  writeStorage(sessionStorage, TOKEN_KEY, token || "");
  if (meta.username) {
    writeStorage(localStorage, SESSION_KEY, meta.username);
  }
}

export function clearAuth() {
  writeStorage(localStorage, TOKEN_KEY, "");
  writeStorage(sessionStorage, TOKEN_KEY, "");
  writeStorage(localStorage, SESSION_KEY, "");
}

export function hasAuth() {
  return Boolean(loadStoredAuth());
}

export function loadStoredUsername() {
  return readStorage(localStorage, SESSION_KEY);
}

export class ApiError extends Error {
  constructor(message, status, payload, code = "") {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.payload = payload;
    this.code = code || (payload && payload.code) || "";
  }
}

const SESSION_EVENT = "nc:session-expired";

export function onSessionExpired(handler) {
  const listener = (event) => handler(event.detail || {});
  window.addEventListener(SESSION_EVENT, listener);
  return () => window.removeEventListener(SESSION_EVENT, listener);
}

function emitSessionExpired(detail) {
  try {
    window.dispatchEvent(new CustomEvent(SESSION_EVENT, { detail }));
  } catch {
    // ignore
  }
}

export function humanizeApiError(status, payload, { isLogin = false } = {}) {
  const nested =
    payload && typeof payload.detail === "object" && payload.detail
      ? payload.detail
      : null;
  const code = String(
    (payload && payload.code) || (nested && nested.code) || ""
  ).toLowerCase();
  const detailRaw =
    (nested && (nested.detail || nested.message)) ||
    (payload && (payload.detail || payload.message || payload.error)) ||
    "";
  const detail = typeof detailRaw === "string" ? detailRaw : JSON.stringify(detailRaw);
  const lower = detail.toLowerCase();

  if (status === 429 || lower.includes("throttl")) {
    return "Quá nhiều lần thử. Vui lòng đợi khoảng một phút rồi thử lại.";
  }
  if (isLogin && (status === 401 || code === "invalid_credentials")) {
    return "Tên đăng nhập hoặc mật khẩu không đúng.";
  }
  if (code === "wrong_current_password") {
    return "Mật khẩu hiện tại không đúng.";
  }
  if (code === "password_mismatch") {
    return "Mật khẩu xác nhận không khớp.";
  }
  if (
    code === "session_expired" ||
    code === "session_invalid" ||
    lower.includes("token expired") ||
    lower.includes("invalid token") ||
    lower.includes("không còn hiệu lực") ||
    lower.includes("hết hạn")
  ) {
    return "Phiên đăng nhập đã hết hạn. Vui lòng đăng nhập lại.";
  }
  if (status === 403) {
    return "Bạn không có quyền thực hiện thao tác này.";
  }
  if (status === 401) {
    return "Bạn cần đăng nhập để tiếp tục.";
  }
  if (status >= 500) {
    return "Máy chủ đang bận hoặc khởi động lại. Vui lòng thử lại sau giây lát.";
  }
  if (detail && !/^invalid token\.?$/i.test(detail)) {
    return detail;
  }
  return `Lỗi HTTP ${status}`;
}

function normalizeUiLanguage(value) {
  if (typeof value === "string") {
    return value.replace(
      /(^|[^\p{L}])Hoa\s+K[ỳì](?=$|[^\p{L}])/giu,
      "$1Mỹ"
    );
  }
  if (Array.isArray(value)) return value.map(normalizeUiLanguage);
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value).map(([key, item]) => [key, normalizeUiLanguage(item)])
    );
  }
  return value;
}

async function parseBody(response) {
  const text = await response.text();
  if (!text) return null;
  try {
    return normalizeUiLanguage(JSON.parse(text));
  } catch {
    return { raw: normalizeUiLanguage(text.slice(0, 300)) };
  }
}

export async function apiRequest(path, options = {}) {
  const {
    method = "GET",
    body,
    auth = true,
    headers: extraHeaders = {},
    signal,
    retries = method === "GET" ? 2 : 0,
  } = options;

  const headers = {
    Accept: "application/json",
    ...extraHeaders,
  };

  if (body !== undefined) {
    headers["Content-Type"] = "application/json";
  }

  if (auth) {
    const token = loadStoredAuth();
    if (token) {
      headers.Authorization = `Token ${token}`;
    }
  }

  let lastError;
  for (let attempt = 0; attempt <= retries; attempt += 1) {
    const response = await fetch(`${getApiBase()}${path}`, {
      method,
      headers,
      body: body !== undefined ? JSON.stringify(body) : undefined,
      signal,
      credentials: "omit",
    });

    const payload = await parseBody(response);
    if (response.ok) {
      return payload;
    }

    const isLogin = path.includes("/auth/login/");
    const message = humanizeApiError(response.status, payload, { isLogin });
    const code = (payload && payload.code) || "";
    lastError = new ApiError(message, response.status, payload, code);

    // Stale / expired session on authenticated calls → clear and notify UI once.
    if (
      auth &&
      !isLogin &&
      response.status === 401 &&
      loadStoredAuth()
    ) {
      clearAuth();
      emitSessionExpired({ message, code, path });
    }

    // Brief retry on gateway blips while backend is still booting.
    if (
      attempt < retries &&
      (response.status === 502 || response.status === 503 || response.status === 504)
    ) {
      await new Promise((r) => setTimeout(r, 700 * (attempt + 1)));
      continue;
    }
    throw lastError;
  }
  throw lastError;
}

export function getThreatMindmap({ focusId, focusRank, days = 14, limit = 48, search = "" } = {}) {
  const params = new URLSearchParams({ days: String(days), limit: String(limit) });
  if (focusId) params.set("focus_id", String(focusId));
  if (focusRank) params.set("focus_rank", String(focusRank));
  if (search.trim()) params.set("search", search.trim());
  return apiRequest(`/api/v1/threats/mindmap/?${params.toString()}`, { retries: 1 });
}

export function analyzeThreatMindmap({ focusId, days = 30, limit = 36 }) {
  return apiRequest("/api/v1/threats/mindmap-analyze/", {
    method: "POST",
    body: { focus_id: focusId, days, limit },
  });
}

export const api = {
  get: (path, opts) => apiRequest(path, { ...opts, method: "GET" }),
  post: (path, body, opts) => apiRequest(path, { ...opts, method: "POST", body }),
  patch: (path, body, opts) => apiRequest(path, { ...opts, method: "PATCH", body }),
  delete: (path, opts) => apiRequest(path, { ...opts, method: "DELETE" }),
};

export async function loginWithPassword(username, password) {
  // Do not send a stale Authorization header on login.
  // Retry on 502/503 while backend is booting (frontend nginx may briefly fail).
  clearAuth();
  const data = await apiRequest("/api/v1/auth/login/", {
    method: "POST",
    body: { username, password },
    auth: false,
    retries: 4,
  });
  if (!data?.token) {
    throw new ApiError("Phản hồi đăng nhập không có mã xác thực", 500, data);
  }
  storeAuth(data.token, { username: data.username || username });
  return {
    username: data.username || username,
    is_staff: Boolean(data.is_staff),
    is_superuser: Boolean(data.is_superuser),
    expires_in_hours: data.expires_in_hours,
  };
}

function firstFieldError(payload) {
  if (!payload || typeof payload !== "object") return "";
  for (const key of ["new_password", "confirm_password", "current_password", "detail"]) {
    const value = payload[key];
    if (typeof value === "string" && value.trim()) return value;
    if (Array.isArray(value) && value.length) return String(value[0]);
  }
  return "";
}

export async function changePassword({
  currentPassword,
  newPassword,
  confirmPassword,
}) {
  try {
    const data = await apiRequest("/api/v1/auth/change-password/", {
      method: "POST",
      body: {
        current_password: currentPassword,
        new_password: newPassword,
        confirm_password: confirmPassword,
      },
      retries: 0,
    });
    if (!data?.token) {
      throw new ApiError("Máy chủ không trả về phiên đăng nhập mới", 500, data);
    }
    storeAuth(data.token, { username: data.username || loadStoredUsername() });
    return data;
  } catch (err) {
    if (err instanceof ApiError) {
      const fieldMsg = firstFieldError(err.payload);
      if (fieldMsg) {
        throw new ApiError(fieldMsg, err.status, err.payload, err.code);
      }
      if (err.code === "wrong_current_password" || err.status === 400) {
        throw new ApiError(
          err.message || "Mật khẩu hiện tại không đúng.",
          err.status,
          err.payload,
          err.code || "wrong_current_password"
        );
      }
    }
    throw err;
  }
}

export async function logoutRemote() {
  try {
    if (hasAuth()) {
      await api.post("/api/v1/auth/logout/", {}, { retries: 0 });
    }
  } catch {
    // best-effort
  } finally {
    clearAuth();
  }
}

export function buildQuery(params = {}) {
  const qs = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value === undefined || value === null || value === "") return;
    qs.set(key, String(value));
  });
  const s = qs.toString();
  return s ? `?${s}` : "";
}
