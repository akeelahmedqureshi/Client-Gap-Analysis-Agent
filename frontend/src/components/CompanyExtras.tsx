/* eslint-disable @typescript-eslint/no-explicit-any */
import { Card, Empty } from "./ui";
import EvidenceRefs from "./EvidenceRefs";

/** Extended company facts, hiring signals and recent announcements (shared by run & client pages). */
export function CompanyFacts({ profile }: { profile: any }) {
  const rows: [string, any][] = [
    ["Legal name", profile.legal_name], ["Headquarters", profile.headquarters], ["Founded", profile.founded_year],
    ["Company size", profile.company_size], ["Business model", profile.business_model],
    ["Revenue model", profile.revenue_model], ["Markets served", profile.geographic_markets?.join(", ")],
    ["Brands", profile.brands?.join(", ")], ["Subsidiaries", profile.subsidiaries?.join(", ")],
    ["Business divisions", profile.divisions?.join(", ")], ["Locations", profile.locations?.join(", ")],
    ["Target customers", profile.target_customers?.join(", ")],
  ];
  const shown = rows.filter(([, v]) => v);
  if (!shown.length) return null;
  return (
    <dl className="mt-3 text-sm grid grid-cols-[max-content_1fr] gap-x-3 gap-y-1">
      {shown.map(([k, v]) => (
        <div key={k} className="contents"><dt className="text-slate-500">{k}</dt><dd>{String(v)}</dd></div>
      ))}
    </dl>
  );
}

export function HiringCard({ hiring, withEvidence = true }: { hiring: any; withEvidence?: boolean }) {
  return (
    <Card title={`Hiring signals${hiring?.job_count ? ` (${hiring.job_count} open roles)` : ""}`}>
      {hiring?.signals?.length ? (
        <>
          <table className="w-full text-sm">
            <thead className="text-left text-slate-500"><tr><th className="py-1">Area</th><th className="px-2">Roles</th><th>Examples</th>{withEvidence && <th />}</tr></thead>
            <tbody>
              {hiring.signals.map((s: any) => (
                <tr key={s.area} className="border-t align-top">
                  <td className="py-1.5 font-medium">{s.area}</td>
                  <td className="px-2">{s.count}</td>
                  <td className="text-xs text-slate-600">{s.examples.slice(0, 3).join("; ")}</td>
                  {withEvidence && <td><EvidenceRefs ids={[s.evidence_id]} /></td>}
                </tr>
              ))}
            </tbody>
          </table>
          <p className="text-xs text-slate-500 mt-2">Source: {hiring.sources?.join(", ") || "careers page"}. Hiring in an area raises the strategic alignment of matching opportunities.</p>
        </>
      ) : (
        <Empty>{hiring?.careers_page ? "Careers page found, but no machine-readable job board." : "No job board found."}</Empty>
      )}
    </Card>
  );
}

export function AnnouncementsCard({ items, withEvidence = true }: { items: any[]; withEvidence?: boolean }) {
  return (
    <Card title="Recent announcements">
      {items?.length ? (
        <ul className="text-sm space-y-1.5">
          {items.map((a) => (
            <li key={a.url}>
              {a.is_product && <span className="mr-1 rounded bg-emerald-100 text-emerald-800 text-[10px] px-1.5 py-0.5 uppercase">product</span>}
              <a className="text-indigo-600 underline" href={a.url} target="_blank" rel="noreferrer">{a.title}</a>
              {a.date && <span className="text-xs text-slate-500"> · {a.date}</span>}
              {withEvidence && <> <EvidenceRefs ids={[a.evidence_id]} /></>}
            </li>
          ))}
        </ul>
      ) : <Empty>No blog, news or changelog posts found.</Empty>}
    </Card>
  );
}
