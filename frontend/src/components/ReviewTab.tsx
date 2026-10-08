/* eslint-disable @typescript-eslint/no-explicit-any */
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../lib/api";
import { useAgent } from "../lib/useAgent";
import { Badge, Button, Card, Empty, ErrorText } from "./ui";

interface Override {
  id: string;
  kind: string;
  target_id: string;
  target_label: string;
  field: string;
  value: string;
  note: string;
  status: string;
  applied_run_id: string | null;
  created_by_email: string | null;
  created_at: string;
}

const STATUS = [["available", "Available"], ["partial", "Partially available"], ["unknown", "Not publicly identified"], ["missing", "Confirmed missing"]];
const PRIORITY = ["high", "medium", "low"];
const PHASES = [["phase_1_quick_wins", "Quick win"], ["phase_2_growth", "Growth (1-3 months)"], ["phase_3_major", "Major (3-6 months)"], ["phase_4_strategic", "Strategic (6-12 months)"]];
const CATEGORIES = ["Critical competitive gap", "High-value product gap", "Customer experience gap", "Operational efficiency gap",
  "Revenue opportunity", "AI opportunity", "Automation opportunity", "Strategic / long-term opportunity", "Risk & compliance gap"];

/** Human review of a finished run: corrections and decisions, applied as a new run version. */
export default function ReviewTab({ runId, finished, canAct }: { runId: string; finished: boolean; canAct: boolean }) {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const list = useQuery({ queryKey: ["review", runId], queryFn: () => api.get<Override[]>(`/api/runs/${runId}/review`) });
  const features = useAgent(runId, "product_features", finished).data?.data;
  const comps = useAgent(runId, "competitor_research", finished).data?.data;
  const gaps = useAgent(runId, "gap_analysis", finished).data?.data;
  const prio = useAgent(runId, "opportunity_prioritization", finished).data?.data;
  const taxonomy = useQuery({ queryKey: ["taxonomy"], queryFn: () => api.get<{ id: string; name: string }[]>("/api/meta/taxonomy") });
  const [error, setError] = useState<unknown>(null);
  const [cap, setCap] = useState("");
  const [capStatus, setCapStatus] = useState("missing");
  const [capNote, setCapNote] = useState("");

  if (!finished) return <Empty>Review is available once the run has finished.</Empty>;
  const overrides = list.data ?? [];
  const pending = overrides.filter((o) => o.status === "pending");
  const editable = canAct && !overrides.some((o) => o.status === "applied" && o.applied_run_id !== runId);
  const name = new Map(taxonomy.data?.map((f) => [f.id, f.name]));

  async function put(body: Record<string, string>, ask = true) {
    const note = ask ? prompt("Reason / note for this change (recorded in the audit trail)") : body.note;
    if (note === null) return;
    setError(null);
    try {
      await api.put(`/api/runs/${runId}/review`, { field: "", ...body, note: note ?? "" });
      qc.invalidateQueries({ queryKey: ["review", runId] });
    } catch (e) {
      setError(e);
    }
  }
  async function remove(id: string) {
    try {
      await api.del(`/api/runs/${runId}/review/${id}`);
      qc.invalidateQueries({ queryKey: ["review", runId] });
    } catch (e) {
      setError(e);
    }
  }
  async function apply() {
    if (!confirm(`Apply ${pending.length} change(s)? A new run version is created; affected stages are re-run.`)) return;
    try {
      const r = await api.post<{ run_id: string }>(`/api/runs/${runId}/review/apply`);
      navigate(`/runs/${r.run_id}`);
    } catch (e) {
      setError(e);
    }
  }
  const sel = "border rounded px-2 py-1 text-sm";
  const describe = (o: Override) =>
    o.kind === "capability_status" ? `${name.get(o.target_label) ?? o.target_label}: ${STATUS.find(([k]) => k === o.value)?.[1] ?? o.value}`
      : o.kind === "competitor" ? `Exclude competitor ${o.target_label}`
        : o.kind === "gap" ? `${o.value} gap “${o.target_label}”`
          : `${o.target_label}: ${o.field.replace("_", " ")} → ${o.value}`;

  return (
    <div className="space-y-4">
      <Card title={`Review changes (${overrides.length})`} actions={editable && pending.length > 0 && <Button onClick={apply}>Apply {pending.length} change(s)</Button>}>
        <p className="text-sm text-slate-600 mb-2">
          Correct the analysis where you know better. Applying creates a new version of this run in which the corrections are used
          and every affected stage is re-run, so the matrix, gaps, priorities, sales summary, outreach and report all agree.
        </p>
        {overrides.length === 0 && <Empty>No review changes yet.</Empty>}
        <ul className="space-y-1 text-sm">
          {overrides.map((o) => (
            <li key={o.id} className="flex items-start gap-2">
              <Badge value={o.status === "applied" ? "approved" : "pending"} />
              <span className="flex-1">{describe(o)}{o.note && <span className="text-slate-500"> — “{o.note}”</span>}
                <span className="text-xs text-slate-400"> · {o.created_by_email}</span></span>
              {o.status === "pending" && editable && <button className="text-xs text-rose-700 underline" onClick={() => remove(o.id)}>remove</button>}
            </li>
          ))}
        </ul>
        <ErrorText error={error} />
      </Card>

      {editable && (
        <>
          <Card title="Capability status">
            <div className="flex flex-wrap gap-2 items-center">
              <select className={sel} value={cap} onChange={(e) => setCap(e.target.value)}>
                <option value="">Choose a capability…</option>
                {taxonomy.data?.map((f) => (
                  <option key={f.id} value={f.id}>{f.name} (now: {features?.observations?.[f.id]?.status ?? "unknown"})</option>
                ))}
              </select>
              <select className={sel} value={capStatus} onChange={(e) => setCapStatus(e.target.value)}>
                {STATUS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
              </select>
              <input className={`${sel} flex-1 min-w-[12rem]`} placeholder="Evidence or reason (e.g. confirmed by the client)" value={capNote} onChange={(e) => setCapNote(e.target.value)} />
              <Button variant="secondary" disabled={!cap} onClick={() => put({ kind: "capability_status", target_id: cap, value: capStatus, note: capNote }, false)}>Set status</Button>
            </div>
            <p className="text-xs text-slate-500 mt-1">Only a reviewer can mark a capability <b>confirmed missing</b>; the analysis never infers it from silence.</p>
          </Card>

          <Card title="Competitor selection">
            <ul className="text-sm divide-y">
              {(comps?.landscape?.length ? comps.landscape : comps?.competitors ?? []).map((c: any) => (
                <li key={c.id} className="py-1 flex items-center gap-2">
                  <span className="flex-1">{c.rank && `#${c.rank} `}{c.name} <span className="text-xs text-slate-500">{c.classification}{c.deep ? " · deep analysis" : ""}</span></span>
                  <button className="text-xs text-rose-700 underline" onClick={() => put({ kind: "competitor", target_id: c.id, value: "exclude" })}>Exclude</button>
                </li>
              ))}
            </ul>
          </Card>

          <Card title="Gaps">
            <ul className="text-sm divide-y">
              {(gaps?.gaps ?? []).map((g: any) => (
                <li key={g.id} className="py-1 flex items-center gap-2">
                  <span className="flex-1">{g.name} <span className="text-xs text-slate-500">{g.gap_type}</span></span>
                  {["approve", "rework", "reject"].map((v) => (
                    <button key={v} className={`text-xs underline ${v === "reject" ? "text-rose-700" : "text-indigo-700"}`}
                      onClick={() => put({ kind: "gap", target_id: g.id, value: v })}>{v === "rework" ? "request rework" : v}</button>
                  ))}
                </li>
              ))}
            </ul>
          </Card>

          <Card title="Recommendations">
            <table className="w-full text-sm">
              <thead className="text-left text-slate-500"><tr><th className="py-1">Recommendation</th><th>Priority</th><th>Horizon</th><th>Category</th></tr></thead>
              <tbody>
                {(prio?.recommendations ?? []).map((r: any) => (
                  <tr key={r.id} className="border-t">
                    <td className="py-1">{r.feature}</td>
                    <td><select className={sel} value={r.priority} onChange={(e) => put({ kind: "recommendation", target_id: r.id, field: "priority", value: e.target.value })}>
                      {PRIORITY.map((p) => <option key={p}>{p}</option>)}</select></td>
                    <td><select className={sel} value={r.phase} onChange={(e) => put({ kind: "recommendation", target_id: r.id, field: "phase", value: e.target.value })}>
                      {PHASES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></td>
                    <td><select className={sel} value={r.business_category} onChange={(e) => put({ kind: "recommendation", target_id: r.id, field: "business_category", value: e.target.value })}>
                      {CATEGORIES.map((c) => <option key={c}>{c}</option>)}</select></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>
        </>
      )}
    </div>
  );
}
