/* eslint-disable @typescript-eslint/no-explicit-any */
import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { api } from "../lib/api";
import type { Client, Project } from "../lib/types";
import { Badge, Card, Empty } from "../components/ui";

interface ClientDetail {
  client: Client;
  projects: Project[];
  profile: any | null;
  profile_run_id: string | null;
  recommendations: { project_id: string; project: string; run_id: string; feature: string; phase: string;
    complexity: string; score: number; opportunity: string }[];
}

const PHASE: Record<string, string> = {
  phase_1_quick_wins: "Quick win (0-4 wks)",
  phase_2_growth: "Growth (1-3 mo)",
  phase_3_major: "Major (3-6 mo)",
  phase_4_strategic: "Strategic / AI (6-12 mo)",
};

export default function ClientDetailPage() {
  const { clientId = "" } = useParams();
  const q = useQuery({ queryKey: ["client", clientId], queryFn: () => api.get<ClientDetail>(`/api/clients/${clientId}`) });
  if (q.error) return <Empty>Client not found.</Empty>;
  if (!q.data) return <p>Loading…</p>;
  const { client, projects, profile, recommendations } = q.data;
  return (
    <div className="space-y-6">
      <div>
        <Link to="/clients" className="text-sm text-indigo-600 underline">← Clients</Link>
        <h1 className="text-2xl font-bold">{client.name}</h1>
        <div className="text-sm text-slate-500">{[client.domain, profile?.industry ?? client.industry].filter(Boolean).join(" · ")}</div>
      </div>

      <div className="grid lg:grid-cols-2 gap-4">
        <Card title="Company">
          {profile ? (
            <div className="text-sm space-y-2">
              <p>{profile.description ?? <span className="text-slate-500 italic">No public description found.</span>}</p>
              <dl className="grid grid-cols-[max-content_1fr] gap-x-3 gap-y-1">
                {["headquarters", "founded_year", "company_size", "business_model"].filter((k) => profile[k]).map((k) => (
                  <div key={k} className="contents"><dt className="text-slate-500 capitalize">{k.replaceAll("_", " ")}</dt><dd>{String(profile[k])}</dd></div>
                ))}
                {profile.locations?.length > 0 && <div className="contents"><dt className="text-slate-500">Locations</dt><dd>{profile.locations.join(", ")}</dd></div>}
              </dl>
              {q.data.profile_run_id && (
                <Link className="text-xs text-indigo-600 underline" to={`/runs/${q.data.profile_run_id}`}>Source analysis & evidence →</Link>
              )}
            </div>
          ) : (
            <Empty>No completed analysis yet. Analyze one of the projects below.</Empty>
          )}
        </Card>
        <Card title="Products & contacts">
          {profile ? (
            <div className="text-sm space-y-3">
              <ul className="space-y-1">
                {profile.products.map((p: any) => <li key={p.name}><span className="font-medium">{p.name}</span> <Badge value={p.kind} /></li>)}
                {profile.products.length === 0 && <li className="text-slate-500 italic">No products discovered.</li>}
              </ul>
              <ul className="space-y-1">
                {profile.contacts.map((c: any) => <li key={c.type + c.value}><Badge value={c.type} /> <span className="break-all">{c.value}</span></li>)}
              </ul>
            </div>
          ) : <Empty>—</Empty>}
        </Card>
      </div>

      <Card title="Top opportunities (latest analysis per project)">
        {recommendations.length ? (
          <table className="w-full text-sm">
            <thead className="text-left text-slate-500"><tr><th className="py-1">Opportunity</th><th>Project</th><th>Phase</th><th>Complexity</th><th>Score</th></tr></thead>
            <tbody>
              {recommendations.map((r) => (
                <tr key={r.run_id + r.feature} className="border-t align-top">
                  <td className="py-1.5"><div className="font-medium">{r.feature}</div><div className="text-xs text-slate-500">{r.opportunity}</div></td>
                  <td><Link className="text-indigo-600 underline" to={`/runs/${r.run_id}`}>{r.project}</Link></td>
                  <td>{PHASE[r.phase] ?? r.phase}</td>
                  <td>{r.complexity}</td>
                  <td className="font-mono">{r.score}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : <Empty>No opportunities yet.</Empty>}
      </Card>

      <Card title={`Projects (${projects.length})`}>
        <table className="w-full text-sm">
          <thead className="text-left text-slate-500"><tr><th className="py-1">Project</th><th>URL</th><th>Repositories</th><th>Latest analysis</th></tr></thead>
          <tbody>
            {projects.map((p) => (
              <tr key={p.id} className="border-t align-top">
                <td className="py-1.5 font-medium">{p.name}{p.restricted && " 🔒"}</td>
                <td className="text-xs">{p.url ?? "—"}</td>
                <td className="text-xs">{[...p.record.sources.github, ...p.record.sources.gitlab].join(", ") || "—"}</td>
                <td>{p.latest_run ? <Link to={`/runs/${p.latest_run.id}`}><Badge value={p.latest_run.status} /></Link> : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </div>
  );
}
