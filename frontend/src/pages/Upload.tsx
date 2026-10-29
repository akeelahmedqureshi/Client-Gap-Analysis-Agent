import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import CsvImport from "../components/CsvImport";
import ManualClient from "../components/ManualClient";
import { api } from "../lib/api";
import type { FieldSpec } from "../lib/intake";

/** Add clients: upload a CSV of many clients, or fill in the form for one. Both use the same client schema,
 * validation and duplicate rules, and nothing that needs a decision is processed without one. */
export default function UploadPage() {
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") === "manual" ? "manual" : "csv";
  const schema = useQuery({ queryKey: ["client-schema"], queryFn: () => api.get<{ fields: FieldSpec[] }>("/api/intake/schema") });
  const fields = schema.data?.fields ?? [];
  const options = [
    { key: "csv", title: "Upload CSV file", text: "Import many clients at once. You review missing information, duplicates and existing clients before anything is saved." },
    { key: "manual", title: "Add client manually", text: "Fill in a form for a single client. It is checked against your existing clients before it is saved." },
  ];
  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold">Add clients</h1>
      <div className="grid sm:grid-cols-2 gap-3" role="tablist">
        {options.map((o) => (
          <button key={o.key} role="tab" aria-selected={tab === o.key} onClick={() => setParams({ tab: o.key })}
            className={`text-left rounded-xl border p-4 transition ${tab === o.key ? "border-indigo-500 ring-2 ring-indigo-200 bg-white" : "border-slate-200 bg-white hover:border-slate-300"}`}>
            <div className="font-semibold">{o.title}</div>
            <div className="text-sm text-slate-600 mt-1">{o.text}</div>
          </button>
        ))}
      </div>
      {fields.length > 0 && (tab === "csv" ? <CsvImport schema={fields} /> : <ManualClient schema={fields} />)}
    </div>
  );
}
