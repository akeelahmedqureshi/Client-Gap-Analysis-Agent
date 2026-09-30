import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../lib/api";
import type { ApprovalPreview, Project, Run } from "../lib/types";
import { Button, ErrorText } from "./ui";

interface ScoringConfig {
  weights: Record<string, number>;
}

export default function StartRunDialog({ project, onClose }: { project: Project; onClose: () => void }) {
  const navigate = useNavigate();
  const preview = useQuery({
    queryKey: ["approval-preview", project.id],
    queryFn: () => api.get<ApprovalPreview[]>(`/api/runs/approval-preview?project_id=${project.id}`),
  });
  const scoring = useQuery({ queryKey: ["scoring"], queryFn: () => api.get<ScoringConfig>("/api/meta/scoring") });
  const [approved, setApproved] = useState<Set<string>>(new Set());
  const [weights, setWeights] = useState<Record<string, number>>({});
  const [showWeights, setShowWeights] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  const toggle = (gate: string) => {
    const next = new Set(approved);
    if (next.has(gate)) next.delete(gate);
    else next.add(gate);
    setApproved(next);
  };

  async function start() {
    setBusy(true);
    try {
      const run = await api.post<Run>("/api/runs", {
        project_id: project.id,
        approve_gates: [...approved],
        scoring_weights: weights,
      });
      navigate(`/runs/${run.run_id}`);
    } catch (e) {
      setError(e);
      setBusy(false);
    }
  }

  return (
    <div className="fixed inset-0 z-10 bg-black/40 grid place-items-center p-4" onClick={onClose}>
      <div className="bg-white rounded-xl shadow-xl w-full max-w-2xl max-h-[90vh] overflow-auto" onClick={(e) => e.stopPropagation()}>
        <div className="px-5 py-4 border-b">
          <h2 className="text-lg font-semibold">Start analysis: {project.name}</h2>
          <p className="text-sm text-slate-500">
            Review what the agents will access. Steps you don't pre-approve will pause the run and wait for approval.
          </p>
        </div>
        <div className="p-5 space-y-3">
          {preview.data?.map((p) => (
            <label key={p.gate} className="flex gap-3 border rounded-lg p-3 cursor-pointer hover:bg-slate-50">
              <input type="checkbox" className="mt-1" checked={approved.has(p.gate)} onChange={() => toggle(p.gate)} />
              <div className="text-sm">
                <div className="font-medium">{p.title}</div>
                <div><span className="text-slate-500">Target:</span> {p.target}</div>
                <div><span className="text-slate-500">What:</span> {p.what}</div>
                <div><span className="text-slate-500">Why:</span> {p.why}</div>
                <div><span className="text-slate-500">Data analyzed:</span> {p.data_analyzed}</div>
              </div>
            </label>
          ))}
          <button className="text-sm text-indigo-600 underline" onClick={() => setShowWeights(!showWeights)}>
            {showWeights ? "Hide" : "Customize"} prioritization weights
          </button>
          {showWeights && scoring.data && (
            <div className="grid grid-cols-2 gap-2">
              {Object.entries(scoring.data.weights).map(([k, v]) => (
                <label key={k} className="text-sm flex justify-between items-center gap-2">
                  <span className="text-slate-600">{k.replaceAll("_", " ")}</span>
                  <input
                    type="number" step="0.1" min="0" max="5"
                    defaultValue={v}
                    onChange={(e) => setWeights({ ...weights, [k]: Number(e.target.value) })}
                    className="w-20 border rounded px-2 py-1"
                  />
                </label>
              ))}
            </div>
          )}
          <ErrorText error={error} />
        </div>
        <div className="px-5 py-3 border-t flex justify-end gap-2">
          <Button variant="secondary" onClick={onClose}>Cancel</Button>
          <Button onClick={start} disabled={busy}>Start analysis</Button>
        </div>
      </div>
    </div>
  );
}
