/* eslint-disable @typescript-eslint/no-explicit-any */
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Fragment, useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { useParams } from "react-router-dom";
import { api } from "../lib/api";
import type { AgentResult, Evidence, Run } from "../lib/types";
import { Badge, BasisTag, Button, Card, Confidence, Empty, ErrorText } from "../components/ui";
import EvidenceRefs, { EvidenceContext } from "../components/EvidenceRefs";
import { AnnouncementsCard, CompanyFacts, HiringCard } from "../components/CompanyExtras";

const TABS = ["Pipeline", "Client", "Project", "Competitors", "Pricing", "Comparison", "Gaps", "Opportunities",
  "Roadmap", "Evidence", "Report"] as const;
type Tab = (typeof TABS)[number];
const ACTIVE = new Set(["queued", "running"]);

function useAgent(runId: string, agent: string, enabled: boolean) {
  return useQuery({
    queryKey: ["agent", runId, agent],
    queryFn: () => api.get<{ result: AgentResult | null }>(`/api/runs/${runId}/agents/${agent}`),
    enabled,
    select: (d) => d.result,
  });
}

export default function RunDetailPage() {
  const { runId = "" } = useParams();
  const qc = useQueryClient();
  const [tab, setTab] = useState<Tab>("Pipeline");
  const run = useQuery({
    queryKey: ["run", runId],
    queryFn: () => api.get<Run>(`/api/runs/${runId}`),
    refetchInterval: (q) => (q.state.data && !ACTIVE.has(q.state.data.status) ? false : 2000),
  });
  const evidence = useQuery({
    queryKey: ["evidence", runId, run.data?.updated_at],
    queryFn: () => api.get<Evidence[]>(`/api/runs/${runId}/evidence`),
    enabled: !!run.data,
  });
  const index = useMemo(() => new Map((evidence.data ?? []).map((e) => [e.id, e])), [evidence.data]);
  const [error, setError] = useState<unknown>(null);

  if (!run.data) return <p>Loading…</p>;
  const r = run.data;
  const done = (agent: string) => r.agents[agent] === "completed";

  async function decide(approvalId: string, approve: boolean) {
    try {
      await api.post(`/api/runs/${runId}/approvals/${approvalId}`, { approve });
      qc.invalidateQueries({ queryKey: ["run", runId] });
    } catch (e) {
      setError(e);
    }
  }

  async function resume() {
    try {
      await api.post(`/api/runs/${runId}/resume`);
      qc.invalidateQueries({ queryKey: ["run", runId] });
    } catch (e) {
      setError(e);
    }
  }

  const pending = r.approvals.filter((a) => a.status === "pending");
  return (
    <EvidenceContext.Provider value={index}>
      <div className="space-y-4">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold">Analysis run</h1>
            <div className="text-sm text-slate-500 font-mono">{r.run_id}</div>
          </div>
          <div className="flex items-center gap-2">
            <Badge value={r.status} />
            {["failed", "completed_with_errors"].includes(r.status) && <Button variant="secondary" onClick={resume}>Retry failed agents</Button>}
            {r.has_report && (
              <>
                <Button onClick={() => api.download(`/api/runs/${runId}/report.pdf`, `report-${runId}.pdf`).catch(setError)}>
                  Download PDF
                </Button>
                <Button variant="secondary" onClick={() => api.download(`/api/runs/${runId}/report.md`, `report-${runId}.md`).catch(setError)}>
                  Markdown
                </Button>
              </>
            )}
          </div>
        </div>
        <ErrorText error={error ?? r.error} />

        {pending.map((a) => (
          <div key={a.id} className="border border-amber-300 bg-amber-50 rounded-xl p-4">
            <div className="font-semibold text-amber-900">Approval required: {a.title}</div>
            <dl className="text-sm mt-2 grid grid-cols-[max-content_1fr] gap-x-3 gap-y-1">
              <dt className="text-slate-500">Target</dt><dd>{a.target}</dd>
              <dt className="text-slate-500">What will be accessed</dt><dd>{a.what}</dd>
              <dt className="text-slate-500">Why</dt><dd>{a.why}</dd>
              <dt className="text-slate-500">Data analyzed</dt><dd>{a.data_analyzed}</dd>
            </dl>
            <div className="mt-3 flex gap-2">
              <Button onClick={() => decide(a.id, true)}>Approve</Button>
              <Button variant="danger" onClick={() => decide(a.id, false)}>Reject (skip step)</Button>
            </div>
          </div>
        ))}

        <div className="flex flex-wrap gap-1 border-b">
          {TABS.map((t) => (
            <button key={t} onClick={() => setTab(t)}
              className={`px-3 py-2 text-sm -mb-px border-b-2 ${tab === t ? "border-indigo-600 text-indigo-700 font-medium" : "border-transparent text-slate-600 hover:text-slate-900"}`}>
              {t}
            </button>
          ))}
        </div>

        {tab === "Pipeline" && <Pipeline run={r} />}
        {tab === "Client" && <ClientTab runId={runId} enabled={done("client_research")} />}
        {tab === "Project" && <ProjectTab runId={runId} enabled={done("product_features")} codeDone={done("code_analysis")} />}
        {tab === "Competitors" && <CompetitorsTab runId={runId} enabled={done("competitor_research")} />}
        {tab === "Pricing" && <PricingTab runId={runId} enabled={done("pricing_analysis")} />}
        {tab === "Comparison" && <ComparisonTab runId={runId} enabled={done("feature_comparison")} />}
        {tab === "Gaps" && <GapsTab runId={runId} enabled={done("gap_analysis")} />}
        {tab === "Opportunities" && <OpportunitiesTab runId={runId} enabled={done("opportunity_prioritization")} />}
        {tab === "Roadmap" && <RoadmapTab runId={runId} enabled={done("enhancement_planning")} />}
        {tab === "Evidence" && <EvidenceTab evidence={evidence.data ?? []} />}
        {tab === "Report" && <ReportTab runId={runId} enabled={r.has_report} />}
      </div>
    </EvidenceContext.Provider>
  );
}

