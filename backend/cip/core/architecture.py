"""Automated architecture diagrams.

Builds a layered model of the client's system from code-analysis findings
(current architecture) and overlays the components introduced by the
roadmap's recommendations (target architecture). Rendered as:

* SVG (server-side, no JavaScript) — for the UI and the offline PDF;
* Mermaid — embedded in the Markdown report (renders on GitHub, many editors).

All labels are escaped: declared technologies come from uploaded CSVs.
"""

from __future__ import annotations

import html
import re
from dataclasses import asdict, dataclass, field

LAYERS = [
    ("users", "Users"),
    ("clients", "Client apps"),
    ("application", "Application"),
    ("data", "Data"),
    ("external", "External services"),
]
PLATFORM = ("platform", "Platform & delivery")

CATEGORY_LAYER = {
    "frontend": "clients", "mobile": "clients", "backend": "application", "messaging": "application",
    "auth": "application", "database": "data", "search": "data", "payments": "external", "ai": "external",
    "integration": "external", "cloud": "platform", "infrastructure": "platform", "ci_cd": "platform",
    "observability": "platform", "security": "platform",
}
# Some detected technologies belong to a more specific layer than their category suggests.
NAME_LAYER = {"pgvector": "data", "Pinecone": "data", "Weaviate": "data", "Qdrant": "data", "ChromaDB": "data",
              "Auth0": "external", "Clerk": "external", "WorkOS SSO": "external", "Firebase Auth": "external",
              "Keycloak": "external", "Celery": "application", "Sidekiq": "application", "BullMQ": "application"}
SKIP_NAMES = {"Tailwind CSS", "Bootstrap", "Password hashing", "CORS policy", "Helmet (HTTP headers)",
              "CSRF protection", "Structured logging", "JWT", "Rate limiting", "Testing Library", "Prisma ORM",
              "Sequelize ORM", "TypeORM", "SQLAlchemy", "Drizzle ORM"}


@dataclass
class Node:
    id: str
    label: str
    layer: str
    new: bool = False
    note: str = ""


@dataclass
class ArchitectureModel:
    title: str
    style: str = ""
    nodes: list[Node] = field(default_factory=list)

    def layer(self, key: str) -> list[Node]:
        return [n for n in self.nodes if n.layer == key]

    def add(self, label: str, layer: str, new: bool = False, note: str = "") -> None:
        if any(n.label.lower() == label.lower() for n in self.nodes):
            return
        self.nodes.append(Node(f"n{len(self.nodes)}", label, layer, new, note))

    def as_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> ArchitectureModel:
        return cls(title=d["title"], style=d.get("style", ""), nodes=[Node(**n) for n in d.get("nodes", [])])


def current_model(profiles: list[dict], declared: list[str]) -> ArchitectureModel:
    m = ArchitectureModel(title="Current architecture")
    styles: list[str] = []
    for p in profiles:
        styles += [s for s in p.get("architecture", []) if s not in styles]
        for t in p.get("technologies", []):
            name, cat = t["name"], t["category"]
            if name in SKIP_NAMES or cat in ("language", "testing", "other"):
                continue
            layer = NAME_LAYER.get(name) or CATEGORY_LAYER.get(cat)
            if layer:
                m.add(name, layer)
    if not profiles:
        for d in declared[:8]:  # no repository access: show the stack declared in the client records
            m.add(d, "application", note="declared")
    if m.layer("clients") or not m.nodes:
        m.add("Web users", "users")
    if any(n.label in ("React Native", "Expo", "Flutter", "Native mobile project", "Capacitor", "Ionic")
           for n in m.nodes):
        m.add("Mobile users", "users")
    if any(n.label == "OpenAPI specification" for n in m.nodes):
        m.add("API consumers", "users")
    m.style = ", ".join(styles[:3])
    return m


