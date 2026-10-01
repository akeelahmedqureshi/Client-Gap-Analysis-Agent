import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "../lib/api";
import type { Project, User } from "../lib/types";
import { Badge, Button, Card, Empty } from "../components/ui";
import StartRunDialog from "../components/StartRunDialog";
import ProjectAccessDialog from "../components/ProjectAccessDialog";
import RepositoriesDialog from "../components/RepositoriesDialog";

export default function ProjectsPage() {
  const [params] = useSearchParams();
  const clientId = params.get("client_id");
  const projects = useQuery({
    queryKey: ["projects", clientId],
    queryFn: () => api.get<Project[]>(`/api/projects${clientId ? `?client_id=${clientId}` : ""}`),
  });
  const [starting, setStarting] = useState<Project | null>(null);
  const [editingAccess, setEditingAccess] = useState<Project | null>(null);
  const [editingRepos, setEditingRepos] = useState<Project | null>(null);
  const me = useQuery({ queryKey: ["me"], queryFn: () => api.get<User>("/api/auth/me") });
  const isAdmin = me.data?.role === "admin";

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold">Projects</h1>
      <Card>
        {projects.data?.length ? (
          <table className="w-full text-sm">
            <thead className="text-left text-slate-500">
              <tr><th className="py-1">Project</th><th>Client</th><th>Technology</th><th>Sources</th><th>Latest analysis</th><th /></tr>
            </thead>
            <tbody>
              {projects.data.map((p) => {
                const s = p.record.sources;
                return (
                  <tr key={p.id} className="border-t align-top">
                    <td className="py-2">
                      <div className="font-medium">{p.name}{p.restricted && <span className="ml-2 text-xs rounded bg-slate-200 px-1.5 py-0.5" title="Visible only to admins and members">🔒 restricted</span>}</div>
                      <div className="text-xs text-slate-500 max-w-md">{p.description}</div>
                    </td>
                    <td><Link className="text-indigo-600 underline" to={`/clients/${p.client_id}`}>{p.client_name}</Link></td>
                    <td className="text-xs">{p.record.project.technology.join(", ") || "—"}</td>
                    <td className="text-xs">
                      {p.url && <div>🌐 {p.url}</div>}
                      {[...s.github, ...s.gitlab].map((u) => <div key={u}>⌥ {u}</div>)}
                    </td>
                    <td>
                      {p.latest_run ? (
                        <Link to={`/runs/${p.latest_run.id}`}><Badge value={p.latest_run.status} /></Link>
                      ) : "—"}
                    </td>
                    <td className="text-right space-x-1 whitespace-nowrap">
                      {me.data?.role !== "viewer" && <Button variant="secondary" onClick={() => setEditingRepos(p)}>Repos</Button>}
                      {isAdmin && <Button variant="secondary" onClick={() => setEditingAccess(p)}>Access</Button>}
                      {me.data?.role !== "viewer" && <Button onClick={() => setStarting(p)}>Analyze</Button>}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        ) : (
          <Empty>No projects. <Link to="/upload" className="underline">Upload a CSV</Link>.</Empty>
        )}
      </Card>
      {starting && <StartRunDialog project={starting} onClose={() => setStarting(null)} />}
      {editingAccess && <ProjectAccessDialog project={editingAccess} onClose={() => setEditingAccess(null)} />}
      {editingRepos && <RepositoriesDialog project={editingRepos} onClose={() => setEditingRepos(null)} />}
    </div>
  );
}
