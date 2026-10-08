import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../lib/api";
import type { AppNotification } from "../lib/types";
import { Button, Card, Empty } from "../components/ui";

const ICON: Record<string, string> = {
  run_completed: "✅", run_failed: "⛔", approval_needed: "✋", needs_review: "🔎", outreach_ready: "✉️",
};

/** The signed-in user's run notifications (preferences are in Settings). */
export default function NotificationsPage() {
  const qc = useQueryClient();
  const [unread, setUnread] = useState(false);
  const list = useQuery({
    queryKey: ["notifications", unread],
    queryFn: () => api.get<AppNotification[]>(`/api/notifications?limit=200${unread ? "&unread_only=true" : ""}`),
  });
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["notifications"] });
    qc.invalidateQueries({ queryKey: ["notifications-unread"] });
  };
  async function read(n: AppNotification) {
    if (!n.read_at) {
      await api.post(`/api/notifications/${n.id}/read`);
      refresh();
    }
  }
  return (
    <div className="space-y-4 max-w-3xl">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Notifications</h1>
          <p className="text-sm text-slate-500">Runs you started (or every run, if you chose that in Settings).</p>
        </div>
        <div className="flex items-center gap-3">
          <label className="text-sm flex items-center gap-1"><input type="checkbox" checked={unread} onChange={(e) => setUnread(e.target.checked)} /> Unread only</label>
          <Button variant="secondary" onClick={async () => { await api.post("/api/notifications/read-all"); refresh(); }}>Mark all read</Button>
        </div>
      </div>
      <Card>
        {list.data?.length === 0 && <Empty>No notifications.</Empty>}
        <ul className="divide-y">
          {list.data?.map((n) => (
            <li key={n.id} className={`py-2 flex gap-3 ${n.read_at ? "text-slate-500" : ""}`}>
              <span aria-hidden>{ICON[n.event] ?? "•"}</span>
              <div className="flex-1 min-w-0">
                <div className={n.read_at ? "" : "font-medium"}>{n.title}</div>
                {n.body && <div className="text-xs text-slate-500 truncate">{n.body}</div>}
                <div className="text-xs text-slate-400">{new Date(n.created_at).toLocaleString()}</div>
              </div>
              {n.run_id && <Link className="text-sm text-indigo-700 underline self-center" to={`/runs/${n.run_id}`} onClick={() => read(n)}>Open</Link>}
              {!n.read_at && <button className="text-xs underline self-center" onClick={() => read(n)}>Mark read</button>}
            </li>
          ))}
        </ul>
      </Card>
    </div>
  );
}