function Pipeline({ run }: { run: Run }) {
  return (
    <Card title="Agent pipeline">
      <ol className="space-y-2">
        {run.agent_details.map((a, i) => (
          <li key={a.agent} className="flex items-start gap-3 text-sm">
            <span className="w-6 text-right text-slate-400">{i + 1}</span>
            <div className="flex-1">
              <div className="flex items-center gap-2">
                <span className="font-medium">{a.agent.replaceAll("_", " ")}</span>
                <Badge value={a.status} />
                {a.status === "running" && <span className="animate-pulse text-sky-600">●</span>}
                <Confidence value={a.confidence} />
              </div>
              <div className="text-slate-500">{a.description}</div>
              {a.status === "completed" && (
                <div className="text-xs text-slate-400">{a.finding_count} findings · {a.evidence_count} evidence items{a.attempts > 1 ? ` · ${a.attempts} attempts` : ""}</div>
              )}
              {a.error && <div className="text-xs text-rose-700">{a.error}</div>}
            </div>
          </li>
        ))}
      </ol>
    </Card>
  );
}

function Pending() {
  return <Empty>Available once the corresponding agent has completed.</Empty>;
}

function ClientTab({ runId, enabled }: { runId: string; enabled: boolean }) {
  const res = useAgent(runId, "client_research", enabled);
  if (!enabled) return <Pending />;
  const d = res.data?.data;
  if (!d) return null;
  const p = d.profile;
  return (
    <div className="grid lg:grid-cols-2 gap-4">
      <Card title={p.name}>
        <p className="text-sm">{p.description ?? <Empty>No public description found.</Empty>} <EvidenceRefs ids={p.evidence_ids} /></p>
        <CompanyFacts profile={p} />
      </Card>
      <Card title="Products & services">
        {p.products.length ? (
          <ul className="space-y-2 text-sm">
            {p.products.map((x: any) => (
              <li key={x.name}>
                <span className="font-medium">{x.name}</span> <Badge value={x.kind} /> <EvidenceRefs ids={x.evidence_ids} />
                <div className="text-slate-500">{x.description}</div>
              </li>
            ))}
          </ul>
        ) : <Empty>None discovered.</Empty>}
      </Card>
      <Card title="Leadership">
        {d.leadership.length ? (
          <ul className="text-sm space-y-1">{d.leadership.map((l: any) => <li key={l.name}>{l.name} — {l.title} <EvidenceRefs ids={l.evidence_ids} /></li>)}</ul>
        ) : <Empty>No public leadership information found.</Empty>}
      </Card>
      <HiringCard hiring={d.hiring} />
      <AnnouncementsCard items={d.announcements ?? []} />
      <Card title="Contact & social">
        {p.contacts.length ? (
          <ul className="text-sm space-y-1">
            {p.contacts.map((c: any) => (
              <li key={c.type + c.value}><Badge value={c.type} /> <span className="break-all">{c.value}</span> <Confidence value={c.confidence} /></li>
            ))}
          </ul>
        ) : <Empty>None found.</Empty>}
      </Card>
    </div>
  );
}

