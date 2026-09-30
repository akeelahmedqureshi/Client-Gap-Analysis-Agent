import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../lib/api";
import type { Connection, User } from "../lib/types";
import { Badge, Button, Card, Empty, ErrorText } from "../components/ui";

export default function SettingsPage() {
  const qc = useQueryClient();
  const me = useQuery({ queryKey: ["me"], queryFn: () => api.get<User>("/api/auth/me") });
  const connections = useQuery({ queryKey: ["connections"], queryFn: () => api.get<Connection[]>("/api/connections") });
  const health = useQuery({ queryKey: ["health"], queryFn: () => api.get<any>("/api/health") }); // eslint-disable-line @typescript-eslint/no-explicit-any
  const [pat, setPat] = useState({ provider: "github", host: "", token: "" });
  const [user, setUser] = useState({ email: "", password: "", role: "analyst" });
  const [error, setError] = useState<unknown>(null);
  const [notice, setNotice] = useState("");
  const isAdmin = me.data?.role === "admin";

  async function run(fn: () => Promise<unknown>, msg: string) {
    setError(null);
    try {
      await fn();
      setNotice(msg);
      qc.invalidateQueries({ queryKey: ["connections"] });
    } catch (e) {
      setError(e);
    }
  }

  async function oauth(provider: string) {
    await run(async () => {
      const { authorize_url } = await api.get<{ authorize_url: string }>(`/api/connections/${provider}/authorize`);
      location.assign(authorize_url);
    }, "");
  }

  return (
    <div className="space-y-6 max-w-3xl">
      <h1 className="text-2xl font-bold">Settings</h1>
      <ErrorText error={error} />
      {notice && <p className="text-sm text-emerald-700">{notice}</p>}

      <Card title="Platform">
        <div className="text-sm space-y-1">
          <div>LLM: OpenRouter · model <code>{health.data?.llm.model}</code> <Badge value={health.data?.llm.enabled ? "completed" : "skipped"} /></div>
          <div>Web search provider: <code>{health.data?.search_provider}</code></div>
        </div>
      </Card>

      <Card title="Source control connections">
        <p className="text-sm text-slate-500 mb-3">
          Required only for private repositories. Tokens are encrypted at rest, never shown again, and never sent to the LLM.
        </p>
        {connections.data?.length ? (
          <ul className="text-sm divide-y mb-4">
            {connections.data.map((c) => (
              <li key={c.id} className="py-2 flex justify-between items-center">
                <span><Badge value={c.provider} /> {c.host} · {c.token_type} {c.scopes && `· ${c.scopes}`}</span>
                {isAdmin && <Button variant="danger" onClick={() => run(() => api.del(`/api/connections/${c.id}`), "Connection removed")}>Remove</Button>}
              </li>
            ))}
          </ul>
        ) : <Empty>No connections.</Empty>}
        {isAdmin && (
          <div className="space-y-3 mt-3">
            <div className="flex gap-2">
              <Button variant="secondary" onClick={() => oauth("github")}>Connect GitHub (OAuth)</Button>
              <Button variant="secondary" onClick={() => oauth("gitlab")}>Connect GitLab (OAuth)</Button>
            </div>
            <div className="flex flex-wrap gap-2 items-end text-sm">
              <select value={pat.provider} onChange={(e) => setPat({ ...pat, provider: e.target.value })} className="border rounded px-2 py-1.5">
                <option value="github">GitHub</option>
                <option value="gitlab">GitLab</option>
              </select>
              <input placeholder="Host (optional, e.g. gitlab.example.com)" value={pat.host} onChange={(e) => setPat({ ...pat, host: e.target.value })} className="border rounded px-2 py-1.5" />
              <input type="password" placeholder="Read-only access token" value={pat.token} onChange={(e) => setPat({ ...pat, token: e.target.value })} className="border rounded px-2 py-1.5 flex-1" />
              <Button onClick={() => run(async () => { await api.post("/api/connections/token", pat); setPat({ ...pat, token: "" }); }, "Token saved")}>Save token</Button>
            </div>
          </div>
        )}
      </Card>

      {isAdmin && (
        <Card title="Add team member">
          <div className="flex flex-wrap gap-2 text-sm">
            <input placeholder="Email" value={user.email} onChange={(e) => setUser({ ...user, email: e.target.value })} className="border rounded px-2 py-1.5" />
            <input type="password" placeholder="Initial password (10+ chars)" value={user.password} onChange={(e) => setUser({ ...user, password: e.target.value })} className="border rounded px-2 py-1.5" />
            <select value={user.role} onChange={(e) => setUser({ ...user, role: e.target.value })} className="border rounded px-2 py-1.5">
              <option value="viewer">Viewer</option>
              <option value="analyst">Analyst</option>
              <option value="admin">Admin</option>
            </select>
            <Button onClick={() => run(() => api.post("/api/auth/users", user), `Added ${user.email}`)}>Add</Button>
          </div>
        </Card>
      )}
    </div>
  );
}
