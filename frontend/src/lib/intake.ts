/** Client intake (CSV upload and manual entry): the shared client schema, review results and decisions.
 * The server (backend/cip/services/intake.py) decides what is valid and what needs a decision. */

export interface FieldSpec {
  key: string;
  label: string;
  kind: "text" | "email" | "url" | "urls" | "list" | "textarea" | "date";
  group: "client" | "project";
  required: boolean;
  aliases: string[];
  help: string;
  max_length: number;
}

export type Action = "add" | "skip" | "update" | "replace" | "merge";

export interface Decision {
  action?: Action | null;
  target?: string | null;
  merge?: Record<string, "existing" | "incoming">;
  confirm_replace?: boolean;
}

export interface Plan {
  mapping?: Record<string, string> | null;
  edits: Record<string, Record<string, string>>;
  decisions: Record<string, Decision>;
  allow_partial: boolean;
}

export interface MatchField {
  field: string;
  label: string;
  existing: string | null;
  incoming: string | null;
  same: boolean;
  conflict: boolean;
}

export interface Match {
  client_id: string;
  client_name: string | null;
  project_id: string | null;
  project_name?: string | null;
  kind: "confirmed" | "potential";
  reasons: string[];
  accessible: boolean;
  note?: string;
  fields?: MatchField[];
  exact?: boolean;
  scope?: "project" | "client";
}

export type RowStatus = "ready" | "pending" | "invalid" | "conflict" | "skipped" | "imported";

export interface ReviewRow {
  row: number;
  values: Record<string, string>;
  raw: Record<string, string>;
  edited: string[];
  errors: Record<string, string>;
  missing: string[];
  group: number | null;
  matches: Match[];
  decision: Decision;
  needs: string[];
  changes: { field: string; label: string; before: string | null; after: string | null }[];
  result: Record<string, string> | null;
  target: { client_id: string; client_name: string; project_id: string | null; project_name: string | null } | null;
  preview: { client: string; project: string; domain: string | null; website: string | null };
  status: RowStatus;
  action: Action | null;
  allowed: Action[];
  done?: { action: string; client_id: string; project_id: string; at: string; by: string };
}

export interface DuplicateGroup {
  id: number;
  kind: "confirmed" | "potential";
  rows: number[];
  links: { kind: string; reason: string; rows: number[] }[];
  conflicts: { field: string; label: string; values: Record<string, string> }[];
}

export interface ReviewSummary {
  total: number;
  imported_before: number;
  valid: number;
  missing_mandatory: number;
  invalid: number;
  csv_duplicates: number;
  csv_confirmed_duplicates: number;
  existing_matches: number;
  potential_matches: number;
  selected_add: number;
  selected_update: number;
  selected_replace: number;
  selected_merge: number;
  selected_skip: number;
  ready: number;
  pending: number;
  conflicts: number;
  can_import: boolean;
}

export interface Review {
  rows: ReviewRow[];
  groups: DuplicateGroup[];
  conflicts: { rows: number[]; message: string }[];
  summary: ReviewSummary;
  allow_partial: boolean;
  schema: FieldSpec[];
  column_mapping: Record<string, string>;
  unmapped_columns: string[];
  headers: string[];
  warnings: string[];
}

export interface ImportResult {
  summary: { added: number; updated: number; replaced: number; merged: number; skipped: number;
    failed_validation: number; pending: number; failed: number; new_clients: number };
  results: { row: number; action: string; client_id: string; client_name: string; project_id: string; changed?: string[] }[];
  failed: { row: number; action: string; reason: string }[];
  pending_rows: number[];
  created_projects: string[];
}

export const ACTION_LABEL: Record<Action, string> = {
  add: "Add", skip: "Skip", update: "Update existing", replace: "Replace existing", merge: "Merge",
};

export const ACTION_HELP: Record<Action, string> = {
  add: "Add as a new record.",
  skip: "Leave it out; existing records are not changed.",
  update: "Values given in the incoming record replace the existing ones; empty incoming fields keep the existing value. The client keeps its id.",
  replace: "The existing record takes the incoming values; fields the incoming record leaves empty are cleared. The client keeps its id.",
  merge: "Combine both: for every field where they differ you choose which value to keep. The client keeps its id.",
};

export const STATUS_STYLE: Record<RowStatus, string> = {
  ready: "bg-emerald-100 text-emerald-800", pending: "bg-amber-100 text-amber-800", invalid: "bg-rose-100 text-rose-800",
  conflict: "bg-rose-100 text-rose-800", skipped: "bg-slate-100 text-slate-600", imported: "bg-sky-100 text-sky-800",
};

export const STATUS_LABEL: Record<RowStatus, string> = {
  ready: "ready", pending: "needs a decision", invalid: "needs correction", conflict: "conflict",
  skipped: "skipped", imported: "imported",
};

export const emptyPlan = (): Plan => ({ edits: {}, decisions: {}, allow_partial: false });
