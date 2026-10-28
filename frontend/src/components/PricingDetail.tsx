/* eslint-disable @typescript-eslint/no-explicit-any */
import EvidenceRefs from "./EvidenceRefs";
import { Card, Empty } from "./ui";
import { SourceLink, shortDate } from "./Verification";

const TERM_LABELS: Record<string, string> = {
  pricing_model: "Pricing model", usage_charges: "Usage-based charges", transaction_fees: "Transaction / processing fees",
  free_trial: "Free trial", onboarding_fees: "Implementation / onboarding fees", contract_terms: "Contract requirements",
  cancellation_terms: "Cancellation terms", discounts: "Discounts", additional_costs: "Additional costs",
  enterprise: "Enterprise pricing",
};
const PERIOD: Record<string, string> = { month: "/month", year: "/year", one_time: " one-time" };

function Quoted({ items }: { items: any[] }) {
  if (!items?.length) return <span className="text-slate-400">—</span>;
  return <ul className="space-y-0.5">{items.map((x) => <li key={x.text}>{x.text} <EvidenceRefs ids={[x.evidence_id]} /></li>)}</ul>;
}

function price(r: any) {
  if (r.quote_based) return <span className="italic text-slate-600">Custom / contact sales</span>;
  if (r.free) return "Free";
  if (r.price == null) return <span className="text-slate-400">not published</span>;
  return `${r.price.toLocaleString()} ${r.currency ?? ""}${PERIOD[r.period] ?? ""}`;
}

/** Plan-by-plan comparison: what each plan costs and what customers receive. */
export function PlanComparison({ cmp }: { cmp: any }) {
  const rows: any[] = cmp?.rows ?? [];
  if (!rows.length) return <Card title="Plans compared"><Empty>No plan could be read from the pricing pages analysed.</Empty></Card>;
  const base = cmp.base_currency;
  const subjects = [...new Set(rows.map((r) => r.subject))];
  return (
    <Card title="Plans compared">
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="text-left text-xs text-slate-500 border-b">
            <tr><th className="py-1 pr-2">Company</th><th className="pr-2">Plan</th><th className="pr-2">Published price</th>
              <th className="pr-2">≈ {base}/month</th><th className="pr-2">Billed annually</th><th className="pr-2">Unit</th>
              <th className="pr-2">Included</th><th className="pr-2">Limits</th><th className="pr-2">Add-ons</th><th className="pr-2">Source · accessed</th><th /></tr>
          </thead>
          <tbody>
            {subjects.map((s) => rows.filter((r) => r.subject === s).map((r, i) => (
              <tr key={s + r.name} className={`align-top ${i === 0 ? "border-t-2" : "border-t"}`}>
                <td className="py-1.5 pr-2 font-medium">{i === 0 ? <>{s}{r.kind === "client" && <span className="text-xs text-slate-500"> (client)</span>}</> : ""}</td>
                <td className="pr-2">{r.name}{r.conflict && <span title="The price parser read a different price for this plan; check the source" className="text-amber-700"> ⚠</span>}</td>
                <td className="pr-2 whitespace-nowrap">{price(r)}</td>
                <td className="pr-2">{r.monthly_normalized != null ? r.monthly_normalized.toLocaleString() : "—"}</td>
                <td className="pr-2">{r.annual_price_per_month != null ? `${r.annual_price_per_month} ${r.currency ?? ""}/month` : "—"}</td>
                <td className="pr-2">{r.unit ?? "—"}</td>
                <td className="pr-2 text-xs max-w-[14rem]">{r.detailed ? <Quoted items={r.included_features} /> : <span className="text-slate-400">not read</span>}</td>
                <td className="pr-2 text-xs max-w-[12rem]">{r.detailed ? <Quoted items={r.limits} /> : <span className="text-slate-400">not read</span>}</td>
                <td className="pr-2 text-xs max-w-[12rem]">{r.detailed ? <Quoted items={r.add_ons} /> : <span className="text-slate-400">not read</span>}</td>
                <td className="pr-2 text-xs"><SourceLink url={r.source_url} /><div className="text-slate-500">{shortDate(r.accessed_at)}</div></td>
                <td><EvidenceRefs ids={r.evidence_id ? [r.evidence_id] : []} /></td>
              </tr>
            )))}
          </tbody>
        </table>
      </div>
      <p className="text-xs text-slate-500 mt-2">
        Prices are as published on each company's own pricing page. "≈ {base}/month" is converted to a monthly figure and to {base}
        with configured exchange rates ({cmp.rates_date}); the published price and currency are kept. ⚠ marks a plan the price
        parser read differently. Rows marked "not read" come from the price parser only (plan contents not extracted).
      </p>
    </Card>
  );
}

export function PricingImplications({ cmp }: { cmp: any }) {
  const items: any[] = cmp?.implications ?? [];
  if (!items.length) return null;
  return (
    <Card title="What the pricing differences mean">
      <ul className="list-disc ml-5 text-sm space-y-1">{items.map((i) => <li key={i.text}>{i.text} <EvidenceRefs ids={i.evidence_ids} /></li>)}</ul>
    </Card>
  );
}

/** Per company: terms, what the page does not state, and what could not be verified. */
export function PricingTerms({ cmp }: { cmp: any }) {
  const details: Record<string, any> = cmp?.details ?? {};
  const names = Object.keys(details);
  if (!names.length) return null;
  return (
    <Card title="Terms and verification, by company">
      <div className="space-y-3">
        {names.map((n) => {
          const d = details[n] ?? {};
          return (
            <details key={n} className="border rounded-lg p-3">
              <summary className="cursor-pointer font-medium">{n}
                {!d.available && <span className="text-xs text-slate-500 font-normal"> — {d.reason ?? "no detail"}</span>}
                {d.unverified?.length > 0 && <span className="text-xs text-amber-700 font-normal"> · {d.unverified.length} plan(s) not verified</span>}
              </summary>
              {d.available && (
                <div className="grid md:grid-cols-2 gap-3 mt-2 text-sm">
                  {Object.entries(d.terms ?? {}).map(([k, v]: any) => (
                    <div key={k}><div className="font-medium">{TERM_LABELS[k] ?? k}</div><Quoted items={v} /></div>
                  ))}
                  {d.not_stated?.length > 0 && <p className="md:col-span-2 text-xs text-slate-500">Not stated on the pricing page: {d.not_stated.join(", ")}.</p>}
                  {d.unverified?.length > 0 && (
                    <div className="md:col-span-2 text-xs text-amber-800">Not shown, could not be verified:
                      <ul className="list-disc ml-5">{d.unverified.map((u: any) => <li key={u.plan}>{u.plan}: {u.reason}</li>)}</ul></div>
                  )}
                  {d.conflicts?.length > 0 && (
                    <div className="md:col-span-2 text-xs text-amber-800">Conflicting readings:
                      <ul className="list-disc ml-5">{d.conflicts.map((c: any) => <li key={c.plan}>{c.plan}: {c.detail}</li>)}</ul></div>
                  )}
                  <div className="md:col-span-2 text-xs text-slate-500">Read from {(d.source_urls ?? []).map((u: string) => <SourceLink key={u} url={u} />)} on {shortDate(d.accessed_at)}.</div>
                </div>
              )}
            </details>
          );
        })}
      </div>
    </Card>
  );
}
