import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../lib/api";
import type { Client, Portfolio, Project, Run } from "../lib/types";
import { Badge, Card, Empty } from "../components/ui";

function Stat({ label, value, to }: { label: string; value: number | string; to: string }) {
  return (
    <Link to={to} className="bg-white border border-slate-200 rounded-xl p-4 hover:shadow">
      <div className="text-sm text-slate-500">{label}</div>
      <div className="text-3xl font-semibold text-slate-800">{value}</div>
    </Link>
  );
}

export default function DashboardPage() {
  const clients = useQuery({ queryKey: ["clients"], queryFn: () => api.get<Client[]>("/api/clients") });
  const projects = useQuery({ queryKey: ["projects"], queryFn: () => api.get<Project[]>("/api/projects") });
  const runs = useQuery({ queryKey: ["runs"], queryFn: () => api.get<Run[]>("/api/runs"), refetchInterval: 5000 });
  const pf = useQuery({ queryKey: ["portfolio", "dashboard"], queryFn: () => api.get<Portfolio>("/api/portfolio") });
  const awaiting = runs.data?.filter((r) => r.status === "awaiting_approval") ?? [];
  const failed = runs.data?.filter((r) => ["failed", "completed_with_errors"].includes(r.status)) ?? [];
  const projectName = (id: string) => projects.data?.find((p) => p.id === id)?.name ?? id;

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold">Dashboard</h1>
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <Stat label="Clients" value={clients.data?.length ?? "–"} to="/clients" />
        <Stat label="Projects" value={projects.data?.length ?? "–"} to="/projects" />
        <Stat label="Analysis runs" value={runs.data?.length ?? "–"} to="/runs" />
        <Stat label="Awaiting approval" value={awaiting.length} to="/runs" />
        <Stat label="Running" value={pf.data?.summary.running ?? "–"} to="/runs" />
        <Stat label="Needs review" value={pf.data?.summary.needs_review ?? "–"} to="/portfolio" />
        <Stat label="Failed" value={failed.length} to="/runs" />
        <Stat label="Not yet analysed" value={pf.data?.summary.never_analysed ?? "–"} to="/projects" />
      </div>
      {pf.data && pf.data.top_opportunities.length > 0 && (
        <div className="grid gap-4 lg:grid-cols-2">
          <Card title="Top opportunities" actions={<Link className="text-sm text-indigo-600 underline" to="/portfolio">Portfolio</Link>}>
            <ul className="text-sm divide-y">
              {pf.data.top_opportunities.slice(0, 6).map((o, i) => (
                <li key={i} className="py-1.5 flex gap-2">
                  <span className="flex-1"><b>{o.feature}</b> <span className="text-slate-500">— {o.project}</span></span>
                  {o.priority && <Badge value={o.priority} />}
                  <Link className="text-xs text-indigo-600 underline" to={`/runs/${o.run_id}`}>open</Link>
                </li>
              ))}
            </ul>
          </Card>
          <Card title="Top gaps and AI opportunities across clients">
            <div className="grid grid-cols-2 gap-4 text-sm">
              <div><div className="text-xs font-semibold text-slate-500 mb-1">Recurring gaps</div>
                <ul>{pf.data.recurring_gaps.slice(0, 6).map((g) => <li key={g.name}>{g.name} <span className="text-slate-400">({g.projects})</span></li>)}</ul></div>
              <div><div className="text-xs font-semibold text-slate-500 mb-1">AI opportunities</div>
                <ul>{pf.data.recurring_ai.slice(0, 6).map((g) => <li key={g.name}>{g.name} <span className="text-slate-400">({g.projects})</span></li>)}</ul></div>
            </div>
          </Card>
        </div>
      )}
      {awaiting.length > 0 && (
        <Card title="Approvals needed">
          <ul className="divide-y">
            {awaiting.map((r) => (
              <li key={r.run_id} className="py-2 flex justify-between text-sm">
                <span>
                  {projectName(r.project_id)} —{" "}
                  {r.approvals.filter((a) => a.status === "pending").map((a) => a.title).join(", ")}
                </span>
                <Link className="text-indigo-600 underline" to={`/runs/${r.run_id}`}>Review</Link>
              </li>
            ))}
          </ul>
        </Card>
      )}
      <Card title="Recent analysis runs">
        {runs.data?.length ? (
          <table className="w-full text-sm">
            <thead className="text-left text-slate-500">
              <tr><th className="py-1">Project</th><th>Status</th><th>Started</th><th /></tr>
            </thead>
            <tbody>
              {runs.data.slice(0, 10).map((r) => (
                <tr key={r.run_id} className="border-t">
                  <td className="py-2">{projectName(r.project_id)}</td>
                  <td><Badge value={r.status} /></td>
                  <td>{new Date(r.created_at).toLocaleString()}</td>
                  <td className="text-right"><Link className="text-indigo-600 underline" to={`/runs/${r.run_id}`}>Open</Link></td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <Empty>No runs yet. <Link className="underline" to="/upload">Upload a client CSV</Link> to get started.</Empty>
        )}
      </Card>
    </div>
  );
}
