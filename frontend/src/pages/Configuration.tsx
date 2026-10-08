/* eslint-disable @typescript-eslint/no-explicit-any */
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api } from "../lib/api";
import type { User } from "../lib/types";
import { Button, Card, Empty, ErrorText } from "../components/ui";

interface Version { version: number; note: string; created_by_email: string | null; created_at: string }
interface View {
  kind: string; key: string | null; version: number; overrides: any; effective: any; defaults: any; history: Version[];
  prompts?: { id: string; agent: string; label: string; default: string; text: string; overridden: boolean; version: string }[];
  tiers?: Record<string, string>; profiles?: string[];
}

const TABS = [["analysis", "Analysis settings"], ["scoring_profile", "Scoring profiles"], ["taxonomy", "Capability taxonomy"],
  ["llm", "Prompts & models"]] as const;
const ANALYSIS_LABELS: Record<string, string> = {
  max_competitors: "Competitors in the landscape (Top-N)", deep_competitors: "Competitors analysed in depth",
  crawler_max_pages: "Pages crawled per client site", competitor_light_pages: "Pages per candidate competitor",
  competitor_deep_pages: "Pages per deep-analysed competitor", crawler_max_pdfs: "PDFs read per site",
  ux_max_pages: "Pages in the UX review", roadmap_top_n: "Recommendations in the roadmap",
  freshness_fresh_days: "Evidence is fresh up to (days)", freshness_stale_days: "Evidence is stale after (days)",
};
const SCORING_LABELS: Record<string, string> = {
  confidence_weight: "Evidence-confidence weight (0-1)", high_min_normalized: "High priority from score",
  medium_min_normalized: "Medium priority from score", high_min_confidence: "High priority needs confidence",
  quick_win_max_complexity: "Quick win: max complexity", growth_max_complexity: "Growth: max complexity",
  major_max_complexity: "Major: max complexity", strategic_min_ai_opportunity: "Strategic: min AI opportunity",
};
const input = "border rounded px-2 py-1 text-sm";

/** Versioned organization configuration. Admins edit; analysts can see what analyses use. */
export default function ConfigurationPage() {
  const me = useQuery({ queryKey: ["me"], queryFn: () => api.get<User>("/api/auth/me") });
  const [tab, setTab] = useState<(typeof TABS)[number][0]>("analysis");
  const [profile, setProfile] = useState("Standard");
  const admin = me.data?.role === "admin";
  return (
    <div className="space-y-4 max-w-5xl">
      <div>
        <h1 className="text-2xl font-bold">Configuration</h1>
        <p className="text-sm text-slate-500">
          Every change is saved as a new version. Each analysis records the versions it used, so editing here never changes past results.
          {!admin && " Only admins can change the configuration."}
        </p>
      </div>
      <div className="flex gap-1 border-b">
        {TABS.map(([k, l]) => (
          <button key={k} onClick={() => setTab(k)}
            className={`px-3 py-2 text-sm -mb-px border-b-2 ${tab === k ? "border-indigo-600 text-indigo-700 font-medium" : "border-transparent text-slate-600"}`}>{l}</button>
        ))}
      </div>
      {tab === "scoring_profile" ? (
        <ScoringProfiles admin={admin} profile={profile} setProfile={setProfile} />
      ) : (
        <Section kind={tab} admin={admin} />
      )}
    </div>
  );
}

function useConfig(kind: string, key?: string) {
  const qs = key ? `?key=${encodeURIComponent(key)}` : "";
  return useQuery({ queryKey: ["config", kind, key ?? ""], queryFn: () => api.get<View>(`/api/config/${kind}${qs}`) });
}

function Section({ kind, admin }: { kind: string; admin: boolean }) {
  const view = useConfig(kind);
  if (!view.data) return <p>Loading…</p>;
  return (
    <Editor key={kind} view={view.data} admin={admin}>
      {(draft, setDraft) => kind === "analysis" ? <AnalysisForm view={view.data} draft={draft} setDraft={setDraft} admin={admin} />
        : kind === "taxonomy" ? <TaxonomyForm draft={draft} setDraft={setDraft} admin={admin} />
          : <LlmForm view={view.data} draft={draft} setDraft={setDraft} admin={admin} />}
    </Editor>
  );
}

