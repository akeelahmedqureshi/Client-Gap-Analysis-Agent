const TOKEN_KEY = "cip_token";

export function getToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function setToken(token: string | null) {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* storage unavailable */
  }
}

// Friendly names for request fields that appear in validation errors.
const FIELD_LABELS: Record<string, string> = {
  password: "Password",
  new_password: "New password",
  current_password: "Current password",
  email: "Email",
  organization: "Organization name",
  name: "Name",
  role: "Role",
  urls: "Repository URLs",
  notify_emails: "Email recipients",
  webhook_url: "Webhook URL",
  frequency: "Frequency",
  min_severity: "Notification level",
  standing_approvals: "Standing approvals",
  scoring_weights: "Scoring weights",
  token: "Token",
};

interface ValidationIssue {
  type?: string;
  loc?: (string | number)[];
  msg?: string;
  ctx?: Record<string, unknown>;
}

function fieldLabel(loc: (string | number)[] = []): string {
  const name = [...loc].reverse().find((p) => typeof p === "string" && !["body", "query", "path"].includes(p)) as
    | string
    | undefined;
  if (!name) return "";
  return FIELD_LABELS[name] ?? name.replaceAll("_", " ").replace(/^./, (c) => c.toUpperCase());
}

function describeIssue(issue: ValidationIssue): string {
  const field = fieldLabel(issue.loc) || "This field";
  const ctx = issue.ctx ?? {};
  const msg = (issue.msg ?? "").replace(/^Value error,\s*/i, "");
  switch (issue.type) {
    case "missing":
      return `${field} is required.`;
    case "string_too_short":
      return Number(ctx.min_length) <= 1 ? `${field} is required.` : `${field} must be at least ${ctx.min_length} characters.`;
    case "string_too_long":
      return `${field} must be at most ${ctx.max_length} characters.`;
    case "too_long":
      return `${field}: at most ${ctx.max_length} entries are allowed.`;
    case "too_short":
      return `${field}: at least ${ctx.min_length} entries are required.`;
    case "literal_error":
    case "enum":
      return `${field} must be one of: ${String(ctx.expected ?? "").replaceAll("'", "")}.`;
    case "greater_than_equal":
      return `${field} must be at least ${ctx.ge}.`;
    case "less_than_equal":
      return `${field} must be at most ${ctx.le}.`;
    default:
      if (/email/i.test(msg)) return `${field}: enter a valid email address.`;
      return msg ? `${field}: ${msg.charAt(0).toLowerCase()}${msg.slice(1)}${/[.!?]$/.test(msg) ? "" : "."}` : `${field} is invalid.`;
  }
}

/** Turn an API error body's `detail` (a string, or FastAPI's list of validation issues) into a readable sentence. */
export function formatApiError(detail: unknown, fallback: string): string {
  if (typeof detail === "string" && detail.trim()) return detail;
  if (Array.isArray(detail) && detail.length) {
    return [...new Set(detail.map((d) => describeIssue(d as ValidationIssue)))].join(" ");
  }
  if (detail && typeof detail === "object" && "msg" in detail) return describeIssue(detail as ValidationIssue);
  return fallback;
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = {};
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  let payload: BodyInit | undefined;
  if (body instanceof FormData) payload = body;
  else if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    payload = JSON.stringify(body);
  }
  const res = await fetch(path, { method, headers, body: payload });
  if (res.status === 401) {
    setToken(null);
    if (!location.pathname.startsWith("/login")) location.assign("/login");
  }
  if (!res.ok) {
    let msg = res.statusText;
    try {
      msg = formatApiError((await res.json()).detail, msg);
    } catch {
      /* not json */
    }
    throw new ApiError(res.status, msg);
  }
  if (res.status === 204) return undefined as T;
  const ct = res.headers.get("content-type") ?? "";
  return (ct.includes("application/json") ? res.json() : res.text()) as Promise<T>;
}

/** Authenticated file download (the API requires a bearer token, so a plain link can't be used). */
async function download(path: string, filename: string): Promise<void> {
  const token = getToken();
  const res = await fetch(path, { headers: token ? { Authorization: `Bearer ${token}` } : {} });
  if (!res.ok) {
    let msg = res.statusText;
    try {
      msg = formatApiError((await res.json()).detail, msg);
    } catch {
      /* not json */
    }
    throw new ApiError(res.status, msg);
  }
  const url = URL.createObjectURL(await res.blob());
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

export const api = {
  download,
  get: <T>(p: string) => request<T>("GET", p),
  post: <T>(p: string, b?: unknown) => request<T>("POST", p, b),
  put: <T>(p: string, b?: unknown) => request<T>("PUT", p, b),
  patch: <T>(p: string, b?: unknown) => request<T>("PATCH", p, b),
  del: <T>(p: string) => request<T>("DELETE", p),
};
