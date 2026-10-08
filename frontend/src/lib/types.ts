export type FeatureStatus = "available" | "partial" | "missing" | "unknown";

export interface User {
  id: string;
  email: string;
  name: string;
  role: "admin" | "analyst" | "viewer";
  org_id: string;
  is_active: boolean;
  locked: boolean;
  created_at: string | null;
}

export interface AuditEntry {
  id: number;
  created_at: string;
  user_email: string | null;
  action: string;
  target_type: string | null;
  target_id: string | null;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  details: Record<string, any>;
  ip: string | null;
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
  restricted: boolean;
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
  usage: Usage | null;
}

export interface Usage {
  llm_calls: number;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  cost_usd: number;
  web_requests: number;
  search_queries: number;
  models: Record<string, number>;
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
  monitor_id: string | null;
  parent_run_id: string | null;
  rerun_stages: string[] | null;
  usage: Usage;
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

export type Severity = "critical" | "warning" | "info";

export interface Change {
  kind: string;
  severity: Severity;
  title: string;
  detail: string;
  subject: string;
  evidence_ids: string[];
  before: unknown;
  after: unknown;
}

export interface RunChanges {
  run_id: string;
  status: string;
  baseline_run_id: string | null;
  baseline_created_at: string | null;
  changes: Change[];
  summary: { counts: Record<Severity, number>; total: number; highest: Severity | null };
}

export interface Monitor {
  id: string;
  project_id: string;
  project_name: string;
  client_name: string;
  enabled: boolean;
  frequency: "daily" | "weekly" | "monthly";
  standing_approvals: string[];
  approvals_valid: boolean;
  approved_by: string | null;
  min_severity: Severity;
  notify_emails: string[];
  webhook: string | null;
  email_enabled: boolean;
  scoring_weights: Record<string, number>;
  next_run_at: string | null;
  last_run_id: string | null;
  last_run_status: string | null;
  last_triggered_at: string | null;
  created_at: string;
}

export interface Alert {
  id: string;
  project_id: string;
  project_name: string;
  run_id: string;
  monitor_id: string | null;
  kind: "changes" | "approval_needed" | "run_failed";
  severity: Severity;
  title: string;
  summary: string;
  changes: Change[];
  notifications: { channel: string; ok: boolean; status?: number; error?: string; recipients?: number }[];
  read_at: string | null;
  created_at: string;
}

export type KnowledgeKind = "capability" | "solution" | "project" | "case_study";
export type KnowledgeStatus = "draft" | "in_review" | "approved" | "restricted" | "archived";

export interface KnowledgeRecord {
  id: string;
  kind: KnowledgeKind;
  title: string;
  summary: string;
  details: string;
  outcomes: string;
  customer_name: string | null;
  industries: string[];
  technologies: string[];
  project_types: string[];
  capability_tags: string[];
  ai: boolean;
  automation: boolean;
  linked_ids: string[];
  status: KnowledgeStatus;
  visibility: "internal" | "client_facing";
  reference_allowed: boolean;
  client_facing: boolean;
  version: number;
  approved_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface KnowledgeVersion {
  version: number;
  change: string;
  note: string;
  changed_by_email: string | null;
  changed_at: string;
  snapshot: Partial<KnowledgeRecord>;
}

export interface KnowledgeMatch {
  record_id: string;
  version: number;
  kind: KnowledgeKind;
  title: string;
  confidence: number;
  reasons: string[];
  client_facing: boolean;
  reference_allowed: boolean;
  technologies: string[];
  customer_name: string | null;
}

export interface PortfolioProject {
  project_id: string;
  project: string;
  client_id: string;
  client: string;
  url: string | null;
  industry: string | null;
  status: string | null;
  run_id: string | null;
  analysed_run_id: string | null;
  quality: string | null;
  quality_state: string | null;
  started: string | null;
  completed: string | null;
  top_priority: string | null;
  top_priority_level: string | null;
  high_priority_count: number;
  opportunity_score: number | null;
  evidence_coverage: number | null;
}

export interface Portfolio {
  summary: { clients: number; projects: number; analysed: number; running: number; awaiting_approval: number;
    failed: number; needs_review: number; never_analysed: number };
  projects: PortfolioProject[];
  top_opportunities: { project_id: string; project: string; run_id: string; feature: string; priority: string | null;
    category: string | null; score: number }[];
  recurring_gaps: { name: string; projects: number; type?: string }[];
  recurring_ai: { name: string; projects: number }[];
  recurring_automation: { name: string; projects: number }[];
  requested_capabilities: { name: string; projects: number }[];
  industries: { name: string; projects: number }[];
  capability_demand: { record_id: string; title: string; kind: string; client_facing: boolean; projects: number; top_needs: string[] }[];
  shared_case_studies: { record_id: string; title: string; projects: number }[];
}
