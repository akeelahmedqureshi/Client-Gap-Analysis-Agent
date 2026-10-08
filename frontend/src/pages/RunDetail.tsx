/* eslint-disable @typescript-eslint/no-explicit-any */
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Fragment, useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api } from "../lib/api";
import type { Evidence, KnowledgeMatch, Run, RunChanges, User } from "../lib/types";
import { Badge, BasisTag, Button, Card, Confidence, Empty, ErrorText } from "../components/ui";
import EvidenceRefs, { EvidenceContext } from "../components/EvidenceRefs";
import { AnnouncementsCard, CompanyFacts, HiringCard } from "../components/CompanyExtras";
import { ChangeList, SeverityBadge } from "../components/Changes";
import SalesTab from "../components/SalesTab";
import { useAgent } from "../lib/useAgent";
import { ComparisonTab, LandscapeCard, MarketTab } from "../components/MarketTabs";

const TABS = ["Pipeline", "Changes", "Client", "Project", "Security", "UX", "Competitors", "Pricing", "Apps", "Market", "Comparison", "Gaps", "Opportunities",
  "Cost & AI", "Roadmap", "Our Fit", "Sales", "Evidence", "Report"] as const;
type Tab = (typeof TABS)[number];
const ACTIVE = new Set(["queued", "running"]);