# Recommendation -> components it introduces: (label, layer).
_FEATURE_COMPONENTS: list[tuple[str, list[tuple[str, str]]]] = [
    ("ai.rag", [("RAG / knowledge service", "application"), ("Vector store (pgvector)", "data"),
                ("LLM API (OpenRouter)", "external")]),
    ("ai.search", [("Semantic search service", "application"), ("Vector store (pgvector)", "data"),
                   ("Embeddings API", "external")]),
    ("ai.", [("AI service / agent orchestration", "application"), ("LLM API (OpenRouter)", "external")]),
    ("comm.sms", [("Notification service", "application"), ("SMS gateway", "external")]),
    ("comm.whatsapp", [("Notification service", "application"), ("WhatsApp Business API", "external")]),
    ("comm.video", [("Video calls API", "external")]),
    ("comm.in_app_chat", [("Realtime messaging", "application")]),
    ("comm.push", [("Push notifications (FCM/APNs)", "external")]),
    ("auth.sso", [("Identity provider (SAML/OIDC)", "external")]),
    ("auth.mfa", [("MFA / authenticator", "application")]),
    ("auth.rbac", [("Roles & permissions", "application")]),
    ("platform.public_api", [("Public REST API + docs", "application"), ("API consumers", "users")]),
    ("platform.webhooks", [("Webhook dispatcher", "application")]),
    ("platform.integrations", [("Integrations hub", "application")]),
    ("platform.crm", [("CRM connector", "external")]),
    ("ux.mobile_app", [("Mobile app", "clients"), ("Mobile users", "users")]),
    ("ux.self_service", [("Customer portal", "clients")]),
    ("analytics.", [("Analytics & reporting service", "application"), ("Analytics store", "data")]),
    ("billing.", [("Billing service", "application"), ("Payments provider", "external")]),
    ("security.audit_log", [("Audit log", "data")]),
    ("security.compliance", [("Compliance controls & evidence", "platform")]),
    ("workflow.automation", [("Workflow engine", "application")]),
]
_NAMED_COMPONENTS = {
    "CI/CD pipeline": [("CI/CD pipeline", "platform")],
    "Observability (error tracking, metrics, tracing)": [("Error tracking & metrics", "platform")],
    "Containerized, reproducible deployment": [("Container images", "platform")],
    "Secrets management": [("Secrets manager", "platform")],
    "Automated test coverage": [("Automated test suite", "platform")],
    "Free trial": [("Billing & entitlements", "application"), ("Payments provider", "external")],
    "Free tier (freemium)": [("Billing & entitlements", "application")],
    "Enterprise tier": [("Billing & entitlements", "application")],
    "Annual billing discount": [("Billing & entitlements", "application")],
    "Transparent self-serve pricing": [("Billing & entitlements", "application"), ("Payments provider", "external")],
}


# A proposed component is already present when a current technology fills the same role.
ROLE_EQUIVALENTS: dict[str, set[str]] = {
    "Payments provider": {"Stripe", "Braintree", "PayPal", "Razorpay", "Paddle"},
    "SMS gateway": {"Twilio"},
    "LLM API (OpenRouter)": {"OpenAI API", "Anthropic API", "Google Gemini", "Mistral", "Cohere", "Ollama"},
    "Embeddings API": {"OpenAI API", "Cohere", "Embeddings (sentence-transformers)"},
    "Vector store (pgvector)": {"pgvector", "Pinecone", "Weaviate", "Qdrant", "ChromaDB"},
    "Identity provider (SAML/OIDC)": {"Auth0", "WorkOS SSO", "Keycloak", "SAML", "Clerk"},
    "Error tracking & metrics": {"Sentry", "Datadog", "New Relic", "OpenTelemetry", "Prometheus"},
    "CI/CD pipeline": {"GitHub Actions", "GitLab CI", "Jenkins", "CircleCI", "Azure Pipelines"},
    "Container images": {"Docker", "Docker Compose"},
    "Realtime messaging": {"WebSockets"},
    "Workflow engine": {"Celery", "BullMQ", "Bull", "Sidekiq"},
    "Mobile app": {"React Native", "Expo", "Flutter", "Capacitor", "Ionic", "Native mobile project"},
}


