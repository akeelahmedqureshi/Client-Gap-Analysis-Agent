/* eslint-disable @typescript-eslint/no-explicit-any */
import { Card, Empty } from "./ui";
import EvidenceRefs from "./EvidenceRefs";
import { SourceLink, VerificationBadge } from "./Verification";

/** Extended company facts, hiring signals and recent announcements (shared by run & client pages). */
const ORIGIN: Record<string, string> = {
  official_site: "official website", schema_org: "schema.org (official website)",
  search_snippet: "search snippet", third_party: "third-party page", csv: "CSV",
};
const RANK = ["unverified", "partial", "verified"];

/** One fact's sources: where it came from and how it was checked. */
function FactSource({ entries }: { entries?: any[] }) {
  if (!entries?.length) return <span className="text-xs text-slate-400">source not recorded</span>;
  const worst = entries.reduce((a, e) => (RANK.indexOf(e.verification) < RANK.indexOf(a) ? e.verification : a), "verified");
  const detail = entries.map((e) => `${e.value}: ${e.detail}`).join("\n");
  const origins = Array.from(new Set(entries.map((e) => ORIGIN[e.origin] ?? e.origin)));
  const urls = Array.from(new Set(entries.map((e) => e.source_url).filter(Boolean)));
  return (
    <span className="inline-flex flex-wrap items-center gap-1 ml-1 align-middle">
      <VerificationBadge status={worst} detail={detail} />
      <span className="text-xs text-slate-500">{origins.join(", ")}</span>
      {urls.slice(0, 2).map((u: any) => <span key={u} className="text-xs"><SourceLink url={u} /></span>)}
      {urls.length > 2 && <span className="text-xs text-slate-400">+{urls.length - 2}</span>}
    </span>
  );
}

export function CompanyFacts({ profile }: { profile: any }) {
  const rows: [string, string, any][] = [
    ["Legal name", "legal_name", profile.legal_name], ["Industry", "industry", profile.industry],
    ["Headquarters", "headquarters", profile.headquarters], ["Founded", "founded_year", profile.founded_year],
    ["Company size", "company_size", profile.company_size], ["Business model", "business_model", profile.business_model],
    ["Revenue model", "revenue_model", profile.revenue_model], ["Markets served", "geographic_markets", profile.geographic_markets?.join(", ")],
    ["Brands", "brands", profile.brands?.join(", ")], ["Subsidiaries", "subsidiaries", profile.subsidiaries?.join(", ")],
    ["Business divisions", "divisions", profile.divisions?.join(", ")], ["Locations", "locations", profile.locations?.join(", ")],
    ["Target customers", "target_customers", profile.target_customers?.join(", ")],
  ];
  const shown = rows.filter(([, , v]) => v);
  if (!shown.length) return null;
  // Profiles analysed before per-fact sources were recorded have none: say so once instead of on every row.
  const tracked = profile.fact_sources !== undefined;
  return (
    <>
      <dl className="mt-3 text-sm grid grid-cols-[max-content_1fr] gap-x-3 gap-y-1.5">
        {shown.map(([k, field, v]) => (
          <div key={k} className="contents">
            <dt className="text-slate-500">{k}</dt>
            <dd>{String(v)}{tracked && <FactSource entries={profile.fact_sources?.[field]} />}</dd>
          </div>
        ))}
      </dl>
      {!tracked && <p className="text-xs text-slate-400 mt-1">Per-fact sources are recorded from the next analysis.</p>}
    </>
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
