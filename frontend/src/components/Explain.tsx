/* eslint-disable @typescript-eslint/no-explicit-any */
import { useQuery } from "@tanstack/react-query";
import { useContext } from "react";
import { Link } from "react-router-dom";
import { api } from "../lib/api";
import type { Evidence } from "../lib/types";
import { useAgent } from "../lib/useAgent";
import { Badge, BasisTag, Card, Empty } from "./ui";
import { EvidenceContext } from "./EvidenceRefs";

const TIER: Record<number, string> = { 1: "official", 2: "official news", 3: "trusted 3rd party", 4: "publication", 5: "search" };

/** The evidence items behind a statement: claim, verbatim quote, source, quality tier and date. */
export function EvidenceList({ ids, empty = "No evidence recorded." }: { ids: string[] | undefined; empty?: string }) {
  const index = useContext(EvidenceContext);
  const items = (ids ?? []).map((i) => index.get(i)).filter(Boolean) as Evidence[];
  if (!items.length) return <p className="text-xs text-slate-500">{empty}</p>;
  return (
    <ul className="space-y-2 text-xs">
      {items.map((e) => (
        <li key={e.id} className="border-l-2 border-indigo-200 pl-2">
          <div className="font-medium text-slate-800">{e.claim}</div>
          {e.extracted_text && <blockquote className="text-slate-600 italic">“{e.extracted_text}”</blockquote>}
          <div className="text-slate-500">
            {e.source_url.startsWith("http") ? <a className="underline text-indigo-700 break-all" href={e.source_url} target="_blank" rel="noreferrer">{e.source_url}</a>
              : <code>{e.source_url}</code>}
            {e.repository_path && <> · <code>{e.repository_path}{e.line_range ? `:${e.line_range}` : ""}</code></>}
            {" · "}{e.source_type}{e.source_tier ? ` · tier ${e.source_tier} (${TIER[e.source_tier]})` : ""}
            {" · "}confidence {Math.round(e.confidence * 100)}% · collected {new Date(e.collected_at).toLocaleDateString()}
          </div>
        </li>
      ))}
    </ul>
  );
}

/** Recommendation → gap → evidence, with the score breakdown (BRS 29 drill-down). */
export function WhyChain({ runId, item }: { runId: string; item: any }) {
  const gaps = useAgent(runId, "gap_analysis", true).data?.data?.gaps ?? [];
  const comps = useAgent(runId, "competitor_research", true).data?.data;
  const gap = gaps.find((g: any) => g.id === item.gap_id);
  const names = new Map<string, string>([...(comps?.competitors ?? []), ...(comps?.landscape ?? [])].map((c: any) => [c.id, c.name]));
  const contributions = Object.entries(item.score?.contributions ?? {}).sort((a: any, b: any) => Math.abs(b[1]) - Math.abs(a[1]));
  return (
    <div className="grid md:grid-cols-3 gap-3 text-sm bg-slate-50 rounded-lg p-3">
      <div>
        <div className="text-xs uppercase text-slate-500 mb-1">1 · Recommendation</div>
        <div className="font-medium">{item.feature ?? item.name}</div>
        <div className="text-xs text-slate-600">{item.business_impact ?? item.business_opportunity}</div>
        <div className="mt-2 text-xs text-slate-500">Score {item.score?.total}{item.score?.confidence_factor ? ` (benefits ×${item.score.confidence_factor} for evidence confidence ${item.score.confidence})` : ""}</div>
        <ul className="text-xs mt-1">
          {contributions.slice(0, 6).map(([k, v]: any) => <li key={k} className={v < 0 ? "text-rose-700" : ""}>{k.replaceAll("_", " ")}: {v > 0 ? "+" : ""}{v}</li>)}
        </ul>
        {item.attributes?.source_quality && <div className="text-xs text-slate-500 mt-1">Best source: {item.attributes.source_quality}</div>}
      </div>
      <div>
        <div className="text-xs uppercase text-slate-500 mb-1">2 · Gap it closes</div>
        {gap ? (
          <>
            <div className="font-medium">{gap.name} <Badge value={gap.gap_type} /> <BasisTag basis={gap.basis} /></div>
            <div className="text-xs text-slate-600">{gap.description}</div>
            {gap.competitors_with?.length > 0 && <div className="text-xs mt-1">Offered by: {gap.competitors_with.map((c: string) => names.get(c) ?? c).join(", ")}</div>}
            {gap.market_class && <div className="text-xs text-slate-500">Market: {gap.market_class.replaceAll("_", " ")}{gap.landscape_share != null ? ` · ${Math.round(gap.landscape_share * 100)}% of the Top 10` : ""}</div>}
            <div className="text-xs text-slate-500">Confidence {Math.round((gap.confidence ?? 0) * 100)}%</div>
          </>
        ) : <Empty>Gap not found in this run.</Empty>}
      </div>
      <div>
        <div className="text-xs uppercase text-slate-500 mb-1">3 · Evidence</div>
        <EvidenceList ids={[...new Set([...(item.evidence_ids ?? []), ...(gap?.evidence_ids ?? [])])]} empty="No supporting evidence: treat as a hypothesis." />
      </div>
    </div>
  );
}

