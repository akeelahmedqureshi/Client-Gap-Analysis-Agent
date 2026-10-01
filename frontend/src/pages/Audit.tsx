import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../lib/api";
import type { AuditEntry } from "../lib/types";
import { Button, Card, Empty } from "../components/ui";

const PAGE = 100;
const FILTERS = ["", "auth.", "user.", "project.", "upload.", "run.", "approval.", "connection.", "org."];

export default function AuditPage() {
  const [action, setAction] = useState("");
  const [page, setPage] = useState(0);
  const log = useQuery({
    queryKey: ["audit", action, page],
    queryFn: () => api.get<AuditEntry[]>(`/api/audit?limit=${PAGE}&offset=${page * PAGE}${action ? `&action=${action}` : ""}`),
  });
  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold">Audit log</h1>
      <Card
        title="Security-relevant activity"
        actions={
          <select value={action} onChange={(e) => { setAction(e.target.value); setPage(0); }} className="border rounded px-2 py-1 text-sm">
            {FILTERS.map((f) => <option key={f} value={f}>{f ? f.replace(".", "") : "All actions"}</option>)}
          </select>
        }
      >
        {log.data?.length ? (
          <table className="w-full text-sm">
            <thead className="text-left text-slate-500">
              <tr><th className="py-1">Time</th><th>User</th><th>Action</th><th>Target</th><th>Details</th><th>IP</th></tr>
            </thead>
            <tbody>
              {log.data.map((e) => (
                <tr key={e.id} className="border-t align-top">
                  <td className="py-1.5 whitespace-nowrap">{new Date(e.created_at).toLocaleString()}</td>
                  <td>{e.user_email ?? "—"}</td>
                  <td><code className="text-xs">{e.action}</code></td>
                  <td className="text-xs">{e.target_type ? `${e.target_type} ${e.target_id ?? ""}` : "—"}</td>
                  <td className="text-xs text-slate-600 max-w-md break-all">{Object.keys(e.details).length ? JSON.stringify(e.details) : ""}</td>
                  <td className="text-xs">{e.ip ?? ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <Empty>No entries.</Empty>
        )}
        <div className="flex justify-end gap-2 mt-3">
          <Button variant="secondary" disabled={page === 0} onClick={() => setPage(page - 1)}>Newer</Button>
          <Button variant="secondary" disabled={(log.data?.length ?? 0) < PAGE} onClick={() => setPage(page + 1)}>Older</Button>
        </div>
      </Card>
    </div>
  );
}
