import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../lib/api";
import type { Decision, FieldSpec, ImportResult, ReviewRow } from "../lib/intake";
import { ACTION_LABEL } from "../lib/intake";
import ClientFields from "./ClientFields";
import MatchPanel from "./MatchPanel";
import { Button, Card, ErrorText } from "./ui";

/** Add one client with a form built from the client schema; the same validation and duplicate rules as the CSV upload. */
export default function ManualClient({ schema }: { schema: FieldSpec[] }) {
  const qc = useQueryClient();
  const [values, setValues] = useState<Record<string, string>>({});
  const [decision, setDecision] = useState<Decision>({});
  const [row, setRow] = useState<ReviewRow | null>(null);
  const [checked, setChecked] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [saved, setSaved] = useState<ImportResult | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  // silent: the background check when a field loses focus; it must not disable the buttons being clicked.
  async function check(d: Decision = decision, silent = false) {
    if (!silent) setBusy(true);
    setError(null);
    try {
      const res = await api.post<{ row: ReviewRow }>("/api/intake/review", { values, decision: d });
      setRow(res.row);
      setChecked(true);
      return res.row;
    } catch (e) {
      setError(e);
      return null;
    } finally {
      if (!silent) setBusy(false);
    }
  }

  async function save() {
    setBusy(true);
    setError(null);
    try {
      const res = await api.post<ImportResult>("/api/intake/clients", { values, decision });
      setSaved(res);
      setConfirming(false);
      qc.invalidateQueries({ queryKey: ["projects"] });
      qc.invalidateQueries({ queryKey: ["clients"] });
    } catch (e) {
      setError(e);
      setConfirming(false);
      await check();
    } finally {
      setBusy(false);
    }
  }

  function reset() {
    setValues({});
    setDecision({});
    setRow(null);
    setChecked(false);
    setSaved(null);
  }

  if (saved) {
    const o = saved.results[0];
    return (
      <Card title="Client saved">
        <p className="text-sm">{ACTION_LABEL[o.action as keyof typeof ACTION_LABEL]}: <Link className="text-indigo-700 underline" to={`/clients/${o.client_id}`}>{o.client_name}</Link>
          {o.changed?.length ? ` (${o.changed.length} field(s) changed)` : ""}. The client is now available for analysis.</p>
        <div className="flex gap-2 mt-3">
          <Link to="/projects" className="text-sm text-indigo-700 underline">Go to projects to start an analysis →</Link>
          <span className="flex-1" />
          <Button variant="secondary" onClick={reset}>Add another client</Button>
        </div>
      </Card>
    );
  }

  const errors = checked ? row?.errors ?? {} : {};
  const groups: [string, FieldSpec[]][] = [["Client", schema.filter((f) => f.group === "client")], ["Project", schema.filter((f) => f.group === "project")]];
  return (
    <div className="space-y-5">
      <Card title="Client information">
        <form className="space-y-5" noValidate onSubmit={async (e) => { e.preventDefault(); const r = await check(); if (r?.status === "ready") setConfirming(true); }}>
          {groups.map(([title, fields]) => (
            <fieldset key={title}>
              <legend className="text-sm font-semibold text-slate-700 mb-2">{title}</legend>
              <ClientFields fields={fields} values={values} errors={errors} idPrefix="manual"
                onChange={(k, v) => { setValues({ ...values, [k]: v }); }}
                onBlur={() => checked && check(decision, true)} />
            </fieldset>
          ))}
          <p className="text-xs text-slate-500"><span className="text-rose-600">*</span> mandatory. The same fields and rules as the CSV upload.</p>
          <div className="flex gap-2">
            <Button type="submit" disabled={busy}>Check and save…</Button>
            <Button type="button" variant="secondary" disabled={busy} onClick={() => check()}>Check only</Button>
          </div>
          <ErrorText error={error} />
        </form>
      </Card>

      {row && row.status === "invalid" && (
        <Card title="Please correct the form">
          <ul className="text-sm text-rose-700 list-disc ml-5">{Object.entries(row.errors).map(([k, m]) => <li key={k}>{m}</li>)}</ul>
        </Card>
      )}

      {row && row.matches.length > 0 && row.status !== "invalid" && (
        <Card title="This client may already exist">
          <p className="text-sm text-slate-600 mb-2">Choose what to do. Nothing is saved until you confirm.</p>
          <MatchPanel row={row} disabled={busy} onDecision={(d) => { setDecision(d); check(d); }} />
          <div className="mt-3"><Button disabled={busy || row.status !== "ready"} onClick={() => setConfirming(true)}>Save…</Button></div>
        </Card>
      )}

      {row && row.status === "ready" && row.matches.length === 0 && !confirming && (
        <Card title="Ready to save"><p className="text-sm">No existing client matches. <Button className="ml-2" disabled={busy} onClick={() => setConfirming(true)}>Save…</Button></p></Card>
      )}

      {confirming && row && (
        <div className="fixed inset-0 z-50 bg-slate-900/40 flex items-start justify-center p-4" role="dialog" aria-modal="true" aria-label="Confirm">
          <div className="bg-white rounded-xl shadow-xl w-full max-w-lg mt-24 p-4 space-y-3 text-sm">
            <h3 className="font-semibold">Confirm</h3>
            {row.action === "add" || !row.target ? (
              <p>Add <b>{row.values.client_name}</b> ({row.values.project_url}) as a new client.</p>
            ) : (
              <>
                <p>{ACTION_LABEL[row.action!]} <b>{row.target.client_name}</b>{row.target.project_name ? ` / ${row.target.project_name}` : ""}. The client keeps its id.</p>
                {row.changes.length ? <ul className="list-disc ml-5">{row.changes.map((c) => <li key={c.field}>{c.label}: {c.before || "empty"} → {c.after || "empty"}</li>)}</ul> : <p>No field changes.</p>}
              </>
            )}
            <div className="flex justify-end gap-2">
              <Button variant="secondary" onClick={() => setConfirming(false)}>Back</Button>
              <Button disabled={busy} onClick={save}>Confirm and save</Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
