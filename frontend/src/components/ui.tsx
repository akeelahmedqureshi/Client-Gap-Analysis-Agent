import type { ReactNode } from "react";

export function Card({ title, actions, children, className = "" }: {
  title?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`bg-white border border-slate-200 rounded-xl shadow-sm ${className}`}>
      {(title || actions) && (
        <header className="flex items-center justify-between px-4 py-3 border-b border-slate-100">
          <h2 className="font-semibold text-slate-800">{title}</h2>
          <div className="flex gap-2">{actions}</div>
        </header>
      )}
      <div className="p-4">{children}</div>
    </section>
  );
}

const STATUS_STYLES: Record<string, string> = {
  completed: "bg-emerald-100 text-emerald-800",
  available: "bg-emerald-100 text-emerald-800",
  approved: "bg-emerald-100 text-emerald-800",
  draft: "bg-slate-100 text-slate-600",
  high: "bg-indigo-100 text-indigo-800",
  medium: "bg-sky-50 text-sky-700",
  low: "bg-slate-100 text-slate-500",
  in_review: "bg-sky-100 text-sky-800",
  restricted: "bg-purple-100 text-purple-800",
  archived: "bg-slate-100 text-slate-400",
  running: "bg-sky-100 text-sky-800",
  queued: "bg-sky-100 text-sky-800",
  pending: "bg-slate-100 text-slate-600",
  partial: "bg-amber-100 text-amber-800",
  awaiting_approval: "bg-amber-100 text-amber-800",
  completed_with_errors: "bg-orange-100 text-orange-800",
  skipped: "bg-slate-100 text-slate-500",
  paused: "bg-amber-100 text-amber-800",
  cancelled: "bg-slate-200 text-slate-600",
  missing: "bg-rose-100 text-rose-800",
  failed: "bg-rose-100 text-rose-800",
  rejected: "bg-rose-100 text-rose-800",
  unknown: "bg-slate-100 text-slate-400",
  active: "bg-emerald-100 text-emerald-800",
  locked: "bg-amber-100 text-amber-800",
  deactivated: "bg-rose-100 text-rose-800",
  enabled: "bg-emerald-100 text-emerald-800",
  disabled: "bg-slate-100 text-slate-500",
};

export function Badge({ value, className = "" }: { value: string; className?: string }) {
  return (
    <span className={`inline-block rounded-full px-2 py-0.5 text-xs font-medium ${STATUS_STYLES[value] ?? "bg-slate-100 text-slate-700"} ${className}`}>
      {value.replaceAll("_", " ")}
    </span>
  );
}

export function BasisTag({ basis }: { basis: string }) {
  if (basis === "evidence") return null;
  return (
    <span
      title={basis === "estimate" ? "AI-generated estimate / hypothesis" : "Inferred from evidence"}
      className={`ml-1 rounded px-1.5 py-0.5 text-[10px] uppercase tracking-wide ${basis === "estimate" ? "bg-violet-100 text-violet-700" : "bg-slate-100 text-slate-600"}`}
    >
      {basis}
    </span>
  );
}

export function Button({ variant = "primary", className = "", ...props }: React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "secondary" | "danger";
}) {
  const styles = {
    primary: "bg-indigo-600 text-white hover:bg-indigo-700 disabled:bg-indigo-300",
    secondary: "bg-white text-slate-700 border border-slate-300 hover:bg-slate-50 disabled:opacity-50",
    danger: "bg-white text-rose-700 border border-rose-300 hover:bg-rose-50 disabled:opacity-50",
  }[variant];
  return <button className={`rounded-lg px-3 py-1.5 text-sm font-medium transition ${styles} ${className}`} {...props} />;
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="text-sm text-slate-500 italic">{children}</p>;
}

export function ErrorText({ error }: { error: unknown }) {
  if (!error) return null;
  return <p className="text-sm text-rose-700">{error instanceof Error ? error.message : String(error)}</p>;
}

export function Confidence({ value }: { value: number | null | undefined }) {
  if (value == null) return null;
  const pct = Math.round(value * 100);
  return (
    <span className="inline-flex items-center gap-1 text-xs text-slate-500" title={`Confidence ${pct}%`}>
      <span className="h-1.5 w-12 rounded bg-slate-200 overflow-hidden">
        <span className="block h-full bg-indigo-500" style={{ width: `${pct}%` }} />
      </span>
      {pct}%
    </span>
  );
}
