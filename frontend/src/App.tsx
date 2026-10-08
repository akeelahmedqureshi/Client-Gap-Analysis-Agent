import { useQuery } from "@tanstack/react-query";
import { NavLink, Navigate, Route, Routes, useNavigate } from "react-router-dom";
import { api, getToken, setToken } from "./lib/api";
import type { User } from "./lib/types";
import LoginPage from "./pages/Login";
import DashboardPage from "./pages/Dashboard";
import UploadPage from "./pages/Upload";
import ProjectsPage from "./pages/Projects";
import ClientsPage from "./pages/Clients";
import RunsPage from "./pages/Runs";
import RunDetailPage from "./pages/RunDetail";
import SettingsPage from "./pages/Settings";
import AuditPage from "./pages/Audit";
import ClientDetailPage from "./pages/ClientDetail";
import MonitoringPage from "./pages/Monitoring";
import KnowledgePage from "./pages/Knowledge";
import PortfolioPage from "./pages/Portfolio";
import NotificationsPage from "./pages/Notifications";
import ConfigurationPage from "./pages/Configuration";

const NAV = [
  ["/", "Dashboard"],
  ["/portfolio", "Portfolio"],
  ["/upload", "Upload"],
  ["/projects", "Projects"],
  ["/clients", "Clients"],
  ["/runs", "Analysis Runs"],
  ["/monitoring", "Monitoring"],
  ["/knowledge", "Knowledge Base"],
  ["/notifications", "Notifications"],
  ["/settings", "Settings"],
] as const;

function Shell() {
  const navigate = useNavigate();
  const me = useQuery({ queryKey: ["me"], queryFn: () => api.get<User>("/api/auth/me") });
  const unread = useQuery({
    queryKey: ["alerts-unread"],
    queryFn: () => api.get<{ count: number }>("/api/alerts/unread-count"),
    refetchInterval: 60_000,
  });
  const notes = useQuery({
    queryKey: ["notifications-unread"],
    queryFn: () => api.get<{ count: number }>("/api/notifications/unread-count"),
    refetchInterval: 30_000,
  });
  return (
    <div className="min-h-screen flex">
      <aside className="w-56 shrink-0 bg-slate-900 text-slate-200 flex flex-col">
        <div className="px-4 py-5 font-bold text-white leading-tight">
          Client Intelligence
          <div className="text-xs font-normal text-slate-400">Gap analysis platform</div>
        </div>
        <nav className="flex-1 px-2 space-y-1">
          {[...NAV, ...(me.data && me.data.role !== "viewer" ? [["/configuration", "Configuration"] as const] : []),
            ...(me.data?.role === "admin" ? [["/audit", "Audit Log"] as const] : [])].map(([to, label]) => (
            <NavLink
              key={to}
              to={to}
              end={to === "/"}
              className={({ isActive }) =>
                `block rounded-md px-3 py-2 text-sm ${isActive ? "bg-slate-700 text-white" : "hover:bg-slate-800"}`
              }
            >
              {label}
              {to === "/notifications" && !!notes.data?.count && (
                <span className="ml-2 rounded-full bg-indigo-500 px-1.5 text-xs text-white" title="Unread notifications">{notes.data.count}</span>
              )}
              {to === "/monitoring" && !!unread.data?.count && (
                <span className="ml-2 rounded-full bg-rose-500 px-1.5 text-xs text-white" title="Unread alerts">{unread.data.count}</span>
              )}
            </NavLink>
          ))}
        </nav>
        <div className="px-4 py-4 text-xs text-slate-400 border-t border-slate-800">
          <div className="truncate">{me.data?.email}</div>
          <div className="capitalize">{me.data?.role}{me.data?.job_function && ` · ${me.data.job_function.replace("_", " ")}`}</div>
          <button
            className="mt-2 text-slate-300 hover:text-white underline"
            onClick={() => {
              setToken(null);
              navigate("/login");
            }}
          >
            Sign out
          </button>
        </div>
      </aside>
      <main className="flex-1 min-w-0 p-6">
        <Routes>
          <Route path="/" element={<DashboardPage />} />
          <Route path="/upload" element={<UploadPage />} />
          <Route path="/projects" element={<ProjectsPage />} />
          <Route path="/clients" element={<ClientsPage />} />
          <Route path="/clients/:clientId" element={<ClientDetailPage />} />
          <Route path="/runs" element={<RunsPage />} />
          <Route path="/runs/:runId" element={<RunDetailPage />} />
          <Route path="/monitoring" element={<MonitoringPage />} />
          <Route path="/knowledge" element={<KnowledgePage />} />
          <Route path="/portfolio" element={<PortfolioPage />} />
          <Route path="/notifications" element={<NotificationsPage />} />
          <Route path="/configuration" element={<ConfigurationPage />} />
          <Route path="/settings" element={<SettingsPage />} />
          <Route path="/audit" element={<AuditPage />} />
          <Route path="*" element={<Navigate to="/" />} />
        </Routes>
      </main>
    </div>
  );
}

function RequireAuth() {
  // Evaluated on every navigation so a fresh login is picked up immediately.
  return getToken() ? <Shell /> : <Navigate to="/login" replace />;
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/*" element={<RequireAuth />} />
    </Routes>
  );
}