/** Competitors across the project's completed analyses (rank and evidenced capabilities). */
export function CompetitorHistory({ projectId, runId }: { projectId: string; runId: string }) {
  const q = useQuery({ queryKey: ["competitor-history", projectId], queryFn: () => api.get<any>(`/api/projects/${projectId}/competitor-history`) });
  const d = q.data;
  if (!d || d.runs.length < 2) {
    return <Card title="History across analyses"><Empty>Shown once this project has two or more completed analyses.</Empty></Card>;
  }
  const arrow = (n: number | null) => n == null || n === 0 ? "" : n > 0 ? ` ▲${n}` : ` ▼${-n}`;
  return (
    <Card title={`History across ${d.runs.length} analyses`}>
      <div className="overflow-x-auto">
        <table className="text-sm w-full">
          <thead className="text-left text-slate-500">
            <tr><th className="py-1 pr-3">Competitor</th>
              {d.runs.map((r: any) => (
                <th key={r.run_id} className="px-2 text-center text-xs font-normal">
                  <Link className={`underline ${r.run_id === runId ? "font-semibold" : ""}`} to={`/runs/${r.run_id}`}>{new Date(r.date).toLocaleDateString()}</Link>
                </th>
              ))}
              <th className="px-2 text-xs font-normal">Change</th></tr>
          </thead>
          <tbody>
            <tr className="border-t bg-indigo-50/40">
              <td className="py-1 pr-3 font-medium">Client capabilities</td>
              {d.runs.map((r: any) => <td key={r.run_id} className="px-2 text-center">{r.client_capabilities}<div className="text-[10px] text-slate-500">standards {r.standards_covered}/{r.standards_total}</div></td>)}
              <td className="px-2 text-xs">{arrow(d.runs[d.runs.length - 1].client_capabilities - d.runs[0].client_capabilities)}</td>
            </tr>
            {d.competitors.map((c: any) => (
              <tr key={c.key} className="border-t">
                <td className="py-1 pr-3">{c.name} {c.new && <Badge value="new" />} {c.dropped && <span className="text-xs text-slate-500">(no longer ranked)</span>}</td>
                {c.points.map((p: any, i: number) => (
                  <td key={i} className="px-2 text-center text-xs">{p ? <>#{p.rank ?? "–"} · {p.features}{p.deep ? " ★" : ""}</> : <span className="text-slate-300">—</span>}</td>
                ))}
                <td className="px-2 text-xs whitespace-nowrap">{c.feature_change ? `${c.feature_change > 0 ? "+" : ""}${c.feature_change} capabilities` : ""}{c.rank_change ? ` · rank${arrow(c.rank_change)}` : ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="text-xs text-slate-500 mt-2">#rank · evidenced capabilities; ★ deep-analysed. Competitors are matched across analyses by domain.</p>
    </Card>
  );
}

/** Value proposition, use cases, customer problems and workflows from the product-features analysis. */
export function PositioningCards({ data }: { data: any }) {
  const p = data?.positioning;
  const flows: any[] = data?.workflows ?? [];
  if (!p && !flows.length) return null;
  const STEP: Record<string, string> = { supported: "✅", mentioned: "🟡", not_identified: "❔" };
  return (
    <div className="grid lg:grid-cols-2 gap-4">
      <Card title="Value proposition & customers">
        {p?.value_proposition ? (
          <div className="text-sm space-y-1">
            <p className="font-medium">“{p.value_proposition.statement}”</p>
            {p.value_proposition.supporting && <p className="text-slate-600">{p.value_proposition.supporting}</p>}
            <EvidenceList ids={p.value_proposition.evidence_ids} />
          </div>
        ) : <Empty>No headline statement found on the client's site.</Empty>}
        <div className="mt-3 text-sm">
          <div className="font-medium">Use cases</div>
          {p?.use_cases?.length ? <ul className="list-disc ml-5">{p.use_cases.map((u: any) => <li key={u.name}>{u.name}{u.quote && <span className="text-xs text-slate-500"> — “{u.quote}”</span>}</li>)}</ul>
            : <Empty>None stated publicly.</Empty>}
        </div>
        <div className="mt-3 text-sm">
          <div className="font-medium">Customer problems the client addresses</div>
          {p?.customer_problems?.length ? <ul className="list-disc ml-5">{p.customer_problems.map((x: any) => <li key={x.statement}>“{x.statement}”</li>)}</ul>
            : <Empty>No problem statements found on the client's pages.</Empty>}
        </div>
      </Card>
      <Card title="Workflows">
        {flows.length === 0 ? <Empty>No workflows visible on the client's public pages.</Empty> : (
          <ul className="space-y-3 text-sm">
            {flows.map((w) => (
              <li key={w.id}>
                <div className="font-medium">{w.name} <span className="text-xs text-slate-500">{w.actor} workflow · {w.supported}/{w.steps.length} steps supported</span></div>
                <div className="flex flex-wrap gap-1 mt-1">
                  {w.steps.map((s: any) => (
                    <span key={s.name} className="text-xs rounded border px-1.5 py-0.5" title={s.status === "mentioned" ? `mentioned: “${s.mention}”` : s.status.replace("_", " ")}>
                      {STEP[s.status]} {s.name}
                    </span>
                  ))}
                </div>
              </li>
            ))}
          </ul>
        )}
        <p className="text-xs text-slate-500 mt-2">✅ capability offered · 🟡 mentioned on the site · ❔ not publicly identified (not the same as absent).</p>
      </Card>
    </div>
  );
}
