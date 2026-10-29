import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "../lib/api";
import type { Action, Decision, FieldSpec, ImportResult, Plan, Review, ReviewRow } from "../lib/intake";
import { ACTION_HELP, ACTION_LABEL, STATUS_LABEL, STATUS_STYLE, emptyPlan } from "../lib/intake";
import ClientFields from "./ClientFields";
import MatchPanel from "./MatchPanel";
import { Button, Card, Empty, ErrorText } from "./ui";

interface UploadInfo {
  id: string;
  filename: string;
  valid: boolean;
  errors: string[];
  warnings: string[];
  plan: Partial<Plan>;
}

interface Bulk {
  title: string;
  action: Action;
  eligible: ReviewRow[];
  ineligible: { row: ReviewRow; reason: string }[];
  note?: string;
}

const label = (r: ReviewRow) => `Row ${r.row} · ${r.values.client_name || "(no client name)"}`;

function StatusChip({ r }: { r: ReviewRow }) {
  return <span className={`text-[11px] rounded-full px-2 py-0.5 whitespace-nowrap ${STATUS_STYLE[r.status]}`}>{STATUS_LABEL[r.status]}{r.action && r.status !== "skipped" && r.status !== "invalid" ? ` · ${ACTION_LABEL[r.action].toLowerCase()}` : ""}</span>;
}

function Modal({ title, children, onClose }: { title: string; children: React.ReactNode; onClose: () => void }) {
  useEffect(() => {
    const esc = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", esc);
    return () => window.removeEventListener("keydown", esc);
  }, [onClose]);
  return (
    <div className="fixed inset-0 z-50 bg-slate-900/40 flex items-start justify-center p-4 overflow-y-auto" role="dialog" aria-modal="true" aria-label={title}>
      <div className="bg-white rounded-xl shadow-xl w-full max-w-2xl mt-16">
        <div className="px-4 py-3 border-b font-semibold flex justify-between">{title}<button aria-label="Close" onClick={onClose}>✕</button></div>
        <div className="p-4 space-y-3 text-sm">{children}</div>
      </div>
    </div>
  );
}

/** CSV upload: columns, corrections, duplicates in the file, existing clients, review, import. No record that needs a
 * decision is ever processed without one. */
