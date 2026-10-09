/* eslint-disable @typescript-eslint/no-explicit-any */
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../lib/api";
import { BasisTag, Card, Empty } from "./ui";

const pct = (v: number) => `${Math.round(v * 100)}%`;
const BAND: Record<string, string> = { standard: "bg-emerald-100 text-emerald-800", common: "bg-sky-100 text-sky-800", emerging: "bg-amber-100 text-amber-800" };

function Bar({ value }: { value: number }) {
  return (
    <div className="h-2 w-28 bg-slate-100 rounded" title={pct(value)}>
      <div className="h-2 bg-indigo-500 rounded" style={{ width: pct(value) }} />
    </div>
  );
}

function Trends({ trends, note }: { trends: any[]; note: string | null }) {
  if (!trends.length) return <Empty>{note ?? "No capability is clearly rising yet."}</Empty>;
  return (
    <ul className="text-sm space-y-1">
      {trends.map((t) => (
        <li key={t.feature_id} className="flex flex-wrap items-center gap-2">
          <span className="font-medium">{t.name}</span>{t.ai && <span className="text-xs rounded bg-violet-100 text-violet-800 px-1">AI</span>}
          <span className="text-xs text-slate-600">{pct(t.before)} → {pct(t.after)} of companies; if the trend continues ≈ {pct(t.projected)}</span>
          <BasisTag basis="estimate" />
        </li>
      ))}
    </ul>
  );
}

/** Industry-wide benchmark across the organization's analyses (Portfolio page). */
export function IndustryBenchmarks() {
  const list = useQuery({ queryKey: ["benchmarks"], queryFn: () => api.get<any[]>("/api/portfolio/benchmarks") });
  const [chosen, setChosen] = useState<string | null>(null);
  const industry = chosen ?? list.data?.[0]?.name;
  const bench = useQuery({
    queryKey: ["benchmark", industry],
    queryFn: () => api.get<any>(`/api/portfolio/benchmarks/${encodeURIComponent(industry!)}`),
    enabled: !!industry,
  });
  const d = bench.data;
  const [showAll, setShowAll] = useState(false);
  if (!list.data?.length) return <Card title="Industry benchmarks"><Empty>Analyse projects to build industry benchmarks.</Empty></Card>;
  return (
    <Card title="Industry benchmarks" actions={
      <select className="border rounded px-2 py-1 text-sm" value={industry} onChange={(e) => setChosen(e.target.value)}>
        {list.data.map((i) => <option key={i.key} value={i.name}>{i.name} ({i.companies} companies)</option>)}
      </select>
    }>
      {d && (
        <div className="space-y-4">
          <p className="text-sm text-slate-600">
            {d.companies} companies ({d.clients} client{d.clients === 1 ? "" : "s"}, {d.competitors} competitors) from {d.analyses} analyses;
            median {d.median_capabilities} evidenced capabilities.
            {!d.sufficient && <span className="text-amber-700"> Small sample: treat as indicative.</span>}
          </p>
          <div className="grid lg:grid-cols-2 gap-4">
            <div>
              <div className="font-medium text-sm mb-1">Capability adoption</div>
              <table className="text-sm w-full">
                <tbody>
                  {(showAll ? d.capabilities : d.capabilities.slice(0, 12)).map((c: any) => (
                    <tr key={c.feature_id} className="border-t">
                      <td className="py-1 pr-2">{c.name}</td>
                      <td><Bar value={c.adoption} /></td>
                      <td className="px-2 text-xs w-10">{pct(c.adoption)}</td>
                      <td><span className={`text-xs rounded px-1.5 ${BAND[c.band]}`}>{c.band}</span></td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {d.capabilities.length > 12 && <button className="text-xs underline mt-1" onClick={() => setShowAll(!showAll)}>{showAll ? "Show fewer" : `Show all ${d.capabilities.length}`}</button>}
            </div>
            <div className="space-y-4">
              <div>
                <div className="font-medium text-sm mb-1">Clients against the industry</div>
                <ul className="text-sm space-y-1">
                  {d.client_positions.map((c: any) => (
                    <li key={c.project_id}>
                      <span className="font-medium">{c.name}</span> — {c.capabilities} capabilities (percentile {c.percentile}),
                      standards {c.standards_covered}/{c.standards_total}
                      {c.standards_missing.length > 0 && <div className="text-xs text-slate-500">Not publicly identified: {c.standards_missing.join(", ")}</div>}
                    </li>
                  ))}
                </ul>
              </div>
              <div>
                <div className="font-medium text-sm mb-1">Predicted trends</div>
                <Trends trends={d.predicted_trends} note={d.trend_note} />
              </div>
              <div>
                <div className="font-medium text-sm mb-1">Trends reported in sources</div>
                {d.sourced_trends.length === 0 ? <Empty>None found (needs web search).</Empty> : (
                  <ul className="text-sm list-disc ml-5">
                    {d.sourced_trends.map((t: any) => (
                      <li key={t.text}>{t.text} <span className="text-xs text-slate-500">({t.clients} client{t.clients === 1 ? "" : "s"}{t.sources[0] && <> · <a className="underline" href={t.sources[0]} target="_blank" rel="noreferrer">source</a></>})</span></li>
                    ))}
                  </ul>
                )}
              </div>
            </div>
          </div>
          <p className="text-xs text-slate-500">{d.method}</p>
        </div>
      )}
    </Card>
  );
}

/** This run's client against its industry (Market tab). Internal: other clients are not named. */
export function RunBenchmark({ runId }: { runId: string }) {
  const q = useQuery({ queryKey: ["run-benchmark", runId], queryFn: () => api.get<any>(`/api/portfolio/runs/${runId}/benchmark`), retry: false });
  const d = q.data;
  if (!d) return null;
  return (
    <Card title={`Industry benchmark — ${d.industry}`}>
      <p className="text-sm">
        {d.capabilities} evidenced capabilities against a median of {d.median_capabilities} across {d.companies} companies analysed in this industry
        (percentile {d.percentile}).{!d.sufficient && <span className="text-amber-700"> Small sample.</span>}{" "}
        <Link className="underline text-indigo-700" to="/portfolio">Full benchmark</Link>
      </p>
      <div className="grid md:grid-cols-2 gap-4 mt-3 text-sm">
        <div>
          <div className="font-medium">Common in the industry, not publicly identified here</div>
          {d.missing_common.length ? <ul className="list-disc ml-5">{d.missing_common.map((c: any) => <li key={c.feature_id}>{c.name} <span className="text-xs text-slate-500">{pct(c.adoption)}</span></li>)}</ul> : <Empty>None.</Empty>}
        </div>
        <div>
          <div className="font-medium">Ahead of most of the industry</div>
          {d.ahead.length ? <ul className="list-disc ml-5">{d.ahead.map((c: any) => <li key={c.feature_id}>{c.name} <span className="text-xs text-slate-500">only {pct(c.adoption)}</span></li>)}</ul> : <Empty>None.</Empty>}
        </div>
      </div>
      <div className="mt-3"><div className="font-medium text-sm mb-1">Predicted trends</div><Trends trends={d.predicted_trends} note={d.trend_note} /></div>
    </Card>
  );
}
