/* eslint-disable @typescript-eslint/no-explicit-any */
import { Fragment, useState } from "react";
import { useAgent } from "../lib/useAgent";
import { EvidenceList } from "./Explain";
import { RunBenchmark } from "./Benchmarks";
import { Badge, BasisTag, Card, Empty } from "./ui";
import EvidenceRefs from "./EvidenceRefs";
import { VerificationBadge } from "./Verification";

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
  available: "Available", partial: "Partially available", unknown: "Not publicly identified", missing: "Confirmed unavailable",
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
  // feature_comparison runs after competitor research and pricing, so both are ready with it.
  const cr = useAgent(runId, "competitor_research", comparisonDone);
  const pa = useAgent(runId, "pricing_analysis", comparisonDone);
  if (!enabled) return <Pending />;
  const d = res.data?.data;
  if (!d) return null;
  const m = fc.data?.data.market;
  const players = { landscape: cr.data?.data.landscape ?? [], pricing: pa.data?.data.comparison };
  const facts: [string, string, string | undefined][] = [
    ["Industry", d.industry, d.basis?.industry], ["Market segment", d.market_segment, d.basis?.market_segment],
    ["Product category", d.product_category, d.basis?.product_category],
    ["Customer segment", (d.customer_segment ?? []).join(", "), d.basis?.customer_segment],
    ["Business model", d.business_model, d.basis?.business_model], ["Geography", (d.geography ?? []).join(", "), undefined],
  ];
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
      <MarketResearch r={d.research} m={m} players={players} trends={d.trends ?? []} notes={d.notes} />
    </div>
  );
}


const SECTIONS: Record<string, [string, string][]> = {
  A: [["industry_definition", "Industry definition and market segment"], ["business_models", "Common business models"],
      ["customer_segments", "Key customer segments"], ["use_cases", "Major use cases"], ["value_chain", "Industry structure and value chain"]],
  B: [["trends", "Emerging industry trends"], ["customer_expectations", "Changing customer expectations"],
      ["technology_trends", "Product and technology trends"], ["ai_automation", "AI and automation adoption"],
      ["regulation", "Regulatory and compliance considerations"], ["emerging_models", "Emerging business models"]],
  C: [["customer_needs", "Common customer problems and needs"], ["buying_motivations", "Buying motivations"],
      ["selection_criteria", "Product selection criteria"]],
  D: [["market_participants", "Major market participants named in sources"], ["differentiation", "How products differentiate"],
      ["underserved_segments", "Potentially underserved segments"]],
  E: [["opportunities", "Growth opportunities"], ["product_extensions", "Potential product extensions"],
      ["threats", "Competitive and technology risks"], ["barriers", "Barriers to adoption"]],
};
const TITLES: Record<string, string> = { A: "A. Industry overview", B: "B. Market trends", C: "C. Customers and demand",
  D: "D. Competitive landscape", E: "E. Opportunities and risks" };

function Statements({ section, data }: { section: string; data: any }) {
  const found = SECTIONS[section].filter(([k]) => data?.[k]?.length);
  const missing = SECTIONS[section].filter(([k]) => !data?.[k]?.length).map(([, l]) => l.toLowerCase());
  return (
    <>
      {found.length > 0 && <div className="grid md:grid-cols-2 gap-4">{found.map(([k, label]) => (
        <div key={k}><h4 className="font-medium text-sm">{label}</h4>
          <ul className="list-disc ml-5 text-sm space-y-0.5">{data[k].map((i: any) => <li key={i.evidence_id}>{i.statement} <EvidenceRefs ids={[i.evidence_id]} /></li>)}</ul></div>
      ))}</div>}
      {missing.length > 0 && <p className="text-xs text-slate-500 mt-2">Not found in the sources read: {missing.join(", ")}.</p>}
    </>
  );
}

const CLASS_GROUPS: [string, string[]][] = [
  ["Direct competitors", ["direct", "enterprise", "emerging"]], ["Indirect and adjacent", ["indirect", "adjacent", "open_source"]],
];

