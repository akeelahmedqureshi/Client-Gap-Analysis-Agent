export type FeatureStatus = "available" | "partial" | "missing" | "unknown";

export interface User {
  id: string;
  email: string;
  name: string;
  role: "admin" | "analyst" | "viewer";
  org_id: string;
}

export interface NormalizedRecord {
  row_number: number;
  client: { name: string; domain: string | null; email: string | null; industry: string | null };
  project: {
    name: string;
    url: string | null;
    description: string | null;
    technology: string[];
    status: string | null;
    existing_features: string[];
  };
  sources: { github: string[]; gitlab: string[]; linkedin: string[]; social: string[]; websites: string[] };
  issues: string[];
  duplicate_of_row: number | null;
}

export interface UploadResult {
  id: string;
  filename: string;
  valid: boolean;
  column_mapping: Record<string, string>;
  unmapped_columns: string[];
  errors: string[];
  warnings: string[];
  records: NormalizedRecord[];
}

export interface Project {
  id: string;
  client_id: string;
  client_name: string;
  name: string;
  url: string | null;
  description: string | null;
  record: NormalizedRecord;
  created_at: string;
  latest_run: { id: string; status: string; created_at: string } | null;
}

export interface Client {
  id: string;
  name: string;
  domain: string | null;
  industry: string | null;
  project_count: number;
  created_at: string;
}

export interface ApprovalPreview {
  gate: string;
  agent: string;
  title: string;
  what: string;
  why: string;
  target: string;
  data_analyzed: string;
}

export interface Approval extends ApprovalPreview {
  id: string;
  status: "pending" | "approved" | "rejected";
  decided_by: string | null;
  decided_at: string | null;
  requested_at: string;
}

export interface AgentState {
  agent: string;
  description: string;
  status: string;
  attempts: number;
  error: string | null;
  started_at: string | null;
  finished_at: string | null;
  confidence: number | null;
  finding_count: number;
  evidence_count: number;
}

export interface Run {
  run_id: string;
  project_id: string;
  status: string;
  error: string | null;
  created_at: string;
  updated_at: string;
  agents: Record<string, string>;
  agent_details: AgentState[];
  approvals: Approval[];
  has_report: boolean;
}

export interface Evidence {
  id: string;
  claim: string;
  source_url: string;
  source_type: string;
  extracted_text: string | null;
  repository_path: string | null;
  line_range: string | null;
  confidence: number;
  collected_at: string;
}

export interface Finding {
  id: string;
  category: string;
  title: string;
  detail: string;
  evidence_ids: string[];
  confidence: number;
  basis: "evidence" | "inferred" | "estimate";
}

export interface AgentResult {
  status: string;
  findings: Finding[];
  evidence: Evidence[];
  confidence: number;
  next_actions: string[];
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  data: Record<string, any>;
  errors: string[];
}

export interface Connection {
  id: string;
  provider: string;
  host: string;
  token_type: string;
  scopes: string;
  account_login: string | null;
  created_at: string;
  expires_at: string | null;
}
