import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../lib/api";
import type { UploadResult } from "../lib/types";
import { Badge, Button, Card, ErrorText } from "../components/ui";

const DOMAIN_STATUS: Record<string, string> = {
  ok: "✓ reachable", redirected: "↪ redirects", parked: "parked / for sale", unreachable: "unreachable", blocked: "blocked",
};

export default function UploadPage() {
  const qc = useQueryClient();
  const [file, setFile] = useState<File | null>(null);
  const [upload, setUpload] = useState<UploadResult | null>(null);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [imported, setImported] = useState<{ created_projects: string[]; skipped_rows: number[] } | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  async function validate() {
    if (!file) return;
    setBusy(true);
    setError(null);
    setImported(null);
    try {
      const fd = new FormData();
      fd.append("file", file);
      const res = await api.post<UploadResult>("/api/uploads", fd);
      setUpload(res);
      setSelected(new Set(res.records.filter((r) => !r.duplicate_of_row).map((r) => r.row_number)));
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }

  async function checkDomains() {
    if (!upload) return;
    setBusy(true);
    setError(null);
    try {
      const res = await api.post<UploadResult>(`/api/uploads/${upload.id}/check-domains`);
      setUpload(res);
      // Rows whose site is parked, unreachable or blocked are deselected; the person can still tick them.
      const bad = new Set(res.records.filter((r) => ["parked", "unreachable", "blocked"].includes(r.domain_check?.status ?? "")).map((r) => r.row_number));
      setSelected(new Set([...selected].filter((n) => !bad.has(n))));
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }

  async function doImport() {
    if (!upload) return;
    setBusy(true);
    try {
      const res = await api.post<{ created_projects: string[]; skipped_rows: number[] }>(
        `/api/uploads/${upload.id}/import`, { rows: [...selected], include_duplicates: true });
      setImported(res);
      qc.invalidateQueries({ queryKey: ["projects"] });
      qc.invalidateQueries({ queryKey: ["clients"] });
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold">Upload client CSV</h1>
      <Card title="1. Choose file">
        <p className="text-sm text-slate-500 mb-3">
          Columns are detected automatically (e.g. Client Name, Client Email, Project Name, Project URL, Description,
          Industry, Technology, Repository URL, Existing Features, Notes). Missing fields are inferred where possible.
        </p>
        <div className="flex gap-3 items-center">
          <input type="file" accept=".csv,text/csv" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
          <Button onClick={validate} disabled={!file || busy}>Validate &amp; preview</Button>
        </div>
        <ErrorText error={error} />
      </Card>

      {upload && (
        <Card
          title={<>2. Preview — {upload.filename} <Badge value={upload.valid ? "completed" : "failed"} className="ml-2" /></>}
          actions={
            <div className="flex gap-2">
              <Button variant="secondary" onClick={checkDomains} disabled={busy} title="Request each homepage once to find unreachable, redirecting or parked domains">
                {busy ? "Working…" : "Check domains"}
              </Button>
              <Button onClick={doImport} disabled={!upload.valid || busy || selected.size === 0}>Import {selected.size} project(s)</Button>
            </div>
          }
        >
          {upload.errors.map((e) => <p key={e} className="text-sm text-rose-700">✖ {e}</p>)}
          {upload.warnings.map((w) => <p key={w} className="text-sm text-amber-700">⚠ {w}</p>)}
          <p className="text-xs text-slate-500 my-2">
            Detected columns: {Object.entries(upload.column_mapping).map(([k, v]) => `${v} → ${k}`).join(", ")}
            {upload.unmapped_columns.length > 0 && ` · Ignored: ${upload.unmapped_columns.join(", ")}`}
          </p>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-left text-slate-500">
                <tr><th /><th>Row</th><th>Client</th><th>Domain</th><th>Project</th><th>URL</th><th>Repositories</th><th>Website</th><th>Issues</th></tr>
              </thead>
              <tbody>
                {upload.records.map((r) => (
                  <tr key={r.row_number} className="border-t align-top">
                    <td className="py-2">
                      <input
                        type="checkbox"
                        checked={selected.has(r.row_number)}
                        onChange={() => {
                          const s = new Set(selected);
                          if (s.has(r.row_number)) s.delete(r.row_number);
                          else s.add(r.row_number);
                          setSelected(s);
                        }}
                      />
                    </td>
                    <td>{r.row_number}</td>
                    <td>{r.client.name}</td>
                    <td>{r.client.domain ?? "—"}</td>
                    <td>{r.project.name}</td>
                    <td className="max-w-48 truncate">{r.project.url ?? "—"}</td>
                    <td className="text-xs">{[...r.sources.github, ...r.sources.gitlab].join(", ") || "—"}</td>
                    <td className="text-xs">
                      {r.domain_check ? (
                        <span title={r.domain_check.detail} className={r.domain_check.status === "ok" ? "text-emerald-700" : r.domain_check.status === "redirected" ? "text-amber-700" : "text-rose-700"}>
                          {DOMAIN_STATUS[r.domain_check.status]}{r.domain_check.status !== "ok" && r.domain_check.detail ? ` — ${r.domain_check.detail}` : ""}
                        </span>
                      ) : <span className="text-slate-400">not checked</span>}
                    </td>
                    <td className="text-xs text-amber-700">{r.issues.join("; ")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      {imported && (
        <Card title="3. Imported">
          <p className="text-sm">
            Created {imported.created_projects.length} project(s).{" "}
            <Link to="/projects" className="text-indigo-600 underline">Go to projects to start an analysis →</Link>
          </p>
        </Card>
      )}
    </div>
  );
}