/** Who competes, directly or indirectly, and at what published prices (from the competitor and pricing research). */
function Participants({ landscape, pricing }: { landscape: any[]; pricing?: any }) {
  if (!landscape.length) return null;
  const rows: any[] = pricing?.rows ?? [];
  const base = pricing?.base_currency;
  const range = (name: string) => {
    const own = rows.filter((r) => r.subject === name);
    const paid = own.filter((r) => r.monthly_normalized && !r.quote_based).map((r) => r.monthly_normalized);
    const parts = [];
    if (own.some((r) => r.free)) parts.push("free plan");
    if (paid.length) {
      const lo = Math.min(...paid), hi = Math.max(...paid);
      parts.push(`≈${lo.toLocaleString()}${hi !== lo ? `–${hi.toLocaleString()}` : ""} ${base}/month`);
    }
    if (own.some((r) => r.quote_based)) parts.push("contact sales");
    return parts.join(", ");
  };
  const all = rows.filter((r) => r.kind === "competitor" && r.monthly_normalized && !r.quote_based).map((r) => r.monthly_normalized);
  return (
    <div className="mt-3 border-t pt-3">
      <h4 className="font-medium text-sm mb-2">Competitors identified in this analysis</h4>
      <div className="grid md:grid-cols-2 gap-4">
        {CLASS_GROUPS.map(([title, classes]) => {
          const items = landscape.filter((c) => classes.includes(c.classification));
          return (
            <div key={title}>
              <div className="text-xs font-semibold uppercase tracking-wide text-slate-500 mb-1">{title} ({items.length})</div>
              {items.length ? (
                <ul className="text-sm space-y-0.5">{items.map((c) => (
                  <li key={c.id}>{c.rank && <span className="text-slate-400">#{c.rank} </span>}<span className="font-medium">{c.name}</span>
                    {c.classification !== classes[0] && <span className="text-xs text-slate-500"> ({c.classification.replace("_", " ")})</span>}
                    {range(c.name) && <span className="text-xs text-slate-600"> · {range(c.name)}</span>}</li>))}</ul>
              ) : <Empty>None identified.</Empty>}
            </div>
          );
        })}
      </div>
      {all.length > 0 && (
        <p className="text-sm mt-2"><span className="text-slate-500">Published paid price range of the deep-analysed competitors:</span>{" "}
          ≈{Math.min(...all).toLocaleString()}–{Math.max(...all).toLocaleString()} {base}/month
          <span className="text-xs text-slate-500"> (converted list prices; quote-based plans excluded; see the Pricing tab)</span></p>
      )}
    </div>
  );
}

function Landscape({ m }: { m: any }) {
  if (!m) return null;
  return (
    <div className="mt-3 border-t pt-3">
      <h4 className="font-medium text-sm mb-2">From this analysis's competitor research</h4>
      <div className="grid sm:grid-cols-3 gap-3 text-sm">
        <div className="rounded border p-2"><div className="text-xs text-slate-500">AI adoption</div><div className="text-xl font-semibold">{pct(m.ai_adoption)}</div><div className="text-xs text-slate-500">of {m.landscape_size} competitors offer an AI capability</div></div>
        <div className="rounded border p-2"><div className="text-xs text-slate-500">Automation adoption</div><div className="text-xl font-semibold">{pct(m.automation_adoption)}</div><div className="text-xs text-slate-500">offer automation capabilities</div></div>
        <div className="rounded border p-2"><div className="text-xs text-slate-500">Industry standards the client offers</div><div className="text-xl font-semibold">{pct(m.client_standard_coverage)}</div><div className="text-xs text-slate-500">of {m.industry_standards.length} must-have capabilities</div></div>
      </div>
      <div className="mt-3 text-sm space-y-1">
        <div><span className="text-slate-500">Common industry features (must-have):</span> {m.industry_standards.join(", ") || "—"}</div>
        <div><span className="text-slate-500">Emerging capabilities:</span> {m.emerging.join(", ") || "—"}</div>
        <div><span className="text-slate-500">Differentiators / niche:</span> {m.differentiators.join(", ") || "—"}</div>
        {m.unique_to_client.length > 0 && <div><span className="text-slate-500">Unique to the client:</span> {m.unique_to_client.join(", ")}</div>}
      </div>
      <p className="text-xs text-slate-500 mt-2">Based on the {m.basis === "top10" ? `top ${m.landscape_size} competitors` : `${m.deep_size} deep-analysed competitors`}.</p>
    </div>
  );
}

/** Sections A-E from the sources read, market figures with their context, and the sources. */
function MarketResearch({ r, m, players, trends, notes }: { r: any; m: any; players: any; trends: any[]; notes?: string[] }) {
  if (!r?.available) {
    return (
      <>
        {r?.reason && <p className="text-sm text-amber-800 bg-amber-50 border border-amber-200 rounded-lg p-3">Deep market research: {r.reason}</p>}
        <Card title="Market trends (from search results)">
          {trends.length === 0 ? <Empty>{notes?.[0] ?? "No sourced trends found."}</Empty> : (
            <ul className="list-disc ml-5 text-sm">{trends.map((t: any) => <li key={t.evidence_id}>{t.statement} <EvidenceRefs ids={[t.evidence_id]} />
              {t.snippet_only && <span className="text-xs text-slate-500"> (search snippet; the page was not read)</span>}</li>)}</ul>
          )}
        </Card>
        {(m || players.landscape.length > 0) && <Card title={TITLES.D}><Participants {...players} /><Landscape m={m} /></Card>}
      </>
    );
  }
  const s = r.sections ?? {};
  return (
    <>
      <Card title="Market figures">
        {r.figures?.length ? (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-left text-xs text-slate-500 border-b"><tr><th className="py-1 pr-2">Metric</th><th className="pr-2">Value</th><th className="pr-2">Period</th>
                <th className="pr-2">Geography</th><th className="pr-2">Definition</th><th className="pr-2">Original source</th><th /></tr></thead>
              <tbody>{r.figures.map((f: any) => (
                <tr key={f.metric + f.value} className="border-t align-top">
                  <td className="py-1.5 pr-2">{f.metric}</td><td className="pr-2 font-medium">{f.value}</td>
                  <td className="pr-2">{f.period ?? <span className="text-amber-700">not stated</span>}</td>
                  <td className="pr-2">{f.geography ?? <span className="text-amber-700">not stated</span>}</td>
                  <td className="pr-2 text-xs">{f.definition ?? "—"}</td>
                  <td className="pr-2 text-xs">{f.original_source ?? "—"}</td>
                  <td><EvidenceRefs ids={f.evidence_id ? [f.evidence_id] : []} /></td>
                </tr>))}</tbody>
            </table>
            <p className="text-xs text-slate-500 mt-2">Only figures stated, with their number, on the page cited. Figures without a period or geography should be read with care.</p>
          </div>
        ) : <Empty>No reliable market figure (size, growth, share) was found in the sources read; the assessment below is qualitative.</Empty>}
      </Card>
      {(["A", "B", "C", "D", "E"] as const).map((k) => (
        <Card key={k} title={TITLES[k]}>
          <Statements section={k} data={s} />
          {k === "D" && <><Participants {...players} /><Landscape m={m} /></>}
        </Card>
      ))}
      {r.qualitative_assessment && (
        <Card title={<>Qualitative assessment <BasisTag basis="estimate" /></>}>
          <p className="text-sm">{r.qualitative_assessment}</p>
          <p className="text-xs text-slate-500 mt-1">The analyst's interpretation of the sources above, not a sourced fact.</p>
        </Card>
      )}
      <Card title="Sources read">
        <ul className="text-sm space-y-0.5">{(r.sources ?? []).map((x: any) => (
          <li key={x.url}><a className="text-indigo-700 underline" href={x.url} target="_blank" rel="noreferrer">{x.title || x.url}</a>
            <span className="text-xs text-slate-500"> · accessed {new Date(x.accessed_at).toLocaleDateString()}</span></li>))}</ul>
        {r.unread?.length > 0 && <p className="text-xs text-slate-500 mt-2">Could not be read: {r.unread.join(", ")}.</p>}
        {r.dropped > 0 && <p className="text-xs text-slate-500 mt-1">{r.dropped} statement(s) were left out because their quote was not found in the cited page.</p>}
      </Card>
    </>
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
                <td><button className="text-left hover:text-indigo-700 underline decoration-dotted" title="Compare in detail"
                  onClick={() => setCell(cell?.feature === row.feature_id ? null : { feature: row.feature_id, party: "client" })}>{row.feature_name}</button></td>
                {[["client", row.client], ...d.competitors.map((c: any) => [c.id, row.competitors[c.id] ?? "unknown"])].map(([party, st]: any) => {
                  const n = row.evidence?.[party]?.length ?? 0;
                  const active = cell?.feature === row.feature_id && cell?.party === party;
                  return (
                    <td key={party} className="text-center">
                      <button onClick={() => setCell(active ? null : { feature: row.feature_id, party })}
                        title={`${STATUS_LABEL[st]}${n ? ` — ${n} source(s), click to see them` : ""}`}
                        className={`rounded px-1 hover:bg-indigo-50 cursor-pointer ${active ? "ring-2 ring-indigo-400" : ""}`}>
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
        if (!row) return null;
        const parties: [string, string, string][] = [["client", "Client", row.client],
          ...d.competitors.map((c: any) => [c.id, c.name, row.competitors[c.id] ?? "unknown"] as [string, string, string])];
        return (
          <div className="mt-3 border rounded-lg p-3 space-y-3">
            <div className="flex flex-wrap items-baseline gap-2">
              <span className="font-medium">{row.feature_name}</span><span className="text-xs text-slate-500">{row.category}</span>
              <button className="ml-auto text-xs underline text-slate-500" onClick={() => setCell(null)}>Close</button>
            </div>
            {row.description && <p className="text-sm"><span className="text-slate-500">What it does:</span> {row.description}</p>}
            <div className="text-sm grid md:grid-cols-2 gap-2">
              {row.relevance && <div><span className="text-slate-500">Business relevance:</span> {row.relevance}</div>}
              {row.significance && <div><span className="text-slate-500">Competitive significance:</span> {row.significance}</div>}
            </div>
            <div className="grid md:grid-cols-2 xl:grid-cols-4 gap-3">
              {parties.map(([id, name, st]) => {
                const c = row.cells?.[id] ?? {};
                return (
                  <div key={id} className={`rounded-lg border p-2 text-sm ${cell.party === id ? "ring-2 ring-indigo-300" : ""}`}>
                    <div className="font-medium">{name}</div>
                    <div className="flex flex-wrap items-center gap-1 my-1">
                      <span>{ICON[st]} {STATUS_LABEL[st]}</span>
                      {c.verification && <VerificationBadge status={c.verification} />}
                    </div>
                    {c.implementation && <p className="text-slate-700">{c.implementation}</p>}
                    {c.packaging && <p className="text-xs mt-1"><span className="text-slate-500">Packaging:</span> {c.packaging.add_on ? "paid add-on" : "included"}
                      {c.packaging.plans?.length > 0 && <> in {c.packaging.plans.join(", ")}</>}</p>}
                    {c.limitations?.length > 0 && <div className="text-xs mt-1"><span className="text-slate-500">Documented limitations:</span> {c.limitations.join("; ")}</div>}
                    {c.platforms?.length > 0 && <div className="text-xs mt-1"><span className="text-slate-500">Platforms:</span> {c.platforms.join(", ")}</div>}
                    {c.note && <div className="text-xs text-slate-500 mt-1">{c.note}</div>}
                    <div className="mt-1"><EvidenceList ids={row.evidence?.[id]} /></div>
                  </div>
                );
              })}
            </div>
          </div>
        );
      })()}
      <p className="text-xs text-slate-500 mt-2">Click a capability or a status to compare it in detail with its evidence.
        ✅ Available: evidence confirms it is offered · 🟡 Partially available: offered with documented limits ·
        ❔ Not publicly identified: the evidence does not establish whether it exists (never treated as missing) ·
        ❌ Confirmed unavailable: the company's own pages say it is not offered.</p>
    </Card>
  );
}
