import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api } from "../lib/api";
import type { Project, User } from "../lib/types";
import { Button, ErrorText } from "./ui";

export default function ProjectAccessDialog({ project, onClose }: { project: Project; onClose: () => void }) {
  const qc = useQueryClient();
  const users = useQuery({ queryKey: ["users"], queryFn: () => api.get<User[]>("/api/users") });
  const access = useQuery({
    queryKey: ["access", project.id],
    queryFn: () => api.get<{ restricted: boolean; member_ids: string[] }>(`/api/projects/${project.id}/access`),
  });
  const [restricted, setRestricted] = useState(project.restricted);
  const [members, setMembers] = useState<Set<string>>(new Set());
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    if (access.data) {
      setRestricted(access.data.restricted);
      setMembers(new Set(access.data.member_ids));
    }
  }, [access.data]);

  async function save() {
    try {
      await api.put(`/api/projects/${project.id}/access`, { restricted, member_ids: [...members] });
      qc.invalidateQueries({ queryKey: ["projects"] });
      onClose();
    } catch (e) {
      setError(e);
    }
  }

  const nonAdmins = (users.data ?? []).filter((u) => u.role !== "admin" && u.is_active);
  return (
    <div className="fixed inset-0 z-10 bg-black/40 grid place-items-center p-4" onClick={onClose}>
      <div className="bg-white rounded-xl shadow-xl w-full max-w-lg" onClick={(e) => e.stopPropagation()}>
        <div className="px-5 py-4 border-b">
          <h2 className="text-lg font-semibold">Access: {project.name}</h2>
        </div>
        <div className="p-5 space-y-3 text-sm">
          <label className="flex gap-2 items-start">
            <input type="checkbox" className="mt-1" checked={restricted} onChange={(e) => setRestricted(e.target.checked)} />
            <span>
              <span className="font-medium">Restrict this project</span>
              <span className="block text-slate-500">Only admins and the members below can see the project, its analysis runs, evidence and reports.</span>
            </span>
          </label>
          <div className={restricted ? "" : "opacity-50 pointer-events-none"}>
            <div className="font-medium mb-1">Members</div>
            {nonAdmins.length === 0 && <p className="text-slate-500">No non-admin users in this organization.</p>}
            {nonAdmins.map((u) => (
              <label key={u.id} className="flex gap-2 py-0.5">
                <input
                  type="checkbox"
                  checked={members.has(u.id)}
                  onChange={() => {
                    const m = new Set(members);
                    if (m.has(u.id)) m.delete(u.id);
                    else m.add(u.id);
                    setMembers(m);
                  }}
                />
                {u.email} <span className="text-slate-400">({u.role})</span>
              </label>
            ))}
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
