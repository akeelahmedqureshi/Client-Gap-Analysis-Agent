/** Shared verification vocabulary for every tab: what was checked, how, and how sure it is. */

const STYLES: Record<string, { label: string; cls: string; hint: string }> = {
  verified: { label: "Verified", cls: "bg-emerald-100 text-emerald-800 border-emerald-200",
    hint: "Confirmed against the source" },
  official_link: { label: "Official link, not checked", cls: "bg-sky-50 text-sky-800 border-sky-200",
    hint: "Published on the official website; the destination could not be checked" },
  partial: { label: "Partially verified", cls: "bg-amber-50 text-amber-800 border-amber-200",
    hint: "Some of it is confirmed" },
  unverified: { label: "Unverified", cls: "bg-slate-100 text-slate-700 border-slate-200",
    hint: "Not confirmed by an official or corroborating source" },
  redirected: { label: "Redirected", cls: "bg-orange-50 text-orange-800 border-orange-200",
    hint: "The link leads somewhere else" },
  inaccessible: { label: "Inaccessible", cls: "bg-rose-50 text-rose-800 border-rose-200",
    hint: "The destination did not open" },
  mismatch: { label: "Possibly wrong account", cls: "bg-rose-50 text-rose-800 border-rose-200",
    hint: "The destination does not identify this company" },
  unavailable: { label: "Not available", cls: "bg-slate-50 text-slate-500 border-slate-200",
    hint: "No public information was found" },
  documented: { label: "Documented", cls: "bg-emerald-100 text-emerald-800 border-emerald-200",
    hint: "Stated on the company's own pages; the quote was found in the source" },
  evidenced: { label: "Evidenced", cls: "bg-teal-50 text-teal-800 border-teal-200",
    hint: "Several independent signals (offering pages, code, records, announcements)" },
  mentioned: { label: "Mentioned only", cls: "bg-slate-100 text-slate-700 border-slate-200",
    hint: "Named once on a page; not confirmed as an offered capability" },
  reviewed: { label: "Reviewed", cls: "bg-violet-50 text-violet-800 border-violet-200",
    hint: "Set or confirmed by a human reviewer" },
  not_checked: { label: "Not checked", cls: "bg-slate-50 text-slate-500 border-slate-200",
    hint: "Analysed before verification was added; re-run to check" },
};

export function VerificationBadge({ status, detail }: { status?: string | null; detail?: string }) {
  const s = STYLES[status ?? "not_checked"] ?? STYLES.unverified;
  return (
    <span title={detail || s.hint} className={`inline-block text-xs border rounded px-1.5 py-0.5 whitespace-nowrap ${s.cls}`}>
      {s.label}
    </span>
  );
}

export const shortDate = (iso?: string | null) =>
  iso ? new Date(iso).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" }) : "";

export function SourceLink({ url, label }: { url?: string | null; label?: string }) {
  if (!url) return <span className="text-slate-400">—</span>;
  if (!url.startsWith("http")) return <code className="text-xs text-slate-500">{label ?? url}</code>;
  let text = label;
  if (!text) {
    try {
      const u = new URL(url);
      text = u.hostname.replace(/^www\./, "") + (u.pathname !== "/" ? u.pathname : "");
    } catch {
      text = url;
    }
  }
  return <a className="text-indigo-700 underline break-all" href={url} target="_blank" rel="noreferrer">{text}</a>;
}
