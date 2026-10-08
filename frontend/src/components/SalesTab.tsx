/* eslint-disable @typescript-eslint/no-explicit-any */
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { useCanExport } from "../lib/useOrg";
import { BasisTag, Badge, Button, Card, Confidence, Empty, ErrorText } from "./ui";
import EvidenceRefs from "./EvidenceRefs";

interface SalesDoc {
  content: any;
  status: "draft" | "approved";
  version: number;
  history: { version: number; action: string; by: string | null; at: string; note: string }[];
  approved_at: string | null;
  edited: boolean;
}

interface SalesResponse {
  summary: SalesDoc | null;
  outreach: SalesDoc | null;
}

const OPPS: [string, string][] = [
  ["ai_opportunity", "Most attractive AI opportunity"],
  ["automation_opportunity", "Most attractive automation opportunity"],
  ["cost_saving_opportunity", "Cost-saving opportunity"],
  ["revenue_opportunity", "Revenue opportunity"],
];

function History({ doc }: { doc: SalesDoc }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="text-xs">
      <button className="text-indigo-700 underline" onClick={() => setOpen(!open)}>{open ? "Hide" : "Show"} history</button>
      {open && (
        <ul className="mt-1 space-y-0.5 text-slate-600">
          {doc.history.map((h, i) => (
            <li key={i}>v{h.version} {h.action} {h.by ? `by ${h.by}` : "by the analysis"} · {new Date(h.at).toLocaleString()}
              {h.note && ` — “${h.note}”`}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** Sales intelligence summary and the outreach email draft of a run (review, edit, approve, export). */
export default function SalesTab({ runId, canAct }: { runId: string; canAct: boolean }) {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["sales", runId], queryFn: () => api.get<SalesResponse>(`/api/runs/${runId}/sales`) });
  const [error, setError] = useState<unknown>(null);
  const refresh = (data?: SalesResponse) => (data ? qc.setQueryData(["sales", runId], data) : qc.invalidateQueries({ queryKey: ["sales", runId] }));

  if (q.isLoading) return null;
  if (q.error) return <ErrorText error={q.error} />;
  if (!q.data?.summary) return <Card title="Sales intelligence"><Empty>Not generated for this run.</Empty></Card>;
  return (
    <div className="space-y-4">
      <ErrorText error={error} />
      <SummaryCard runId={runId} doc={q.data.summary} canAct={canAct} onError={setError}
        onSaved={(summary) => refresh({ ...q.data!, summary })} />
      {q.data.outreach && (
        <OutreachCard runId={runId} doc={q.data.outreach} canAct={canAct} onError={setError}
          onSaved={(outreach) => refresh({ ...q.data!, outreach })} />
      )}
    </div>
  );
}

function SummaryCard({ runId, doc, canAct, onSaved, onError }: {
  runId: string; doc: SalesDoc; canAct: boolean; onSaved: (d: SalesDoc) => void; onError: (e: unknown) => void;
}) {
  const c = doc.content;
  const canExport = useCanExport();
  const [editing, setEditing] = useState(false);
  const [angle, setAngle] = useState(c.conversation_angle);
  const [next, setNext] = useState(c.next_step);
  const [notes, setNotes] = useState(c.reviewer_notes ?? "");
  useEffect(() => {
    setAngle(c.conversation_angle);
    setNext(c.next_step);
    setNotes(c.reviewer_notes ?? "");
  }, [c.conversation_angle, c.next_step, c.reviewer_notes]);

  async function save() {
    try {
      onSaved(await api.patch<SalesDoc>(`/api/runs/${runId}/sales/summary`,
        { conversation_angle: angle, next_step: next, reviewer_notes: notes }));
      setEditing(false);
    } catch (e) {
      onError(e);
    }
  }
  async function approve() {
    try {
      onSaved(await api.post<SalesDoc>(`/api/runs/${runId}/sales/summary/approve`, {}));
    } catch (e) {
      onError(e);
    }
  }

  return (
    <Card
      title={`Sales intelligence — ${c.client}`}
      actions={
        <div className="flex items-center gap-2">
          <Badge value={doc.status} />
          {canAct && !editing && <Button variant="secondary" onClick={() => setEditing(true)}>Edit</Button>}
          {canAct && doc.status !== "approved" && <Button onClick={approve}>Approve</Button>}
          {canExport && <Button variant="secondary" onClick={() => api.download(`/api/runs/${runId}/sales-summary.md`, `sales-summary-${runId}.md`).catch(onError)}>
            Export
          </Button>}
        </div>
      }
    >
      <div className="grid gap-5 lg:grid-cols-2 text-sm">
        <section>
          <h3 className="font-semibold mb-1">Key pain points</h3>
          {c.pain_points.length === 0 && <Empty>None evidenced.</Empty>}
          <ul className="space-y-1">
            {c.pain_points.map((p: any, i: number) => (
              <li key={i}>
                {p.text}{" "}
                {p.internal_only && <span className="text-xs rounded bg-amber-100 text-amber-800 px-1">internal only</span>}{" "}
                <EvidenceRefs ids={p.evidence_ids} />
              </li>
            ))}
          </ul>
        </section>
        <section>
          <h3 className="font-semibold mb-1">Top 3 competitive gaps</h3>
          {c.top_gaps.length === 0 && <Empty>No gap is confident enough to lead with.</Empty>}
          <ol className="list-decimal ml-5 space-y-1">
            {c.top_gaps.map((g: any) => (
              <li key={g.gap_id}>
                <b>{g.name}</b> — {g.competitors.join(", ")} ({g.coverage}). <span className="text-slate-600">{g.why}</span>{" "}
                <EvidenceRefs ids={g.evidence_ids} />
              </li>
            ))}
          </ol>
          {c.claim_safety?.excluded_gaps?.length > 0 && (
            <p className="text-xs text-slate-500 mt-1">
              Not used (low confidence): {c.claim_safety.excluded_gaps.map((g: any) => g.name).join(", ")}
            </p>
          )}
        </section>
        <section>
          <h3 className="font-semibold mb-1">Top 3 recommended improvements</h3>
          <ol className="list-decimal ml-5 space-y-1">
            {c.top_improvements.map((r: any) => (
              <li key={r.recommendation_id}><b>{r.feature}</b> <span className="text-xs text-slate-500">({r.phase})</span> — {r.business_impact}</li>
            ))}
          </ol>
        </section>
        <section>
          <h3 className="font-semibold mb-1">Opportunities</h3>
          <dl className="space-y-1">
            {OPPS.map(([k, label]) => (
              <div key={k}>
                <dt className="text-xs text-slate-500">{label}</dt>
                <dd>{c[k] ? <>{c[k].name}{c[k].impact && <span className="text-slate-600"> — {c[k].impact}</span>}<BasisTag basis="estimate" /></> : <span className="text-slate-400">none identified</span>}</dd>
              </div>
            ))}
          </dl>
        </section>
        <section className="lg:col-span-2">
          <h3 className="font-semibold mb-1">Recommended conversation angle</h3>
          {editing ? <textarea className="border rounded w-full p-2" rows={4} value={angle} onChange={(e) => setAngle(e.target.value)} /> :
            <p>{c.conversation_angle}</p>}
        </section>
        <section>
          <h3 className="font-semibold mb-1">Relevant company capabilities</h3>
          {c.relevant_capabilities.length === 0 && c.internal_capabilities.length === 0 && (
            <Empty>No approved knowledge-base match.</Empty>
          )}
          <ul className="space-y-1">
            {c.relevant_capabilities.map((x: any) => (
              <li key={x.record_id}>{x.title} <span className="text-xs text-slate-500">for {x.need}</span> <Confidence value={x.confidence} /></li>
            ))}
            {c.internal_capabilities.map((x: any) => (
              <li key={x.record_id} className="text-slate-600">{x.title} <span className="text-xs rounded bg-amber-100 text-amber-800 px-1">internal only</span></li>
            ))}
          </ul>
          {c.case_studies.length > 0 && (
            <>
              <h4 className="text-xs font-semibold text-slate-500 mt-2">Case studies you may reference</h4>
              <ul>{c.case_studies.map((x: any) => <li key={x.record_id}>{x.title} ({x.customer})</li>)}</ul>
            </>
          )}
        </section>
        <section>
          <h3 className="font-semibold mb-1">Suggested next step</h3>
          {editing ? <textarea className="border rounded w-full p-2" rows={2} value={next} onChange={(e) => setNext(e.target.value)} /> :
            <p>{c.next_step}</p>}
          {c.contact && <p className="text-xs text-slate-500 mt-1">Contact: {c.contact.email} ({c.contact.source})</p>}
          <h3 className="font-semibold mt-3 mb-1">Reviewer notes</h3>
          {editing ? <textarea className="border rounded w-full p-2" rows={2} value={notes} onChange={(e) => setNotes(e.target.value)} /> :
            <p className="text-slate-600">{c.reviewer_notes || "—"}</p>}
        </section>
      </div>
      {editing && (
        <div className="flex justify-end gap-2 mt-3">
          <Button variant="secondary" onClick={() => setEditing(false)}>Cancel</Button>
          <Button onClick={save}>Save</Button>
        </div>
      )}
      <div className="mt-3"><History doc={doc} /></div>
    </Card>
  );
}

function OutreachCard({ runId, doc, canAct, onSaved, onError }: {
  runId: string; doc: SalesDoc; canAct: boolean; onSaved: (d: SalesDoc) => void; onError: (e: unknown) => void;
}) {
  const c = doc.content;
  const canExport = useCanExport();
  const [to, setTo] = useState(c.to ?? "");
  const [subject, setSubject] = useState(c.subject);
  const [body, setBody] = useState(c.body);
  const [instructions, setInstructions] = useState("");
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);
  const [showFacts, setShowFacts] = useState(false);
  useEffect(() => {
    setTo(c.to ?? "");
    setSubject(c.subject);
    setBody(c.body);
  }, [c.to, c.subject, c.body]);
  const dirty = to !== (c.to ?? "") || subject !== c.subject || body !== c.body;
  const problems: string[] = c.problems ?? [];
  const blocking = problems.filter((p) => /internal-only|security/i.test(p));

  async function run(fn: () => Promise<SalesDoc>) {
    setBusy(true);
    try {
      onSaved(await fn());
    } catch (e) {
      onError(e);
    } finally {
      setBusy(false);
    }
  }
  const save = () => run(() => api.patch<SalesDoc>(`/api/runs/${runId}/outreach`, { to, subject, body }));
  const regenerate = () => {
    if (dirty && !confirm("Discard your unsaved edits and regenerate?")) return;
    run(() => api.post<SalesDoc>(`/api/runs/${runId}/outreach/regenerate`, { instructions }));
  };
  const approve = () => {
    if (problems.length && !confirm(`Approve despite these warnings?\n\n${problems.join("\n")}`)) return;
    run(() => api.post<SalesDoc>(`/api/runs/${runId}/outreach/approve`, { acknowledge_warnings: problems.length > 0 }));
  };
  async function copy() {
    await navigator.clipboard.writeText(`Subject: ${c.subject}\n\n${c.body}`);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  }

  const input = "border rounded px-2 py-1 text-sm w-full";
  return (
    <Card
      title="Personalized outreach email"
      actions={
        <div className="flex items-center gap-2">
          <Badge value={doc.status} />
          <span className="text-xs text-slate-500">v{doc.version} · {c.generated_by === "llm" ? "AI draft" : c.generated_by === "edited" ? "edited" : "template draft"}</span>
          <Button variant="secondary" onClick={copy}>{copied ? "Copied" : "Copy"}</Button>
          {canExport && <Button variant="secondary" onClick={() => api.download(`/api/runs/${runId}/outreach.eml`, `outreach-${runId}.eml`).catch(onError)}>
            Download .eml
          </Button>}
        </div>
      }
    >
      <div className="space-y-2 text-sm">
        <label className="block">To<input className={input} value={to} onChange={(e) => setTo(e.target.value)} disabled={!canAct} /></label>
        <label className="block">Subject<input className={input} value={subject} onChange={(e) => setSubject(e.target.value)} disabled={!canAct} /></label>
        <label className="block">Body<textarea className={`${input} font-sans`} rows={16} value={body} onChange={(e) => setBody(e.target.value)} disabled={!canAct} /></label>
        {problems.length > 0 && (
          <div className={`rounded border p-2 text-xs ${blocking.length ? "border-rose-300 bg-rose-50 text-rose-800" : "border-amber-300 bg-amber-50 text-amber-800"}`}>
            <b>{blocking.length ? "Cannot be approved:" : "Check before approving:"}</b>
            <ul className="list-disc ml-4">{problems.map((p) => <li key={p}>{p}</li>)}</ul>
          </div>
        )}
        {problems.length === 0 && <p className="text-xs text-emerald-700">Claim check passed: only facts from this analysis, no internal-only knowledge.</p>}
        {canAct && (
          <div className="flex flex-wrap items-center gap-2 pt-1">
            <Button onClick={save} disabled={!dirty || busy}>Save edits</Button>
            <Button onClick={approve} disabled={dirty || busy || doc.status === "approved" || blocking.length > 0}>Approve</Button>
            <input className="border rounded px-2 py-1 text-sm flex-1 min-w-[12rem]" placeholder="Regenerate with instructions, e.g. shorter, focus on automation"
              value={instructions} onChange={(e) => setInstructions(e.target.value)} />
            <Button variant="secondary" onClick={regenerate} disabled={busy}>Regenerate</Button>
          </div>
        )}
        <div className="flex gap-4">
          <button className="text-xs text-indigo-700 underline" onClick={() => setShowFacts(!showFacts)}>{showFacts ? "Hide" : "Show"} facts used</button>
          <History doc={doc} />
        </div>
        {showFacts && (
          <ul className="text-xs space-y-1">
            {(c.facts ?? []).map((f: any, i: number) => (
              <li key={i}><span className="text-slate-500">[{f.kind}{f.estimate ? ", idea" : ""}]</span> {f.text} <EvidenceRefs ids={f.evidence_ids} /></li>
            ))}
          </ul>
        )}
      </div>
    </Card>
  );
}
