import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../lib/api";
import type { Project, Run } from "../lib/types";
import { Badge, Card, Empty } from "../components/ui";

export default function RunsPage() {
  const runs = useQuery({ queryKey: ["runs"], queryFn: () => api.get<Run[]>("/api/runs"), refetchInterval: 5000 });
  const projects = useQuery({ queryKey: ["projects"], queryFn: () => api.get<Project[]>("/api/projects") });
  const project = (id: string) => projects.data?.find((p) => p.id === id);

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold">Analysis runs</h1>
      <Card>
        {runs.data?.length ? (
          <table className="w-full text-sm">
            <thead className="text-left text-slate-500">
              <tr><th className="py-1">Run</th><th>Project</th><th>Status</th><th>Progress</th><th>Started</th></tr>
            </thead>
            <tbody>
              {runs.data.map((r) => {
                const done = r.agent_details.filter((a) => ["completed", "skipped"].includes(a.status)).length;
                return (
                  <tr key={r.run_id} className="border-t">
                    <td className="py-2"><Link className="text-indigo-600 underline font-mono text-xs" to={`/runs/${r.run_id}`}>{r.run_id}</Link></td>
                    <td>{project(r.project_id)?.name ?? r.project_id} <span className="text-slate-400">· {project(r.project_id)?.client_name}</span></td>
                    <td><Badge value={r.status} /></td>
                    <td>{done}/{r.agent_details.length} agents</td>
                    <td>{new Date(r.created_at).toLocaleString()}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        ) : (
          <Empty>No runs yet.</Empty>
        )}
      </Card>
    </div>
  );
}