/** Shared save / history / revert frame around a draft of the kind's data. */
function Editor({ view, admin, children, keyName }: {
  view: View; admin: boolean; keyName?: string;
  children: (draft: any, setDraft: (d: any) => void) => React.ReactNode;
}) {
  const qc = useQueryClient();
  const start = view.kind === "taxonomy" ? view.effective : view.kind === "llm" ? { ...view.overrides } : { ...view.effective };
  const [draft, setDraft] = useState<any>(structuredClone(start));
  const [note, setNote] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [msg, setMsg] = useState("");
  // A new current version (saved or restored) resets the draft to it.
  useEffect(() => { setDraft(structuredClone(start)); }, [view.version]); // eslint-disable-line react-hooks/exhaustive-deps
  const qs = keyName ? `?key=${encodeURIComponent(keyName)}` : "";
  async function save() {
    setError(null);
    try {
      const next = await api.put<View>(`/api/config/${view.kind}${qs}`, { data: payload(view, draft), note });
      qc.setQueryData(["config", view.kind, keyName ?? ""], next);
      qc.invalidateQueries({ queryKey: ["config-overview"] });
      setNote("");
      setMsg(`Saved version ${next.version}.`);
    } catch (e) {
      setError(e);
    }
  }
  async function revert(version: number) {
    if (!confirm(`Make version ${version} current again? It is saved as a new version.`)) return;
    try {
      const next = await api.post<View>(`/api/config/${view.kind}/revert${qs}`, { version });
      qc.setQueryData(["config", view.kind, keyName ?? ""], next);
      setMsg(`Restored version ${version} as version ${next.version}.`);
    } catch (e) {
      setError(e);
    }
  }
  return (
    <div className="grid lg:grid-cols-[1fr_16rem] gap-4 items-start">
      <Card title={`Version ${view.version || "0 (built-in defaults)"}`}>
        {children(draft, setDraft)}
        {admin && (
          <div className="flex flex-wrap gap-2 items-center mt-4 pt-3 border-t">
            <input className={`${input} flex-1 min-w-[12rem]`} placeholder="What changed and why (kept in the history)" value={note} onChange={(e) => setNote(e.target.value)} />
            <Button onClick={save}>Save as new version</Button>
            <Button variant="secondary" onClick={() => setDraft(structuredClone(view.kind === "llm" ? {} : view.defaults))}>Load built-in defaults</Button>
          </div>
        )}
        {msg && <p className="text-sm text-emerald-700 mt-2">{msg}</p>}
        <ErrorText error={error} />
      </Card>
      <Card title="History">
        {view.history.length === 0 ? <Empty>Built-in defaults.</Empty> : (
          <ul className="text-xs space-y-2">
            {view.history.map((v) => (
              <li key={v.version}>
                <div className="font-medium">v{v.version} · {new Date(v.created_at).toLocaleString()}</div>
                <div className="text-slate-500">{v.created_by_email}{v.note && ` — ${v.note}`}</div>
                {admin && v.version !== view.version && <button className="underline text-indigo-700" onClick={() => revert(v.version)}>restore</button>}
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}

function payload(view: View, draft: any): any {
  if (view.kind === "taxonomy") return draft;
  if (view.kind === "llm") return draft;
  // Store only what differs from the built-in defaults.
  const out: any = {};
  for (const [k, v] of Object.entries(draft)) {
    if (k === "weights") {
      const w = Object.fromEntries(Object.entries(v as Record<string, number>).filter(([f, n]) => n !== view.defaults.weights[f]));
      if (Object.keys(w).length) out.weights = w;
    } else if (JSON.stringify(v) !== JSON.stringify(view.defaults[k])) out[k] = v;
  }
  return out;
}

function NumberField({ label, value, onChange, disabled, step = 1 }: { label: string; value: number; onChange: (n: number) => void; disabled: boolean; step?: number }) {
  return (
    <label className="flex justify-between items-center gap-3 text-sm">
      <span className="text-slate-600">{label}</span>
      <input type="number" step={step} className={`${input} w-24`} value={value} disabled={disabled} onChange={(e) => onChange(Number(e.target.value))} />
    </label>
  );
}

function AnalysisForm({ view, draft, setDraft, admin }: { view: View; draft: any; setDraft: (d: any) => void; admin: boolean }) {
  return (
    <div className="space-y-4">
      <div className="grid md:grid-cols-2 gap-x-6 gap-y-2">
        {Object.entries(ANALYSIS_LABELS).map(([k, l]) => (
          <NumberField key={k} label={l} value={draft[k]} disabled={!admin} onChange={(n) => setDraft({ ...draft, [k]: n })} />
        ))}
      </div>
      <div>
        <div className="text-sm font-medium">Source-quality tiers</div>
        <p className="text-xs text-slate-500 mb-1">A gap's confidence is multiplied by the factor of its best source (0.3-1.0). Source quality influences, but never alone decides, priority.</p>
        <div className="grid md:grid-cols-2 gap-x-6 gap-y-1">
          {Object.entries(view.tiers ?? {}).map(([t, l]) => (
            <NumberField key={t} step={0.05} label={`${t}. ${l}`} value={draft.source_tier_factors[t]} disabled={!admin}
              onChange={(n) => setDraft({ ...draft, source_tier_factors: { ...draft.source_tier_factors, [t]: n } })} />
          ))}
        </div>
      </div>
      <label className="text-sm flex items-center gap-2">Default scoring profile
        <select className={input} value={draft.default_scoring_profile} disabled={!admin} onChange={(e) => setDraft({ ...draft, default_scoring_profile: e.target.value })}>
          {(view.profiles ?? []).map((p) => <option key={p}>{p}</option>)}
        </select>
      </label>
    </div>
  );
}

function ScoringProfiles({ admin, profile, setProfile }: { admin: boolean; profile: string; setProfile: (p: string) => void }) {
  const overview = useQuery({ queryKey: ["config-overview"], queryFn: () => api.get<any>("/api/config") });
  const view = useConfig("scoring_profile", profile);
  const names: string[] = overview.data?.scoring_profiles.map((p: any) => p.name) ?? [];
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        Profile
        <select className={input} value={profile} onChange={(e) => setProfile(e.target.value)}>
          {[...new Set([...names, profile])].map((n) => <option key={n}>{n}{n === overview.data?.default_scoring_profile ? " (default)" : ""}</option>)}
        </select>
        {admin && <Button variant="secondary" onClick={() => { const n = prompt("Name of the new profile (e.g. Growth, Cost reduction)"); if (n) setProfile(n.trim()); }}>New profile</Button>}
      </div>
      {view.data && (
        <Editor key={profile} view={view.data} admin={admin} keyName={profile}>
          {(draft, setDraft) => (
            <div className="space-y-4">
              <div>
                <div className="text-sm font-medium mb-1">Factor weights</div>
                <div className="grid md:grid-cols-2 gap-x-6 gap-y-1">
                  {Object.keys(draft.weights).map((f) => (
                    <NumberField key={f} step={0.1} label={f.replaceAll("_", " ")} value={draft.weights[f]} disabled={!admin}
                      onChange={(n) => setDraft({ ...draft, weights: { ...draft.weights, [f]: n } })} />
                  ))}
                </div>
              </div>
              <div>
                <div className="text-sm font-medium mb-1">Priority and horizon rules</div>
                <div className="grid md:grid-cols-2 gap-x-6 gap-y-1">
                  {Object.entries(SCORING_LABELS).map(([k, l]) => (
                    <NumberField key={k} step={0.01} label={l} value={draft[k]} disabled={!admin} onChange={(n) => setDraft({ ...draft, [k]: n })} />
                  ))}
                </div>
              </div>
            </div>
          )}
        </Editor>
      )}
    </div>
  );
}

const csv = (xs: string[]) => xs.join(", ");
const list = (s: string) => s.split(",").map((x) => x.trim()).filter(Boolean);

function TaxonomyForm({ draft, setDraft, admin }: { draft: any; setDraft: (d: any) => void; admin: boolean }) {
  const [open, setOpen] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const update = (ci: number, fi: number, patch: any) => {
    const next = structuredClone(draft);
    next.categories[ci].features[fi] = { ...next.categories[ci].features[fi], ...patch };
    setDraft(next);
  };
  const count = draft.categories.reduce((n: number, c: any) => n + c.features.length, 0);
  return (
    <div className="space-y-3">
      <div className="flex gap-2 items-center text-sm">
        <input className={`${input} w-64`} placeholder="Filter capabilities…" value={q} onChange={(e) => setQ(e.target.value)} />
        <span className="text-slate-500">{draft.categories.length} categories · {count} capabilities</span>
        {admin && <Button variant="secondary" onClick={() => {
          const name = prompt("New category name");
          if (!name) return;
          const id = name.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "");
          setDraft({ ...draft, categories: [...draft.categories, { id, name, features: [] }] });
        }}>Add category</Button>}
      </div>
      {draft.categories.map((c: any, ci: number) => {
        const feats = c.features.map((f: any, fi: number) => [f, fi]).filter(([f]: any) => !q || `${f.id} ${f.name} ${csv(f.keywords ?? [])}`.toLowerCase().includes(q.toLowerCase()));
        if (q && !feats.length) return null;
        return (
          <div key={c.id} className="border rounded-lg">
            <div className="px-3 py-2 bg-slate-50 flex justify-between items-center">
              <span className="font-medium text-sm">{c.name} <span className="text-xs text-slate-500">{c.id}</span></span>
              {admin && <button className="text-xs underline text-indigo-700" onClick={() => {
                const name = prompt(`New capability in ${c.name}`);
                if (!name) return;
                const slug = name.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "");
                const next = structuredClone(draft);
                next.categories[ci].features.push({ id: `${c.id}.${slug}`, name, keywords: [name.toLowerCase()], code_signals: [], defaults: {} });
                setDraft(next);
                setOpen(`${c.id}.${slug}`);
              }}>add capability</button>}
            </div>
            <ul className="divide-y">
              {feats.map(([f, fi]: any) => (
                <li key={f.id} className="px-3 py-1.5 text-sm">
                  <button className="text-left w-full flex justify-between" onClick={() => setOpen(open === f.id ? null : f.id)}>
                    <span>{f.name} <span className="text-xs text-slate-400">{f.id}</span></span>
                    <span className="text-xs text-slate-500">{(f.keywords ?? []).length} keywords</span>
                  </button>
                  {open === f.id && (
                    <div className="mt-2 grid gap-2">
                      <label className="grid gap-1"><span className="text-xs text-slate-500">Name</span>
                        <input className={input} value={f.name} disabled={!admin} onChange={(e) => update(ci, fi, { name: e.target.value })} /></label>
                      <label className="grid gap-1"><span className="text-xs text-slate-500">Keywords and synonyms (comma separated) — matched in website and document text</span>
                        <input className={input} defaultValue={csv(f.keywords ?? [])} disabled={!admin} onBlur={(e) => update(ci, fi, { keywords: list(e.target.value) })} /></label>
                      <label className="grid gap-1"><span className="text-xs text-slate-500">Code signals (dependency names or /path/ fragments)</span>
                        <input className={input} defaultValue={csv(f.code_signals ?? [])} disabled={!admin} onBlur={(e) => update(ci, fi, { code_signals: list(e.target.value) })} /></label>
                      <label className="flex items-center gap-2 text-xs"><input type="checkbox" checked={!!f.ai} disabled={!admin} onChange={(e) => update(ci, fi, { ai: e.target.checked })} /> AI capability</label>
                      {admin && <button className="text-xs text-rose-700 underline justify-self-start" onClick={() => {
                        if (!confirm(`Remove ${f.name}? Past analyses keep it; new ones won't look for it.`)) return;
                        const next = structuredClone(draft);
                        next.categories[ci].features.splice(fi, 1);
                        setDraft(next);
                      }}>remove capability</button>}
                    </div>
                  )}
                </li>
              ))}
            </ul>
          </div>
        );
      })}
    </div>
  );
}

function LlmForm({ view, draft, setDraft, admin }: { view: View; draft: any; setDraft: (d: any) => void; admin: boolean }) {
  const [open, setOpen] = useState<string | null>(null);
  const prompts = draft.prompts ?? {};
  const agents = [...new Set((view.prompts ?? []).map((p) => p.agent))];
  useEffect(() => { if (!draft.agents) setDraft({ ...draft, agents: {}, prompts: draft.prompts ?? {} }); }, [draft, setDraft]);
  return (
    <div className="space-y-4 text-sm">
      <div className="flex flex-wrap gap-4">
        <label className="flex items-center gap-2">Default model
          <input className={`${input} w-64`} placeholder={view.defaults.default_model} value={draft.default_model ?? ""} disabled={!admin}
            onChange={(e) => setDraft({ ...draft, default_model: e.target.value || undefined })} /></label>
        <label className="flex items-center gap-2">Temperature
          <input type="number" step={0.1} className={`${input} w-20`} placeholder={String(view.defaults.temperature)} value={draft.temperature ?? ""} disabled={!admin}
            onChange={(e) => setDraft({ ...draft, temperature: e.target.value === "" ? undefined : Number(e.target.value) })} /></label>
      </div>
      <div>
        <div className="font-medium mb-1">Per agent</div>
        <table className="text-sm">
          <thead className="text-left text-slate-500"><tr><th className="pr-4">Agent</th><th className="pr-2">Model</th><th>Temperature</th></tr></thead>
          <tbody>
            {agents.map((a) => {
              const cfg = draft.agents?.[a] ?? {};
              const set = (patch: any) => setDraft({ ...draft, agents: { ...draft.agents, [a]: { ...cfg, ...patch } } });
              return (
                <tr key={a}>
                  <td className="pr-4 py-0.5">{a.replaceAll("_", " ")}</td>
                  <td className="pr-2"><input className={`${input} w-56`} placeholder="default" value={cfg.model ?? ""} disabled={!admin} onChange={(e) => set({ model: e.target.value || undefined })} /></td>
                  <td><input type="number" step={0.1} className={`${input} w-20`} placeholder="default" value={cfg.temperature ?? ""} disabled={!admin}
                    onChange={(e) => set({ temperature: e.target.value === "" ? undefined : Number(e.target.value) })} /></td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <div>
        <div className="font-medium mb-1">Prompts</div>
        <p className="text-xs text-slate-500 mb-2">Edits change the instructions only: outputs are still validated against each agent's schema, and extracted facts still need a source URL and a verbatim quote before they become evidence. Prompts must never contain credentials.</p>
        <ul className="divide-y border rounded-lg">
          {(view.prompts ?? []).map((p) => {
            const text = prompts[p.id] ?? p.default;
            const changed = text.trim() !== p.default.trim();
            return (
              <li key={p.id} className="px-3 py-2">
                <button className="w-full text-left flex justify-between" onClick={() => setOpen(open === p.id ? null : p.id)}>
                  <span>{p.label} <span className="text-xs text-slate-400">{p.id}</span></span>
                  <span className={`text-xs ${changed ? "text-amber-700" : "text-slate-400"}`}>{changed ? "customized" : "default"} · v{p.version}</span>
                </button>
                {open === p.id && (
                  <div className="mt-2 space-y-1">
                    <textarea className={`${input} w-full h-48 font-mono text-xs`} value={text} disabled={!admin}
                      onChange={(e) => setDraft({ ...draft, prompts: { ...prompts, [p.id]: e.target.value } })} />
                    {admin && changed && <button className="text-xs underline" onClick={() => { const next = { ...prompts }; delete next[p.id]; setDraft({ ...draft, prompts: next }); }}>reset to default</button>}
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      </div>
    </div>
  );
}
