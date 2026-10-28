/* eslint-disable @typescript-eslint/no-explicit-any */
import { useState } from "react";
import EvidenceRefs from "./EvidenceRefs";
import { Badge, Card, Empty } from "./ui";
import { SourceLink, VerificationBadge } from "./Verification";

const FACT_LABELS: [string, string][] = [
  ["how_it_works", "How it works"], ["key_functionality", "Key functionality"], ["inputs", "Inputs"],
  ["outputs", "Outputs"], ["platforms", "Supported platforms"], ["integrations", "Integrations & third-party services"],
  ["prerequisites", "Dependencies & prerequisites"], ["limitations", "Documented limitations"],
];

function FeatureDetail({ f }: { f: any }) {
  const facts = FACT_LABELS.filter(([k]) => (f.facts?.[k] ?? []).length);
  const it = f.interpretation ?? {};
  const hasIt = it.purpose || it.intended_users?.length || it.use_cases?.length || it.benefits?.length;
  return (
    <div className="grid md:grid-cols-2 gap-4 text-sm bg-slate-50 rounded-lg p-3 mt-2">
      <div>
        <div className="text-xs font-semibold uppercase tracking-wide text-emerald-800 mb-1">From the sources (quoted)</div>
        {facts.length === 0 ? <Empty>No further detail is stated in the pages read.</Empty> : (
          <dl className="space-y-2">
            {facts.map(([k, label]) => (
              <div key={k}>
                <dt className="font-medium">{label}</dt>
                <dd><ul className="list-disc ml-5">{f.facts[k].map((x: any) => <li key={x.text}>{x.text} <EvidenceRefs ids={[x.evidence_id]} /></li>)}</ul></dd>
              </div>
            ))}
          </dl>
        )}
        {f.dropped_details > 0 && <p className="text-xs text-slate-500 mt-2">{f.dropped_details} detail(s) not shown: their quotes were not found in the cited pages.</p>}
      </div>
      <div>
        <div className="text-xs font-semibold uppercase tracking-wide text-violet-800 mb-1">Analysis (interpretation of the cited pages)</div>
        {!hasIt ? <Empty>No interpretation recorded.</Empty> : (
          <dl className="space-y-2">
            {it.purpose && <div><dt className="font-medium">Purpose / business problem</dt><dd>{it.purpose}</dd></div>}
            {it.intended_users?.length > 0 && <div><dt className="font-medium">Intended users</dt><dd>{it.intended_users.join(", ")}</dd></div>}
            {it.use_cases?.length > 0 && <div><dt className="font-medium">Primary use cases</dt><dd><ul className="list-disc ml-5">{it.use_cases.map((u: string) => <li key={u}>{u}</li>)}</ul></dd></div>}
            {it.benefits?.length > 0 && <div><dt className="font-medium">Business & customer benefits</dt><dd><ul className="list-disc ml-5">{it.benefits.map((u: string) => <li key={u}>{u}</li>)}</ul></dd></div>}
          </dl>
        )}
        {(f.sources ?? []).length > 0 && (
          <div className="mt-3"><div className="font-medium">Sources</div>
            <ul className="text-xs space-y-0.5">{f.sources.map((u: string) => <li key={u}><SourceLink url={u} /></li>)}</ul></div>
        )}
        {(f.technology ?? []).length > 0 && <div className="mt-2 text-xs"><span className="font-medium">Implemented with:</span> {f.technology.join(", ")}</div>}
      </div>
    </div>
  );
}

/** Detailed feature inventory, grouped by category, with expandable quoted facts. */
export function FeatureInventory({ items, title = "Features", subject }: { items: any[]; title?: string; subject: string }) {
  const [open, setOpen] = useState<Set<string>>(new Set());
  const toggle = (k: string) => setOpen((s) => { const n = new Set(s); if (n.has(k)) n.delete(k); else n.add(k); return n; });
  if (!items.length) return <Card title={title}><Empty>No features identified for {subject}.</Empty></Card>;
  const groups = new Map<string, any[]>();
  for (const f of items) {
    const g = f.category || "Other";
    groups.set(g, [...(groups.get(g) ?? []), f]);
  }
  const count = (v: string) => items.filter((f) => f.verification === v).length;
  const detailed = items.some((f) => f.facts);
  return (
    <Card title={title} actions={<button className="text-xs text-indigo-700 underline" onClick={() =>
      setOpen(open.size ? new Set() : new Set(items.map((f) => f.name)))}>{open.size ? "Collapse all" : "Expand all"}</button>}>
      <p className="text-sm text-slate-600 mb-3">
        {items.length} feature(s): {count("documented")} documented, {count("evidenced")} evidenced
        {count("unverified") > 0 && <>, <span className="text-amber-700">{count("unverified")} unverified</span></>}.
        {!detailed && <span className="text-slate-500"> Analysed before detailed extraction was added; start a new analysis for details.</span>}
      </p>
      {[...groups.entries()].map(([g, fs]) => (
        <div key={g} className="mb-4">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500 mb-1">{g}</h3>
          <ul className="divide-y border rounded-lg">
            {fs.map((f) => (
              <li key={f.name} className="p-2">
                <button className="w-full text-left flex flex-wrap items-center gap-2" onClick={() => toggle(f.name)}>
                  <span className="text-slate-400 w-3">{open.has(f.name) ? "▾" : "▸"}</span>
                  <span className="font-medium">{f.name}</span>
                  <Badge value={f.status} />
                  {f.verification && <VerificationBadge status={f.verification} />}
                  <span className="ml-auto" onClick={(e) => e.stopPropagation()}><EvidenceRefs ids={f.evidence_ids} /></span>
                </button>
                {f.description && <p className="text-sm text-slate-700 ml-5 mt-1">{f.description}</p>}
                {open.has(f.name) && <div className="ml-5"><FeatureDetail f={f} /></div>}
              </li>
            ))}
          </ul>
        </div>
      ))}
    </Card>
  );
}