def target_model(current: ArchitectureModel, recommendations: list[dict], gaps: list[dict]) -> ArchitectureModel:
    m = ArchitectureModel(title="Target architecture (after roadmap)", style=current.style,
                          nodes=[Node(**asdict(n)) for n in current.nodes])
    by_gap = {g["id"]: g for g in gaps}
    for rec in recommendations:
        gap = by_gap.get(rec.get("gap_id"), {})
        fid = gap.get("feature_id") or ""
        comps = _NAMED_COMPONENTS.get(rec["feature"])
        if comps is None and fid:
            comps = next((c for prefix, c in _FEATURE_COMPONENTS if fid.startswith(prefix)), None)
        if comps is None and gap.get("gap_type") in ("missing", "partial", "ux"):
            comps = [(f"{rec['feature']} module", "application")]
        existing = {n.label for n in m.nodes}
        for label, layer in comps or []:
            if label in existing or ROLE_EQUIVALENTS.get(label, set()) & existing:
                continue
            m.add(label, layer, new=True, note=rec["feature"])
            existing.add(label)
    if m.layer("clients") and not m.layer("users"):
        m.add("Web users", "users")
    return m


# ------------------------------------------------------------------ renderers

def _mid(label: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", label)[:40]


def to_mermaid(m: ArchitectureModel) -> str:
    def esc(s: str) -> str:
        return s.replace('"', "'").replace("<", "‹").replace(">", "›")

    lines = ["flowchart LR"]
    present = []
    for key, title in LAYERS + [PLATFORM]:
        nodes = m.layer(key)
        if not nodes:
            continue
        present.append(key)
        lines.append(f'  subgraph {key}["{title}"]')
        for n in nodes:
            lines.append(f'    {n.id}["{esc(n.label)}"]' + (":::new" if n.new else ""))
        lines.append("  end")
    flow = [k for k, _ in LAYERS if k in present and k != "external"]
    for a, b in zip(flow, flow[1:], strict=False):
        lines.append(f"  {a} --> {b}")
    if "external" in present and "application" in present:
        lines.append("  application --> external")
    if "platform" in present and "application" in present:
        lines.append("  platform -.-> application")
    lines.append("  classDef new fill:#d1fae5,stroke:#059669,stroke-dasharray: 4 3")
    return "\n".join(lines)


def _wrap(label: str, width: int) -> list[str]:
    """At most two lines; the second is truncated with an ellipsis if still too long."""
    if len(label) <= width:
        return [label]
    words, first = label.split(), ""
    while words and len(first + " " + words[0]) - (0 if first else 1) <= width:
        first = (first + " " + words.pop(0)).strip()
    if not first:  # a single very long word
        first, words = label[:width], [label[width:]]
    rest = " ".join(words)
    return [first, rest if len(rest) <= width else rest[: width - 1] + "…"]


def to_svg(m: ArchitectureModel) -> str:
    col_w, gap_x, node_h, gap_y, pad, head = 168, 46, 34, 10, 16, 34
    cols = [(k, t) for k, t in LAYERS if m.layer(k)]
    platform = m.layer("platform")
    rows = max((len(m.layer(k)) for k, _ in cols), default=1)
    width = max(pad * 2 + len(cols) * col_w + max(0, len(cols) - 1) * gap_x, 520)
    body_h = head + rows * (node_h + gap_y) + pad
    plat_y = pad + body_h + 18
    plat_rows = (len(platform) + 3) // 4 if platform else 0
    height = plat_y + (plat_rows * (node_h + gap_y) + head + 6 if platform else 0) + 40
    e = html.escape
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="100%" '
           f'font-family="DejaVu Sans, Arial, sans-serif" role="img" aria-label="{e(m.title)}">',
           '<defs><marker id="arr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
           'orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="#64748b"/></marker></defs>',
           f'<rect x="0" y="0" width="{width}" height="{height}" fill="#ffffff"/>']

    def box(x, y, w, n: Node):
        fill, stroke, dash = ("#d1fae5", "#059669", ' stroke-dasharray="5 3"') if n.new else ("#eef2ff", "#6366f1", "")
        lines = _wrap(n.label, max(10, int(w / 6.4)))
        size = 11.5 if len(lines) == 1 else 10
        first_y = y + node_h / 2 + 4 - (len(lines) - 1) * 6
        text = "".join(f'<tspan x="{x + w / 2}" y="{first_y + i * 12}">{e(line)}</tspan>'
                       for i, line in enumerate(lines))
        out.append(f'<g><title>{e(n.label + (" — " + n.note if n.note else ""))}</title>'
                   f'<rect x="{x}" y="{y}" width="{w}" height="{node_h}" rx="7" fill="{fill}" stroke="{stroke}" '
                   f'stroke-width="1.4"{dash}/>'
                   f'<text font-size="{size}" text-anchor="middle" fill="#0f172a">{text}</text></g>')

    xs = {}
    for i, (key, title) in enumerate(cols):
        x = pad + i * (col_w + gap_x)
        xs[key] = x
        out.append(f'<rect x="{x - 6}" y="{pad}" width="{col_w + 12}" height="{body_h - pad}" rx="10" '
                   f'fill="#f8fafc" stroke="#e2e8f0"/>')
        out.append(f'<text x="{x + col_w / 2}" y="{pad + 20}" font-size="12" font-weight="bold" text-anchor="middle" '
                   f'fill="#334155">{e(title)}</text>')
        for j, n in enumerate(m.layer(key)):
            box(x + 8, pad + head + j * (node_h + gap_y), col_w - 16, n)
    keys = [k for k, _ in cols]
    arrow_y = pad + head + node_h / 2
    flow = [k for k in keys if k != "external"]
    for a, b in zip(flow, flow[1:], strict=False):
        out.append(f'<line x1="{xs[a] + col_w + 6}" y1="{arrow_y}" x2="{xs[b] - 8}" y2="{arrow_y}" stroke="#64748b" '
                   f'stroke-width="1.6" marker-end="url(#arr)"/>')
    if "external" in xs and "application" in xs and "data" in xs:  # application -> external skips over data
        y = pad + body_h - 10
        out.append(f'<path d="M{xs["application"] + col_w / 2},{pad + body_h - pad} V{y} H{xs["external"] + col_w / 2} '
                   f'V{pad + body_h - pad}" fill="none" stroke="#64748b" stroke-width="1.4" stroke-dasharray="4 3" '
                   f'marker-end="url(#arr)"/>')
    elif "external" in xs and "application" in xs:
        out.append(f'<line x1="{xs["application"] + col_w + 6}" y1="{arrow_y}" x2="{xs["external"] - 8}" '
                   f'y2="{arrow_y}" stroke="#64748b" stroke-width="1.6" marker-end="url(#arr)"/>')
    if platform:
        pw = width - pad * 2
        out.append(f'<rect x="{pad - 6}" y="{plat_y}" width="{pw + 12}" height="{plat_rows * (node_h + gap_y) + head}" '
                   f'rx="10" fill="#f8fafc" stroke="#e2e8f0"/>')
        out.append(f'<text x="{pad + 4}" y="{plat_y + 20}" font-size="12" font-weight="bold" fill="#334155">'
                   f'{e(PLATFORM[1])}</text>')
        bw = (pw - 3 * 12) / 4
        for i, n in enumerate(platform):
            box(pad + (i % 4) * (bw + 12), plat_y + head + (i // 4) * (node_h + gap_y), bw, n)
    ly = height - 16
    out.append(f'<text x="{pad}" y="{ly}" font-size="10.5" fill="#475569">{e(m.title)}'
               + (f" · style: {e(m.style)}" if m.style else "") + "</text>")
    if any(n.new for n in m.nodes):
        out.append(f'<rect x="{width - 196}" y="{ly - 11}" width="14" height="14" rx="3" fill="#d1fae5" '
                   f'stroke="#059669" stroke-dasharray="4 2"/><text x="{width - 176}" y="{ly}" font-size="10.5" '
                   f'fill="#475569">new component (roadmap)</text>')
    out.append("</svg>")
    return "".join(out)


def build_architecture(profiles: list[dict], declared: list[str], recommendations: list[dict],
                       gaps: list[dict]) -> dict:
    current = current_model(profiles, declared)
    target = target_model(current, recommendations, gaps)
    return {
        "based_on": "repository analysis" if profiles else "declared technology (no repository access)",
        "current": current.as_dict(), "target": target.as_dict(),
        "mermaid_current": to_mermaid(current), "mermaid_target": to_mermaid(target),
        "svg_current": to_svg(current), "svg_target": to_svg(target),
        "new_components": [{"label": n.label, "layer": n.layer, "for": n.note} for n in target.nodes if n.new],
    }
