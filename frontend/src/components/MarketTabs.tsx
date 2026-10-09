/* eslint-disable @typescript-eslint/no-explicit-any */
import { Fragment, useState } from "react";
import { useAgent } from "../lib/useAgent";
import { EvidenceList } from "./Explain";
import { RunBenchmark } from "./Benchmarks";
import { Badge, Card, Empty } from "./ui";
import EvidenceRefs from "./EvidenceRefs";

const FACTOR_LABEL: Record<string, string> = {
  feature_overlap: "Feature overlap", product_similarity: "Product", industry_similarity: "Industry",
  customer_similarity: "Customers", geographic_relevance: "Geography", business_model_similarity: "Business model",
  market_presence: "Presence", product_maturity: "Maturity", evidence_confidence: "Evidence",
};
const CLASS_LABEL: Record<string, string> = {
  industry_standard: "Industry standard", emerging: "Emerging expectation", differentiator: "Differentiator",
  niche: "Niche", unique_to_client: "Unique to client",
};
const CLASS_STYLE: Record<string, string> = {
  industry_standard: "bg-indigo-100 text-indigo-800", emerging: "bg-violet-100 text-violet-800",
  differentiator: "bg-amber-100 text-amber-800", niche: "bg-slate-100 text-slate-600",
  unique_to_client: "bg-emerald-100 text-emerald-800",
};
const ICON: Record<string, string> = { available: "✅", partial: "🟡", missing: "❌", unknown: "❔" };
const STATUS_LABEL: Record<string, string> = {
  available: "available", partial: "partially available", unknown: "not publicly identified", missing: "confirmed missing",
};
const pct = (v: number | null | undefined) => (v == null ? "—" : `${Math.round(v * 100)}%`);

function Pending() {
  return <Empty>Available once this step has completed.</Empty>;
}

/** Top-10 competitive landscape, ranked by relevance with the factor breakdown. */
export function LandscapeCard({ landscape, ranking }: { landscape: any[]; ranking?: any }) {
  const [open, setOpen] = useState<string | null>(null);
  return (
    <Card title={`Competitive landscape — top ${landscape.length} by relevance`}>
      <p className="text-xs text-slate-500 mb-2">
        Ranked by relevance to this client (capability overlap, product, industry, customers, geography, business model),
        not company size. {ranking && `${ranking.candidates_considered} candidate(s) considered; the top ${ranking.deep_count} get a deep analysis.`}
      </p>
      <table className="w-full text-sm">
        <thead className="text-left text-slate-500">
          <tr><th className="py-1 pr-2">#</th><th className="pr-2">Competitor</th><th className="pr-2">Type</th><th className="pr-2">Why included</th><th className="pr-2">Relevance</th></tr>
        </thead>
        <tbody>
          {landscape.map((c) => (
            <Fragment key={c.id}>
              <tr className="border-t align-top cursor-pointer hover:bg-slate-50" onClick={() => setOpen(open === c.id ? null : c.id)}>
                <td className="py-1.5 pr-2">{c.rank}</td>
                <td className="pr-2">
                  <a className="underline" href={c.url} target="_blank" rel="noreferrer" onClick={(e) => e.stopPropagation()}>{c.name}</a>
                  {c.deep && <span className="ml-1 text-xs rounded bg-indigo-100 text-indigo-800 px-1">deep analysis</span>}
                  <div className="text-xs text-slate-500">{c.description}</div>
                </td>
                <td className="pr-2"><Badge value={c.classification} /></td>
                <td className="pr-2 text-slate-600">{c.reason} <EvidenceRefs ids={c.evidence_ids} /></td>
                <td className="pr-2 font-mono">{pct(c.relevance?.overall)}</td>
              </tr>
              {open === c.id && (
                <tr className="bg-slate-50">
                  <td />
                  <td colSpan={4} className="py-2">
                    <div className="grid grid-cols-3 md:grid-cols-5 gap-2 text-xs">
                      {Object.entries(c.relevance?.factors ?? {}).map(([k, v]) => (
                        <div key={k}>
                          <div className="text-slate-500">{FACTOR_LABEL[k] ?? k}</div>
                          <div className="h-1.5 rounded bg-slate-200 overflow-hidden"><div className="h-full bg-indigo-500" style={{ width: pct(v as number) }} /></div>
                          <div>{pct(v as number)}</div>
                        </div>
                      ))}
                    </div>
                  </td>
                </tr>
              )}
            </Fragment>
          ))}
        </tbody>
      </table>
      <p className="text-xs text-slate-500 mt-1">Click a row for its factor scores.</p>
    </Card>
  );
}

