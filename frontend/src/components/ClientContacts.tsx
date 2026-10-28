/* eslint-disable @typescript-eslint/no-explicit-any */
import EvidenceRefs from "./EvidenceRefs";
import { Card, Empty } from "./ui";
import { SourceLink, VerificationBadge, shortDate } from "./Verification";

const LABELS: Record<string, string> = {
  website: "Official website", about_page: "About page", contact_page: "Contact page", email: "Email", phone: "Phone",
  address: "Address", linkedin: "LinkedIn", github: "GitHub", gitlab: "GitLab", app_store: "App Store",
  google_play: "Google Play", docs: "Documentation", developer_portal: "Developer portal", community: "Community",
};
const DETAILS = new Set(["website", "about_page", "contact_page", "email", "phone", "address"]);
// Excluded from the analysis; older runs may still hold them.
const EXCLUDED = new Set(["facebook", "instagram", "twitter", "youtube"]);

function Value({ c }: { c: any }) {
  if (c.type === "email") return <a className="text-indigo-700 underline break-all" href={`mailto:${c.value}`}>{c.value}</a>;
  if (c.type === "phone") return <a className="text-indigo-700 underline" href={`tel:${c.value.replace(/[^+\d]/g, "")}`}>{c.value}</a>;
  if (c.type === "address") return <span>{c.value}</span>;
  return <SourceLink url={c.value} />;
}

function Rows({ items }: { items: any[] }) {
  return (
    <div className="overflow-x-auto">
      <table className="text-sm w-full">
        <thead>
          <tr className="text-left text-xs text-slate-500 border-b">
            <th className="py-1 pr-3">Type</th><th className="pr-3">Contact / link</th><th className="pr-3">Account</th>
            <th className="pr-3">Status</th><th className="pr-3">Published at</th><th className="pr-3">Checked</th><th>Evidence</th>
          </tr>
        </thead>
        <tbody>
          {items.map((c) => (
            <tr key={c.type + c.value} className="border-b last:border-0 align-top">
              <td className="py-1.5 pr-3 whitespace-nowrap font-medium">{LABELS[c.type] ?? c.type}</td>
              <td className="pr-3 max-w-xs"><Value c={c} />
                {c.final_url && <div className="text-xs text-orange-700 break-all">→ {c.final_url}</div>}</td>
              <td className="pr-3">{c.account_name || <span className="text-slate-400">—</span>}</td>
              <td className="pr-3">
                <VerificationBadge status={c.status} detail={c.status_detail} />
                {c.status_detail && <div className="text-xs text-slate-500 mt-0.5 max-w-xs">{c.status_detail}</div>}
              </td>
              <td className="pr-3 text-xs max-w-[12rem]">
                {c.official_source ? <span className="text-emerald-700">Official website</span>
                  : c.source?.startsWith("csv://") ? "Client records (CSV)" : c.status ? "Search result" : ""}
                <div><SourceLink url={c.source} /></div>
              </td>
              <td className="pr-3 text-xs whitespace-nowrap">{shortDate(c.verified_at) || "—"}</td>
              <td><EvidenceRefs ids={c.evidence_ids} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** The client's validated contact details and official online presence. */
export default function ClientContacts({ contacts }: { contacts: any[] | undefined }) {
  const all = (contacts ?? []).filter((c) => !EXCLUDED.has(c.type));
  if (!all.length) return <Card title="Contact & official profiles" className="lg:col-span-2"><Empty>None found on the official website.</Empty></Card>;
  const details = all.filter((c) => DETAILS.has(c.type));
  const profiles = all.filter((c) => !DETAILS.has(c.type));
  const verified = all.filter((c) => c.status === "verified").length;
  const attention = all.filter((c) => ["redirected", "inaccessible", "mismatch"].includes(c.status)).length;
  const older = all.some((c) => !c.status);
  return (
    <Card title="Contact & official profiles" className="lg:col-span-2">
      <p className="text-sm text-slate-600 mb-3">
        {older ? "This analysis was made before contacts were verified; start a new analysis to check them." : <>
          {verified} of {all.length} verified{attention > 0 && <span className="text-rose-700"> · {attention} need attention</span>}.
          {" "}<span className="text-slate-500">Verified means published on the official website and, for links, opened and confirmed
          to name the company. Facebook, Instagram, X and YouTube are not analysed.</span></>}
      </p>
      {details.length > 0 && <><h3 className="font-medium text-sm mb-1">Company & contact details</h3><Rows items={details} /></>}
      {profiles.length > 0 && <><h3 className="font-medium text-sm mt-4 mb-1">Official profiles</h3><Rows items={profiles} /></>}
    </Card>
  );
}
