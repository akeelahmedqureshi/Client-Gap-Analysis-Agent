import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, setToken } from "../lib/api";
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
          <div>LLM: OpenRouter · model <code>{health.data?.llm.model}</code> <Badge value={health.data?.llm.enabled ? "enabled" : "disabled"} /></div>
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

      <ChangePassword />

      {isAdmin && <TeamMembers currentUserId={me.data?.id} />}

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
            <Button onClick={() => run(async () => { await api.post("/api/auth/users", user); qc.invalidateQueries({ queryKey: ["users"] }); }, `Added ${user.email}`)}>Add</Button>
          </div>
        </Card>
      )}
    </div>
  );
}

function ChangePassword() {
  const [form, setForm] = useState({ current_password: "", new_password: "" });
  const [msg, setMsg] = useState("");
  const [error, setError] = useState<unknown>(null);
  async function submit() {
    setError(null);
    try {
      const res = await api.post<{ access_token: string }>("/api/auth/change-password", form);
      setToken(res.access_token); // other sessions are signed out; this one continues
      setForm({ current_password: "", new_password: "" });
      setMsg("Password changed. Other sessions have been signed out.");
    } catch (e) {
      setError(e);
    }
  }
  return (
    <Card title="Change your password">
      <div className="flex flex-wrap gap-2 text-sm">
        <input type="password" placeholder="Current password" value={form.current_password}
          onChange={(e) => setForm({ ...form, current_password: e.target.value })} className="border rounded px-2 py-1.5" />
        <input type="password" placeholder="New password (10+ chars)" value={form.new_password}
          onChange={(e) => setForm({ ...form, new_password: e.target.value })} className="border rounded px-2 py-1.5" />
        <Button onClick={submit} disabled={!form.current_password || form.new_password.length < 10}>Change</Button>
      </div>
      {msg && <p className="text-sm text-emerald-700 mt-2">{msg}</p>}
      <ErrorText error={error} />
    </Card>
  );
}

function TeamMembers({ currentUserId }: { currentUserId?: string }) {
  const qc = useQueryClient();
  const users = useQuery({ queryKey: ["users"], queryFn: () => api.get<User[]>("/api/users") });
  const [error, setError] = useState<unknown>(null);
  async function act(fn: () => Promise<unknown>) {
    setError(null);
    try {
      await fn();
      qc.invalidateQueries({ queryKey: ["users"] });
    } catch (e) {
      setError(e);
    }
  }
  return (
    <Card title="Team members">
      <table className="w-full text-sm">
        <thead className="text-left text-slate-500">
          <tr><th className="py-1">Email</th><th>Role</th><th>Status</th><th /></tr>
        </thead>
        <tbody>
          {users.data?.map((u) => (
            <tr key={u.id} className="border-t">
              <td className="py-1.5">{u.email}{u.id === currentUserId && <span className="text-slate-400"> (you)</span>}</td>
              <td>
                <select value={u.role} onChange={(e) => act(() => api.patch(`/api/users/${u.id}`, { role: e.target.value }))}
                  className="border rounded px-1 py-0.5">
                  <option value="viewer">viewer</option>
                  <option value="analyst">analyst</option>
                  <option value="admin">admin</option>
                </select>
              </td>
              <td>
                <Badge value={!u.is_active ? "deactivated" : u.locked ? "locked" : "active"} />
              </td>
              <td className="text-right space-x-1 whitespace-nowrap">
                {u.locked && <Button variant="secondary" onClick={() => act(() => api.post(`/api/users/${u.id}/unlock`))}>Unlock</Button>}
                <Button variant="secondary" onClick={() => {
                  const pw = prompt(`Temporary password for ${u.email} (10+ characters). Share it securely; they should change it after signing in.`);
                  if (pw) act(() => api.post(`/api/users/${u.id}/reset-password`, { new_password: pw }));
                }}>Reset password</Button>
                {u.id !== currentUserId && (
                  <Button variant={u.is_active ? "danger" : "secondary"}
                    onClick={() => act(() => api.patch(`/api/users/${u.id}`, { is_active: !u.is_active }))}>
                    {u.is_active ? "Deactivate" : "Reactivate"}
                  </Button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="text-xs text-slate-500 mt-2">Role, status and password changes sign the user out of existing sessions.</p>
      <ErrorText error={error} />
    </Card>
  );
}