/** Workflows the company's own pages document, step by step. */
export function DocumentedWorkflows({ flows, subject }: { flows: any[]; subject: string }) {
  return (
    <Card title="Documented workflows">
      {!flows.length ? <Empty>No workflow is documented step by step on {subject}'s public pages.</Empty> : (
        <div className="space-y-4">
          {flows.map((w) => (
            <details key={w.name} className="border rounded-lg p-3" open={flows.length <= 2}>
              <summary className="cursor-pointer flex flex-wrap items-center gap-2">
                <span className="font-medium">{w.name}</span>
                <VerificationBadge status={w.verification === "documented" ? "documented" : "partial"}
                  detail={`${w.documented_steps} of ${w.steps.length} steps are documented in the sources`} />
                <span className="text-xs text-slate-500">{w.documented_steps} of {w.steps.length} steps documented</span>
              </summary>
              <div className="grid md:grid-cols-2 gap-3 text-sm mt-2">
                {w.objective && <div><span className="font-medium">Business objective:</span> {w.objective}</div>}
                {w.trigger && <div><span className="font-medium">Trigger:</span> {w.trigger}</div>}
                {w.actors?.length > 0 && <div><span className="font-medium">Actors:</span> {w.actors.map((a: any) => `${a.role}${a.responsibilities ? ` (${a.responsibilities})` : ""}`).join("; ")}</div>}
                {w.prerequisites?.length > 0 && <div><span className="font-medium">Prerequisites:</span> {w.prerequisites.join("; ")}</div>}
              </div>
              <ol className="mt-3 space-y-1.5 text-sm">
                {w.steps.map((s: any) => (
                  <li key={s.order} className="flex gap-2">
                    <span className="w-6 h-6 shrink-0 rounded-full bg-slate-100 text-xs flex items-center justify-center">{s.order}</span>
                    <div className="flex-1">
                      <span className={`text-xs rounded px-1 mr-1 ${s.kind === "system" ? "bg-sky-100 text-sky-800" : "bg-amber-50 text-amber-800"}`}>{s.kind === "system" ? "System" : s.actor || "User"}</span>
                      {s.action}
                      {s.decision && <span className="text-xs text-slate-600"> · decision: {s.decision}</span>}
                      {(s.inputs?.length > 0 || s.outputs?.length > 0) && <div className="text-xs text-slate-500">
                        {s.inputs?.length > 0 && <>in: {s.inputs.join(", ")} </>}{s.outputs?.length > 0 && <>· out: {s.outputs.join(", ")}</>}</div>}
                    </div>
                    {s.documented ? <EvidenceRefs ids={[s.evidence_id]} /> : <span className="text-xs text-violet-700 whitespace-nowrap" title="Not described in the sources; needed to connect the documented steps">inferred</span>}
                  </li>
                ))}
              </ol>
              <div className="grid md:grid-cols-2 gap-3 text-sm mt-3">
                {w.integrations?.length > 0 && <div><span className="font-medium">Integrations:</span> {w.integrations.join(", ")}</div>}
                {w.automation?.length > 0 && <div><span className="font-medium">Automation:</span> {w.automation.join("; ")}</div>}
                {w.exceptions?.length > 0 && <div><span className="font-medium">Exceptions & error handling:</span> {w.exceptions.join("; ")}</div>}
                {w.related_features?.length > 0 && <div><span className="font-medium">Related features:</span> {w.related_features.join(", ")}</div>}
                {w.outcome && <div><span className="font-medium">Outcome:</span> {w.outcome}</div>}
                {w.business_value && <div><span className="font-medium">Business value</span> <span className="text-xs text-violet-700">(analysis)</span>: {w.business_value}</div>}
              </div>
            </details>
          ))}
        </div>
      )}
    </Card>
  );
}
