import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../lib/api";
import type { Alert, Monitor, User } from "../lib/types";
import { Badge, Button, Card, Empty, ErrorText } from "../components/ui";
import { ChangeList, SeverityBadge } from "../components/Changes";

const KIND_LABEL: Record<Alert["kind"], string> = {
  changes: "Changes detected",
  approval_needed: "Approval needed",
  run_failed: "Run failed",
};

export default function MonitoringPage() {
  const qc = useQueryClient();
  const [unreadOnly, setUnreadOnly] = useState(false);
  const [open, setOpen] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  const me = useQuery({ queryKey: ["me"], queryFn: () => api.get<User>("/api/auth/me") });
  const monitors = useQuery({ queryKey: ["monitors"], queryFn: () => api.get<Monitor[]>("/api/monitors") });
  const alerts = useQuery({
    queryKey: ["alerts", unreadOnly],
    queryFn: () => api.get<Alert[]>(`/api/alerts?limit=100${unreadOnly ? "&unread_only=true" : ""}`),
    refetchInterval: 60_000,
  });

  function refresh() {
    qc.invalidateQueries({ queryKey: ["alerts"] });
    qc.invalidateQueries({ queryKey: ["alerts-unread"] });
  }

  async function markRead(a: Alert) {
    if (a.read_at) return;
    try {
      await api.post(`/api/alerts/${a.id}/read`);
      refresh();
    } catch (e) {
      setError(e);
    }
  }

  async function runNow(m: Monitor) {
    try {
      await api.post(`/api/projects/${m.project_id}/monitor/run-now`);
      qc.invalidateQueries({ queryKey: ["monitors"] });
    } catch (e) {
      setError(e);
    }
  }

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold">Monitoring</h1>
      <ErrorText error={error} />
      <Card
        title="Alerts"
        actions={
          <>
            <label className="text-sm flex items-center gap-1">
              <input type="checkbox" checked={unreadOnly} onChange={(e) => setUnreadOnly(e.target.checked)} /> Unread only
            </label>
            <Button variant="secondary" onClick={async () => { await api.post("/api/alerts/read-all"); refresh(); }}>
              Mark all read
            </Button>
          </>
        }
      >
        {alerts.data?.length ? (
          <ul className="divide-y">
            {alerts.data.map((a) => (
              <li key={a.id} className={`py-3 ${a.read_at ? "opacity-70" : ""}`}>
                <div className="flex gap-3 items-start">
                  {!a.read_at && <span className="mt-1.5 h-2 w-2 rounded-full bg-indigo-600" title="unread" />}
                  <SeverityBadge value={a.severity} />
                  <div className="flex-1 min-w-0">
                    <button
                      className="font-medium text-left hover:underline"
                      onClick={() => {
                        setOpen(open === a.id ? null : a.id);
                        markRead(a);
                      }}
                    >
                      {a.project_name}: {a.title}
                    </button>
                    <div className="text-xs text-slate-500">
                      {KIND_LABEL[a.kind]} · {new Date(a.created_at).toLocaleString()}
                      {a.summary && <> · {a.summary}</>}
                      {a.notifications.length > 0 && (
                        <> · notified: {a.notifications.map((n) => (
                          <span key={n.channel} title={n.ok ? "delivered" : n.error ?? `HTTP ${n.status}`}>{n.channel} {n.ok ? "✓" : "✗"} </span>
                        ))}</>
                      )}
                    </div>
                  </div>
                  <Link className="text-sm text-indigo-600 underline whitespace-nowrap" to={`/runs/${a.run_id}`} onClick={() => markRead(a)}>
                    Open run
                  </Link>
                </div>
                {open === a.id && a.changes.length > 0 && (
                  <div className="mt-2 ml-8 border-l pl-3">
                    <ChangeList changes={a.changes} withEvidence={false} />
                    <p className="text-xs text-slate-500">Open the run's Changes tab to see the evidence for each change.</p>
                  </div>
                )}
              </li>
            ))}
          </ul>
        ) : (
          <Empty>No alerts{unreadOnly ? " to read" : " yet"}.</Empty>
        )}
      </Card>

      <Card title="Monitored projects">
        {monitors.data?.length ? (
          <table className="w-full text-sm">
            <thead className="text-left text-slate-500">
              <tr><th className="py-1">Project</th><th>Schedule</th><th>Standing approvals</th><th>Notify</th><th>Next run</th><th>Last run</th><th /></tr>
            </thead>
            <tbody>
              {monitors.data.map((m) => (
                <tr key={m.id} className="border-t align-top">
                  <td className="py-2">
                    <div className="font-medium">{m.project_name}</div>
                    <div className="text-xs text-slate-500">{m.client_name}</div>
                  </td>
                  <td><Badge value={m.enabled ? "enabled" : "disabled"} /> <span className="capitalize">{m.frequency}</span></td>
                  <td className="text-xs">
                    {m.standing_approvals.length ? m.standing_approvals.join(", ").replaceAll("_", " ") : "none"}
                    {m.standing_approvals.length > 0 && !m.approvals_valid && (
                      <div className="text-amber-700">suspended (approver lost access)</div>
                    )}
                  </td>
                  <td className="text-xs">
                    {m.webhook && <div>webhook {m.webhook}</div>}
                    {m.notify_emails.map((e) => <div key={e}>{e}</div>)}
                    {m.notify_emails.length > 0 && !m.email_enabled && <div className="text-amber-700">email not configured on server</div>}
                    <div className="text-slate-500">{m.min_severity}+</div>
                  </td>
                  <td className="text-xs">{m.enabled && m.next_run_at ? new Date(m.next_run_at).toLocaleString() : "—"}</td>
                  <td>{m.last_run_id ? <Link to={`/runs/${m.last_run_id}`}><Badge value={m.last_run_status ?? "pending"} /></Link> : "—"}</td>
                  <td className="text-right whitespace-nowrap">
                    {me.data?.role !== "viewer" && <Button variant="secondary" onClick={() => runNow(m)}>Run now</Button>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <Empty>No monitored projects. Use <Link to="/projects" className="underline">Projects → Monitor</Link> to set one up.</Empty>
        )}
      </Card>
    </div>
  );
}
