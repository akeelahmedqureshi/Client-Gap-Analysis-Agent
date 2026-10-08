import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { ApiError, api } from "../lib/api";
import type { ApprovalPreview, Project, Run } from "../lib/types";
import { Button, ErrorText } from "./ui";

interface ScoringConfig {
  weights: Record<string, number>;
  profile: string;
  profiles: string[];
}

interface BulkResult {
  runs: Run[];
  skipped: { project_id: string; reason: string; run_id?: string }[];
}

/** Start an analysis of one project, or one independent analysis per selected project. */
export default function StartRunDialog({ projects, onClose }: { projects: Project[]; onClose: () => void }) {
  const navigate = useNavigate();
  const project = projects[0];
  const bulk = projects.length > 1;
  const [existing, setExisting] = useState<string | null>(null);
  const [result, setResult] = useState<BulkResult | null>(null);
  const preview = useQuery({
    queryKey: ["approval-preview", project.id],
    queryFn: () => api.get<ApprovalPreview[]>(`/api/runs/approval-preview?project_id=${project.id}`),
  });
  const scoring = useQuery({ queryKey: ["scoring"], queryFn: () => api.get<ScoringConfig>("/api/meta/scoring") });
  const [profile, setProfile] = useState<string | null>(null);
  const chosen = profile ?? scoring.data?.profile ?? "Standard";
  const profileWeights = useQuery({
    queryKey: ["scoring-profile", chosen],
    queryFn: () => api.get<{ effective: { weights: Record<string, number> } }>(`/api/config/scoring_profile?key=${encodeURIComponent(chosen)}`),
    enabled: !!scoring.data,
  });
  const shownWeights = profileWeights.data?.effective.weights ?? scoring.data?.weights;
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
    setError(null);
    try {
      if (bulk) {
        setResult(await api.post<BulkResult>("/api/runs/bulk", {
          project_ids: projects.map((p) => p.id), approve_gates: [...approved], scoring_weights: weights,
          scoring_profile: chosen,
        }));
        setBusy(false);
        return;
      }
      const run = await api.post<Run>("/api/runs", {
        project_id: project.id,
        approve_gates: [...approved],
        scoring_weights: weights,
        scoring_profile: chosen,
      });
      navigate(`/runs/${run.run_id}`);
    } catch (e) {
      // An analysis of this project is already in progress: offer to open it instead.
      const id = e instanceof ApiError && e.status === 409 ? /run_[a-z0-9]+/.exec(e.message)?.[0] : undefined;
      if (id) setExisting(id);
      else setError(e);
      setBusy(false);
    }
  }

  return (
    <div className="fixed inset-0 z-10 bg-black/40 grid place-items-center p-4" onClick={onClose}>
      <div className="bg-white rounded-xl shadow-xl w-full max-w-2xl max-h-[90vh] overflow-auto" onClick={(e) => e.stopPropagation()}>
        <div className="px-5 py-4 border-b">
          <h2 className="text-lg font-semibold">{bulk ? `Start ${projects.length} analyses` : `Start analysis: ${project.name}`}</h2>
          <p className="text-sm text-slate-500">
            Review what the agents will access. Steps you don't pre-approve will pause the run and wait for approval.
            {bulk && " Each project gets its own independent analysis; the targets below are for the first project."}
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
          {scoring.data && (
            <label className="text-sm flex items-center gap-2">
              Scoring profile
              <select className="border rounded px-2 py-1" value={chosen} onChange={(e) => setProfile(e.target.value)}>
                {scoring.data.profiles.map((p) => <option key={p} value={p}>{p}{p === scoring.data.profile ? " (default)" : ""}</option>)}
              </select>
            </label>
          )}
          <button className="text-sm text-indigo-600 underline" onClick={() => setShowWeights(!showWeights)}>
            {showWeights ? "Hide" : "Customize"} prioritization weights
          </button>
          {showWeights && shownWeights && (
            <div className="grid grid-cols-2 gap-2" key={chosen}>
              <p className="col-span-2 text-xs text-slate-500">Changes here apply to this start only, on top of the “{chosen}” profile.</p>
              {Object.entries(shownWeights).map(([k, v]) => (
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
          {existing && (
            <p className="text-sm rounded border border-amber-300 bg-amber-50 p-2">
              An analysis of this project is already in progress. <Link className="underline" to={`/runs/${existing}`}>Open it</Link>.
            </p>
          )}
          {result && (
            <div className="text-sm rounded border p-2 space-y-1">
              <div>Started {result.runs.length} analysis(es).</div>
              {result.skipped.map((s) => {
                const name = projects.find((p) => p.id === s.project_id)?.name ?? s.project_id;
                return <div key={s.project_id} className="text-slate-600">Skipped {name}: {s.reason}{s.run_id && <> — <Link className="underline" to={`/runs/${s.run_id}`}>open</Link></>}</div>;
              })}
            </div>
          )}
          <ErrorText error={error} />
        </div>
        <div className="px-5 py-3 border-t flex justify-end gap-2">
          {result ? (
            <Button onClick={() => navigate("/runs")}>View analysis runs</Button>
          ) : (
            <>
              <Button variant="secondary" onClick={onClose}>Cancel</Button>
              <Button onClick={start} disabled={busy}>{bulk ? `Start ${projects.length} analyses` : "Start analysis"}</Button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
