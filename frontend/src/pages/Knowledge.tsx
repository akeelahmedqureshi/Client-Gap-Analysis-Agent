import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../lib/api";
import type { KnowledgeKind, KnowledgeRecord, KnowledgeStatus, KnowledgeVersion, User } from "../lib/types";
import { Badge, Button, Card, Empty, ErrorText } from "../components/ui";

const KINDS: [KnowledgeKind, string][] = [
  ["capability", "Capability"],
  ["solution", "Reusable solution"],
  ["project", "Previous project"],
  ["case_study", "Case study"],
];
const KIND_LABEL = Object.fromEntries(KINDS) as Record<KnowledgeKind, string>;
const STATUS_LABEL: Record<KnowledgeStatus, string> = {
  draft: "Draft",
  in_review: "In review",
  approved: "Approved",
  restricted: "Restricted",
  archived: "Archived",
};

interface TaxonomyFeature {
  id: string;
  name: string;
  category: string;
}

const splitTags = (s: string) => s.split(",").map((t) => t.trim()).filter(Boolean);

/** Internal knowledge base: what we have delivered, governed for client-facing use. */
export default function KnowledgePage() {
  const qc = useQueryClient();
  const me = useQuery({ queryKey: ["me"], queryFn: () => api.get<User>("/api/auth/me") });
  const [q, setQ] = useState("");
  const [kind, setKind] = useState("");
  const [status, setStatus] = useState("");
  const [archived, setArchived] = useState(false);
  const [editing, setEditing] = useState<KnowledgeRecord | "new" | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const params = new URLSearchParams();
  if (q.trim()) params.set("q", q.trim());
  if (kind) params.set("kind", kind);
  if (status) params.set("status", status);
  if (archived) params.set("include_archived", "true");
  const list = useQuery({
    queryKey: ["knowledge", params.toString()],
    queryFn: () => api.get<KnowledgeRecord[]>(`/api/knowledge?${params}`),
  });
  const role = me.data?.role;
  const canEdit = role === "admin" || role === "analyst";
  const current = list.data?.find((r) => r.id === selected) ?? null;
  const refresh = () => qc.invalidateQueries({ queryKey: ["knowledge"] });

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold">Knowledge Base</h1>
          <p className="text-sm text-slate-500">
            Capabilities, reusable solutions, previous projects and case studies. Analyses match client opportunities
            to <b>approved</b> records; only approved <b>client-facing</b> records may appear in client-facing output.
          </p>
        </div>
        {canEdit && <Button onClick={() => setEditing("new")}>Add record</Button>}
      </div>

      <div className="flex flex-wrap gap-2 items-center">
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search title, outcomes, tags…"
          className="border rounded px-2 py-1 text-sm w-64" />
        <select value={kind} onChange={(e) => setKind(e.target.value)} className="border rounded px-2 py-1 text-sm">
          <option value="">All types</option>
          {KINDS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
        </select>
        <select value={status} onChange={(e) => setStatus(e.target.value)} className="border rounded px-2 py-1 text-sm">
          <option value="">Any status</option>
          {Object.entries(STATUS_LABEL).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
        </select>
        <label className="text-sm text-slate-600 flex items-center gap-1">
          <input type="checkbox" checked={archived} onChange={(e) => setArchived(e.target.checked)} /> Show archived
        </label>
      </div>

      <div className="grid gap-4 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
        <Card title={`Records${list.data ? ` (${list.data.length})` : ""}`}>
          <ErrorText error={list.error} />
          {list.data?.length === 0 && (
            <Empty>{canEdit ? "No records yet. Add the capabilities and case studies your team has delivered." :
              "No approved records yet."}</Empty>
          )}
          <ul className="divide-y">
            {list.data?.map((r) => (
              <li key={r.id}>
                <button className={`w-full text-left py-2 px-1 rounded ${selected === r.id ? "bg-indigo-50" : "hover:bg-slate-50"}`}
                  onClick={() => setSelected(r.id)}>
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="font-medium">{r.title}</span>
                    <Badge value={r.status} />
                    {r.client_facing && <span className="text-xs text-emerald-700">client-facing</span>}
                    {r.ai && <span className="text-xs rounded bg-violet-100 text-violet-800 px-1.5">AI</span>}
                    {r.automation && <span className="text-xs rounded bg-cyan-100 text-cyan-800 px-1.5">Automation</span>}
                  </div>
                  <div className="text-xs text-slate-500">
                    {KIND_LABEL[r.kind]} · v{r.version}
                    {r.industries.length > 0 && ` · ${r.industries.join(", ")}`}
                    {r.technologies.length > 0 && ` · ${r.technologies.slice(0, 4).join(", ")}`}
                  </div>
                </button>
              </li>
            ))}
          </ul>
        </Card>
        {current ? (
          <RecordDetail record={current} role={role} onEdit={() => setEditing(current)} onChanged={refresh} />
        ) : (
          <Card title="Details"><Empty>Select a record to see its details, approval state and history.</Empty></Card>
        )}
      </div>

      {editing && (
        <RecordDialog record={editing === "new" ? null : editing} isAdmin={role === "admin"}
          onClose={() => setEditing(null)}
          onSaved={(r) => { setEditing(null); setSelected(r.id); refresh(); }} />
      )}
    </div>
  );
}

function Tags({ label, values }: { label: string; values: string[] }) {
  if (!values.length) return null;
  return (
    <div className="text-sm">
      <span className="text-slate-500">{label}: </span>
      {values.map((v) => <span key={v} className="inline-block mr-1 mb-1 rounded bg-slate-100 px-1.5 text-xs">{v}</span>)}
    </div>
  );
}

function RecordDetail({ record: r, role, onEdit, onChanged }: {
  record: KnowledgeRecord; role?: string; onEdit: () => void; onChanged: () => void;
}) {
  const isAdmin = role === "admin";
  const canEdit = isAdmin || (role === "analyst" && !["restricted", "archived"].includes(r.status));
  const [error, setError] = useState<unknown>(null);
  const [showHistory, setShowHistory] = useState(false);
  const taxonomy = useQuery({ queryKey: ["taxonomy"], queryFn: () => api.get<TaxonomyFeature[]>("/api/meta/taxonomy") });
  const featureName = new Map(taxonomy.data?.map((f) => [f.id, f.name]));
  const history = useQuery({
    queryKey: ["knowledge-versions", r.id, r.version],
    queryFn: () => api.get<KnowledgeVersion[]>(`/api/knowledge/${r.id}/versions`),
    enabled: showHistory && role !== "viewer",
  });

  async function setStatus(status: KnowledgeStatus) {
    const note = ["approved", "restricted", "archived"].includes(status) ? prompt("Note (optional)") ?? "" : "";
    setError(null);
    try {
      await api.post(`/api/knowledge/${r.id}/status`, { status, note });
      onChanged();
    } catch (e) {
      setError(e);
    }
  }

  const actions: [KnowledgeStatus, string, boolean][] = [
    ["in_review", "Submit for review", r.status === "draft" && role !== "viewer"],
    ["draft", "Back to draft", r.status === "in_review" && role !== "viewer"],
    ["approved", "Approve", isAdmin && r.status !== "approved"],
    ["restricted", "Restrict", isAdmin && r.status !== "restricted"],
    ["archived", "Archive", isAdmin && r.status !== "archived"],
  ];

  return (
    <Card title={r.title} actions={canEdit ? <Button variant="secondary" onClick={onEdit}>Edit</Button> : undefined}>
      <div className="space-y-3">
        <div className="flex items-center gap-2 flex-wrap text-sm">
          <Badge value={r.status} />
          <span className="text-slate-500">{KIND_LABEL[r.kind]} · version {r.version}</span>
          <span className={r.client_facing ? "text-emerald-700" : "text-slate-500"}>
            {r.client_facing ? "Usable in client-facing output" :
              r.visibility === "client_facing" ? "Client-facing once approved" : "Internal only"}
          </span>
        </div>
        {r.summary && <p className="text-sm">{r.summary}</p>}
        {r.outcomes && <p className="text-sm"><span className="text-slate-500">Outcomes: </span>{r.outcomes}</p>}
        {r.customer_name && (
          <p className="text-sm">
            <span className="text-slate-500">Customer: </span>{r.customer_name}
            <span className="text-xs text-slate-500">
              {r.reference_allowed ? " (may be named to clients)" : " (do not name to clients)"}
            </span>
          </p>
        )}
        {r.details && <p className="text-sm whitespace-pre-wrap text-slate-700">{r.details}</p>}
        <Tags label="Industries" values={r.industries} />
        <Tags label="Technologies" values={r.technologies} />
        <Tags label="Project types" values={r.project_types} />
        <Tags label="Capabilities" values={r.capability_tags.map((t) => featureName.get(t) ?? t)} />
        <div className="flex flex-wrap gap-2 pt-2 border-t">
          {actions.filter(([, , show]) => show).map(([s, label]) => (
            <Button key={s} variant={s === "archived" || s === "restricted" ? "danger" : s === "approved" ? "primary" : "secondary"}
              onClick={() => setStatus(s)}>{label}</Button>
          ))}
          {r.status === "archived" && isAdmin && <Button variant="secondary" onClick={() => setStatus("draft")}>Restore to draft</Button>}
        </div>
        {role === "analyst" && r.status === "approved" && (
          <p className="text-xs text-slate-500">Editing an approved record sends it back for review.</p>
        )}
        <ErrorText error={error} />
        {role !== "viewer" && (
          <div>
            <button className="text-sm text-indigo-700 underline" onClick={() => setShowHistory(!showHistory)}>
              {showHistory ? "Hide history" : "Show history"}
            </button>
            {showHistory && (
              <ul className="mt-2 space-y-1 text-xs text-slate-600">
                {history.data?.map((v) => (
                  <li key={v.version}>
                    <b>v{v.version}</b> {v.change.replace("status:", "status → ").replace("_", " ")} by {v.changed_by_email ?? "—"}{" "}
                    on {new Date(v.changed_at).toLocaleString()}{v.note && ` — “${v.note}”`}
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </div>
    </Card>
  );
}

function RecordDialog({ record, isAdmin, onClose, onSaved }: {
  record: KnowledgeRecord | null; isAdmin: boolean; onClose: () => void; onSaved: (r: KnowledgeRecord) => void;
}) {
  const taxonomy = useQuery({ queryKey: ["taxonomy"], queryFn: () => api.get<TaxonomyFeature[]>("/api/meta/taxonomy") });
  const [kind, setKind] = useState<KnowledgeKind>(record?.kind ?? "capability");
  const [title, setTitle] = useState(record?.title ?? "");
  const [summary, setSummary] = useState(record?.summary ?? "");
  const [details, setDetails] = useState(record?.details ?? "");
  const [outcomes, setOutcomes] = useState(record?.outcomes ?? "");
  const [customer, setCustomer] = useState(record?.customer_name ?? "");
  const [industries, setIndustries] = useState(record?.industries.join(", ") ?? "");
  const [technologies, setTechnologies] = useState(record?.technologies.join(", ") ?? "");
  const [projectTypes, setProjectTypes] = useState(record?.project_types.join(", ") ?? "");
  const [capabilities, setCapabilities] = useState<string[]>(record?.capability_tags ?? []);
  const [extraTags, setExtraTags] = useState("");
  const [ai, setAi] = useState(record?.ai ?? false);
  const [automation, setAutomation] = useState(record?.automation ?? false);
  const [clientFacing, setClientFacing] = useState(record ? record.visibility === "client_facing" : false);
  const [referenceAllowed, setReferenceAllowed] = useState(record?.reference_allowed ?? false);
  const [note, setNote] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const known = new Set(taxonomy.data?.map((f) => f.id));
  const categories = [...new Set(taxonomy.data?.map((f) => f.category))];

  function toggleCap(id: string) {
    setCapabilities(capabilities.includes(id) ? capabilities.filter((c) => c !== id) : [...capabilities, id]);
  }

  async function save(submit: boolean) {
    if (title.trim().length < 2) {
      setError(new Error("Enter a title."));
      return;
    }
    setBusy(true);
    setError(null);
    const body = {
      kind, title: title.trim(), summary, details, outcomes, customer_name: customer.trim() || null,
      industries: splitTags(industries), technologies: splitTags(technologies), project_types: splitTags(projectTypes),
      capability_tags: [...capabilities, ...splitTags(extraTags)], ai, automation,
      visibility: clientFacing ? "client_facing" : "internal", reference_allowed: referenceAllowed, note,
    };
    try {
      let saved: KnowledgeRecord;
      if (record) {
        saved = await api.patch<KnowledgeRecord>(`/api/knowledge/${record.id}`, body);
      } else {
        saved = await api.post<KnowledgeRecord>("/api/knowledge", { ...body, status: submit && isAdmin ? "approved" : "draft" });
      }
      if (submit && !isAdmin && saved.status === "draft") {
        saved = await api.post<KnowledgeRecord>(`/api/knowledge/${saved.id}/status`, { status: "in_review" });
      }
      onSaved(saved);
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }

  const input = "border rounded px-2 py-1 text-sm w-full";
  return (
    <div className="fixed inset-0 z-10 bg-black/40 grid place-items-center p-4" onClick={onClose}>
      <div className="bg-white rounded-xl shadow-xl w-full max-w-3xl max-h-[90vh] overflow-auto" onClick={(e) => e.stopPropagation()}>
        <div className="px-5 py-4 border-b">
          <h2 className="text-lg font-semibold">{record ? "Edit record" : "Add knowledge record"}</h2>
          <p className="text-sm text-slate-500">
            Describe work your organization has delivered. Tag the capabilities it covers so analyses can match it
            to client opportunities.
          </p>
        </div>
        <div className="px-5 py-4 space-y-3">
          <div className="grid gap-3 sm:grid-cols-[12rem_1fr]">
            <label className="text-sm">Type
              <select value={kind} onChange={(e) => setKind(e.target.value as KnowledgeKind)} className={input}>
                {KINDS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
              </select>
            </label>
            <label className="text-sm">Title
              <input value={title} onChange={(e) => setTitle(e.target.value)} className={input} maxLength={300} />
            </label>
          </div>
          <label className="text-sm block">Summary
            <textarea value={summary} onChange={(e) => setSummary(e.target.value)} className={input} rows={2} />
          </label>
          <label className="text-sm block">Outcomes / results delivered
            <textarea value={outcomes} onChange={(e) => setOutcomes(e.target.value)} className={input} rows={2}
              placeholder="e.g. No-shows down 30% within 3 months" />
          </label>
          <label className="text-sm block">Details
            <textarea value={details} onChange={(e) => setDetails(e.target.value)} className={input} rows={3} />
          </label>
          <div className="grid gap-3 sm:grid-cols-2">
            <label className="text-sm">Industries (comma-separated)
              <input value={industries} onChange={(e) => setIndustries(e.target.value)} className={input} placeholder="Healthcare, Dental" />
            </label>
            <label className="text-sm">Technologies (comma-separated)
              <input value={technologies} onChange={(e) => setTechnologies(e.target.value)} className={input} placeholder="React, Twilio" />
            </label>
            <label className="text-sm">Project types (comma-separated)
              <input value={projectTypes} onChange={(e) => setProjectTypes(e.target.value)} className={input} placeholder="Web app, Integration" />
            </label>
            <label className="text-sm">Customer name {kind === "case_study" ? "" : "(optional)"}
              <input value={customer} onChange={(e) => setCustomer(e.target.value)} className={input} />
            </label>
          </div>
          <div className="text-sm">
            Capabilities covered
            <div className="mt-1 max-h-48 overflow-auto rounded border p-2 space-y-2">
              {categories.map((cat) => (
                <div key={cat}>
                  <div className="text-xs font-semibold text-slate-500">{cat}</div>
                  <div className="flex flex-wrap gap-1">
                    {taxonomy.data?.filter((f) => f.category === cat).map((f) => (
                      <button type="button" key={f.id} onClick={() => toggleCap(f.id)}
                        className={`rounded px-1.5 py-0.5 text-xs border ${capabilities.includes(f.id) ? "bg-indigo-600 text-white border-indigo-600" : "bg-white"}`}>
                        {f.name}
                      </button>
                    ))}
                  </div>
                </div>
              ))}
            </div>
            {capabilities.filter((c) => !known.has(c)).length > 0 && (
              <p className="text-xs text-slate-500 mt-1">Other tags: {capabilities.filter((c) => !known.has(c)).join(", ")}</p>
            )}
            <input value={extraTags} onChange={(e) => setExtraTags(e.target.value)} className={`${input} mt-1`}
              placeholder="Other capability keywords (comma-separated), e.g. appointment reminders" />
          </div>
          <div className="flex flex-wrap gap-4 text-sm">
            <label className="flex items-center gap-1"><input type="checkbox" checked={ai} onChange={(e) => setAi(e.target.checked)} /> AI capability</label>
            <label className="flex items-center gap-1"><input type="checkbox" checked={automation} onChange={(e) => setAutomation(e.target.checked)} /> Automation capability</label>
            <label className="flex items-center gap-1"><input type="checkbox" checked={clientFacing} onChange={(e) => setClientFacing(e.target.checked)} /> May be used in client-facing output</label>
            {kind === "case_study" && (
              <label className="flex items-center gap-1"><input type="checkbox" checked={referenceAllowed} onChange={(e) => setReferenceAllowed(e.target.checked)} /> Customer may be named as a reference</label>
            )}
          </div>
          <label className="text-sm block">Change note (optional)
            <input value={note} onChange={(e) => setNote(e.target.value)} className={input} />
          </label>
          <ErrorText error={error} />
        </div>
        <div className="px-5 py-3 border-t flex justify-end gap-2">
          <Button variant="secondary" onClick={onClose}>Cancel</Button>
          {!record && <Button variant="secondary" disabled={busy} onClick={() => save(false)}>Save as draft</Button>}
          <Button disabled={busy} onClick={() => save(!record)}>
            {record ? "Save changes" : isAdmin ? "Save and approve" : "Save and submit for review"}
          </Button>
        </div>
      </div>
    </div>
  );
}