export default function RunDetailPage() {
  const { runId = "" } = useParams();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [tab, setTab] = useState<Tab>("Pipeline");
  const me = useQuery({ queryKey: ["me"], queryFn: () => api.get<User>("/api/auth/me") });
  const canAct = !!me.data && me.data.role !== "viewer";
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

  async function control(action: "cancel" | "pause") {
    if (action === "cancel" && !confirm("Cancel this analysis? Completed stages are kept; the rest is skipped.")) return;
    try {
      await api.post(`/api/runs/${runId}/${action}`);
      qc.invalidateQueries({ queryKey: ["run", runId] });
    } catch (e) {
      setError(e);
    }
  }

  async function refresh(stage: string) {
    try {
      const next = await api.post<Run>(`/api/runs/${runId}/rerun`, { stages: [stage] });
      navigate(`/runs/${next.run_id}`);
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
            <div className="text-sm text-slate-500 font-mono">
              {r.run_id}
              {r.monitor_id && <span className="ml-2 font-sans rounded bg-indigo-50 text-indigo-700 px-1.5 py-0.5 text-xs">⟳ scheduled</span>}
              {r.parent_run_id && (
                <span className="ml-2 font-sans text-xs">
                  version of <Link className="underline" to={`/runs/${r.parent_run_id}`}>{r.parent_run_id}</Link>
                  {r.rerun_stages?.length ? ` · refreshed ${r.rerun_stages.join(", ").replaceAll("_", " ")}` : ""}
                </span>
              )}
            </div>
          </div>
          <div className="flex items-center gap-2">
            <Badge value={r.status} />
            {done("quality_assurance") && <QualityBadge runId={runId} />}
            {canAct && ["failed", "completed_with_errors"].includes(r.status) && <Button variant="secondary" onClick={resume}>Retry failed agents</Button>}
            {canAct && ["paused", "cancelled"].includes(r.status) && <Button variant="secondary" onClick={resume}>Resume</Button>}
            {canAct && ["queued", "running"].includes(r.status) && <Button variant="secondary" onClick={() => control("pause")}>Pause</Button>}
            {canAct && ["queued", "running", "awaiting_approval", "paused"].includes(r.status) && <Button variant="danger" onClick={() => control("cancel")}>Cancel</Button>}
            {canAct && ["completed", "completed_with_errors", "failed", "cancelled"].includes(r.status) && (
              <select className="border rounded-lg px-2 py-1.5 text-sm" value="" onChange={(e) => e.target.value && refresh(e.target.value)}
                title="Re-run selected stages as a new version; everything else is reused">
                <option value="">Refresh…</option>
                <option value="industry">Industry research</option>
                <option value="competitors">Competitor research</option>
                <option value="opportunities">AI, automation & cost opportunities</option>
                <option value="sales">Sales summary & outreach</option>
                <option value="outreach">Outreach email</option>
                <option value="report">Report</option>
              </select>
            )}
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
              {canAct ? (
                <>
                  <Button onClick={() => decide(a.id, true)}>Approve</Button>
                  <Button variant="danger" onClick={() => decide(a.id, false)}>Reject (skip step)</Button>
                </>
              ) : <span className="text-xs text-slate-500">Waiting for an analyst or admin to decide</span>}
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

        {tab === "Pipeline" && <>{done("quality_assurance") && <QualityCard runId={runId} />}<Pipeline run={r} /></>}
        {tab === "Changes" && <ChangesTab runId={runId} status={r.status} />}
        {tab === "Client" && <ClientTab runId={runId} enabled={done("client_research")} />}
        {tab === "Project" && <ProjectTab runId={runId} enabled={done("product_features")} codeDone={done("code_analysis")} />}
        {tab === "Competitors" && <CompetitorsTab runId={runId} enabled={done("competitor_research")} />}
        {tab === "Security" && <SecurityTab runId={runId} enabled={done("security_review")} />}
        {tab === "Pricing" && <PricingTab runId={runId} enabled={done("pricing_analysis")} />}
        {tab === "UX" && <UxTab runId={runId} enabled={["completed", "skipped"].includes(r.agents["ux_review"])} />}
        {tab === "Apps" && <AppsTab runId={runId} enabled={["completed", "skipped"].includes(r.agents["app_store"])} />}
        {tab === "Market" && <MarketTab runId={runId} enabled={done("industry_market")} comparisonDone={done("feature_comparison")} />}
        {tab === "Comparison" && <ComparisonTab runId={runId} enabled={done("feature_comparison")} />}
        {tab === "Gaps" && <GapsTab runId={runId} enabled={done("gap_analysis")} />}
        {tab === "Opportunities" && <OpportunitiesTab runId={runId} enabled={done("opportunity_prioritization")} />}
        {tab === "Roadmap" && <RoadmapTab runId={runId} enabled={done("enhancement_planning")} />}
        {tab === "Cost & AI" && <CostAiTab runId={runId} enabled={done("business_process")} prioDone={done("opportunity_prioritization")} />}
        {tab === "Our Fit" && <FitTab runId={runId} enabled={done("capability_matching")} />}
        {tab === "Sales" && (done("sales_intelligence") ? <SalesTab runId={runId} canAct={canAct} /> : <Pending />)}
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

const QA_STYLE: Record<string, string> = {
  complete: "bg-emerald-100 text-emerald-800", complete_with_warnings: "bg-amber-100 text-amber-800",
  partial: "bg-amber-100 text-amber-800", needs_review: "bg-rose-100 text-rose-800",
};

function QualityBadge({ runId }: { runId: string }) {
  const q = useAgent(runId, "quality_assurance", true).data?.data;
  if (!q) return null;
  return <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${QA_STYLE[q.state]}`} title="Analysis completeness and quality">{q.label}</span>;
}

function QualityCard({ runId }: { runId: string }) {
  const q = useAgent(runId, "quality_assurance", true).data?.data;
  const [open, setOpen] = useState(false);
  if (!q) return null;
  const m = q.metrics;
  const pct = (v: number | null) => (v == null ? "—" : `${Math.round(v * 100)}%`);
  const shown = q.issues.filter((i: any) => i.severity !== "info");
  return (
    <Card title={<span>Analysis quality <span className={`ml-2 rounded-full px-2 py-0.5 text-xs ${QA_STYLE[q.state]}`}>{q.label}</span></span>}>
      <div className="grid grid-cols-2 md:grid-cols-5 gap-3 text-sm">
        <div><div className="text-xs text-slate-500">Evidence coverage</div><div className="font-semibold">{pct(m.evidence_coverage)}</div></div>
        <div><div className="text-xs text-slate-500">Low-confidence findings</div><div className="font-semibold">{m.low_confidence_findings}</div></div>
        <div><div className="text-xs text-slate-500">Inferred / assumptions</div><div className="font-semibold">{m.inferred_findings} / {m.assumption_findings}</div></div>
        <div><div className="text-xs text-slate-500">Sources (fresh / aging / stale)</div><div className="font-semibold">{m.source_freshness.fresh} / {m.source_freshness.aging} / {m.source_freshness.stale}</div></div>
        <div><div className="text-xs text-slate-500">Conflicts</div><div className="font-semibold">{m.conflicts}</div></div>
      </div>
      {shown.length > 0 && (
        <ul className="mt-3 space-y-1 text-sm">
          {shown.slice(0, open ? undefined : 5).map((i: any, n: number) => (
            <li key={n}><span className={`text-xs rounded px-1 mr-1 ${i.severity === "blocking" ? "bg-rose-100 text-rose-800" : "bg-amber-100 text-amber-800"}`}>{i.severity}</span>{i.message}</li>
          ))}
        </ul>
      )}
      {shown.length > 5 && <button className="text-xs text-indigo-700 underline mt-1" onClick={() => setOpen(!open)}>{open ? "Show less" : `Show all ${shown.length}`}</button>}
      <p className="text-xs text-slate-500 mt-2">Model: {q.reproducibility.model} · prompts {q.reproducibility.prompt_version} · taxonomy {q.reproducibility.taxonomy_version}</p>
    </Card>
  );
}

function Pending() {
  return <Empty>Available once the corresponding agent has completed.</Empty>;
}

function ChangesTab({ runId, status }: { runId: string; status: string }) {
  const res = useQuery({
    queryKey: ["changes", runId, status],
    queryFn: () => api.get<RunChanges>(`/api/runs/${runId}/changes`),
  });
  const d = res.data;
  if (!d) return <ErrorText error={res.error} />;
  if (!["completed", "completed_with_errors"].includes(d.status))
    return <Empty>Changes are computed when the run completes.</Empty>;
  if (!d.baseline_run_id) return <Empty>This is the first completed analysis of the project — nothing to compare with yet.</Empty>;
  return (
    <Card
      title={
        <span>
          Changes since <Link className="text-indigo-600 underline" to={`/runs/${d.baseline_run_id}`}>the previous analysis</Link>
          {d.baseline_created_at && <span className="text-sm font-normal text-slate-500"> ({new Date(d.baseline_created_at).toLocaleDateString()})</span>}
        </span>
      }
      actions={(["critical", "warning", "info"] as const).filter((s) => d.summary.counts[s]).map((s) => (
        <span key={s} className="text-sm"><SeverityBadge value={s} /> {d.summary.counts[s]}</span>
      ))}
    >
      {d.changes.length ? <ChangeList changes={d.changes} /> : <Empty>No material changes detected.</Empty>}
      <p className="mt-3 text-xs text-slate-500">
        Categories are compared only when the same research step completed in both runs (a skipped step is never reported as a removal).
        Competitors are matched by domain.
      </p>
    </Card>
  );
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
      {d.landscape?.length > 0 && <LandscapeCard landscape={d.landscape} ranking={d.ranking} />}
      <h2 className="font-semibold">Top {d.competitors.length} — deep analysis</h2>
      <div className="grid lg:grid-cols-2 gap-4">
        {d.competitors.map((c: any) => (
          <Card key={c.id} title={<>{c.rank && <span className="text-slate-400">#{c.rank} </span>}<a className="underline" href={c.url} target="_blank" rel="noreferrer">{c.name}</a> <Badge value={c.classification} /></>}>
            <p className="text-sm">{c.description} <EvidenceRefs ids={c.evidence_ids} /></p>
            {c.pricing && <p className="text-sm mt-1"><span className="text-slate-500">Pricing:</span> {c.pricing}</p>}
            {c.target_market && <p className="text-sm"><span className="text-slate-500">Market:</span> {c.target_market}</p>}
            <p className="text-xs text-slate-500 mt-1">{c.rationale}</p>
            <div className="mt-2 flex flex-wrap gap-1">
              {c.features.map((f: any) => <span key={f.feature_id} className="text-xs bg-slate-100 rounded px-1.5 py-0.5">{f.feature_id}</span>)}
            </div>
            <div className="mt-2 flex items-center gap-3"><Confidence value={c.confidence} />
              <span className="text-xs text-slate-500">{c.pages_analysed?.length ?? 0} page(s) analysed{c.deep_error && " · deep analysis failed, light profile shown"}</span></div>
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

const SEV_STYLE: Record<string, string> = {
  critical: "bg-rose-600 text-white", high: "bg-rose-100 text-rose-800", medium: "bg-amber-100 text-amber-800",
  low: "bg-slate-100 text-slate-700", info: "bg-sky-50 text-sky-700",
};
const GRADE_STYLE: Record<string, string> = {
  A: "text-emerald-600", B: "text-emerald-600", C: "text-amber-600", D: "text-rose-600", F: "text-rose-700",
};

function SecurityTab({ runId, enabled }: { runId: string; enabled: boolean }) {
  const res = useAgent(runId, "security_review", enabled);
  if (!enabled) return <Empty>Available once the security review has completed (it is skipped when disabled).</Empty>;
  const d = res.data?.data;
  if (!d) return null;
  const order = ["critical", "high", "medium", "low", "info"];
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 lg:grid-cols-6 gap-3">
        <div className="bg-white border rounded-xl p-3 col-span-2 flex items-center gap-4">
          <div className={`text-5xl font-bold ${GRADE_STYLE[d.grade] ?? ""}`}>{d.grade}</div>
          <div><div className="text-xs text-slate-500">Security score</div><div className="text-2xl font-semibold">{d.score}/100</div></div>
        </div>
        {order.map((s) => (
          <div key={s} className="bg-white border rounded-xl p-3">
            <div className="text-xs text-slate-500 capitalize">{s}</div>
            <div className="text-2xl font-semibold">{d.counts?.[s] ?? 0}</div>
          </div>
        ))}
      </div>
      <Card title="Findings">
        {d.issues.length ? (
          <table className="w-full text-sm">
            <thead className="text-left text-slate-500"><tr><th className="py-1">Severity</th><th>Issue</th><th>Recommendation</th><th /></tr></thead>
            <tbody>
              {d.issues.map((i: any) => (
                <tr key={i.key} className="border-t align-top">
                  <td className="py-1.5 pr-2"><span className={`rounded px-1.5 py-0.5 text-xs font-medium ${SEV_STYLE[i.severity]}`}>{i.severity}</span></td>
                  <td className="pr-2"><div className="font-medium">{i.title}</div><div className="text-xs text-slate-500">{i.detail}</div>
                    {i.references?.filter((r: string) => r.startsWith("http")).map((r: string) => (
                      <a key={r} href={r} target="_blank" rel="noreferrer" className="text-xs text-indigo-600 underline mr-2">advisory</a>))}
                  </td>
                  <td className="text-xs pr-2">{i.recommendation}</td>
                  <td><EvidenceRefs ids={[i.evidence_id]} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : <Empty>No issues found in the reviewed scope.</Empty>}
      </Card>
      <Card title="Scope & method">
        <ul className="list-disc pl-5 text-sm space-y-1">{d.scope.map((s: string) => <li key={s}>{s}</li>)}</ul>
        <p className="text-xs text-slate-500 mt-2">{d.note} Scoring: {d.method}.</p>
      </Card>
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

const UX_PRACTICES: Record<string, string> = {
  self_serve_cta: "Self-serve sign-up CTA", sales_cta: "Book-a-demo CTA", sign_in: "Sign-in link", help: "Help center / FAQ",
  live_chat: "Live chat", trust: "Trust signals", search: "Site search",
};

function UxTab({ runId, enabled }: { runId: string; enabled: boolean }) {
  const res = useAgent(runId, "ux_review", enabled);
  if (!enabled) return <Pending />;
  const d = res.data?.data;
  if (!d) return null;
  if (!d.client) return <Empty>UX review did not run ({d.reason ?? "disabled with CIP_UX_REVIEW_ENABLED"}).</Empty>;
  const companies: any[] = d.companies;
  const cats = ["accessibility", "mobile", "performance", "conversion"].filter((c) => companies.some((x) => x.score?.categories?.[c] != null));
  const scoreCell = (v: number | undefined) => (
    <td className={`text-center font-medium ${v == null ? "text-slate-400" : v >= 90 ? "text-emerald-700" : v >= 70 ? "text-amber-700" : "text-rose-700"}`}>{v ?? "—"}</td>
  );
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        {[["Client UX score", d.market.client_score != null ? `${d.market.client_score}/100` : "—"],
          ["Competitor median", d.market.competitor_median_score != null ? `${d.market.competitor_median_score}/100` : "—"],
          ["Pages audited", String(d.client.pages.length)],
          ["Browser checks", d.client.browser ? "contrast · phone · speed" : "static only"]].map(([k, v]) => (
          <div key={k} className="bg-white border rounded-xl p-3">
            <div className="text-xs text-slate-500">{k}</div>
            <div className="text-lg font-semibold">{v}</div>
          </div>
        ))}
      </div>
      <Card title="Scores (0–100)">
        <table className="w-full text-sm">
          <thead className="text-left text-slate-500">
            <tr><th className="py-1">Company</th><th className="text-center">Overall</th>{cats.map((c) => <th key={c} className="text-center capitalize">{c}</th>)}</tr>
          </thead>
          <tbody>
            {companies.map((x) => (
              <tr key={x.name} className="border-t">
                <td className="py-1.5 font-medium">{x.name}{x.is_client && <span className="ml-1 text-xs text-slate-500">(client)</span>}</td>
                {scoreCell(x.score?.overall)}
                {cats.map((c) => <Fragment key={c}>{scoreCell(x.score?.categories?.[c])}</Fragment>)}
              </tr>
            ))}
          </tbody>
        </table>
        <p className="text-xs text-slate-500 mt-2">{d.method} {d.disclaimer}</p>
        {d.notes.map((n: string) => <p key={n} className="text-xs text-amber-700 mt-1">{n}</p>)}
      </Card>
      <Card title="Website practices">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="text-left text-slate-500">
              <tr><th className="py-1">Practice</th>{companies.map((x) => <th key={x.name} className="text-center">{x.name}</th>)}</tr>
            </thead>
            <tbody>
              {Object.entries(UX_PRACTICES).map(([key, label]) => (
                <tr key={key} className="border-t">
                  <td className="py-1.5">{label}</td>
                  {companies.map((x) => (
                    <td key={x.name} className="text-center">
                      {x.practices?.[key] ? <span title="found"><EvidenceRefs ids={[x.practices[key]]} /></span> : <span className="text-slate-400">—</span>}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
      <Card title={`Client issues (${d.issues.length})`}>
        {d.issues.length ? (
          <ul className="divide-y">
            {d.issues.map((i: any) => (
              <li key={i.key} className="py-2 text-sm flex gap-3 items-start">
                <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${SEV_STYLE[i.severity]}`}>{i.severity}</span>
                <div className="flex-1 min-w-0">
                  <div className="font-medium">{i.title}
                    {i.wcag && <span className="ml-2 text-xs rounded bg-indigo-50 text-indigo-700 px-1.5 py-0.5">WCAG {i.wcag}</span>}
                    <span className="ml-2 text-xs text-slate-400 capitalize">{i.category}</span></div>
                  <div className="text-slate-600">{i.recommendation}</div>
                  {i.detail && <div className="text-xs text-slate-500">{i.detail}</div>}
                  <div className="text-xs text-slate-500">Pages: {i.pages.map((p: string) => new URL(p).pathname || "/").join(", ")}</div>
                  {i.examples.length > 0 && <code className="block mt-1 text-xs bg-slate-50 rounded p-1 break-all">{i.examples[0]}</code>}
                </div>
                <EvidenceRefs ids={[i.evidence_id]} />
              </li>
            ))}
          </ul>
        ) : <Empty>No issues found by the automated checks.</Empty>}
      </Card>
      <Card title="UX gaps">
        {d.gaps.length ? (
          <ul className="space-y-2 text-sm">
            {d.gaps.map((g: any) => (
              <li key={g.id}><span className="font-medium">{g.name}</span><BasisTag basis={g.basis} /> <Confidence value={g.confidence} /> <EvidenceRefs ids={g.evidence_ids} />
                <div className="text-slate-600">{g.description}</div></li>
            ))}
          </ul>
        ) : <Empty>No UX gaps.</Empty>}
      </Card>
    </div>
  );
}

