import type { Action, Decision, Match, ReviewRow } from "../lib/intake";
import { ACTION_HELP, ACTION_LABEL } from "../lib/intake";

const show = (v: string | null | undefined) => v || <span className="text-slate-400">—</span>;

/** Choose what to do with a record: the matching existing clients side by side with the incoming values,
 * the actions that are allowed, merge choices per conflicting field and the changes that will be made.
 * Nothing is pre-selected: the person picks the action. */
export default function MatchPanel({ row, onDecision, disabled }: {
  row: ReviewRow;
  onDecision: (d: Decision) => void;
  disabled?: boolean;
}) {
  const d = row.decision ?? {};
  const reachable = row.matches.filter((m) => m.accessible);
  const target: Match | undefined = reachable.find((m) => m.client_id === (d.target ?? (reachable.length === 1 ? reachable[0].client_id : "")));
  const set = (patch: Decision) => onDecision({ ...d, ...patch });
  const choose = (action: Action) => set({ action, ...(action !== "replace" ? { confirm_replace: false } : {}) });
  const existingAction = d.action === "update" || d.action === "replace" || d.action === "merge";

  return (
    <div className="space-y-3">
      {row.matches.map((m) => (
        <div key={m.client_id} className={`rounded-lg border p-2.5 text-sm ${target?.client_id === m.client_id ? "border-indigo-300 bg-indigo-50/40" : "border-slate-200"}`}>
          <div className="flex flex-wrap items-center gap-2">
            {reachable.length > 1 && m.accessible && (
              <input type="radio" name={`target-${row.row}`} aria-label={`Use ${m.client_name}`} disabled={disabled}
                checked={d.target === m.client_id} onChange={() => set({ target: m.client_id, merge: {} })} />
            )}
            <span className={`text-[10px] uppercase rounded px-1.5 py-0.5 ${m.kind === "confirmed" ? "bg-rose-100 text-rose-800" : "bg-amber-100 text-amber-800"}`}>
              {m.kind === "confirmed" ? "existing client" : "possible match"}
            </span>
            <span className="font-medium">{m.client_name ?? "Client you cannot access"}</span>
            {m.project_name && <span className="text-slate-500">· project {m.project_name}</span>}
            {m.scope === "client" && <span className="text-xs text-slate-500">(no matching project: changes apply to the client and the incoming project is added to it)</span>}
            <span className="text-xs text-slate-500">— {m.reasons.join(", ")}</span>
            {m.exact && <span className="text-xs text-emerald-700">identical to the incoming record</span>}
          </div>
          {m.note && <p className="text-xs text-slate-600 mt-1">{m.note}</p>}
        </div>
      ))}
      {reachable.length > 1 && !d.target && <p className="text-xs text-amber-800">Several existing clients match. Choose the one this record refers to before updating, replacing or merging.</p>}

      {target?.fields && (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="text-left text-xs text-slate-500 border-b">
              <tr><th className="py-1 pr-2">Field</th><th className="pr-2">Existing record</th><th className="pr-2">Incoming record</th>
                {d.action === "merge" && <th className="pr-2">Keep</th>}</tr>
            </thead>
            <tbody>
              {target.fields.map((f) => (
                <tr key={f.field} className={`border-t align-top ${f.conflict ? "bg-amber-50/60" : ""}`}>
                  <td className="py-1 pr-2 text-slate-600">{f.label}</td>
                  <td className="pr-2 break-all">{show(f.existing)}</td>
                  <td className={`pr-2 break-all ${!f.same ? "font-medium" : ""}`}>{show(f.incoming)}</td>
                  {d.action === "merge" && (
                    <td className="pr-2 whitespace-nowrap text-xs">
                      {f.conflict ? (["existing", "incoming"] as const).map((side) => (
                        <label key={side} className="mr-2"><input type="radio" name={`merge-${row.row}-${f.field}`} disabled={disabled}
                          checked={d.merge?.[f.field] === side} onChange={() => set({ merge: { ...(d.merge ?? {}), [f.field]: side } })} /> {side}</label>
                      )) : <span className="text-slate-400">{f.existing && f.incoming ? "same" : f.existing ? "existing" : "incoming"}</span>}
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
          <p className="text-xs text-slate-500 mt-1">Highlighted rows differ between the two records.</p>
        </div>
      )}

      <div className="flex flex-wrap gap-2" role="group" aria-label="Action">
        {row.allowed.map((a) => (
          <button key={a} type="button" disabled={disabled} title={ACTION_HELP[a]} onClick={() => choose(a)}
            className={`rounded-lg border px-3 py-1 text-sm ${d.action === a ? "bg-indigo-600 text-white border-indigo-600" : "bg-white hover:bg-slate-50 border-slate-300"}`}>
            {ACTION_LABEL[a]}
          </button>
        ))}
      </div>
      {d.action && <p className="text-xs text-slate-600">{ACTION_HELP[d.action]}</p>}
      {d.action === "replace" && (
        <label className="flex items-start gap-2 text-sm text-rose-800 bg-rose-50 border border-rose-200 rounded-lg p-2">
          <input type="checkbox" disabled={disabled} checked={!!d.confirm_replace} onChange={(e) => set({ confirm_replace: e.target.checked })} />
          I understand that the existing values are overwritten, and fields the incoming record leaves empty are cleared.
        </label>
      )}
      {existingAction && row.changes.length > 0 && (
        <div className="text-sm">
          <div className="text-xs font-semibold text-slate-500 uppercase tracking-wide">Changes that will be made</div>
          <ul className="mt-1 space-y-0.5">
            {row.changes.map((c) => (
              <li key={c.field}><span className="text-slate-600">{c.label}:</span> <span className="line-through text-slate-400">{c.before || "empty"}</span> → <span className="font-medium">{c.after || "empty"}</span></li>
            ))}
          </ul>
        </div>
      )}
      {existingAction && row.target && row.changes.length === 0 && row.status === "ready" && <p className="text-xs text-slate-500">No field changes.</p>}
      {row.needs.length > 0 && <ul className="text-xs text-amber-800 list-disc ml-4">{row.needs.map((n) => <li key={n}>{n}</li>)}</ul>}
    </div>
  );
}
