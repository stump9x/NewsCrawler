import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import {
  ApiError,
  api,
  clearAuth,
  hasAuth,
  loadStoredAuth,
  loadStoredUsername,
  loginWithPassword,
  logoutRemote,
  onSessionExpired,
  storeAuth,
} from "../api/client";

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [authed, setAuthed] = useState(() => hasAuth());
  const [username, setUsername] = useState(() => loadStoredUsername());
  const [isStaff, setIsStaff] = useState(false);
  const [isSuperuser, setIsSuperuser] = useState(false);
  const [authReady, setAuthReady] = useState(() => !hasAuth());
  const [error, setError] = useState("");
  const [sessionNotice, setSessionNotice] = useState("");

  const applyLoggedOut = useCallback((notice = "") => {
    clearAuth();
    setAuthed(false);
    setUsername("");
    setIsStaff(false);
    setIsSuperuser(false);
    setAuthReady(true);
    if (notice) setSessionNotice(notice);
  }, []);

  const hydrateFromMe = useCallback(() => {
    if (!hasAuth()) {
      setAuthed(false);
      setAuthReady(true);
      return Promise.resolve(null);
    }
    setAuthReady(false);
    return api
      .get("/api/v1/auth/me/", { retries: 1 })
      .then((data) => {
        setAuthed(true);
        setUsername(data.username || "");
        setIsStaff(Boolean(data.is_staff));
        setIsSuperuser(Boolean(data.is_superuser));
        storeAuth(loadStoredAuth(), { username: data.username || "" });
        return data;
      })
      .catch((err) => {
        const notice =
          err instanceof ApiError
            ? err.message
            : "Phiên đăng nhập đã hết hạn. Vui lòng đăng nhập lại.";
        applyLoggedOut(notice);
        return null;
      })
      .finally(() => setAuthReady(true));
  }, [applyLoggedOut]);

  useEffect(() => {
    hydrateFromMe();
  }, [hydrateFromMe]);

  // Cross-tab sync: login/logout in another tab updates this tab.
  useEffect(() => {
    const onStorage = (event) => {
      if (event.key !== "nc_api_token") return;
      if (!event.newValue) {
        applyLoggedOut("Bạn đã đăng xuất từ một tab khác.");
        return;
      }
      storeAuth(event.newValue);
      setAuthed(true);
      setAuthReady(false);
      setSessionNotice("");
      setError("");
      hydrateFromMe();
    };
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, [applyLoggedOut, hydrateFromMe]);

  useEffect(() => {
    return onSessionExpired(({ message }) => {
      applyLoggedOut(
        message || "Phiên đăng nhập đã hết hạn. Vui lòng đăng nhập lại."
      );
    });
  }, [applyLoggedOut]);

  const login = useCallback(async (user, password) => {
    setError("");
    setSessionNotice("");
    try {
      const data = await loginWithPassword(user, password);
      setAuthed(true);
      setUsername(data.username || user);
      setIsStaff(Boolean(data.is_staff));
      setIsSuperuser(Boolean(data.is_superuser));
      setAuthReady(true);
    } catch (err) {
      applyLoggedOut();
      const msg =
        err instanceof ApiError
          ? err.message
          : "Đăng nhập thất bại. Vui lòng thử lại.";
      setError(msg);
      throw err;
    }
  }, [applyLoggedOut]);

  const logout = useCallback(async () => {
    await logoutRemote();
    setAuthed(false);
    setUsername("");
    setIsStaff(false);
    setIsSuperuser(false);
    setAuthReady(true);
    setError("");
    setSessionNotice("");
  }, []);

  const value = useMemo(
    () => ({
      authed: authed && Boolean(loadStoredAuth()),
      username,
      isStaff,
      isSuperuser,
      authReady,
      error,
      sessionNotice,
      login,
      logout,
      clearError: () => setError(""),
      clearSessionNotice: () => setSessionNotice(""),
    }),
    [authed, username, isStaff, isSuperuser, authReady, error, sessionNotice, login, logout]
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
