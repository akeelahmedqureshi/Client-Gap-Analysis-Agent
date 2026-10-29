import type { FieldSpec } from "../lib/intake";

const INPUT_TYPE: Record<string, string> = { email: "email", url: "url", date: "text", text: "text", urls: "text", list: "text" };

/** Inputs for client fields, generated from the client schema (the same schema the CSV upload uses). */
export default function ClientFields({ fields, values, errors = {}, onChange, onBlur, only, idPrefix = "f" }: {
  fields: FieldSpec[];
  values: Record<string, string>;
  errors?: Record<string, string>;
  onChange: (key: string, value: string) => void;
  onBlur?: () => void;
  only?: string[];
  idPrefix?: string;
}) {
  const shown = only ? fields.filter((f) => only.includes(f.key)) : fields;
  return (
    <div className="grid gap-3 sm:grid-cols-2">
      {shown.map((f) => {
        const id = `${idPrefix}-${f.key}`;
        const err = errors[f.key];
        const cls = `w-full rounded-lg border px-2.5 py-1.5 text-sm ${err ? "border-rose-400 bg-rose-50" : "border-slate-300"}`;
        const common = {
          id, value: values[f.key] ?? "", "aria-invalid": !!err, "aria-describedby": `${id}-help`,
          onBlur, onChange: (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => onChange(f.key, e.target.value),
        };
        return (
          <div key={f.key} className={f.kind === "textarea" ? "sm:col-span-2" : ""}>
            <label htmlFor={id} className="block text-xs font-medium text-slate-600 mb-0.5">
              {f.label}{f.required && <span className="text-rose-600" title="Mandatory"> *</span>}
            </label>
            {f.kind === "textarea" ? (
              <textarea rows={3} maxLength={f.max_length} className={cls} {...common} />
            ) : (
              <input type={INPUT_TYPE[f.kind] ?? "text"} required={f.required} maxLength={f.max_length}
                placeholder={f.kind === "url" ? "https://…" : f.kind === "date" ? "YYYY-MM-DD" : ""} className={cls} {...common} />
            )}
            <p id={`${id}-help`} className={`text-xs mt-0.5 ${err ? "text-rose-700" : "text-slate-500"}`}>{err || f.help}</p>
          </div>
        );
      })}
    </div>
  );
}
