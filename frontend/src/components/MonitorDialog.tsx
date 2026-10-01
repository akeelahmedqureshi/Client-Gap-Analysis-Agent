import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api } from "../lib/api";
import type { ApprovalPreview, Monitor, Project, Severity } from "../lib/types";
import { Button, ErrorText } from "./ui";

const LARGE_REPO = {
  gate: "large_repository_scan",
  title: "Large repository scans",
  what: "Scan repositories above the large-repository threshold without asking each time.",
};

/** Create / edit the monitoring schedule of a project. */
export default function MonitorDialog({ project, onClose }: { project: Project; onClose: () => void }) {
  const qc = useQueryClient();
  const existing = useQuery({
    queryKey: ["monitors"],
    queryFn: () => api.get<Monitor[]>("/api/monitors"),
    select: (ms) => ms.find((m) => m.project_id === project.id) ?? null,
  });
  const preview = useQuery({
    queryKey: ["approval-preview", project.id],
    queryFn: () => api.get<ApprovalPreview[]>(`/api/runs/approval-preview?project_id=${project.id}`),
  });
  const [enabled, setEnabled] = useState(true);
  const [frequency, setFrequency] = useState<Monitor["frequency"]>("weekly");
  const [gates, setGates] = useState<Set<string>>(new Set());
  const [minSeverity, setMinSeverity] = useState<Severity>("warning");
  const [emails, setEmails] = useState("");
  const [webhook, setWebhook] = useState("");
  const [removeWebhook, setRemoveWebhook] = useState(false);
  const [runNow, setRunNow] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [testResult, setTestResult] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const m = existing.data;
    if (m) {
      setEnabled(m.enabled);
      setFrequency(m.frequency);
      setGates(new Set(m.standing_approvals));
      setMinSeverity(m.min_severity);
      setEmails(m.notify_emails.join(", "));
    }
  }, [existing.data]);

  const toggle = (g: string) => {
    const next = new Set(gates);
    if (next.has(g)) next.delete(g);
    else next.add(g);
    setGates(next);
  };

  async function save() {
    setBusy(true);
    setError(null);
    try {
      await api.put<Monitor>(`/api/projects/${project.id}/monitor`, {
        enabled,
        frequency,
        standing_approvals: [...gates],
        min_severity: minSeverity,
        notify_emails: emails.split(/[,\s]+/).filter(Boolean),
        webhook_url: removeWebhook ? "" : webhook.trim() || null,
        run_now: runNow,
      });
      qc.invalidateQueries({ queryKey: ["monitors"] });
      onClose();
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    if (!confirm("Stop monitoring this project? Past alerts are kept.")) return;
    try {
      await api.del(`/api/projects/${project.id}/monitor`);
      qc.invalidateQueries({ queryKey: ["monitors"] });
      onClose();
    } catch (e) {
      setError(e);
    }
  }

  async function test() {
    setTestResult(null);
    try {
      const r = await api.post<{ results: { channel: string; ok: boolean; error?: string; status?: number }[] }>(
        `/api/projects/${project.id}/monitor/test`,
      );
      setTestResult(r.results.map((x) => `${x.channel}: ${x.ok ? "sent" : `failed (${x.error ?? x.status})`}`).join(" · "));
    } catch (e) {
      setError(e);
    }
  }

  const options = [...(preview.data ?? []), LARGE_REPO];
  const m = existing.data;
  return (
    <div className="fixed inset-0 z-10 bg-black/40 grid place-items-center p-4" onClick={onClose}>
      <div className="bg-white rounded-xl shadow-xl w-full max-w-2xl max-h-[90vh] overflow-auto" onClick={(e) => e.stopPropagation()}>
        <div className="px-5 py-4 border-b">
          <h2 className="text-lg font-semibold">Monitoring: {project.name}</h2>
          <p className="text-sm text-slate-500">
            Re-analyze this project on a schedule, compare with the previous analysis and alert on what changed —
            competitor launches and price moves, new gaps, new security issues, client announcements.
          </p>
        </div>
        <div className="p-5 space-y-4 text-sm">
          <div className="flex flex-wrap gap-4 items-center">
            <label className="flex gap-2 items-center">
              <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} /> Enabled
            </label>
            <label className="flex gap-2 items-center">
              Frequency
              <select className="border rounded px-2 py-1" value={frequency} onChange={(e) => setFrequency(e.target.value as Monitor["frequency"])}>
                <option value="daily">Daily</option>
                <option value="weekly">Weekly</option>
                <option value="monthly">Monthly (30 days)</option>
              </select>
            </label>
            {m?.next_run_at && <span className="text-slate-500">Next run: {new Date(m.next_run_at).toLocaleString()}</span>}
          </div>

          <div>
            <div className="font-medium">Standing approvals for scheduled runs</div>
            <p className="text-slate-500 mb-2">
              Steps you approve here run unattended and are recorded in the audit log under your name. Anything not approved
              pauses the scheduled run and raises an "approval needed" alert. Approvals lapse automatically if you lose
              access to the project or are deactivated.
            </p>
            {m && m.standing_approvals.length > 0 && !m.approvals_valid && (
              <p className="mb-2 rounded bg-amber-50 text-amber-800 px-2 py-1">
                The previous approver no longer has access, so these approvals are suspended. Saving re-grants them under your name.
              </p>
            )}
            {options.map((p) => (
              <label key={p.gate} className="flex gap-3 border rounded-lg p-2 mb-1 cursor-pointer hover:bg-slate-50">
                <input type="checkbox" className="mt-1" checked={gates.has(p.gate)} onChange={() => toggle(p.gate)} />
                <span>
                  <span className="font-medium">{p.title}</span>
                  <span className="block text-xs text-slate-500">{p.what}</span>
                </span>
              </label>
            ))}
          </div>

          <div className="grid sm:grid-cols-2 gap-3">
            <label className="block">
              <span className="font-medium">Webhook (Slack-compatible)</span>
              <input
                className="mt-1 w-full border rounded px-2 py-1"
                placeholder={m?.webhook ? `${m.webhook} (stored — leave empty to keep)` : "https://hooks.slack.com/services/…"}
                value={webhook}
                disabled={removeWebhook}
                onChange={(e) => setWebhook(e.target.value)}
              />
              {m?.webhook && (
                <label className="flex gap-1 items-center text-xs text-slate-500 mt-1">
                  <input type="checkbox" checked={removeWebhook} onChange={(e) => setRemoveWebhook(e.target.checked)} /> Remove stored webhook
                </label>
              )}
            </label>
            <label className="block">
              <span className="font-medium">Email recipients</span>
              <input
                className="mt-1 w-full border rounded px-2 py-1"
                placeholder="pm@example.com, cto@example.com"
                value={emails}
                onChange={(e) => setEmails(e.target.value)}
              />
              {m && !m.email_enabled && (
                <span className="block text-xs text-amber-700 mt-1">Email is not configured on the server (CIP_SMTP_HOST); use a webhook or ask an admin.</span>
              )}
            </label>
          </div>
          <label className="flex gap-2 items-center">
            Notify for
            <select className="border rounded px-2 py-1" value={minSeverity} onChange={(e) => setMinSeverity(e.target.value as Severity)}>
              <option value="critical">critical changes only</option>
              <option value="warning">warning and above</option>
              <option value="info">every change</option>
            </select>
            <span className="text-xs text-slate-500">(all alerts always appear in the Monitoring page)</span>
          </label>
          {enabled && (
            <label className="flex gap-2 items-center">
              <input type="checkbox" checked={runNow} onChange={(e) => setRunNow(e.target.checked)} /> Run the first analysis now
            </label>
          )}
          {testResult && <p className="text-slate-600">Test: {testResult}</p>}
          <ErrorText error={error} />
        </div>
        <div className="px-5 py-3 border-t flex justify-between gap-2">
          <div className="space-x-2">
            {m && <Button variant="danger" onClick={remove}>Stop monitoring</Button>}
            {m && (m.webhook || m.notify_emails.length > 0) && (
              <Button variant="secondary" onClick={test}>Send test</Button>
            )}
          </div>
          <div className="space-x-2">
            <Button variant="secondary" onClick={onClose}>Cancel</Button>
            <Button onClick={save} disabled={busy}>{m ? "Save" : "Start monitoring"}</Button>
          </div>
        </div>
      </div>
    </div>
  );
}
