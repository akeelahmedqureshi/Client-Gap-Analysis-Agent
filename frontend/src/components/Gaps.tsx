/* eslint-disable @typescript-eslint/no-explicit-any */
import { useState } from "react";
import EvidenceRefs from "./EvidenceRefs";
import { Badge, BasisTag, Card, Confidence, Empty } from "./ui";

export const CLASSIFICATIONS: Record<string, string> = {
  competitive_feature: "Competitive feature gap", product_functionality: "Product functionality gap",
  customer_experience: "Customer experience gap", operational_efficiency: "Operational efficiency gap",
  ai_automation: "AI and automation opportunity", pricing_packaging: "Pricing or packaging opportunity",
  market_positioning: "Market positioning gap", strategic_product: "Strategic product opportunity",
  security_compliance: "Security and compliance (internal)",
};
const VERDICTS: [string, string, string][] = [
  ["confirmed", "Confirmed gaps", "Every check passed: the competitor capability is verified, the client's status is established, customers care, and the value justifies the effort."],
  ["potential", "Potential gaps — need validation", "Worth pursuing if the evidence holds: the client side or the competitor evidence still needs to be checked."],
  ["optional", "Optional opportunities", "The expected value does not clearly justify the effort; consider them later."],
  ["unassessed", "Not assessed", "Analysed before gap verdicts were added; start a new analysis to assess them."],
];
const level = (v?: number) => (v == null ? "—" : v >= 4 ? "high" : v >= 2.5 ? "medium" : "low");

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return <div><dt className="text-xs text-slate-500">{label}</dt><dd className="text-sm">{children || <span className="text-slate-400">—</span>}</dd></div>;
}

function GapDetail({ g, o, names }: { g: any; o: any; names: Record<string, string> }) {
  const f = o?.factors ?? {};
  return (
    <div className="mt-2 bg-slate-50 rounded-lg p-3 space-y-3">
      {o?.checks?.length > 0 && (
        <div>
          <div className="text-xs font-semibold uppercase tracking-wide text-slate-500 mb-1">Assessment</div>
          <ul className="grid md:grid-cols-2 gap-x-4 gap-y-1 text-sm">
            {o.checks.map((c: any) => (
              <li key={c.name} className="flex gap-1.5"><span className={c.passed ? "text-emerald-600" : "text-amber-600"}>{c.passed ? "✓" : "?"}</span>
                <span><span className="font-medium">{c.label}</span>{c.detail && <span className="text-slate-600"> — {c.detail}</span>}</span></li>
            ))}
          </ul>
        </div>
      )}
      <dl className="grid md:grid-cols-2 lg:grid-cols-3 gap-3">
        <Field label="Client's current capability">
          {g.assessment?.client_status?.detail}{g.client_evidence_ids?.length > 0 && <> <EvidenceRefs ids={g.client_evidence_ids} /></>}
        </Field>
        <Field label="Competitors offering it">
          {(g.competitors_with ?? []).length ? g.competitors_with.map((id: string) => (
            <span key={id} className="mr-2">{names[id] ?? id} <EvidenceRefs ids={g.competitor_evidence?.[id] ?? []} /></span>)) : "None (not a competitive gap)"}
        </Field>
        <Field label="Competitive significance">
          {g.market_class ? g.market_class.replace("_", " ") : ""}{g.landscape_share != null && ` · ${Math.round(g.landscape_share * 100)}% of the landscape`}
        </Field>
        <Field label="Customer problem addressed">{o?.business_opportunity || g.description}</Field>
        <Field label="Business relevance">{o?.attributes?.business_relevance}</Field>
        <Field label="Customer experience impact">{o?.attributes?.customer_value}{o?.user_impact && ` — ${o.user_impact}`}</Field>
        <Field label="Revenue opportunity">{o?.attributes?.revenue_impact}{o?.revenue_opportunity && ` — ${o.revenue_opportunity}`}</Field>
        <Field label="Cost-saving opportunity">{level(f.cost_saving)}</Field>
        <Field label="Productivity improvement">{level(f.productivity)}</Field>
        <Field label="Implementation complexity">{o?.complexity}</Field>
        <Field label="Dependencies and prerequisites">{o?.dependencies?.length ? o.dependencies.join("; ") : ""}</Field>
        <Field label="Recommended priority">{o?.priority}{o?.phase && ` · ${o.phase.replace("_", " ")}`}</Field>
      </dl>
      {o?.technical_approach && <Field label="Proposed implementation approach"><span>{o.technical_approach} <BasisTag basis="estimate" /></span></Field>}
      {o?.risks?.length > 0 && <Field label="Risks and limitations"><ul className="list-disc ml-5">{o.risks.map((r: string) => <li key={r}>{r}</li>)}</ul></Field>}
      <Field label="All sources"><EvidenceRefs ids={g.evidence_ids} /></Field>
    </div>
  );
}

