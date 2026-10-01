import type { Change, Severity } from "../lib/types";
import EvidenceRefs from "./EvidenceRefs";

export const SEVERITY_STYLE: Record<Severity, string> = {
  critical: "bg-rose-100 text-rose-800",
  warning: "bg-amber-100 text-amber-800",
  info: "bg-sky-100 text-sky-800",
};

export function SeverityBadge({ value }: { value: Severity }) {
  return <span className={`inline-block rounded-full px-2 py-0.5 text-xs font-medium ${SEVERITY_STYLE[value]}`}>{value}</span>;
}

const KIND_LABEL: Record<string, string> = {
  competitor_new: "Competitor",
  competitor_dropped: "Competitor",
  competitor_feature: "Competitor feature",
  competitor_price: "Competitor pricing",
  competitor_pricing_practice: "Competitor pricing",
  client_price: "Client pricing",
  client_pricing_practice: "Client pricing",
  market_position: "Market position",
  gap_new: "Gap",
  gap_closed: "Gap",
  security_new: "Security",
  security_resolved: "Security",
  security_grade: "Security",
  client_announcement: "Announcement",
  client_hiring: "Hiring",
  client_hiring_volume: "Hiring",
  client_app_new: "Client app",
  client_app_rating: "Client app",
  client_app_release: "Client app",
  client_app_theme: "App reviews",
  competitor_app_new: "Competitor app",
  competitor_app_rating: "Competitor app",
};

/** List of run-to-run changes. Evidence citations resolve when an EvidenceContext for the run is provided. */
export function ChangeList({ changes, withEvidence = true }: { changes: Change[]; withEvidence?: boolean }) {
  return (
    <ul className="divide-y">
      {changes.map((c, i) => (
        <li key={`${c.kind}-${i}`} className="py-2 flex gap-3 items-start text-sm">
          <SeverityBadge value={c.severity} />
          <div className="flex-1 min-w-0">
            <div className="font-medium text-slate-800">{c.title}</div>
            {c.detail && <div className="text-xs text-slate-500">{c.detail}</div>}
          </div>
          <span className="text-xs text-slate-400 whitespace-nowrap">{KIND_LABEL[c.kind] ?? c.kind}</span>
          {withEvidence && <EvidenceRefs ids={c.evidence_ids} />}
        </li>
      ))}
    </ul>
  );
}
