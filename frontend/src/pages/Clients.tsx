import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../lib/api";
import type { Client } from "../lib/types";
import { Card, Empty } from "../components/ui";

export default function ClientsPage() {
  const clients = useQuery({ queryKey: ["clients"], queryFn: () => api.get<Client[]>("/api/clients") });
  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold">Clients</h1>
      <Card>
        {clients.data?.length ? (
          <table className="w-full text-sm">
            <thead className="text-left text-slate-500">
              <tr><th className="py-1">Client</th><th>Domain</th><th>Industry</th><th>Projects</th></tr>
            </thead>
            <tbody>
              {clients.data.map((c) => (
                <tr key={c.id} className="border-t">
                  <td className="py-2 font-medium">{c.name}</td>
                  <td>{c.domain ?? "—"}</td>
                  <td>{c.industry ?? "—"}</td>
                  <td><Link className="text-indigo-600 underline" to={`/projects?client_id=${c.id}`}>{c.project_count}</Link></td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <Empty>No clients yet.</Empty>
        )}
      </Card>
    </div>
  );
}