export default function CsvImport({ schema }: { schema: FieldSpec[] }) {
  const qc = useQueryClient();
  const [params, setParams] = useSearchParams();
  const [file, setFile] = useState<File | null>(null);
  const [upload, setUpload] = useState<UploadInfo | null>(null);
  const [plan, setPlan] = useState<Plan>(emptyPlan());
  const [review, setReview] = useState<Review | null>(null);
  const [result, setResult] = useState<ImportResult | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [bulk, setBulk] = useState<Bulk | null>(null);
  const [bulkConfirm, setBulkConfirm] = useState(false);
  const [bulkReport, setBulkReport] = useState<string | null>(null);
  const [confirmImport, setConfirmImport] = useState(false);
  const resultRef = useRef<HTMLDivElement>(null);
  useEffect(() => { if (result) resultRef.current?.scrollIntoView({ behavior: "smooth" }); }, [result]);

  async function runReview(id: string, p: Plan) {
    setBusy(true);
    setError(null);
    try {
      setReview(await api.post<Review>(`/api/uploads/${id}/review`, p));
      setPlan(p);
      return true;
    } catch (e) {
      setError(e);
      return false;
    } finally {
      setBusy(false);
    }
  }

  async function open(id: string) {
    setBusy(true);
    try {
      const up = await api.get<UploadInfo>(`/api/uploads/${id}`);
      setUpload(up);
      setResult(null);
      if (up.valid) await runReview(id, { ...emptyPlan(), ...(up.plan ?? {}), allow_partial: false });
      else setReview(null);
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    const id = params.get("upload");
    if (id && id !== upload?.id) open(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [params]);

  async function start() {
    if (!file) return;
    setBusy(true);
    setError(null);
    try {
      const fd = new FormData();
      fd.append("file", file);
      const up = await api.post<UploadInfo>("/api/uploads", fd);
      setParams({ tab: "csv", upload: up.id });
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }

  const update = (p: Plan) => upload && runReview(upload.id, p);
  const decide = (row: number, d: Decision) => update({ ...plan, decisions: { ...plan.decisions, [row]: d } });
  const decideMany = (rows: number[], f: (r: number) => Decision) =>
    update({ ...plan, decisions: { ...plan.decisions, ...Object.fromEntries(rows.map((r) => [r, f(r)])) } });
  const undecide = (row: number) => {
    const decisions = { ...plan.decisions };
    delete decisions[row];
    return update({ ...plan, decisions });
  };

  const rows = review?.rows ?? [];
  const open_ = rows.filter((r) => r.status !== "imported");
  const toFix = open_.filter((r) => Object.keys(r.errors).length > 0);
  const dupRows = open_.filter((r) => r.group != null);
  const matched = open_.filter((r) => r.matches.length > 0);

  async function doImport() {
    if (!upload) return;
    setBusy(true);
    setError(null);
    try {
      const res = await api.post<ImportResult>(`/api/uploads/${upload.id}/import`, plan);
      setResult(res);
      setConfirmImport(false);
      qc.invalidateQueries({ queryKey: ["projects"] });
      qc.invalidateQueries({ queryKey: ["clients"] });
      await runReview(upload.id, { ...plan, allow_partial: false });
    } catch (e) {
      setError(e);
      setConfirmImport(false);
      await runReview(upload.id, plan);
    } finally {
      setBusy(false);
    }
  }

  function openBulk(action: Action, scope: ReviewRow[], title: string, note?: string) {
    const eligible: ReviewRow[] = [];
    const ineligible: { row: ReviewRow; reason: string }[] = [];
    for (const r of scope) {
      if (r.status === "imported") continue;
      if (!r.allowed.includes(action)) ineligible.push({ row: r, reason: `${ACTION_LABEL[action]} is not available for this record` });
      else if (action !== "skip" && Object.keys(r.errors).length) ineligible.push({ row: r, reason: "has fields to correct first" });
      else eligible.push(r);
    }
    setBulk({ title, action, eligible, ineligible, note });
    setBulkConfirm(false);
  }

  async function applyBulk() {
    if (!bulk) return;
    const { action, eligible } = bulk;
    const ok = await decideMany(eligible.map((r) => r.row), (n) => ({
      ...(plan.decisions[n] ?? {}), action, ...(action === "replace" ? { confirm_replace: true } : {}),
    }));
    setBulk(null);
    if (ok) setBulkReport(`${ACTION_LABEL[action]} applied to ${eligible.length} record(s).`);
  }

  // After a bulk action, list the records that still need an individual decision.
  const stillOpen = useMemo(() => rows.filter((r) => ["pending", "conflict", "invalid"].includes(r.status)), [rows]);

  const fieldLabel = (k: string) => schema.find((f) => f.key === k)?.label ?? k;

  return (
    <div className="space-y-5">
      <Card title="1. Choose a CSV file">
        <p className="text-sm text-slate-600 mb-3">
          One row per client project. Mandatory columns: <b>{schema.filter((f) => f.required).map((f) => f.label).join(", ")}</b>.
          Other recognised columns: {schema.filter((f) => !f.required).map((f) => f.label).join(", ")}.{" "}
          <a className="underline text-indigo-700" href="/client-template.csv" download>Download the CSV template</a>.
          Nothing is imported until you have reviewed the records and confirmed.
        </p>
        <div className="flex flex-wrap gap-3 items-center">
          <input type="file" accept=".csv,text/csv" aria-label="CSV file" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
          <Button onClick={start} disabled={!file || busy}>Upload and review</Button>
          {upload && <span className="text-sm text-slate-500">Reviewing <b>{upload.filename}</b></span>}
        </div>
        {upload && !upload.valid && <div className="mt-2">{upload.errors.map((e) => <p key={e} className="text-sm text-rose-700">✖ {e}</p>)}</div>}
        <ErrorText error={error} />
      </Card>

      {review && upload && (
        <>
          <Card title="2. Columns">
            <p className="text-xs text-slate-500 mb-2">Each CSV column is matched to a client field. Change a match if it is wrong; unmatched columns are ignored.</p>
            {review.warnings.map((w) => <p key={w} className="text-sm text-amber-700">⚠ {w}</p>)}
            <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-2 mt-2">
              {review.headers.map((h) => {
                const current = Object.entries(review.column_mapping).find(([, v]) => v === h)?.[0] ?? "";
                return (
                  <label key={h} className="text-sm flex items-center gap-2">
                    <span className="w-40 truncate" title={h}>“{h}”</span>
                    <select className="border rounded px-1.5 py-1 text-sm flex-1" value={current} disabled={busy}
                      onChange={(e) => {
                        const mapping = Object.fromEntries(Object.entries(review.column_mapping).filter(([k, v]) => v !== h && k !== e.target.value));
                        if (e.target.value) mapping[e.target.value] = h;
                        update({ ...plan, mapping });
                      }}>
                      <option value="">— ignore —</option>
                      {schema.map((f) => <option key={f.key} value={f.key}>{f.label}{f.required ? " *" : ""}</option>)}
                    </select>
                  </label>
                );
              })}
            </div>
          </Card>

          <Card title={`3. Missing or invalid information (${toFix.length})`}
            actions={toFix.length > 0 && (
              <div className="flex gap-2">
                <Button variant="secondary" disabled={busy} onClick={() => openBulk("skip", toFix.filter((r) => r.missing.length), "Skip all rows with missing mandatory information")}>Skip all with missing mandatory info</Button>
                <Button variant="secondary" disabled={busy} onClick={() => openBulk("skip", toFix, "Skip all rows that need correction")}>Skip all rows to correct</Button>
              </div>
            )}>
            {toFix.length === 0 ? <Empty>Every row has its mandatory information and valid values.</Empty> : (
              <div className="space-y-3">
                <p className="text-xs text-slate-500">Fill in or correct each row (it is validated again), or skip it. Mandatory fields are never filled in automatically.</p>
                {toFix.map((r) => <FixRow key={r.row} r={r} schema={schema} busy={busy}
                  onSave={(vals) => update({ ...plan, edits: { ...plan.edits, [r.row]: { ...(plan.edits[r.row] ?? {}), ...vals } } })}
                  onSkip={() => decide(r.row, { action: "skip" })} onUnskip={() => undecide(r.row)} fieldLabel={fieldLabel} />)}
              </div>
            )}
          </Card>

          <Card title={`4. Duplicates within the file (${review.groups.length} group${review.groups.length === 1 ? "" : "s"})`}
            actions={dupRows.length > 0 && (
              <div className="flex flex-wrap gap-2">
                <Button variant="secondary" disabled={busy} onClick={() => openBulk("skip", dupRows, "Skip all duplicates", "Every row of every duplicate group is skipped. You can still add individual rows afterwards.")}>Skip all duplicates</Button>
                <Button variant="secondary" disabled={busy} onClick={() => openBulk("skip", dupRows.filter((r) => review.groups.find((g) => g.id === r.group)?.rows[0] !== r.row), "Keep the first row of each group, skip the others", "The later rows of each group are skipped; the first row of each group still needs your decision.")}>Skip all but the first of each group</Button>
              </div>
            )}>
            {review.groups.length === 0 ? <Empty>No duplicate records in this file.</Empty> : (
              <div className="space-y-4">
                <p className="text-xs text-slate-500"><b>Confirmed</b>: identical rows, the same website, client ID, or client and project name. <b>Potential</b>: the same client name or domain; they may be separate records. Every row needs Add or Skip.</p>
                {review.groups.map((g) => (
                  <DupGroup key={g.id} g={g} rows={rows.filter((r) => g.rows.includes(r.row))} busy={busy} fieldLabel={fieldLabel}
                    onDecide={(row, action) => decide(row, { action })}
                    onBulk={(action, sel) => openBulk(action, sel, `${ACTION_LABEL[action]} selected rows`)} />
                ))}
              </div>
            )}
          </Card>

          <ExistingSection rows={matched} plan={plan} busy={busy} onDecide={decide} openBulk={openBulk} />

          <Card title="6. Review and import">
            <Summary review={review} />
            {review.conflicts.length > 0 && (
              <div className="mt-3 rounded border border-rose-200 bg-rose-50 p-2 text-sm text-rose-800">
                {review.conflicts.map((c) => <p key={c.message + c.rows.join()}>Rows {c.rows.join(", ")}: {c.message}</p>)}
              </div>
            )}
            <AllRows rows={rows} />
            <div className="mt-3 flex flex-wrap items-center gap-3">
              {review.summary.pending > 0 && review.summary.ready > 0 && (
                <label className="text-sm flex items-center gap-2">
                  <input type="checkbox" checked={plan.allow_partial} disabled={busy} onChange={(e) => update({ ...plan, allow_partial: e.target.checked })} />
                  Import the {review.summary.ready} ready record(s) now; the {review.summary.pending} record(s) still needing a decision stay pending.
                </label>
              )}
              <Button disabled={busy || !review.summary.can_import} onClick={() => setConfirmImport(true)}>Review and confirm import…</Button>
              {!review.summary.can_import && <span className="text-xs text-slate-500">
                {review.summary.ready === 0 ? "No record is ready to import." : `${review.summary.pending} record(s) still need a decision or a correction.`}</span>}
            </div>
          </Card>
        </>
      )}

      {result && (
        <div ref={resultRef}><Card title="7. Result">
          <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-8 gap-2 text-sm">
            {([["Added", result.summary.added], ["Updated", result.summary.updated], ["Replaced", result.summary.replaced],
              ["Merged", result.summary.merged], ["Skipped", result.summary.skipped], ["Failed validation", result.summary.failed_validation],
              ["Still pending", result.summary.pending], ["Failed", result.summary.failed]] as [string, number][]).map(([k, v]) => (
              <div key={k} className="rounded border p-2"><div className="text-xs text-slate-500">{k}</div><div className="text-lg font-semibold">{v}</div></div>
            ))}
          </div>
          {result.failed.length > 0 && (
            <ul className="mt-2 text-sm text-rose-700 list-disc ml-5">{result.failed.map((f) => <li key={f.row}>Row {f.row} ({ACTION_LABEL[f.action as Action] ?? f.action}): {f.reason}</li>)}</ul>
          )}
          {result.results.length > 0 && (
            <ul className="mt-2 text-sm list-disc ml-5">{result.results.map((o) => (
              <li key={o.row}>Row {o.row}: {ACTION_LABEL[o.action as Action]} — <Link className="text-indigo-700 underline" to={`/clients/${o.client_id}`}>{o.client_name}</Link>
                {o.changed?.length ? <span className="text-xs text-slate-500"> ({o.changed.map(fieldLabel).join(", ")} changed)</span> : null}</li>))}</ul>
          )}
          <p className="text-sm mt-2"><Link to="/projects" className="text-indigo-600 underline">Go to projects to start an analysis →</Link></p>
        </Card></div>
      )}

      {bulkReport && (
        <div className="fixed bottom-4 right-4 z-40 max-w-md rounded-lg border border-slate-300 bg-white shadow-lg p-3 text-sm">
          <p>{bulkReport}</p>
          {stillOpen.length > 0 && <p className="mt-1 text-amber-800">{stillOpen.length} record(s) still need an individual decision or correction: rows {stillOpen.slice(0, 15).map((r) => r.row).join(", ")}{stillOpen.length > 15 ? "…" : ""}.</p>}
          <button className="text-xs underline mt-1" onClick={() => setBulkReport(null)}>Dismiss</button>
        </div>
      )}

      {bulk && (
        <Modal title={bulk.title} onClose={() => setBulk(null)}>
          <p><b>{bulk.eligible.length}</b> record(s) will be set to <b>{ACTION_LABEL[bulk.action]}</b>. {ACTION_HELP[bulk.action]}</p>
          {bulk.note && <p className="text-slate-600">{bulk.note}</p>}
          {bulk.eligible.length > 0 && <p className="text-xs text-slate-600">Rows: {bulk.eligible.map((r) => r.row).join(", ")}</p>}
          {bulk.action === "merge" && <p className="text-amber-800">Records whose fields conflict still need you to choose the value to keep, field by field; they stay pending until you do.</p>}
          {(bulk.action === "update" || bulk.action === "replace" || bulk.action === "merge") && bulk.eligible.some((r) => r.matches.filter((m) => m.accessible).length > 1 && !plan.decisions[r.row]?.target) &&
            <p className="text-amber-800">Records that match several existing clients stay pending until you choose which client they refer to.</p>}
          {bulk.ineligible.length > 0 && (
            <div className="text-amber-800"><p>Not changed (they need individual resolution):</p>
              <ul className="list-disc ml-5 text-xs">{bulk.ineligible.map((x) => <li key={x.row.row}>{label(x.row)}: {x.reason}</li>)}</ul></div>
          )}
          {bulk.action === "replace" && bulk.eligible.length > 0 && (
            <label className="flex items-start gap-2 text-rose-800 bg-rose-50 border border-rose-200 rounded-lg p-2">
              <input type="checkbox" checked={bulkConfirm} onChange={(e) => setBulkConfirm(e.target.checked)} />
              I understand that the existing values of these {bulk.eligible.length} record(s) are overwritten, and fields left empty in the incoming records are cleared.
            </label>
          )}
          <div className="flex justify-end gap-2">
            <Button variant="secondary" onClick={() => setBulk(null)}>Cancel</Button>
            <Button disabled={busy || bulk.eligible.length === 0 || (bulk.action === "replace" && !bulkConfirm)} onClick={applyBulk}>Apply to {bulk.eligible.length} record(s)</Button>
          </div>
        </Modal>
      )}

      {confirmImport && review && (
        <Modal title="Confirm the import" onClose={() => setConfirmImport(false)}>
          <p>The following will be applied now:</p>
          <ul className="list-disc ml-5">
            <li>{review.summary.selected_add} record(s) added</li>
            <li>{review.summary.selected_update} existing record(s) updated</li>
            <li>{review.summary.selected_replace} existing record(s) replaced</li>
            <li>{review.summary.selected_merge} record(s) merged into existing ones</li>
            <li>{review.summary.selected_skip} record(s) skipped</li>
            {review.summary.pending > 0 && <li className="text-amber-800">{review.summary.pending} record(s) still need a decision and will not be imported</li>}
          </ul>
          <p className="text-xs text-slate-500">Existing clients keep their ids. Every change is recorded in the audit log with the values before and after.</p>
          <div className="flex justify-end gap-2">
            <Button variant="secondary" onClick={() => setConfirmImport(false)}>Back</Button>
            <Button disabled={busy} onClick={doImport}>Confirm and import</Button>
          </div>
        </Modal>
      )}
    </div>
  );
}

function FixRow({ r, schema, busy, onSave, onSkip, onUnskip, fieldLabel }: {
  r: ReviewRow; schema: FieldSpec[]; busy: boolean; onSave: (v: Record<string, string>) => void;
  onSkip: () => void; onUnskip: () => void; fieldLabel: (k: string) => string;
}) {
  const [editing, setEditing] = useState(false);
  const fields = Object.keys(r.errors);
  const [vals, setVals] = useState<Record<string, string>>({});
  const skipped = r.status === "skipped";
  return (
    <div className={`rounded-lg border p-3 ${skipped ? "border-slate-200 bg-slate-50" : "border-rose-200"}`}>
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span className="font-medium">{label(r)}</span>
        <span className="text-slate-500">{r.values.project_url || r.values.project_name || ""}</span>
        <StatusChip r={r} />
        <span className="flex-1" />
        {!skipped && <Button variant="secondary" disabled={busy} onClick={() => { setVals(Object.fromEntries(fields.map((k) => [k, r.values[k] ?? ""]))); setEditing(!editing); }}>{editing ? "Close" : "Fill in / correct"}</Button>}
        {skipped ? <Button variant="secondary" disabled={busy} onClick={onUnskip}>Undo skip</Button> : <Button variant="secondary" disabled={busy} onClick={onSkip}>Skip row</Button>}
      </div>
      <ul className="mt-1 text-xs text-rose-700">
        {fields.map((k) => <li key={k}><b>{fieldLabel(k)}</b>: {r.missing.includes(k) ? "missing (mandatory)" : r.errors[k]}</li>)}
      </ul>
      {editing && !skipped && (
        <div className="mt-2 space-y-2">
          <ClientFields fields={schema} only={fields} values={vals} errors={r.errors} idPrefix={`row${r.row}`} onChange={(k, v) => setVals({ ...vals, [k]: v })} />
          <Button disabled={busy} onClick={() => { onSave(vals); setEditing(false); }}>Validate correction</Button>
        </div>
      )}
    </div>
  );
}

function DupGroup({ g, rows, busy, onDecide, onBulk, fieldLabel }: {
  g: Review["groups"][number]; rows: ReviewRow[]; busy: boolean; fieldLabel: (k: string) => string;
  onDecide: (row: number, action: Action) => void; onBulk: (action: Action, rows: ReviewRow[]) => void;
}) {
  const [sel, setSel] = useState<Set<number>>(new Set());
  const conflictFields = g.conflicts.map((c) => c.field);
  const cols = Array.from(new Set(["client_name", "project_name", "project_url", ...conflictFields]));
  return (
    <div className="rounded-lg border border-slate-200 p-3">
      <div className="flex flex-wrap items-center gap-2 text-sm mb-2">
        <span className={`text-[10px] uppercase rounded px-1.5 py-0.5 ${g.kind === "confirmed" ? "bg-rose-100 text-rose-800" : "bg-amber-100 text-amber-800"}`}>{g.kind} duplicate</span>
        <span className="text-slate-600">{g.links.map((l) => `${l.reason} (rows ${l.rows.join(", ")})`).join("; ")}</span>
        <span className="flex-1" />
        <Button variant="secondary" disabled={busy || sel.size === 0} onClick={() => onBulk("add", rows.filter((r) => sel.has(r.row)))}>Add selected</Button>
        <Button variant="secondary" disabled={busy || sel.size === 0} onClick={() => onBulk("skip", rows.filter((r) => sel.has(r.row)))}>Skip selected</Button>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="text-left text-xs text-slate-500"><tr><th /><th className="pr-2">Row</th>{cols.map((c) => <th key={c} className="pr-2">{fieldLabel(c)}</th>)}<th className="pr-2">Status</th><th>Decision</th></tr></thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.row} className="border-t align-top">
                <td className="py-1 pr-1"><input type="checkbox" aria-label={`Select row ${r.row}`} checked={sel.has(r.row)} onChange={() => { const s = new Set(sel); if (s.has(r.row)) s.delete(r.row); else s.add(r.row); setSel(s); }} /></td>
                <td className="pr-2">{r.row}</td>
                {cols.map((c) => <td key={c} className={`pr-2 break-all ${conflictFields.includes(c) ? "bg-amber-50 font-medium" : ""}`}>{r.values[c] || <span className="text-slate-400">—</span>}</td>)}
                <td className="pr-2"><StatusChip r={r} /></td>
                <td className="whitespace-nowrap">
                  {r.matches.length > 0 ? (
                    <span className="text-xs text-slate-600">matches an existing client — decide in section 5</span>
                  ) : (["add", "skip"] as const).map((a) => (
                    <label key={a} className="mr-2 text-xs"><input type="radio" name={`dup-${r.row}`} disabled={busy || r.status === "invalid" && a === "add"} checked={r.decision.action === a} onChange={() => onDecide(r.row, a)} /> {ACTION_LABEL[a]}</label>
                  ))}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {g.conflicts.length > 0 && <p className="text-xs text-slate-500 mt-1">Highlighted values differ between the rows ({g.conflicts.map((c) => c.label).join(", ")}). Rows are never merged automatically: add the ones to keep and skip the others.</p>}
    </div>
  );
}

function ExistingSection({ rows, plan, busy, onDecide, openBulk }: {
  rows: ReviewRow[]; plan: Plan; busy: boolean; onDecide: (row: number, d: Decision) => void;
  openBulk: (action: Action, scope: ReviewRow[], title: string, note?: string) => void;
}) {
  const [sel, setSel] = useState<Set<number>>(new Set());
  const selected = rows.filter((r) => sel.has(r.row));
  const existing = rows.filter((r) => r.matches.some((m) => m.accessible));
  const actions: Action[] = ["update", "replace", "merge", "skip"];
  return (
    <Card title={`5. Matches with existing clients (${rows.length})`}>
      {rows.length === 0 ? <Empty>No record matches an existing client.</Empty> : (
        <div className="space-y-3">
          <p className="text-xs text-slate-500"><b>Existing client</b>: the same client ID, website, or domain/name key; adding it again would create a duplicate. <b>Possible match</b>: a similar name or a related domain; it may be a different company.</p>
          <div className="flex flex-wrap gap-2 items-center rounded-lg bg-slate-50 p-2">
            <span className="text-sm text-slate-600">{selected.length} selected</span>
            {actions.map((a) => <Button key={a} variant="secondary" disabled={busy || selected.length === 0} onClick={() => openBulk(a, selected, `${ACTION_LABEL[a]} — selected records`)}>{ACTION_LABEL[a]} selected</Button>)}
            <span className="w-px h-6 bg-slate-300" />
            {actions.map((a) => <Button key={`all-${a}`} variant="secondary" disabled={busy} onClick={() => openBulk(a, a === "skip" ? rows : existing, `${ACTION_LABEL[a]} — all matching records`)}>{a === "skip" ? "Skip all" : `${ACTION_LABEL[a].replace(" existing", "")} all`}</Button>)}
          </div>
          {rows.map((r) => (
            <div key={r.row} className="rounded-lg border border-slate-200 p-3">
              <div className="flex flex-wrap items-center gap-2 mb-2 text-sm">
                <input type="checkbox" aria-label={`Select row ${r.row}`} checked={sel.has(r.row)} onChange={() => { const s = new Set(sel); if (s.has(r.row)) s.delete(r.row); else s.add(r.row); setSel(s); }} />
                <span className="font-medium">{label(r)}</span>
                <span className="text-slate-500 break-all">{r.values.project_url}</span>
                <StatusChip r={r} />
                {Object.keys(r.errors).length > 0 && <span className="text-xs text-rose-700">correct the row in section 3 first</span>}
              </div>
              <MatchPanel row={r} disabled={busy} onDecision={(d) => onDecide(r.row, { ...(plan.decisions[r.row] ?? {}), ...d })} />
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}

function Summary({ review }: { review: Review }) {
  const s = review.summary;
  const items: [string, number, string?][] = [
    ["Rows detected", s.total], ["Valid", s.valid], ["Missing mandatory fields", s.missing_mandatory, "text-rose-700"],
    ["Invalid data", s.invalid, "text-rose-700"], ["Duplicates in the file", s.csv_duplicates], ["Matching existing clients", s.existing_matches],
    ["Possible matches to verify", s.potential_matches], ["To add", s.selected_add, "text-emerald-700"], ["To update", s.selected_update],
    ["To replace", s.selected_replace], ["To merge", s.selected_merge], ["To skip", s.selected_skip],
    ["Awaiting a decision", s.pending, "text-amber-700"], ["Imported earlier", s.imported_before],
  ];
  return (
    <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-7 gap-2 text-sm">
      {items.map(([k, v, cls]) => (
        <div key={k} className="rounded border p-2"><div className="text-xs text-slate-500">{k}</div><div className={`text-lg font-semibold ${v ? cls ?? "" : ""}`}>{v}</div></div>
      ))}
    </div>
  );
}

function AllRows({ rows }: { rows: ReviewRow[] }) {
  const [onlyOpen, setOnlyOpen] = useState(false);
  const shown = rows.filter((r) => !onlyOpen || ["pending", "invalid", "conflict"].includes(r.status));
  return (
    <div className="mt-3">
      <label className="text-xs flex items-center gap-1 mb-1"><input type="checkbox" checked={onlyOpen} onChange={(e) => setOnlyOpen(e.target.checked)} /> Only records awaiting a decision</label>
      <div className="max-h-96 overflow-y-auto border rounded">
        <table className="w-full text-sm">
          <thead className="text-left text-xs text-slate-500 sticky top-0 bg-white"><tr><th className="px-2 py-1">Row</th><th className="pr-2">Client</th><th className="pr-2">Website</th><th className="pr-2">Status</th><th>What is needed</th></tr></thead>
          <tbody>
            {shown.map((r) => (
              <tr key={r.row} className="border-t align-top">
                <td className="px-2 py-1">{r.row}</td>
                <td className="pr-2">{r.values.client_name || <span className="text-rose-700">missing</span>}{r.target && <div className="text-xs text-slate-500">→ {r.target.client_name}</div>}</td>
                <td className="pr-2 break-all">{r.values.project_url || <span className="text-rose-700">missing</span>}</td>
                <td className="pr-2"><StatusChip r={r} /></td>
                <td className="text-xs text-amber-800">{r.needs.join(" ")}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