function Stars({ value }: { value: number | null | undefined }) {
  if (value == null) return <span className="text-slate-400">—</span>;
  return <span className="font-medium" title={`${value} out of 5`}>{value.toFixed(1)}★</span>;
}

function AppsTab({ runId, enabled }: { runId: string; enabled: boolean }) {
  const res = useAgent(runId, "app_store", enabled);
  if (!enabled) return <Pending />;
  const d = res.data?.data;
  if (!d) return null;
  if (!d.enabled || d.reason) return <Empty>App-store analysis did not run ({d.reason ?? "disabled with CIP_APP_STORE_ENABLED"}).</Empty>;
  const m = d.market ?? {};
  const rv = d.client_reviews ?? {};
  const row = (owner: React.ReactNode, a: any) => (
    <tr key={`${a.platform}-${a.app_id}`} className="border-t align-top">
      <td className="py-1.5 font-medium">{owner}</td>
      <td><a className="text-indigo-600 underline" href={a.url} target="_blank" rel="noreferrer">{a.name}</a>
        <div className="text-xs text-slate-500">{a.developer} · {a.found_via}</div></td>
      <td>{a.platform === "ios" ? "iOS" : "Android"}</td>
      <td><Stars value={a.rating} /></td>
      <td>{a.rating_count?.toLocaleString() ?? "—"}</td>
      <td className="text-xs">{a.version ?? ""} {a.updated ? new Date(a.updated).toLocaleDateString() : "—"}
        {a.days_since_update > 180 && <span className="ml-1 rounded bg-amber-100 text-amber-800 px-1">stale</span>}</td>
      <td><EvidenceRefs ids={[a.evidence_id]} /></td>
    </tr>
  );
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        {[["Client rating", m.client_rating ? `${m.client_rating}★ (${m.client_rating_count.toLocaleString()})` : "no app found"],
          ["Competitor median", m.competitor_median_rating ? `${m.competitor_median_rating}★` : "—"],
          ["Competitors with apps", `${m.competitors_with_apps ?? 0} of ${m.competitors_checked ?? 0}`],
          ["Recent client reviews", rv.total ? `${rv.total} · avg ${rv.average}★` : "—"]].map(([k, v]) => (
          <div key={k} className="bg-white border rounded-xl p-3">
            <div className="text-xs text-slate-500">{k}</div>
            <div className="text-lg font-semibold">{v}</div>
          </div>
        ))}
      </div>
      <Card title="Apps in the stores">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="text-left text-slate-500">
              <tr><th className="py-1">Company</th><th>App</th><th>Platform</th><th>Rating</th><th>Ratings</th><th>Last release</th><th /></tr>
            </thead>
            <tbody>
              {d.client_apps.length ? d.client_apps.map((a: any) => row("Client", a)) :
                <tr className="border-t"><td className="py-1.5 font-medium">Client</td><td colSpan={6} className="text-slate-500 italic">No app found in the App Store or on Google Play</td></tr>}
              {d.competitor_apps.map((e: any) => e.apps.map((a: any) => row(e.name, a)))}
            </tbody>
          </table>
        </div>
        {d.notes.map((n: string) => <p key={n} className="text-xs text-slate-500 mt-2">{n}</p>)}
      </Card>
      {rv.total > 0 && (
        <Card title={`What the client's users say (${rv.sentiment.negative} negative · ${rv.sentiment.neutral} neutral · ${rv.sentiment.positive} positive)`}>
          <ul className="space-y-3 text-sm">
            {rv.themes.map((t: any) => (
              <li key={t.theme}>
                <div><span className="font-medium">{t.label}</span>
                  <span className="text-slate-500"> — {t.negative} negative / {t.count} mentions</span> <EvidenceRefs ids={t.evidence_ids} /></div>
                {t.examples.slice(0, 2).map((ex: any, i: number) => (
                  <blockquote key={i} className="mt-1 border-l-2 border-slate-200 pl-2 text-slate-600 italic">
                    {"★".repeat(ex.rating)} “{ex.quote}”
                  </blockquote>
                ))}
              </li>
            ))}
          </ul>
        </Card>
      )}
      {(d.requests.length > 0 || d.competitor_review_themes.length > 0) && (
        <div className="grid lg:grid-cols-2 gap-4">
          <Card title="Feature requests in reviews">
            {d.requests.length ? (
              <ul className="text-sm space-y-1">
                {d.requests.map((r: any) => <li key={r.feature_id}><span className="font-medium">{r.name}</span> — {r.count} review(s) <EvidenceRefs ids={r.evidence_ids} /></li>)}
              </ul>
            ) : <Empty>No feature requests detected.</Empty>}
            <p className="text-xs text-slate-500 mt-2">Requests raise the market-demand factor of the matching gap by +1.</p>
          </Card>
          <Card title="Competitor review complaints">
            {d.competitor_review_themes.length ? (
              <ul className="text-sm space-y-1">
                {d.competitor_review_themes.map((c: any) => (
                  <li key={c.competitor_id}><span className="font-medium">{c.name}</span> ({c.reviews} recent reviews, avg {c.average}★):{" "}
                    {c.themes.map((t: any) => `${t.label} (${t.negative})`).join(", ")}</li>
                ))}
              </ul>
            ) : <Empty>No recurring complaints found.</Empty>}
          </Card>
        </div>
      )}
      <Card title="App gaps">
        {d.gaps.length ? (
          <ul className="space-y-2 text-sm">
            {d.gaps.map((g: any) => (
              <li key={g.id}><span className="font-medium">{g.name}</span><BasisTag basis={g.basis} /> <Confidence value={g.confidence} /> <EvidenceRefs ids={g.evidence_ids} />
                <div className="text-slate-600">{g.description}</div></li>
            ))}
          </ul>
        ) : <Empty>No app-related gaps.</Empty>}
        <p className="text-xs text-slate-500 mt-2">{d.method}</p>
      </Card>
    </div>
  );
}

