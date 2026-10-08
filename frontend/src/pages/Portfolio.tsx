import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../lib/api";
import type { Portfolio } from "../lib/types";
import { Badge, Card, Empty } from "../components/ui";

const pct = (v: number | null | undefined) => (v == null ? "—" : `${Math.round(v * 100)}%`);

function Ranked({ title, items, empty }: { title: string; items: { name: string; projects: number }[]; empty: string }) {
  return (
    <Card title={title}>
      {items.length === 0 ? <Empty>{empty}</Empty> : (
        <ul className="text-sm space-y-1">
          {items.map((i) => (
            <li key={i.name} className="flex justify-between gap-2">
              <span>{i.name}</span><span className="text-slate-500 whitespace-nowrap">{i.projects} project{i.projects === 1 ? "" : "s"}</span>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

/** Portfolio & cross-client intelligence from each project's latest completed analysis. */
export default function PortfolioPage() {
  const [q, setQ] = useState("");
  const [industry, setIndustry] = useState("");
  const [status, setStatus] = useState("");
  const [priority, setPriority] = useState("");
  const [sort, setSort] = useState("score");
  const params = new URLSearchParams({ sort });
  if (q.trim()) params.set("q", q.trim());
  if (industry) params.set("industry", industry);
  if (status) params.set("status", status);
  if (priority) params.set("priority", priority);
  const pf = useQuery({ queryKey: ["portfolio", params.toString()], queryFn: () => api.get<Portfolio>(`/api/portfolio?${params}`) });
  const d = pf.data;
  const sel = "border rounded px-2 py-1 text-sm";

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-2xl font-bold">Portfolio</h1>
        <p className="text-sm text-slate-500">Every client's latest completed analysis, with patterns across the whole portfolio.</p>
      </div>
      {d && (
        <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-7 gap-3">
          {([["Clients", d.summary.clients], ["Projects", d.summary.projects], ["Analysed", d.summary.analysed],
            ["Running", d.summary.running], ["Awaiting approval", d.summary.awaiting_approval],
            ["Needs review", d.summary.needs_review], ["Failed", d.summary.failed]] as [string, number][]).map(([l, v]) => (
            <div key={l} className="bg-white border rounded-xl p-3">
              <div className="text-xs text-slate-500">{l}</div><div className="text-2xl font-semibold">{v}</div>
            </div>
          ))}
        </div>
      )}

      <Card title="Clients & projects">
        <div className="flex flex-wrap gap-2 mb-3">
          <input className={`${sel} w-56`} placeholder="Search client, project, priority…" value={q} onChange={(e) => setQ(e.target.value)} />
          <select className={sel} value={industry} onChange={(e) => setIndustry(e.target.value)}>
            <option value="">All industries</option>
            {d?.industries.map((i) => <option key={i.name} value={i.name}>{i.name}</option>)}
          </select>
          <select className={sel} value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">Any status</option>
            {["completed", "completed_with_errors", "running", "awaiting_approval", "failed", "cancelled", "needs_review", "partial"].map((s) =>
              <option key={s} value={s}>{s.replaceAll("_", " ")}</option>)}
          </select>
          <select className={sel} value={priority} onChange={(e) => setPriority(e.target.value)}>
            <option value="">Any priority</option><option value="high">Has high-priority items</option>
            <option value="medium">Top item medium</option><option value="low">Top item low</option>
          </select>
          <select className={sel} value={sort} onChange={(e) => setSort(e.target.value)}>
            <option value="score">Sort: opportunity score</option><option value="date">Sort: analysis date</option>
            <option value="name">Sort: name</option><option value="confidence">Sort: evidence coverage</option>
          </select>
        </div>
        {d && d.projects.length === 0 && <Empty>No matching projects.</Empty>}
        {d && d.projects.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-left text-slate-500">
                <tr><th className="py-1">Client / project</th><th>Industry</th><th>Status</th><th>Quality</th><th>Top priority</th>
                  <th className="text-right px-2">Opportunity</th><th className="text-right px-2">Evidence</th><th className="pl-3">Analysed</th></tr>
              </thead>
              <tbody>
                {d.projects.map((p) => (
                  <tr key={p.project_id} className="border-t align-top">
                    <td className="py-1.5"><Link className="text-indigo-700 underline" to={`/clients/${p.client_id}`}>{p.client}</Link>
                      <div className="text-xs text-slate-500">{p.project}</div></td>
                    <td>{p.industry ?? "—"}</td>
                    <td>{p.status ? (p.run_id ? <Link to={`/runs/${p.run_id}`}><Badge value={p.status} /></Link> : <Badge value={p.status} />) : <span className="text-slate-400">not analysed</span>}</td>
                    <td className="text-xs">{p.quality ?? "—"}</td>
                    <td>{p.top_priority ? <>{p.top_priority} {p.top_priority_level && <Badge value={p.top_priority_level} />}</> : "—"}
                      {p.high_priority_count > 1 && <div className="text-xs text-slate-500">{p.high_priority_count} high-priority items</div>}</td>
                    <td className="text-right font-mono px-2">{pct(p.opportunity_score)}</td>
                    <td className="text-right px-2">{pct(p.evidence_coverage)}</td>
                    <td className="text-xs pl-3">{p.completed ? <Link className="underline" to={`/runs/${p.analysed_run_id}`}>{new Date(p.completed).toLocaleDateString()}</Link> : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {d && (
        <>
          <Card title="Top opportunities across clients">
            {d.top_opportunities.length === 0 ? <Empty>Analyse projects to see opportunities.</Empty> : (
              <ul className="text-sm divide-y">
                {d.top_opportunities.map((o, i) => (
                  <li key={`${o.run_id}-${i}`} className="py-1 flex gap-2">
                    <span className="flex-1"><b>{o.feature}</b> <span className="text-slate-500">— {o.project}</span>
                      {o.category && <span className="text-xs text-slate-500"> · {o.category}</span>}</span>
                    {o.priority && <Badge value={o.priority} />}
                    <Link className="text-xs text-indigo-700 underline" to={`/runs/${o.run_id}`}>open</Link>
                  </li>
                ))}
              </ul>
            )}
          </Card>
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
            <Ranked title="Recurring capability gaps" items={d.recurring_gaps} empty="No gaps yet." />
            <Ranked title="Recurring AI opportunities" items={d.recurring_ai} empty="None yet." />
            <Ranked title="Recurring automation opportunities" items={d.recurring_automation} empty="None yet." />
            <Ranked title="Capabilities customers ask for (app reviews)" items={d.requested_capabilities} empty="No app-review requests." />
            <Ranked title="Industries" items={d.industries} empty="—" />
            <Card title="Our capabilities in demand">
              {d.capability_demand.length === 0 ? <Empty>No knowledge-base matches yet.</Empty> : (
                <ul className="text-sm space-y-1">
                  {d.capability_demand.map((c) => (
                    <li key={c.record_id}>
                      <div className="flex justify-between gap-2"><span>{c.title} <span className="text-xs text-slate-500">{c.kind.replace("_", " ")}</span></span>
                        <span className="text-slate-500 whitespace-nowrap">{c.projects} project{c.projects === 1 ? "" : "s"}</span></div>
                      <div className="text-xs text-slate-500">for {c.top_needs.join(", ")}</div>
                    </li>
                  ))}
                </ul>
              )}
              {d.shared_case_studies.length > 0 && (
                <p className="text-xs text-slate-600 mt-2">Case studies relevant to several clients: {d.shared_case_studies.map((c) => c.title).join(", ")}</p>
              )}
            </Card>
          </div>
        </>
      )}
    </div>
  );
}
