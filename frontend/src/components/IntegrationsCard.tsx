/* eslint-disable @typescript-eslint/no-explicit-any */
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../lib/api";
import { useCanExport } from "../lib/useOrg";
import { Badge, Button, Card, Empty, ErrorText } from "./ui";

const ACTION: Record<string, string> = { "crm.opportunity": "CRM opportunity", "marketing.contact": "Marketing contact", "email.sent": "Email sent" };

/** Push the approved sales output of a run to the CRM / marketing system, or send the outreach email. */
export function RunIntegrations({ runId, canAct, summaryApproved, outreachApproved }: {
  runId: string; canAct: boolean; summaryApproved: boolean; outreachApproved: boolean;
}) {
  const qc = useQueryClient();
  const canExport = useCanExport();
  const cfg = useQuery({ queryKey: ["integrations"], queryFn: () => api.get<any>("/api/integrations") });
  // Re-read after approval: the CRM opportunity may have been created automatically.
  const syncs = useQuery({ queryKey: ["syncs", runId, summaryApproved], queryFn: () => api.get<any[]>(`/api/runs/${runId}/integrations`) });
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const c = cfg.data;
  if (!c) return null;
  if (!c.crm && !c.marketing && !c.email_sending && !syncs.data?.length) return null;
  async function run(path: string, body?: unknown) {
    setBusy(true);
    setError(null);
    try {
      await api.post(path, body);
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
      qc.invalidateQueries({ queryKey: ["syncs", runId] });
    }
  }
  const act = canAct && canExport;
  return (
    <Card title="Share with other systems">
      <div className="flex flex-wrap gap-2 items-center">
        {c.crm && <Button variant="secondary" disabled={!act || !summaryApproved || busy} onClick={() => run(`/api/runs/${runId}/crm`)}
          title={summaryApproved ? "" : "Approve the sales summary first"}>Create {c.crm.provider === "hubspot" ? "HubSpot" : "CRM"} opportunity</Button>}
        {c.marketing && <Button variant="secondary" disabled={!act || !summaryApproved || busy} onClick={() => run(`/api/runs/${runId}/marketing`)}
          title={summaryApproved ? "" : "Approve the sales summary first"}>Add contact to {c.marketing.provider === "mailchimp" ? "Mailchimp" : "marketing"}</Button>}
        {c.email_sending && <Button disabled={!act || !outreachApproved || busy} title={outreachApproved ? "" : "Approve the outreach email first"}
          onClick={() => confirm("Send the approved outreach email now? Replies will come to you.") && run(`/api/runs/${runId}/outreach/send`, { confirm: true, resend: !!syncs.data?.some((s) => s.action === "email.sent" && s.status === "ok") })}>
          Send outreach email</Button>}
        {c.crm?.auto_create && <span className="text-xs text-slate-500">The CRM opportunity is created automatically when the summary is approved.</span>}
      </div>
      <p className="text-xs text-slate-500 mt-2">Only the approved, client-facing content is sent: never internal-only capabilities, reviewer notes or evidence.</p>
      <ErrorText error={error} />
      {syncs.data && syncs.data.length > 0 && (
        <ul className="text-sm mt-3 divide-y">
          {syncs.data.map((s) => (
            <li key={s.id} className="py-1 flex flex-wrap gap-2 items-center">
              <Badge value={s.status === "ok" ? "completed" : "failed"} />
              <span>{ACTION[s.action] ?? s.action} · {s.provider}{s.automatic ? " · automatic" : ""}</span>
              {s.external_ids?.deal_id && <span className="text-xs text-slate-500">deal {s.external_ids.deal_id}</span>}
              {s.error && <span className="text-xs text-rose-700">{s.error}</span>}
              <span className="text-xs text-slate-400 ml-auto">{s.created_by_email ?? "automation"} · {new Date(s.created_at).toLocaleString()}</span>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

/** Admin settings for the CRM and marketing integrations. */
export function IntegrationSettings() {
  const cfg = useQuery({ queryKey: ["integrations"], queryFn: () => api.get<any>("/api/integrations") });
  if (!cfg.data) return null;
  return (
    <Card title="Integrations">
      <div className="space-y-5">
        <IntegrationForm kind="crm" current={cfg.data.crm} />
        <IntegrationForm kind="marketing" current={cfg.data.marketing} />
        <p className="text-xs text-slate-500">
          Sending outreach email uses the server's SMTP settings ({cfg.data.email_sending ? "configured" : "not configured: set CIP_SMTP_HOST"}).
          Tokens and webhook URLs are encrypted and never shown again. Webhooks are signed: <code>X-CIP-Signature: sha256=HMAC(secret, timestamp + "." + body)</code>.
        </p>
      </div>
    </Card>
  );
}

function IntegrationForm({ kind, current }: { kind: "crm" | "marketing"; current: any }) {
  const qc = useQueryClient();
  const providers = kind === "crm" ? [["hubspot", "HubSpot"], ["webhook", "Webhook (Salesforce, Pipedrive, Zapier…)"]]
    : [["mailchimp", "Mailchimp"], ["webhook", "Webhook"]];
  const [provider, setProvider] = useState<string>(current?.provider ?? providers[0][0]);
  const [secret, setSecret] = useState("");
  const [config, setConfig] = useState<Record<string, string>>({});
  const [auto, setAuto] = useState<boolean>(!!current?.auto_create);
  const [msg, setMsg] = useState("");
  const [error, setError] = useState<unknown>(null);
  const input = "border rounded px-2 py-1 text-sm";
  const set = (k: string) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => setConfig({ ...config, [k]: e.target.value });
  async function act(fn: () => Promise<any>, ok: string) {
    setError(null);
    try {
      const r = await fn();
      setMsg(r && r.ok === false ? `Test failed: ${r.error}` : ok);
      setSecret("");
      qc.invalidateQueries({ queryKey: ["integrations"] });
    } catch (e) {
      setError(e);
    }
  }
  return (
    <div>
      <div className="font-medium text-sm">{kind === "crm" ? "CRM" : "Email marketing"}
        {current && <span className="text-xs text-slate-500"> · connected to {current.provider}{current.config?.url ? ` (${current.config.url})` : ""}</span>}</div>
      <div className="flex flex-wrap gap-2 mt-1 items-center">
        <select className={input} value={provider} onChange={(e) => setProvider(e.target.value)}>
          {providers.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
        </select>
        {provider === "webhook" && <input className={`${input} w-72`} placeholder={current?.config?.url ?? "https://… inbound hook URL"} onChange={set("url")} />}
        {provider === "mailchimp" && <input className={`${input} w-40`} placeholder={current?.config?.list_id ?? "Audience (list) id"} onChange={set("list_id")} />}
        {provider === "mailchimp" && (
          <select className={input} defaultValue={current?.config?.status_if_new ?? "pending"} onChange={set("status_if_new")}>
            <option value="pending">New contacts: double opt-in (pending)</option>
            <option value="transactional">New contacts: transactional only</option>
            <option value="subscribed">New contacts: subscribed (you hold consent)</option>
          </select>
        )}
        {provider === "hubspot" && <>
          <input className={`${input} w-28`} placeholder={current?.config?.pipeline ?? "pipeline"} onChange={set("pipeline")} />
          <input className={`${input} w-44`} placeholder={current?.config?.dealstage ?? "deal stage"} onChange={set("dealstage")} />
        </>}
        <input type="password" className={`${input} w-64`} value={secret} onChange={(e) => setSecret(e.target.value)}
          placeholder={provider === "webhook" ? "Signing secret (optional)" : current?.has_secret ? "API token (saved; enter to replace)" : "API token"} />
        {kind === "crm" && <label className="text-xs flex items-center gap-1"><input type="checkbox" checked={auto} onChange={(e) => setAuto(e.target.checked)} /> Create the opportunity when a sales summary is approved</label>}
      </div>
      <div className="flex gap-2 mt-2">
        <Button variant="secondary" onClick={() => act(() => api.put(`/api/integrations/${kind}`, { provider, secret: secret || null, config, auto_create: auto }), "Saved.")}>Save</Button>
        {current && <Button variant="secondary" onClick={() => act(() => api.post(`/api/integrations/${kind}/test`), "Test succeeded.")}>Test</Button>}
        {current && <Button variant="danger" onClick={() => confirm("Remove this integration?") && act(() => api.del(`/api/integrations/${kind}`), "Removed.")}>Remove</Button>}
        {!current && <Empty>Not connected.</Empty>}
      </div>
      {msg && <p className="text-xs text-slate-600 mt-1">{msg}</p>}
      <ErrorText error={error} />
    </div>
  );
}