function ProjectTab({ runId, enabled, codeDone }: { runId: string; enabled: boolean; codeDone: boolean }) {
  const features = useAgent(runId, "product_features", enabled);
  const code = useAgent(runId, "code_analysis", codeDone);
  if (!enabled) return <Pending />;
  const inv = features.data?.data.inventory ?? [];
  const profiles = code.data?.data.profiles ?? [];
  return (
    <div className="space-y-4">
      <Card title="Features">
        <table className="w-full text-sm">
          <thead className="text-left text-slate-500"><tr><th>Feature</th><th>Status</th><th>Technology</th><th>Evidence</th></tr></thead>
          <tbody>
            {inv.map((f: any) => (
              <tr key={f.name} className="border-t align-top">
                <td className="py-1.5">{f.name}<BasisTag basis={f.basis} /><div className="text-xs text-slate-500">{f.description}</div></td>
                <td><Badge value={f.status} /></td>
                <td className="text-xs">{(f.technology ?? []).join(", ") || "—"}</td>
                <td><EvidenceRefs ids={f.evidence_ids} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
      {profiles.map((p: any) => (
        <Card key={p.url} title={<a className="underline" href={p.url} target="_blank" rel="noreferrer">{p.full_name}</a>}>
          <div className="text-sm space-y-2">
            <div><span className="text-slate-500">Architecture:</span> {p.architecture.join(", ") || "Not determined"}</div>
            {p.summary && <div>{p.summary}<BasisTag basis="estimate" /></div>}
            <div className="flex flex-wrap gap-2">
              {p.technologies.map((t: any) => (
                <span key={t.category + t.name} className="border rounded px-2 py-0.5 text-xs">
                  <span className="text-slate-400">{t.category}</span> {t.name} <EvidenceRefs ids={t.evidence_ids} />
                </span>
              ))}
            </div>
            <div className="text-xs text-slate-600">Tests: {p.has_tests ? "yes" : "no"} · CI/CD: {p.has_ci ? "yes" : "no"} · Docker: {p.has_docker ? "yes" : "no"} · IaC: {p.has_iac ? "yes" : "no"}</div>
            {p.technical_debt_indicators.length > 0 && (
              <ul className="list-disc pl-5 text-amber-800">{p.technical_debt_indicators.map((d: string) => <li key={d}>{d}</li>)}</ul>
            )}
            {p.skipped_sensitive_files.length > 0 && (
              <div className="text-xs text-rose-700">Not read (sensitive): {p.skipped_sensitive_files.join(", ")}</div>
            )}
          </div>
        </Card>
      ))}
    </div>
  );
}

function CompetitorsTab({ runId, enabled }: { runId: string; enabled: boolean }) {
  const res = useAgent(runId, "competitor_research", enabled);
  if (!enabled) return <Pending />;
  const d = res.data?.data;
  if (!d) return null;
  return (
    <div className="space-y-4">
      <div className="grid lg:grid-cols-2 gap-4">
        {d.competitors.map((c: any) => (
          <Card key={c.id} title={<><a className="underline" href={c.url} target="_blank" rel="noreferrer">{c.name}</a> <Badge value={c.classification} /></>}>
            <p className="text-sm">{c.description} <EvidenceRefs ids={c.evidence_ids} /></p>
            {c.pricing && <p className="text-sm mt-1"><span className="text-slate-500">Pricing:</span> {c.pricing}</p>}
            {c.target_market && <p className="text-sm"><span className="text-slate-500">Market:</span> {c.target_market}</p>}
            <p className="text-xs text-slate-500 mt-1">{c.rationale}</p>
            <div className="mt-2 flex flex-wrap gap-1">
              {c.features.map((f: any) => <span key={f.feature_id} className="text-xs bg-slate-100 rounded px-1.5 py-0.5">{f.feature_id}</span>)}
            </div>
            <div className="mt-2"><Confidence value={c.confidence} /></div>
          </Card>
        ))}
      </div>
      {d.competitors.length === 0 && <Empty>No verified competitors. {res.data?.findings[0]?.detail}</Empty>}
      {d.rejected.length > 0 && (
        <Card title="Rejected candidates">
          <ul className="text-sm space-y-1">{d.rejected.map((c: any) => <li key={c.url}>{c.name} <span className="text-slate-500">— {c.rationale}</span></li>)}</ul>
        </Card>
      )}
    </div>
  );
}

const MODEL_LABELS: Record<string, string> = {
  per_seat: "per seat", flat: "flat fee", usage_based: "usage-based", tiered: "tiered", freemium: "freemium",
  quote_based: "quote only",
};

function PricingTab({ runId, enabled }: { runId: string; enabled: boolean }) {
  const res = useAgent(runId, "pricing_analysis", enabled);
  if (!enabled) return <Pending />;
  const d = res.data?.data;
  if (!d) return null;
  const m = d.market ?? {};
  const money = (v: number | null, cur: string | null) => (v ? `${v.toLocaleString()} ${cur ?? ""}` : "—");
  const row = (name: React.ReactNode, p: any, ids: string[]) => (
    <tr className="border-t align-top">
      <td className="py-1.5 font-medium">{name}</td>
      <td>{money(p.entry_price_monthly, p.currency)}</td>
      <td>{money(p.max_price_monthly, p.currency)}</td>
      <td className="text-xs">{(p.models ?? []).map((x: string) => MODEL_LABELS[x] ?? x).join(", ") || "—"}</td>
      <td className="text-center">{p.free_trial ? `✓${p.trial_days ? ` ${p.trial_days}d` : ""}` : "—"}</td>
      <td className="text-center">{p.free_tier ? "✓" : "—"}</td>
      <td className="text-center">{p.annual_discount_pct ? `${p.annual_discount_pct}%` : "—"}</td>
      <td className="text-center">{p.enterprise_contact ? "✓" : "—"}</td>
      <td><EvidenceRefs ids={ids} /></td>
    </tr>
  );
  const pos: Record<string, string> = { above: "above", below: "below", within: "within", unknown: "unknown vs" };
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        {[["Client position", `${pos[d.position] ?? d.position} market`],
          ["Market entry price (median)", m.entry_price_median ? `${m.entry_price_median} ${m.currency ?? ""}/mo` : "—"],
          ["Competitors with free trial", m.free_trial_share != null ? `${Math.round(m.free_trial_share * 100)}%` : "—"],
          ["Competitors pricing per seat", m.per_seat_share != null ? `${Math.round(m.per_seat_share * 100)}%` : "—"]].map(([k, v]) => (
          <div key={k} className="bg-white border rounded-xl p-3">
            <div className="text-xs text-slate-500">{k}</div>
            <div className="text-lg font-semibold">{v}</div>
          </div>
        ))}
      </div>
      <Card title="Pricing comparison (per month)">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="text-left text-slate-500">
              <tr><th className="py-1">Company</th><th>Entry</th><th>Highest</th><th>Model</th><th className="text-center">Trial</th>
                <th className="text-center">Free tier</th><th className="text-center">Annual disc.</th><th className="text-center">Enterprise</th><th /></tr>
            </thead>
            <tbody>
              {d.client ? row("Client", d.client, d.client.evidence_ids ?? []) :
                <tr className="border-t"><td className="py-1.5 font-medium">Client</td><td colSpan={8} className="text-slate-500 italic">No public pricing page found</td></tr>}
              {d.competitors.map((c: any) => <Fragment key={c.competitor_id}>{row(c.name, c, c.evidence_ids)}</Fragment>)}
            </tbody>
          </table>
        </div>
        {m.excluded_other_currency > 0 && <p className="text-xs text-slate-500 mt-2">{m.excluded_other_currency} competitor(s) priced in another currency are excluded from the market statistics.</p>}
      </Card>
      <Card title="Pricing gaps">
        {d.gaps.length ? (
          <ul className="space-y-2 text-sm">
            {d.gaps.map((g: any) => (
              <li key={g.id}><span className="font-medium">{g.name}</span><BasisTag basis={g.basis} /> <Confidence value={g.confidence} /> <EvidenceRefs ids={g.evidence_ids} />
                <div className="text-slate-600">{g.description}</div></li>
            ))}
          </ul>
        ) : <Empty>No pricing gaps — the client's pricing practices match the market.</Empty>}
      </Card>
    </div>
  );
}

const ICON: Record<string, string> = { available: "✅", partial: "🟡", missing: "❌", unknown: "·" };

function ComparisonTab({ runId, enabled }: { runId: string; enabled: boolean }) {
  const res = useAgent(runId, "feature_comparison", enabled);
  if (!enabled) return <Pending />;
  const d = res.data?.data;
  if (!d) return null;
  return (
    <Card title="Feature comparison">
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="text-left text-slate-500">
            <tr><th className="py-1">Category</th><th>Feature</th><th className="text-center">Client</th>
              {d.competitors.map((c: any) => <th key={c.id} className="text-center">{c.name}</th>)}</tr>
          </thead>
          <tbody>
            {d.rows.map((row: any) => (
              <tr key={row.feature_id} className="border-t">
                <td className="py-1 text-slate-500">{row.category}</td>
                <td>{row.feature_name}</td>
                <td className="text-center">{ICON[row.client]}</td>
                {d.competitors.map((c: any) => <td key={c.id} className="text-center">{ICON[row.competitors[c.id]]}</td>)}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="text-xs text-slate-500 mt-2">✅ available · 🟡 partial · ❌ not found · · not evidenced</p>
    </Card>
  );
}

function GapsTab({ runId, enabled }: { runId: string; enabled: boolean }) {
  const res = useAgent(runId, "gap_analysis", enabled);
  if (!enabled) return <Pending />;
  const d = res.data?.data;
  if (!d) return null;
  const groups: Record<string, any[]> = {};
  for (const g of d.gaps) (groups[g.gap_type] ??= []).push(g);
  return (
    <div className="space-y-4">
      <Card title="Existing capabilities">
        <div className="flex flex-wrap gap-2">
          {d.existing.map((f: any) => <span key={f.feature_id} className="text-sm"><Badge value={f.status} /> {f.name}</span>)}
        </div>
      </Card>
      {Object.entries(groups).map(([type, gaps]) => (
        <Card key={type} title={`${type === "ai" ? "AI" : type === "ux" ? "UX" : type[0].toUpperCase() + type.slice(1)} gaps (${gaps.length})`}>
          <ul className="space-y-2 text-sm">
            {gaps.map((g) => (
              <li key={g.id}>
                <span className="font-medium">{g.name}</span><BasisTag basis={g.basis} /> <Confidence value={g.confidence} /> <EvidenceRefs ids={g.evidence_ids} />
                <div className="text-slate-600">{g.description}</div>
              </li>
            ))}
          </ul>
        </Card>
      ))}
    </div>
  );
}

function OpportunitiesTab({ runId, enabled }: { runId: string; enabled: boolean }) {
  const res = useAgent(runId, "opportunity_prioritization", enabled);
  if (!enabled) return <Pending />;
  const d = res.data?.data;
  if (!d) return null;
  const opps: any[] = d.opportunities;
  // Opportunity matrix: business impact (y) vs technical complexity (x)
  const impact = (o: any) => (o.factors.business_value + o.factors.user_impact + o.factors.revenue_potential) / 3;
  return (
    <div className="space-y-4">
      <Card title="Opportunity matrix — business impact vs. technical complexity">
        <div className="relative h-80 border-l border-b border-slate-300 ml-8 mb-6">
          <div className="absolute -left-8 top-1/2 -rotate-90 text-xs text-slate-500 origin-center">Impact →</div>
          <div className="absolute left-1/2 -bottom-6 text-xs text-slate-500">Complexity →</div>
          <div className="absolute left-0 top-0 w-1/2 h-1/2 bg-emerald-50/60" title="High impact, low complexity" />
          {opps.map((o) => (
            <div key={o.gap_id} title={`${o.name}\nscore ${o.score.total}\nAI opportunity ${o.factors.ai_opportunity}`}
              className="absolute -translate-x-1/2 translate-y-1/2 rounded-full bg-indigo-500/70 text-[10px] text-white px-1.5 py-0.5 whitespace-nowrap"
              style={{ left: `${(o.factors.complexity / 5) * 100}%`, bottom: `${(impact(o) / 5) * 100}%` }}>
              {o.name.slice(0, 22)}
            </div>
          ))}
        </div>
      </Card>
      <Card title="Scored opportunities">
        <table className="w-full text-sm">
          <thead className="text-left text-slate-500">
            <tr><th className="py-1 pr-2">#</th><th className="pr-2">Opportunity</th><th className="pr-2">Business opportunity</th><th className="px-2">AI</th><th className="px-2">Complexity</th><th className="px-2">Score</th></tr>
          </thead>
          <tbody>
            {opps.map((o, i) => (
              <tr key={o.gap_id} className="border-t align-top">
                <td className="py-1.5">{i + 1}</td>
                <td className="font-medium">{o.name}</td>
                <td>{o.business_opportunity}<BasisTag basis={o.basis} /><div className="text-xs text-slate-500">{o.revenue_opportunity}</div></td>
                <td className="px-2 text-center">{o.factors.ai_opportunity}</td>
                <td className="px-2 text-center">{o.factors.complexity}</td>
                <td className="px-2" title={Object.entries(o.score.contributions).map(([k, v]) => `${k}: ${v}`).join("\n")}>
                  <span className="font-mono">{o.score.total}</span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="text-xs text-slate-500 mt-2">Hover a score for its factor breakdown. Weights: {Object.entries(d.scoring_weights).map(([k, v]) => `${k} ${v}`).join(", ")}</p>
      </Card>
    </div>
  );
}

const PHASES: [string, string][] = [
  ["phase_1_quick_wins", "Phase 1 — Quick wins (0-4 weeks)"],
  ["phase_2_growth", "Phase 2 — Growth features (1-3 months)"],
  ["phase_3_major", "Phase 3 — Major improvements (3-6 months)"],
  ["phase_4_strategic", "Phase 4 — Strategic / AI (6-12 months)"],
];

function ArchitectureCard({ arch }: { arch: any }) {
  const [view, setView] = useState<"current" | "target">("target");
  return (
    <Card
      title="Architecture"
      actions={
        <div className="flex rounded-lg border overflow-hidden text-sm">
          {(["current", "target"] as const).map((v) => (
            <button key={v} onClick={() => setView(v)}
              className={`px-3 py-1 ${view === v ? "bg-indigo-600 text-white" : "bg-white text-slate-700"}`}>
              {v === "current" ? "Current" : "Target (after roadmap)"}
            </button>
          ))}
        </div>
      }
    >
      {/* SVG is rendered server-side with every label escaped (cip/core/architecture.py). */}
      <div className="w-full overflow-x-auto" dangerouslySetInnerHTML={{ __html: arch[`svg_${view}`] }} />
      <p className="text-xs text-slate-500 mt-2">
        Derived from {arch.based_on}.{view === "target" && arch.new_components?.length
          ? ` ${arch.new_components.length} new component(s) from the roadmap are highlighted in green.` : ""}
      </p>
    </Card>
  );
}

function RoadmapTab({ runId, enabled }: { runId: string; enabled: boolean }) {
  const prio = useAgent(runId, "opportunity_prioritization", enabled);
  const plans = useAgent(runId, "enhancement_planning", enabled);
  const [open, setOpen] = useState<string | null>(null);
  if (!enabled) return <Pending />;
  const recs: any[] = prio.data?.data.recommendations ?? [];
  const planBy = new Map((plans.data?.data.plans ?? []).map((p: any) => [p.recommendation_id, p]));
  const arch = plans.data?.data.architecture;
  return (
    <div className="space-y-4">
    {arch && <ArchitectureCard arch={arch} />}
    <div className="grid lg:grid-cols-2 gap-4">
      {PHASES.map(([key, label]) => (
        <Card key={key} title={label}>
          {recs.filter((r) => r.phase === key).map((r) => {
            const plan: any = planBy.get(r.id);
            return (
              <div key={r.id} className="border rounded-lg p-3 mb-2 text-sm">
                <div className="flex justify-between">
                  <span className="font-medium">{r.feature}</span>
                  <span className="text-xs text-slate-500">{r.complexity} · {plan?.estimated_effort}</span>
                </div>
                <div className="text-slate-600">{r.opportunity}<BasisTag basis={r.basis} /></div>
                <div className="text-xs mt-1"><EvidenceRefs ids={r.evidence_ids} /></div>
                {plan && (
                  <button className="text-xs text-indigo-600 underline mt-1" onClick={() => setOpen(open === r.id ? null : r.id)}>
                    {open === r.id ? "Hide" : "Show"} patch plan
                  </button>
                )}
                {open === r.id && plan && (
                  <div className="mt-2 space-y-1 text-xs">
                    <div><b>Objective:</b> {plan.objective}</div>
                    <div><b>Architecture impact:</b> {plan.architecture_impact}</div>
                    {[["frontend_changes", "Frontend"], ["backend_changes", "Backend"], ["database_changes", "Database"],
                      ["api_changes", "API"], ["ai_changes", "AI"], ["infrastructure_changes", "Infrastructure"],
                      ["security_changes", "Security"], ["testing_requirements", "Testing"],
                      ["migration_requirements", "Migration"], ["acceptance_criteria", "Acceptance criteria"]]
                      .filter(([k]) => plan[k]?.length)
                      .map(([k, l]) => (
                        <div key={k}><b>{l}:</b><ul className="list-disc pl-5">{plan[k].map((x: string) => <li key={x}>{x}</li>)}</ul></div>
                      ))}
                    <div><b>Team:</b> {plan.recommended_team.join(", ")}</div>
                  </div>
                )}
              </div>
            );
          })}
          {!recs.some((r) => r.phase === key) && <Empty>No items.</Empty>}
        </Card>
      ))}
    </div>
    </div>
  );
}

function EvidenceTab({ evidence }: { evidence: Evidence[] }) {
  const [filter, setFilter] = useState("");
  const [type, setType] = useState("");
  const types = [...new Set(evidence.map((e) => e.source_type))];
  const rows = evidence.filter((e) => (!type || e.source_type === type) && e.claim.toLowerCase().includes(filter.toLowerCase()));
  return (
    <Card title={`Evidence (${rows.length})`} actions={
      <>
        <input placeholder="Filter claims…" value={filter} onChange={(e) => setFilter(e.target.value)} className="border rounded px-2 py-1 text-sm" />
        <select value={type} onChange={(e) => setType(e.target.value)} className="border rounded px-2 py-1 text-sm">
          <option value="">All sources</option>
          {types.map((t) => <option key={t}>{t}</option>)}
        </select>
      </>
    }>
      <table className="w-full text-sm">
        <thead className="text-left text-slate-500"><tr><th className="py-1">Claim</th><th>Source</th><th>Type</th><th>Confidence</th></tr></thead>
        <tbody>
          {rows.map((e) => (
            <tr key={e.id} className="border-t align-top">
              <td className="py-1.5">{e.claim}{e.extracted_text && <div className="text-xs text-slate-500 line-clamp-2">“{e.extracted_text}”</div>}</td>
              <td className="text-xs break-all max-w-xs">
                {e.source_url.startsWith("http") ? <a className="text-indigo-600 underline" href={e.source_url} target="_blank" rel="noreferrer">{e.source_url}</a> : e.source_url}
                {e.repository_path && <div><code>{e.repository_path}{e.line_range ? `:${e.line_range}` : ""}</code></div>}
              </td>
              <td><Badge value={e.source_type} /></td>
              <td><Confidence value={e.confidence} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
}

function ReportTab({ runId, enabled }: { runId: string; enabled: boolean }) {
  const report = useQuery({
    queryKey: ["report", runId],
    queryFn: () => api.get<{ markdown: string }>(`/api/runs/${runId}/report`),
    enabled,
  });
  if (!enabled) return <Empty>The report is generated after the "Generate client-facing report" approval.</Empty>;
  return (
    <Card>
      <article className="prose-report text-sm text-slate-800">
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{report.data?.markdown ?? ""}</ReactMarkdown>
      </article>
    </Card>
  );
}