/** Industry & market profile, trends and adoption across the competitive landscape. */
export function MarketTab({ runId, enabled, comparisonDone }: { runId: string; enabled: boolean; comparisonDone: boolean }) {
  const res = useAgent(runId, "industry_market", enabled);
  const fc = useAgent(runId, "feature_comparison", comparisonDone);
  if (!enabled) return <Pending />;
  const d = res.data?.data;
  if (!d) return null;
  const m = fc.data?.data.market;
  const facts: [string, string, string | undefined][] = [
    ["Industry", d.industry, d.basis?.industry], ["Market segment", d.market_segment, d.basis?.market_segment],
    ["Product category", d.product_category, d.basis?.product_category],
    ["Customer segment", (d.customer_segment ?? []).join(", "), d.basis?.customer_segment],
    ["Business model", d.business_model, d.basis?.business_model], ["Geography", (d.geography ?? []).join(", "), undefined],
  ];
  const kinds: [string, string][] = [["trend", "Industry trends"], ["technology", "Emerging technology"], ["ai_adoption", "AI adoption"], ["automation", "Automation trends"]];
  return (
    <div className="space-y-4">
      {comparisonDone && <RunBenchmark runId={runId} />}
      <Card title="Industry & market profile">
        <dl className="grid sm:grid-cols-2 gap-x-6 gap-y-2 text-sm">
          {facts.map(([k, v, basis]) => (
            <div key={k}>
              <dt className="text-xs text-slate-500">{k}{basis === "inferred" && <span className="ml-1 text-amber-700">(inferred)</span>}</dt>
              <dd>{v || <span className="text-slate-400">not publicly identified</span>}</dd>
            </div>
          ))}
        </dl>
        <div className="mt-2"><EvidenceRefs ids={d.evidence_ids} /></div>
      </Card>
      {m && (
        <Card title="Across the competitive landscape">
          <div className="grid sm:grid-cols-3 gap-3 text-sm">
            <div className="rounded border p-2"><div className="text-xs text-slate-500">AI adoption</div><div className="text-xl font-semibold">{pct(m.ai_adoption)}</div><div className="text-xs text-slate-500">of {m.landscape_size} competitors offer an AI capability</div></div>
            <div className="rounded border p-2"><div className="text-xs text-slate-500">Automation adoption</div><div className="text-xl font-semibold">{pct(m.automation_adoption)}</div><div className="text-xs text-slate-500">offer automation capabilities</div></div>
            <div className="rounded border p-2"><div className="text-xs text-slate-500">Industry standards the client offers</div><div className="text-xl font-semibold">{pct(m.client_standard_coverage)}</div><div className="text-xs text-slate-500">of {m.industry_standards.length} must-have capabilities</div></div>
          </div>
          <div className="mt-3 text-sm space-y-1">
            <div><span className="text-slate-500">Industry standards (must-have):</span> {m.industry_standards.join(", ") || "—"}</div>
            <div><span className="text-slate-500">Emerging expectations:</span> {m.emerging.join(", ") || "—"}</div>
            <div><span className="text-slate-500">Differentiators / niche:</span> {m.differentiators.join(", ") || "—"}</div>
            {m.unique_to_client.length > 0 && <div><span className="text-slate-500">Unique to the client:</span> {m.unique_to_client.join(", ")}</div>}
          </div>
          <p className="text-xs text-slate-500 mt-2">Based on the {m.basis === "top10" ? `top ${m.landscape_size} competitors` : `${m.deep_size} deep-analysed competitors`}.</p>
        </Card>
      )}
      <Card title="Market trends (from public sources)">
        {d.trends.length === 0 && <Empty>{d.notes?.[0] ?? "No sourced trends found."}</Empty>}
        {kinds.map(([k, label]) => {
          const items = d.trends.filter((t: any) => t.kind === k);
          if (!items.length) return null;
          return (
            <div key={k} className="mb-2">
              <h3 className="text-xs font-semibold text-slate-500">{label}</h3>
              <ul className="list-disc ml-5 text-sm">{items.map((t: any) => <li key={t.evidence_id}>{t.statement} <EvidenceRefs ids={[t.evidence_id]} /></li>)}</ul>
            </div>
          );
        })}
      </Card>
    </div>
  );
}

