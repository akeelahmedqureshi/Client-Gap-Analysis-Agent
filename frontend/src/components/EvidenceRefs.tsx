import { createContext, useContext, useState } from "react";
import type { Evidence } from "../lib/types";

export const EvidenceContext = createContext<Map<string, Evidence>>(new Map());

/** Inline evidence citations; click to see the claims, sources and extracted text. */
export default function EvidenceRefs({ ids }: { ids: string[] | undefined }) {
  const index = useContext(EvidenceContext);
  const [open, setOpen] = useState(false);
  const items = (ids ?? []).map((i) => index.get(i)).filter(Boolean) as Evidence[];
  if (!items.length) return <span className="text-xs text-slate-400">no evidence</span>;
  return (
    <span className="relative">
      <button
        onClick={() => setOpen(!open)}
        className="text-xs rounded bg-indigo-50 text-indigo-700 px-1.5 py-0.5 hover:bg-indigo-100"
      >
        {items.length} source{items.length > 1 ? "s" : ""}
      </button>
      {open && (
        <div className="absolute z-20 mt-1 left-0 w-96 max-w-[80vw] bg-white border rounded-lg shadow-lg p-3 space-y-2 text-xs">
          {items.map((e) => (
            <div key={e.id} className="border-b last:border-0 pb-2">
              <div className="font-medium text-slate-800">{e.claim}</div>
              <div className="text-slate-500">
                {e.source_type} · confidence {Math.round(e.confidence * 100)}%
                {e.repository_path && <> · <code>{e.repository_path}{e.line_range ? `:${e.line_range}` : ""}</code></>}
              </div>
              {e.source_url.startsWith("http") ? (
                <a href={e.source_url} target="_blank" rel="noreferrer" className="text-indigo-600 underline break-all">
                  {e.source_url}
                </a>
              ) : (
                <code className="text-slate-500">{e.source_url}</code>
              )}
              {e.extracted_text && <blockquote className="mt-1 border-l-2 pl-2 text-slate-600">{e.extracted_text}</blockquote>}
            </div>
          ))}
        </div>
      )}
    </span>
  );
}