/** Evidence-based gaps, grouped by verdict, with the six-point assessment behind each. */
export default function GapsView({ gaps, opportunities, competitors, existing }: {
  gaps: any[]; opportunities: any[]; competitors: any[]; existing: any[];
}) {
  const byGap = new Map<string, any>(opportunities.map((o) => [o.gap_id, o]));
  const names = Object.fromEntries(competitors.map((c: any) => [c.id, c.name]));
  const [verdict, setVerdict] = useState("");
  const [cls, setCls] = useState("");
  const [open, setOpen] = useState<Set<string>>(new Set());
  const verdictOf = (g: any) => byGap.get(g.id)?.verdict ?? "unassessed";
  const shown = gaps.filter((g) => (!verdict || verdictOf(g) === verdict) && (!cls || g.classification === cls));
  const count = (v: string) => gaps.filter((g) => verdictOf(g) === v).length;
  const toggle = (id: string) => setOpen((s) => { const n = new Set(s); if (n.has(id)) n.delete(id); else n.add(id); return n; });
  const sel = "border rounded px-2 py-1 text-sm";
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-3 gap-3">
        {VERDICTS.slice(0, 3).map(([k, label]) => (
          <button key={k} onClick={() => setVerdict(verdict === k ? "" : k)}
            className={`text-left bg-white border rounded-xl p-3 ${verdict === k ? "ring-2 ring-indigo-400" : ""}`}>
            <div className="text-xs text-slate-500">{label}</div><div className="text-2xl font-semibold">{count(k)}</div>
          </button>
        ))}
      </div>
      <div className="flex flex-wrap gap-2">
        <select className={sel} value={cls} onChange={(e) => setCls(e.target.value)}>
          <option value="">All classifications</option>
          {Object.entries(CLASSIFICATIONS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </select>
        {(verdict || cls) && <button className="text-sm underline text-slate-600" onClick={() => { setVerdict(""); setCls(""); }}>Clear filters</button>}
      </div>
      {VERDICTS.map(([k, label, help]) => {
        const items = shown.filter((g) => verdictOf(g) === k);
        if (!items.length) return null;
        return (
          <Card key={k} title={`${label} (${items.length})`}>
            <p className="text-xs text-slate-500 mb-2">{help}</p>
            <ul className="divide-y">
              {items.map((g) => {
                const o = byGap.get(g.id);
                return (
                  <li key={g.id} className="py-2">
                    <button className="w-full text-left flex flex-wrap items-center gap-2" onClick={() => toggle(g.id)}>
                      <span className="text-slate-400 w-3">{open.has(g.id) ? "▾" : "▸"}</span>
                      <span className="font-medium">{g.name}</span>
                      {g.classification && <span className="text-xs rounded bg-slate-100 px-1.5 py-0.5">{CLASSIFICATIONS[g.classification] ?? g.classification}</span>}
                      {o && <Badge value={o.priority} />}
                      <BasisTag basis={g.basis} /> <Confidence value={g.confidence} />
                    </button>
                    <p className="text-sm text-slate-600 ml-5">{g.description}</p>
                    {open.has(g.id) && <div className="ml-5"><GapDetail g={g} o={o} names={names} /></div>}
                  </li>
                );
              })}
            </ul>
          </Card>
        );
      })}
      {!shown.length && <Empty>No gaps match these filters.</Empty>}
      <Card title="Existing capabilities">
        <div className="flex flex-wrap gap-2">
          {existing.map((f: any) => <span key={f.feature_id} className="text-sm"><Badge value={f.status} /> {f.name}</span>)}
        </div>
      </Card>
    </div>
  );
}