/** Client vs top competitors, with frequency, market classification, search, filters and CSV export. */
export function ComparisonTab({ runId, enabled }: { runId: string; enabled: boolean }) {
  const res = useAgent(runId, "feature_comparison", enabled);
  const [q, setQ] = useState("");
  const [category, setCategory] = useState("");
  const [status, setStatus] = useState("");
  const [cls, setCls] = useState("");
  const [cell, setCell] = useState<{ feature: string; party: string } | null>(null);
  if (!enabled) return <Pending />;
  const d = res.data?.data;
  if (!d) return null;
  const categories = [...new Set<string>(d.rows.map((r: any) => r.category))];
  const rows = d.rows.filter((r: any) => (!q || r.feature_name.toLowerCase().includes(q.toLowerCase()))
    && (!category || r.category === category) && (!status || r.client === status) && (!cls || r.market_class === cls));

  function exportCsv() {
    if (!d) return;
    const head = ["Category", "Capability", "Client", ...d.competitors.map((c: any) => c.name), "Top-3 frequency", "Top-10 frequency", "Market class"];
    const esc = (v: string) => `"${String(v).replaceAll('"', '""')}"`;
    const lines = rows.map((r: any) => [r.category, r.feature_name, STATUS_LABEL[r.client], ...d.competitors.map((c: any) => STATUS_LABEL[r.competitors[c.id] ?? "unknown"]),
      `${r.top3_count}/${r.top3_total}`, `${r.top10_count}/${r.top10_total}`, CLASS_LABEL[r.market_class] ?? r.market_class].map(esc).join(","));
    const blob = new Blob([[head.map(esc).join(","), ...lines].join("\n")], { type: "text/csv" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `feature-comparison-${runId}.csv`;
    a.click();
    URL.revokeObjectURL(a.href);
  }

  const sel = "border rounded px-2 py-1 text-sm";
  return (
    <Card title="Feature comparison" actions={<button className="text-sm text-indigo-700 underline" onClick={exportCsv}>Export CSV</button>}>
      <div className="flex flex-wrap gap-2 mb-3">
        <input className={sel} placeholder="Search capabilities…" value={q} onChange={(e) => setQ(e.target.value)} />
        <select className={sel} value={category} onChange={(e) => setCategory(e.target.value)}>
          <option value="">All categories</option>{categories.map((c) => <option key={c}>{c}</option>)}
        </select>
        <select className={sel} value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">Any client status</option>{Object.entries(STATUS_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </select>
        <select className={sel} value={cls} onChange={(e) => setCls(e.target.value)}>
          <option value="">Any market class</option>{Object.entries(CLASS_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </select>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="text-left text-slate-500">
            <tr><th className="py-1">Category</th><th>Capability</th><th className="text-center">Client</th>
              {d.competitors.map((c: any) => <th key={c.id} className="text-center">{c.name}</th>)}
              <th className="text-center" title="Deep-analysed competitors offering it">Top 3</th>
              <th className="text-center" title="Competitive landscape offering it">Top 10</th><th>Market</th></tr>
          </thead>
          <tbody>
            {rows.map((row: any) => (
              <tr key={row.feature_id} className="border-t">
                <td className="py-1 text-slate-500">{row.category}</td>
                <td>{row.feature_name}</td>
                {[["client", row.client], ...d.competitors.map((c: any) => [c.id, row.competitors[c.id] ?? "unknown"])].map(([party, st]: any) => {
                  const n = row.evidence?.[party]?.length ?? 0;
                  const active = cell?.feature === row.feature_id && cell?.party === party;
                  return (
                    <td key={party} className="text-center">
                      <button disabled={!n} onClick={() => setCell(active ? null : { feature: row.feature_id, party })}
                        title={`${STATUS_LABEL[st]}${n ? ` — ${n} source(s), click to see them` : ""}`}
                        className={`rounded px-1 ${n ? "hover:bg-indigo-50 cursor-pointer" : "cursor-default"} ${active ? "ring-2 ring-indigo-400" : ""}`}>
                        {ICON[st]}{n ? <sup className="text-[9px] text-indigo-600">{n}</sup> : null}
                      </button>
                    </td>
                  );
                })}
                <td className="text-center text-xs">{row.top3_count}/{row.top3_total}</td>
                <td className="text-center text-xs">{row.top10_total ? `${row.top10_count}/${row.top10_total}` : "—"}</td>
                <td><span className={`text-xs rounded px-1.5 py-0.5 ${CLASS_STYLE[row.market_class] ?? ""}`}>{CLASS_LABEL[row.market_class] ?? row.market_class}</span></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {cell && (() => {
        const row = d.rows.find((r: any) => r.feature_id === cell.feature);
        const who = cell.party === "client" ? "the client" : d.competitors.find((c: any) => c.id === cell.party)?.name;
        return (
          <div className="mt-3 border rounded-lg p-3">
            <div className="text-sm font-medium mb-2">Evidence: {row?.feature_name} — {who}</div>
            <EvidenceList ids={row?.evidence?.[cell.party]} />
          </div>
        );
      })()}
      <p className="text-xs text-slate-500 mt-2">Click a status with a number to see its evidence.
        ✅ available · 🟡 partially available · ❔ not publicly identified · ❌ confirmed missing.
        Not publicly identified means no public evidence was found, not that the capability is absent.</p>
    </Card>
  );
}
