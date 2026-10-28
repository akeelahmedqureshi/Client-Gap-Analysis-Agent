/* eslint-disable @typescript-eslint/no-explicit-any */
import EvidenceRefs from "./EvidenceRefs";
import { DocumentedWorkflows, FeatureInventory } from "./Offering";
import { Badge, Confidence, Empty } from "./ui";
import { SourceLink, VerificationBadge } from "./Verification";

const SECTIONS: [string, string][] = [
  ["overview", "Company & business overview"], ["products", "Products & services"],
  ["target_customers", "Target customers"], ["industries", "Industries"],
  ["value_proposition", "Core value proposition"], ["positioning", "Market positioning"],
  ["integrations", "Integrations & technology"], ["ai_capabilities", "AI capabilities"],
  ["automation", "Automation"], ["onboarding_support", "Onboarding & support"],
  ["differentiators", "Key differentiators (as the competitor presents them)"],
  ["strengths", "Strengths (shown by its pages)"], ["limitations", "Limitations (shown by its pages)"],
];

function Section({ label, items }: { label: string; items: any[] }) {
  return (
    <div>
      <h4 className="font-medium text-sm">{label}</h4>
      <ul className="list-disc ml-5 text-sm">
        {items.map((i) => <li key={i.value}>{i.value} <EvidenceRefs ids={[i.evidence_id]} /></li>)}
      </ul>
    </div>
  );
}

function Overlap({ o }: { o: any }) {
  if (!o || !(o.shared?.length || o.competitor_only?.length || o.client_only?.length)) return null;
  const col = (title: string, items: any[], cls: string) => (
    <div>
      <div className={`text-xs font-semibold uppercase tracking-wide mb-1 ${cls}`}>{title} ({items.length})</div>
      {items.length ? <ul className="text-sm space-y-0.5">{items.map((x) => <li key={x.feature_id}>{x.name}</li>)}</ul> : <Empty>None.</Empty>}
    </div>
  );
  return (
    <div className="grid md:grid-cols-3 gap-4 bg-slate-50 rounded-lg p-3">
      {col("Both offer", o.shared ?? [], "text-slate-600")}
      {col("Only the competitor", o.competitor_only ?? [], "text-rose-700")}
      {col("Only the client", o.client_only ?? [], "text-emerald-700")}
      <p className="md:col-span-3 text-xs text-slate-500">From evidenced capabilities only. "Only the competitor" means not publicly
        identified for the client, which is not proof that the client lacks it.</p>
    </div>
  );
}

function Profile({ c, open }: { c: any; open: boolean }) {
  const profile = c.profile ?? {};
  const found = SECTIONS.filter(([k]) => profile[k]?.length);
  const missing = SECTIONS.filter(([k]) => !profile[k]?.length).map(([, l]) => l.split(" (")[0].toLowerCase());
  const legacy = !c.profile;
  return (
    <details className="bg-white border border-slate-200 rounded-xl shadow-sm" open={open}>
      <summary className="cursor-pointer px-4 py-3 border-b border-slate-100 flex flex-wrap items-center gap-2">
        {c.rank && <span className="text-slate-400">#{c.rank}</span>}
        <span className="font-semibold">{c.name}</span>
        <SourceLink url={c.url} />
        <Badge value={c.classification} />
        {c.relevance?.overall != null && <span className="text-xs text-slate-500">relevance {Math.round(c.relevance.overall * 100)}%</span>}
        <span className="text-xs text-slate-500">{c.pages_analysed?.length ?? 0} page(s) analysed</span>
        {c.deep_error && <span className="text-xs text-rose-700">deep analysis failed; light profile shown</span>}
      </summary>
      <div className="p-4 space-y-4">
        <div className="text-sm space-y-1">
          {c.description && <p>{c.description} <EvidenceRefs ids={c.evidence_ids} /></p>}
          {c.target_market && <p><span className="text-slate-500">Market:</span> {c.target_market}</p>}
          {c.pricing && <p><span className="text-slate-500">Pricing:</span> {c.pricing} <span className="text-xs text-slate-500">(details in the Pricing tab)</span></p>}
          <p className="text-xs text-slate-500">{c.rationale}</p>
          <Confidence value={c.confidence} />
        </div>
        {legacy ? <Empty>Analysed before deep profiles were added; start a new analysis for the full profile.</Empty> : (
          <>
            {found.length > 0 && <div className="grid md:grid-cols-2 gap-4">{found.map(([k, l]) => <Section key={k} label={l} items={profile[k]} />)}</div>}
            {missing.length > 0 && <p className="text-xs text-slate-500">Not found on the pages analysed: {missing.join(", ")}.</p>}
          </>
        )}
        <div><h4 className="font-medium text-sm mb-1">Capabilities compared with the client</h4><Overlap o={c.overlap} /></div>
        {c.inventory?.length > 0 ? <FeatureInventory items={c.inventory} title={`${c.name} features`} subject={c.name} /> : (
          <div>
            <h4 className="font-medium text-sm mb-1">Capabilities identified</h4>
            <div className="flex flex-wrap gap-1">
              {(c.features ?? []).filter((f: any) => f.status !== "unknown").map((f: any) => (
                <span key={f.feature_id} className="text-xs bg-slate-100 rounded px-1.5 py-0.5 inline-flex items-center gap-1">
                  {f.feature_id} {f.verification && <VerificationBadge status={f.verification} />}
                </span>
              ))}
            </div>
          </div>
        )}
        {c.workflows?.length > 0 && <DocumentedWorkflows flows={c.workflows} subject={c.name} />}
        {c.pages_analysed?.length > 0 && (
          <details className="text-xs"><summary className="cursor-pointer text-slate-600">Pages analysed ({c.pages_analysed.length})</summary>
            <ul className="mt-1 space-y-0.5">{c.pages_analysed.map((u: string) => <li key={u}><SourceLink url={u} /></li>)}</ul>
          </details>
        )}
      </div>
    </details>
  );
}

/** Deep, competitor-specific profiles of the Top 3. */
export default function CompetitorProfiles({ competitors }: { competitors: any[] }) {
  if (!competitors.length) return null; // the tab explains why no competitor was verified
  return (
    <div className="space-y-3">
      <h2 className="font-semibold">Top {competitors.length} — deep analysis</h2>
      <p className="text-sm text-slate-600">Every statement below is quoted from the competitor's own pages (click "sources");
        claims that could not be matched to a page were left out.</p>
      {competitors.map((c, i) => <Profile key={c.id} c={c} open={i === 0} />)}
    </div>
  );
}