const GAP_TITLES: Record<string, string> = {
  ai: "AI", ux: "UX", process: "Process (cost & automation)", missing: "Missing", partial: "Partial",
  technology: "Technology", pricing: "Pricing", security: "Security",
};

function GapsTab({ runId, enabled }: { runId: string; enabled: boolean }) {
  const res = useAgent(runId, "gap_analysis", enabled);
  const prio = useAgent(runId, "opportunity_prioritization", enabled);
  const byGap = new Map<string, any>((prio.data?.data.opportunities ?? []).map((o: any) => [o.gap_id, o]));
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
        <Card key={type} title={`${GAP_TITLES[type] ?? type} gaps (${gaps.length})`}>
          <ul className="space-y-2 text-sm">
            {gaps.map((g) => {
              const o = byGap.get(g.id);
              return (
                <li key={g.id}>
                  <span className="font-medium">{g.name}</span><BasisTag basis={g.basis} /> <Confidence value={g.confidence} />{" "}
                  {o && <><Badge value={o.priority} /> <span className="text-xs text-slate-500">{o.business_category}</span></>}{" "}
                  <EvidenceRefs ids={g.evidence_ids} />
                  <div className="text-slate-600">{g.description}</div>
                  {o?.attributes && (
                    <div className="text-xs text-slate-500">
                      relevance {o.attributes.business_relevance} · customer value {o.attributes.customer_value} · revenue {o.attributes.revenue_impact} ·
                      efficiency {o.attributes.efficiency_impact} · complexity {o.attributes.complexity}
                    </div>
                  )}
                </li>
              );
            })}
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
            <tr><th className="py-1 pr-2">#</th><th className="pr-2">Opportunity</th><th className="pr-2">Priority</th><th className="pr-2">Business opportunity</th><th className="px-2">AI</th><th className="px-2">Complexity</th><th className="px-2">Score</th></tr>
          </thead>
          <tbody>
            {opps.map((o, i) => (
              <tr key={o.gap_id} className="border-t align-top">
                <td className="py-1.5">{i + 1}</td>
                <td className="font-medium">{o.name}<div className="text-xs font-normal text-slate-500">{o.business_category}</div></td>
                <td className="pr-2"><Badge value={o.priority} /></td>
                <td>{o.business_opportunity}<BasisTag basis={o.basis} /><div className="text-xs text-slate-500">{o.revenue_opportunity}</div></td>
                <td className="px-2 text-center">{o.factors.ai_opportunity}</td>
                <td className="px-2 text-center">{o.factors.complexity}</td>
                <td className="px-2" title={Object.entries(o.score.contributions).map(([k, v]) => `${k}: ${v}`).join("\n")
                  + (o.score.confidence_factor ? `\nevidence confidence ${o.score.confidence} → ×${o.score.confidence_factor} on benefits` : "")}>
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

const LEVEL: Record<string, string> = { high: "text-emerald-700", medium: "text-sky-700", low: "text-slate-500" };

function CostAiTab({ runId, enabled, prioDone }: { runId: string; enabled: boolean; prioDone: boolean }) {
  const res = useAgent(runId, "business_process", enabled);
  const prio = useAgent(runId, "opportunity_prioritization", prioDone);
  const gaps = useAgent(runId, "gap_analysis", prioDone);
  const [filter, setFilter] = useState<"all" | "ai" | "automation">("all");
  if (!enabled) return <Pending />;
  const d = res.data?.data;
  if (!d) return null;
  const gapByProcess = new Map<string, string>((gaps.data?.data.gaps ?? []).filter((g: any) => g.process_id).map((g: any) => [g.process_id, g.id]));
  const oppByGap = new Map<string, any>((prio.data?.data.opportunities ?? []).map((o: any) => [o.gap_id, o]));
  const items: any[] = d.opportunities.filter((o: any) => filter === "all" || o[filter]);
  return (
    <div className="space-y-4">
      <div className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">{d.disclaimer}</div>
      <div className="flex gap-2 text-sm">
        {(["all", "ai", "automation"] as const).map((f) => (
          <button key={f} onClick={() => setFilter(f)}
            className={`rounded-full px-3 py-1 border ${filter === f ? "bg-indigo-600 text-white border-indigo-600" : "bg-white"}`}>
            {f === "all" ? `All (${d.opportunities.length})` : f === "ai" ? `AI (${d.ai_opportunities.length})` : `Automation (${d.automation_opportunities.length})`}
          </button>
        ))}
      </div>
      {items.length === 0 && <Card title="No opportunities"><Empty>No business process with an open improvement was observed.</Empty></Card>}
      {items.map((o) => {
        const s = oppByGap.get(gapByProcess.get(o.process_id) ?? "");
        return (
          <Card key={o.id} title={`${o.area}: ${o.name}`}
            actions={<div className="flex items-center gap-2">
              {s && <><Badge value={s.priority} /><span className="text-xs text-slate-500">score {s.score.total}</span></>}
              {o.ai && <span className="text-xs rounded bg-violet-100 text-violet-800 px-1.5">AI</span>}
              {o.automation && <span className="text-xs rounded bg-cyan-100 text-cyan-800 px-1.5">Automation</span>}
              <Confidence value={o.confidence} />
            </div>}>
            <div className="grid gap-4 md:grid-cols-2 text-sm">
              <div className="space-y-2">
                <div><div className="text-xs font-semibold text-slate-500">Observed (public evidence)</div>
                  <ul className="list-disc ml-5">{o.observed.map((x: string) => <li key={x}>{x}</li>)}</ul>
                  <EvidenceRefs ids={o.evidence_ids} /></div>
                <div><div className="text-xs font-semibold text-slate-500">Likely current process <span className="text-amber-700">(assumption)</span></div>{o.current_process}</div>
                <div><div className="text-xs font-semibold text-slate-500">Business problem <span className="text-amber-700">(assumption)</span></div>{o.business_problem}</div>
              </div>
              <div className="space-y-2">
                <div><div className="text-xs font-semibold text-slate-500">Proposed solution</div>{o.proposed_solution}<BasisTag basis="estimate" /></div>
                <div><div className="text-xs font-semibold text-slate-500">How it works</div>{o.how_it_works}</div>
                <div className="grid grid-cols-3 gap-2 text-xs">
                  {[["Resource saving", o.cost_reduction], ["Processing time", o.time_reduction], ["Errors / rework", o.error_reduction]].map(([l, v]) => (
                    <div key={l} className="rounded border p-1.5"><div className="text-slate-500">{l}</div><div className={`font-medium capitalize ${LEVEL[v]}`}>{v}</div></div>
                  ))}
                </div>
                <div className="text-xs text-slate-600">Productivity: <b>{o.productivity}</b> · Complexity: <b>{o.complexity}/5</b>
                  {o.missing_capabilities.length > 0 && <> · Needs: {o.missing_capabilities.join(", ")}</>}</div>
                <div><div className="text-xs font-semibold text-slate-500">Customer impact</div>{o.customer_impact}</div>
                <div><div className="text-xs font-semibold text-slate-500">Revenue opportunity</div>{o.revenue_opportunity}</div>
              </div>
            </div>
          </Card>
        );
      })}
      {d.in_place.length > 0 && (
        <Card title="Already in place">
          <p className="text-sm text-slate-600">{d.in_place.map((p: any) => p.name).join(" · ")}</p>
        </Card>
      )}
      <p className="text-xs text-slate-500">{d.method}</p>
    </div>
  );
}

function FitTab({ runId, enabled }: { runId: string; enabled: boolean }) {
  const res = useAgent(runId, "capability_matching", enabled);
  if (!enabled) return <Pending />;
  const d = res.data?.data;
  if (!d) return null;
  const kb = d.knowledge_base ?? {};
  return (
    <div className="space-y-4">
      <p className="text-sm text-slate-600">
        Opportunities matched to your <Link className="text-indigo-700 underline" to="/knowledge">knowledge base</Link>{" "}
        ({kb.records_considered ?? 0} approved record(s), {kb.client_facing_records ?? 0} client-facing). Internal-only
        matches are for your team; only client-facing ones may be used with the client.
      </p>
      {d.matches.length === 0 && (
        <Card title="No matches">
          <Empty>
            {kb.records_considered ? "None of the approved records cover this run's opportunities." :
              "The knowledge base has no approved records yet."}
          </Empty>
        </Card>
      )}
      {d.matches.map((m: any) => (
        <Card key={m.recommendation_id} title={m.need}>
          <ul className="space-y-2">
            {m.matches.map((x: KnowledgeMatch) => (
              <li key={x.record_id} className="border rounded-lg p-3 text-sm">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-medium">{x.title}</span>
                  <span className="text-xs text-slate-500">{x.kind.replace("_", " ")} · v{x.version}</span>
                  <span className={`text-xs ${x.client_facing ? "text-emerald-700" : "text-amber-700"}`}>
                    {x.client_facing ? "client-facing" : "internal only"}
                  </span>
                  {x.reference_allowed && x.customer_name && (
                    <span className="text-xs text-slate-600">reference: {x.customer_name}</span>
                  )}
                  <span className="ml-auto"><Confidence value={x.confidence} /></span>
                </div>
                <ul className="list-disc ml-5 text-xs text-slate-600 mt-1">
                  {x.reasons.map((r) => <li key={r}>{r}</li>)}
                </ul>
              </li>
            ))}
          </ul>
        </Card>
      ))}
      {d.unmatched.length > 0 && (
        <Card title="No internal match">
          <p className="text-sm text-slate-600">{d.unmatched.join(" · ")}</p>
        </Card>
      )}
    </div>
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
