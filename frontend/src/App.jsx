import { lazy, Suspense } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { useAuth } from "./auth/AuthContext";
import AppShell from "./layout/AppShell";
import LoginPage from "./pages/LoginPage";
import DashboardPage from "./pages/DashboardPage";
import WirePage from "./pages/WirePage";
import BriefingsPage from "./pages/BriefingsPage";
import NotebookAIPage from "./pages/NotebookAIPage";
import FeedSourcesPage from "./pages/FeedSourcesPage";
import TrendPage from "./pages/TrendPage";
import UsersPage from "./pages/UsersPage";
import PolicyPage from "./pages/PolicyPage";

// Mindmap ships the graph renderer and is not needed on the other screens.
// Load it only when the route is opened to keep the main application fast.
const MindmapPage = lazy(() => import("./pages/MindmapPage"));

function PublicOnly({ children }) {
  const { authed } = useAuth();
  if (authed) return <Navigate to="/" replace />;
  return children;
}

function SuperuserOnly({ children }) {
  const { authReady, isSuperuser } = useAuth();
  if (!authReady) return <div style={{ padding: 24 }}>Đang xác thực quyền quản trị…</div>;
  if (!isSuperuser) return <Navigate to="/" replace />;
  return children;
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<PublicOnly><LoginPage /></PublicOnly>} />
      <Route element={<AppShell />}>
        <Route path="/" element={<DashboardPage />} />
        <Route path="/feeds" element={<WirePage />} />
        {/* Document scan UI disabled — DOCUMENT_SCAN_ENABLED=false */}
        <Route path="/documents" element={<Navigate to="/" replace />} />
        <Route path="/trend" element={<TrendPage />} />
        <Route path="/last30days" element={<Navigate to="/trend" replace />} />
        <Route
          path="/mindmap"
          element={(
            <Suspense fallback={<div style={{ padding: 24 }}>Đang mở bản đồ liên kết…</div>}>
              <MindmapPage />
            </Suspense>
          )}
        />
        <Route path="/threats" element={<Navigate to="/feeds" replace />} />
        <Route path="/intelligence" element={<BriefingsPage />} />
        <Route path="/notebook-ai" element={<NotebookAIPage />} />
        <Route path="/sources" element={<FeedSourcesPage />} />
        <Route
          path="/users"
          element={<SuperuserOnly><UsersPage /></SuperuserOnly>}
        />
        <Route
          path="/policy"
          element={<PolicyPage />}
        />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
