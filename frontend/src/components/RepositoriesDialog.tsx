import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../lib/api";
import type { Connection, Project } from "../lib/types";
import { Button, ErrorText } from "./ui";

interface Repo {
  url: string;
  full_name: string;
  private: boolean | null;
  description: string | null;
  archived: boolean;
}

/** Choose which GitHub/GitLab repositories are analysed for a project. */
export default function RepositoriesDialog({ project, onClose }: { project: Project; onClose: () => void }) {
  const qc = useQueryClient();
  const [urls, setUrls] = useState<string[]>([...project.record.sources.github, ...project.record.sources.gitlab]);
  const [manual, setManual] = useState("");
  const [connectionId, setConnectionId] = useState("");
  const [search, setSearch] = useState("");
  const [error, setError] = useState<unknown>(null);
  const connections = useQuery({ queryKey: ["connections"], queryFn: () => api.get<Connection[]>("/api/connections") });
  const repos = useQuery({
    queryKey: ["repos", connectionId],
    queryFn: () => api.get<Repo[]>(`/api/connections/${connectionId}/repositories`),
    enabled: !!connectionId,
  });

  const add = (u: string) => {
    const v = u.trim().replace(/\/$/, "");
    if (v && !urls.includes(v)) setUrls([...urls, v]);
  };

  async function save() {
    try {
      await api.put(`/api/projects/${project.id}/repositories`, { urls });
      qc.invalidateQueries({ queryKey: ["projects"] });
      onClose();
    } catch (e) {
      setError(e);
    }
  }

  const visible = (repos.data ?? []).filter((r) => r.full_name.toLowerCase().includes(search.toLowerCase()));
  return (
    <div className="fixed inset-0 z-10 bg-black/40 grid place-items-center p-4" onClick={onClose}>
      <div className="bg-white rounded-xl shadow-xl w-full max-w-2xl max-h-[90vh] overflow-auto" onClick={(e) => e.stopPropagation()}>
        <div className="px-5 py-4 border-b">
          <h2 className="text-lg font-semibold">Repositories: {project.name}</h2>
          <p className="text-sm text-slate-500">Analysed in the next run (up to 3). Private repositories need a connection in Settings.</p>
        </div>
        <div className="p-5 space-y-4 text-sm">
          <div>
            <div className="font-medium mb-1">Selected</div>
            {urls.length === 0 && <p className="text-slate-500 italic">None — code analysis will be skipped.</p>}
            <ul className="space-y-1">
              {urls.map((u) => (
                <li key={u} className="flex justify-between items-center border rounded px-2 py-1">
                  <span className="break-all">{u}</span>
                  <button className="text-rose-700 text-xs underline" onClick={() => setUrls(urls.filter((x) => x !== u))}>remove</button>
                </li>
              ))}
            </ul>
          </div>
          <div className="flex gap-2">
            <input value={manual} onChange={(e) => setManual(e.target.value)} placeholder="https://github.com/org/repo"
              className="flex-1 border rounded px-2 py-1.5" />
            <Button variant="secondary" onClick={() => { add(manual); setManual(""); }}>Add URL</Button>
          </div>
          <div>
            <div className="font-medium mb-1">Pick from a connected account</div>
            {connections.data?.length ? (
              <div className="flex gap-2 mb-2">
                <select value={connectionId} onChange={(e) => setConnectionId(e.target.value)} className="border rounded px-2 py-1.5">
                  <option value="">Choose connection…</option>
                  {connections.data.map((c) => <option key={c.id} value={c.id}>{c.provider} · {c.host}</option>)}
                </select>
                {connectionId && <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Filter…" className="flex-1 border rounded px-2 py-1.5" />}
              </div>
            ) : <p className="text-slate-500 italic">No connections. An admin can add one in Settings.</p>}
            {repos.isLoading && connectionId && <p className="text-slate-500">Loading repositories…</p>}
            <ErrorText error={repos.error} />
            <ul className="max-h-60 overflow-auto divide-y border rounded">
              {visible.map((r) => (
                <li key={r.url} className="flex justify-between items-center px-2 py-1.5">
                  <span>
                    <span className="font-medium">{r.full_name}</span>
                    {r.private && <span className="ml-1 text-xs text-slate-500">private</span>}
                    {r.archived && <span className="ml-1 text-xs text-amber-700">archived</span>}
                    {r.description && <span className="block text-xs text-slate-500">{r.description}</span>}
                  </span>
                  <Button variant="secondary" disabled={urls.includes(r.url)} onClick={() => add(r.url)}>
                    {urls.includes(r.url) ? "Added" : "Add"}
                  </Button>
                </li>
              ))}
            </ul>
          </div>
          <ErrorText error={error} />
        </div>
        <div className="px-5 py-3 border-t flex justify-end gap-2">
          <Button variant="secondary" onClick={onClose}>Cancel</Button>
          <Button onClick={save}>Save</Button>
        </div>
      </div>
    </div>
  );
}
