"""Deterministic repository scanning.

Pipeline (no LLM involved here):

    tree ─▶ file classification ─▶ important-file selection ─▶ manifest/dependency
         extraction ─▶ technology & architecture rules ─▶ debt/security indicators

Only the small set of files selected here is ever fetched, and those pass the
secret scanner before they are stored or shown to an LLM.
"""

from __future__ import annotations

import json
import re
import tomllib
from collections import Counter
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from cip.core.security.secrets import is_sensitive_path

VENDORED = re.compile(
    r"(^|/)(node_modules|vendor|bower_components|dist|build|\.next|\.nuxt|target|out|coverage|"
    r"__pycache__|\.venv|venv|env|site-packages|Pods|\.gradle|\.idea|\.vscode)(/|$)"
)
LANG_BY_EXT = {
    ".py": "Python", ".js": "JavaScript", ".jsx": "JavaScript", ".mjs": "JavaScript", ".cjs": "JavaScript",
    ".ts": "TypeScript", ".tsx": "TypeScript", ".rb": "Ruby", ".php": "PHP", ".java": "Java", ".kt": "Kotlin",
    ".go": "Go", ".rs": "Rust", ".cs": "C#", ".swift": "Swift", ".dart": "Dart", ".scala": "Scala",
    ".ex": "Elixir", ".exs": "Elixir", ".vue": "Vue", ".svelte": "Svelte", ".m": "Objective-C", ".cpp": "C++",
    ".c": "C", ".sql": "SQL",
}
MANIFESTS = {
    "package.json", "requirements.txt", "requirements-dev.txt", "pyproject.toml", "pipfile", "setup.py",
    "gemfile", "go.mod", "pom.xml", "build.gradle", "build.gradle.kts", "composer.json", "cargo.toml",
    "pubspec.yaml", "mix.exs",
}
LOCKFILES = {"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "pipfile.lock", "gemfile.lock",
             "go.sum", "composer.lock", "cargo.lock", "pubspec.lock", "uv.lock", "bun.lockb"}
INFRA_FILES = {"dockerfile", "docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml",
               ".gitlab-ci.yml", "jenkinsfile", "serverless.yml", "serverless.yaml", "vercel.json",
               "netlify.toml", "app.yaml", "procfile", "chart.yaml", "nginx.conf", "fly.toml", "render.yaml",
               "azure-pipelines.yml", "bitbucket-pipelines.yml", "cloudbuild.yaml", "skaffold.yaml",
               "template.yaml", "cdk.json"}
API_FILES = re.compile(r"(^|/)(openapi|swagger)[^/]*\.(ya?ml|json)$|\.graphql$|\.proto$", re.I)
DB_FILES = re.compile(r"(^|/)(schema\.prisma|schema\.rb|models\.py|structure\.sql)$|(^|/)(migrations?|migrate|alembic)/", re.I)
ROUTE_FILES = re.compile(r"(^|/)(routes\.rb|urls\.py|routes?\.(ts|js|php|py)|router\.(ts|js)|app\.(py|ts|js)|main\.(py|ts|go)|server\.(ts|js))$", re.I)
TEST_PATH = re.compile(r"(^|/)(tests?|__tests__|spec|specs|e2e|cypress|playwright)(/|$)|[._-](test|spec)\.[a-z]+$|(^|/)test_[^/]+\.py$", re.I)
DOC_PATH = re.compile(r"(^|/)(readme|contributing|architecture|changelog)[^/]*\.(md|rst|txt)$|(^|/)docs?/.*\.md$", re.I)


@dataclass
class FileClass:
    source: list[str] = field(default_factory=list)
    tests: list[str] = field(default_factory=list)
    docs: list[str] = field(default_factory=list)
    manifests: list[str] = field(default_factory=list)
    lockfiles: list[str] = field(default_factory=list)
    infra: list[str] = field(default_factory=list)
    ci: list[str] = field(default_factory=list)
    iac: list[str] = field(default_factory=list)
    api_specs: list[str] = field(default_factory=list)
    db: list[str] = field(default_factory=list)
    routes: list[str] = field(default_factory=list)
    sensitive: list[str] = field(default_factory=list)
    languages: Counter = field(default_factory=Counter)
    top_dirs: Counter = field(default_factory=Counter)


def classify(paths: list[str]) -> FileClass:
    fc = FileClass()
    for path in paths:
        if VENDORED.search(path):
            continue
        p = PurePosixPath(path)
        name = p.name.lower()
        if is_sensitive_path(path):
            fc.sensitive.append(path)
            continue
        if len(p.parts) > 1:
            fc.top_dirs[p.parts[0]] += 1
        lang = LANG_BY_EXT.get(p.suffix.lower())
        if lang:
            fc.languages[lang] += 1
        if name in MANIFESTS or name.endswith(".csproj"):
            fc.manifests.append(path)
        elif name in LOCKFILES:
            fc.lockfiles.append(path)
        if name in INFRA_FILES or name.startswith("dockerfile"):
            fc.infra.append(path)
        if path.startswith(".github/workflows/") or name in {".gitlab-ci.yml", "jenkinsfile", "azure-pipelines.yml",
                                                                 "bitbucket-pipelines.yml", "cloudbuild.yaml"} \
                or path.startswith(".circleci/"):
            fc.ci.append(path)
        if p.suffix == ".tf" or name in {"cdk.json", "pulumi.yaml", "template.yaml"} or "/helm/" in f"/{path}" \
                or re.search(r"(^|/)(k8s|kubernetes|manifests)/.*\.ya?ml$", path):
            fc.iac.append(path)
        if API_FILES.search(path):
            fc.api_specs.append(path)
        if DB_FILES.search(path):
            fc.db.append(path)
        if ROUTE_FILES.search(path):
            fc.routes.append(path)
        if TEST_PATH.search(path):
            fc.tests.append(path)
        elif DOC_PATH.search(path):
            fc.docs.append(path)
        elif lang:
            fc.source.append(path)
    return fc


def select_important_files(fc: FileClass, limit: int) -> list[str]:
    """Pick the files worth fetching, most informative first."""
    def depth(p: str) -> int:
        return p.count("/")

    ordered: list[str] = []

    def take(items: list[str], n: int) -> None:
        for it in sorted(items, key=lambda x: (depth(x), x))[:n]:
            if it not in ordered:
                ordered.append(it)

    take([d for d in fc.docs if PurePosixPath(d).name.lower().startswith("readme")], 2)
    take([m for m in fc.manifests if depth(m) <= 3], 12)
    take([i for i in fc.infra if depth(i) <= 2], 6)
    take(fc.ci, 3)
    take(fc.api_specs, 2)
    take([d for d in fc.db if not re.search(r"migrations?/|migrate/|alembic/", d)], 3)
    take(fc.routes, 4)
    take([d for d in fc.docs if not PurePosixPath(d).name.lower().startswith("readme")], 2)
    return ordered[:limit]


# ------------------------------------------------------------------ manifests

def parse_dependencies(path: str, content: str) -> list[str]:
    name = PurePosixPath(path).name.lower()
    deps: list[str] = []
    try:
        if name == "package.json" or name == "composer.json":
            data = json.loads(content)
            for key in ("dependencies", "devDependencies", "peerDependencies", "require", "require-dev"):
                deps += list((data.get(key) or {}).keys())
        elif name.startswith("requirements") and name.endswith(".txt"):
            for line in content.splitlines():
                line = line.split("#")[0].strip()
                if line and not line.startswith(("-", "git+", "http")):
                    deps.append(re.split(r"[<>=!~;\[ ]", line, maxsplit=1)[0])
        elif name == "pyproject.toml":
            data = tomllib.loads(content)
            for d in data.get("project", {}).get("dependencies", []) or []:
                deps.append(re.split(r"[<>=!~;\[ ]", d, maxsplit=1)[0])
            for group in (data.get("project", {}).get("optional-dependencies") or {}).values():
                deps += [re.split(r"[<>=!~;\[ ]", d, maxsplit=1)[0] for d in group]
            poetry = data.get("tool", {}).get("poetry", {})
            deps += [k for k in (poetry.get("dependencies") or {}) if k.lower() != "python"]
            for grp in (poetry.get("group") or {}).values():
                deps += list((grp.get("dependencies") or {}).keys())
        elif name == "pipfile":
            section = None
            for line in content.splitlines():
                s = line.strip()
                if s.startswith("["):
                    section = s
                elif section in ("[packages]", "[dev-packages]") and "=" in s:
                    deps.append(s.split("=")[0].strip().strip('"'))
        elif name == "setup.py":
            m = re.search(r"install_requires\s*=\s*\[([^\]]*)\]", content, re.S)
            if m:
                deps += [re.split(r"[<>=!~;\[ ]", d.strip().strip("'\""), maxsplit=1)[0]
                         for d in m.group(1).split(",") if d.strip()]
        elif name == "gemfile":
            deps += re.findall(r"^\s*gem\s+['\"]([^'\"]+)['\"]", content, re.M)
        elif name == "go.mod":
            deps += re.findall(r"^\s*(?:require\s+)?([a-z0-9.\-]+\.[a-z]+/[^\s]+)\s+v[\d.]+", content, re.M)
        elif name == "pom.xml":
            deps += re.findall(r"<artifactId>([^<]+)</artifactId>", content)
        elif name in ("build.gradle", "build.gradle.kts"):
            deps += [m.split(":")[1] if m.count(":") >= 1 else m for m in
                     re.findall(r"(?:implementation|api|compile|runtimeOnly|testImplementation)\s*\(?\s*['\"]([^'\"]+)['\"]", content)]
        elif name == "cargo.toml":
            data = tomllib.loads(content)
            deps += list((data.get("dependencies") or {}).keys())
        elif name == "pubspec.yaml":
            in_deps = False
            for line in content.splitlines():
                if re.match(r"^(dev_)?dependencies:", line):
                    in_deps = True
                    continue
                if in_deps and re.match(r"^\S", line):
                    in_deps = False
                if in_deps:
                    m = re.match(r"^\s{2}([a-z_0-9]+):", line)
                    if m:
                        deps.append(m.group(1))
            if "sdk: flutter" in content or "flutter:" in content:
                deps.append("flutter")
        elif name.endswith(".csproj"):
            deps += re.findall(r'PackageReference\s+Include="([^"]+)"', content)
    except (ValueError, tomllib.TOMLDecodeError):
        return []
    return [d.strip().lower() for d in deps if d and d.strip()]


def find_line(content: str, token: str) -> str | None:
    for i, line in enumerate(content.splitlines(), start=1):
        if token.lower() in line.lower():
            return str(i)
    return None


# ------------------------------------------------------------------ technology rules

# dependency (exact, prefix "x/" or "@x/") -> (category, technology name)
DEPENDENCY_RULES: dict[str, tuple[str, str]] = {
    # frontend
    "react": ("frontend", "React"), "react-dom": ("frontend", "React"), "next": ("frontend", "Next.js"),
    "vue": ("frontend", "Vue"), "nuxt": ("frontend", "Nuxt"), "@angular/core": ("frontend", "Angular"),
    "angular": ("frontend", "AngularJS (legacy)"), "svelte": ("frontend", "Svelte"),
    "@sveltejs/kit": ("frontend", "SvelteKit"), "jquery": ("frontend", "jQuery"),
    "tailwindcss": ("frontend", "Tailwind CSS"), "bootstrap": ("frontend", "Bootstrap"),
    "@remix-run/react": ("frontend", "Remix"), "gatsby": ("frontend", "Gatsby"),
    # backend
    "express": ("backend", "Express"), "fastify": ("backend", "Fastify"), "@nestjs/core": ("backend", "NestJS"),
    "koa": ("backend", "Koa"), "hapi": ("backend", "hapi"), "django": ("backend", "Django"),
    "djangorestframework": ("backend", "Django REST Framework"), "flask": ("backend", "Flask"),
    "fastapi": ("backend", "FastAPI"), "rails": ("backend", "Ruby on Rails"), "sinatra": ("backend", "Sinatra"),
    "laravel/framework": ("backend", "Laravel"), "symfony/framework-bundle": ("backend", "Symfony"),
    "spring-boot-starter-web": ("backend", "Spring Boot"), "spring-boot-starter": ("backend", "Spring Boot"),
    "github.com/gin-gonic/gin": ("backend", "Gin"), "github.com/labstack/echo/v4": ("backend", "Echo"),
    "github.com/gofiber/fiber/v2": ("backend", "Fiber"), "actix-web": ("backend", "Actix"),
    "axum": ("backend", "Axum"), "microsoft.aspnetcore.app": ("backend", ".NET / ASP.NET Core"),
    "phoenix": ("backend", "Phoenix"), "graphql": ("backend", "GraphQL"), "apollo-server": ("backend", "GraphQL"),
    "@apollo/server": ("backend", "GraphQL"),
    # databases / ORMs
    "pg": ("database", "PostgreSQL"), "psycopg2": ("database", "PostgreSQL"),
    "psycopg2-binary": ("database", "PostgreSQL"), "psycopg": ("database", "PostgreSQL"),
    "asyncpg": ("database", "PostgreSQL"), "github.com/lib/pq": ("database", "PostgreSQL"),
    "github.com/jackc/pgx/v5": ("database", "PostgreSQL"), "npgsql": ("database", "PostgreSQL"),
    "postgresql": ("database", "PostgreSQL"), "mysql": ("database", "MySQL"), "mysql2": ("database", "MySQL"),
    "pymysql": ("database", "MySQL"), "mysqlclient": ("database", "MySQL"),
    "mongoose": ("database", "MongoDB"), "mongodb": ("database", "MongoDB"), "pymongo": ("database", "MongoDB"),
    "motor": ("database", "MongoDB"), "mongoid": ("database", "MongoDB"),
    "redis": ("database", "Redis"), "ioredis": ("database", "Redis"), "sqlite3": ("database", "SQLite"),
    "@elastic/elasticsearch": ("search", "Elasticsearch"), "elasticsearch": ("search", "Elasticsearch"),
    "opensearch-py": ("search", "OpenSearch"), "algoliasearch": ("search", "Algolia"),
    "meilisearch": ("search", "Meilisearch"), "prisma": ("database", "Prisma ORM"),
    "@prisma/client": ("database", "Prisma ORM"), "sequelize": ("database", "Sequelize ORM"),
    "typeorm": ("database", "TypeORM"), "sqlalchemy": ("database", "SQLAlchemy"),
    "drizzle-orm": ("database", "Drizzle ORM"), "firebase": ("database", "Firebase"),
    "firebase-admin": ("database", "Firebase"), "@supabase/supabase-js": ("database", "Supabase"),
    "dynamoose": ("database", "DynamoDB"), "cassandra-driver": ("database", "Cassandra"),
    # cloud
    "aws-sdk": ("cloud", "AWS"), "boto3": ("cloud", "AWS"), "@aws-sdk/client-s3": ("cloud", "AWS"),
    "aws-cdk-lib": ("cloud", "AWS"), "@google-cloud/storage": ("cloud", "Google Cloud"),
    "google-cloud-storage": ("cloud", "Google Cloud"), "@azure/storage-blob": ("cloud", "Azure"),
    "azure-storage-blob": ("cloud", "Azure"), "@vercel/analytics": ("cloud", "Vercel"),
    # auth
    "passport": ("auth", "Passport"), "next-auth": ("auth", "NextAuth"), "devise": ("auth", "Devise"),
    "@auth0/auth0-react": ("auth", "Auth0"), "auth0": ("auth", "Auth0"), "jsonwebtoken": ("auth", "JWT"),
    "pyjwt": ("auth", "JWT"), "djangorestframework-simplejwt": ("auth", "JWT"), "firebase-auth": ("auth", "Firebase Auth"),
    "@clerk/nextjs": ("auth", "Clerk"), "keycloak-js": ("auth", "Keycloak"), "omniauth": ("auth", "OmniAuth"),
    "python3-saml": ("auth", "SAML"), "passport-saml": ("auth", "SAML"), "@workos-inc/node": ("auth", "WorkOS SSO"),
    # payments
    "stripe": ("payments", "Stripe"), "braintree": ("payments", "Braintree"), "paypal-rest-sdk": ("payments", "PayPal"),
    "@paypal/checkout-server-sdk": ("payments", "PayPal"), "razorpay": ("payments", "Razorpay"),
    "@paddle/paddle-js": ("payments", "Paddle"),
    # AI
    "openai": ("ai", "OpenAI API"), "anthropic": ("ai", "Anthropic API"), "@anthropic-ai/sdk": ("ai", "Anthropic API"),
    "google-generativeai": ("ai", "Google Gemini"), "@google/generative-ai": ("ai", "Google Gemini"),
    "langchain": ("ai", "LangChain"), "langchain-core": ("ai", "LangChain"), "@langchain/core": ("ai", "LangChain"),
    "langgraph": ("ai", "LangGraph"), "llama-index": ("ai", "LlamaIndex"), "llamaindex": ("ai", "LlamaIndex"),
    "transformers": ("ai", "Hugging Face Transformers"), "torch": ("ai", "PyTorch"), "tensorflow": ("ai", "TensorFlow"),
    "scikit-learn": ("ai", "scikit-learn"), "sklearn": ("ai", "scikit-learn"), "openai-whisper": ("ai", "Whisper"),
    "sentence-transformers": ("ai", "Embeddings (sentence-transformers)"), "pgvector": ("ai", "pgvector"),
    "pinecone-client": ("ai", "Pinecone"), "@pinecone-database/pinecone": ("ai", "Pinecone"),
    "pinecone": ("ai", "Pinecone"), "weaviate-client": ("ai", "Weaviate"), "qdrant-client": ("ai", "Qdrant"),
    "chromadb": ("ai", "ChromaDB"), "cohere": ("ai", "Cohere"), "mistralai": ("ai", "Mistral"),
    "ollama": ("ai", "Ollama"), "crewai": ("ai", "CrewAI"), "ai": ("ai", "Vercel AI SDK"),
    # testing
    "jest": ("testing", "Jest"), "vitest": ("testing", "Vitest"), "mocha": ("testing", "Mocha"),
    "cypress": ("testing", "Cypress"), "@playwright/test": ("testing", "Playwright"), "pytest": ("testing", "pytest"),
    "rspec-rails": ("testing", "RSpec"), "rspec": ("testing", "RSpec"), "phpunit/phpunit": ("testing", "PHPUnit"),
    "junit": ("testing", "JUnit"), "junit-jupiter": ("testing", "JUnit"), "@testing-library/react": ("testing", "Testing Library"),
    "xunit": ("testing", "xUnit"),
    # observability
    "@sentry/node": ("observability", "Sentry"), "@sentry/react": ("observability", "Sentry"),
    "@sentry/nextjs": ("observability", "Sentry"), "sentry-sdk": ("observability", "Sentry"),
    "sentry-ruby": ("observability", "Sentry"), "dd-trace": ("observability", "Datadog"),
    "ddtrace": ("observability", "Datadog"), "newrelic": ("observability", "New Relic"),
    "@opentelemetry/api": ("observability", "OpenTelemetry"), "opentelemetry-api": ("observability", "OpenTelemetry"),
    "prom-client": ("observability", "Prometheus"), "prometheus-client": ("observability", "Prometheus"),
    "winston": ("observability", "Structured logging"), "pino": ("observability", "Structured logging"),
    "structlog": ("observability", "Structured logging"), "lograge": ("observability", "Structured logging"),
    # messaging / jobs
    "celery": ("messaging", "Celery"), "bullmq": ("messaging", "BullMQ"), "bull": ("messaging", "Bull"),
    "sidekiq": ("messaging", "Sidekiq"), "kafkajs": ("messaging", "Kafka"), "kafka-python": ("messaging", "Kafka"),
    "confluent-kafka": ("messaging", "Kafka"), "amqplib": ("messaging", "RabbitMQ"), "pika": ("messaging", "RabbitMQ"),
    "socket.io": ("messaging", "WebSockets"), "channels": ("messaging", "WebSockets"),
    # mobile
    "react-native": ("mobile", "React Native"), "expo": ("mobile", "Expo"), "flutter": ("mobile", "Flutter"),
    "@capacitor/core": ("mobile", "Capacitor"), "@ionic/angular": ("mobile", "Ionic"), "@ionic/react": ("mobile", "Ionic"),
    # security
    "helmet": ("security", "Helmet (HTTP headers)"), "csurf": ("security", "CSRF protection"),
    "express-rate-limit": ("security", "Rate limiting"), "django-ratelimit": ("security", "Rate limiting"),
    "rack-attack": ("security", "Rate limiting"), "slowapi": ("security", "Rate limiting"),
    "bcrypt": ("security", "Password hashing"), "bcryptjs": ("security", "Password hashing"),
    "argon2": ("security", "Password hashing"), "argon2-cffi": ("security", "Password hashing"),
    "django-cors-headers": ("security", "CORS policy"), "cors": ("security", "CORS policy"),
    # integrations
    "twilio": ("integration", "Twilio"), "@sendgrid/mail": ("integration", "SendGrid"), "sendgrid": ("integration", "SendGrid"),
    "nodemailer": ("integration", "Email (SMTP)"), "mailgun.js": ("integration", "Mailgun"),
    "@slack/web-api": ("integration", "Slack"), "slack-sdk": ("integration", "Slack"),
    "googleapis": ("integration", "Google APIs"), "@hubspot/api-client": ("integration", "HubSpot"),
    "jsforce": ("integration", "Salesforce"), "simple-salesforce": ("integration", "Salesforce"),
    "zapier-platform-core": ("integration", "Zapier"), "segment": ("integration", "Segment"),
    "mixpanel": ("integration", "Mixpanel"), "posthog-js": ("integration", "PostHog"), "posthog": ("integration", "PostHog"),
}

PATH_RULES: list[tuple[re.Pattern[str], str, str]] = [
    (re.compile(r"(^|/)dockerfile[^/]*$", re.I), "infrastructure", "Docker"),
    (re.compile(r"(^|/)(docker-)?compose\.ya?ml$", re.I), "infrastructure", "Docker Compose"),
    (re.compile(r"^\.github/workflows/"), "ci_cd", "GitHub Actions"),
    (re.compile(r"(^|/)\.gitlab-ci\.yml$"), "ci_cd", "GitLab CI"),
    (re.compile(r"(^|/)jenkinsfile$", re.I), "ci_cd", "Jenkins"),
    (re.compile(r"^\.circleci/"), "ci_cd", "CircleCI"),
    (re.compile(r"(^|/)azure-pipelines\.yml$"), "ci_cd", "Azure Pipelines"),
    (re.compile(r"\.tf$"), "infrastructure", "Terraform"),
    (re.compile(r"(^|/)chart\.yaml$|(^|/)(k8s|kubernetes)/", re.I), "infrastructure", "Kubernetes"),
    (re.compile(r"(^|/)serverless\.ya?ml$"), "infrastructure", "Serverless Framework"),
    (re.compile(r"(^|/)template\.yaml$|(^|/)samconfig\.toml$"), "infrastructure", "AWS SAM"),
    (re.compile(r"(^|/)cdk\.json$"), "infrastructure", "AWS CDK"),
    (re.compile(r"(^|/)nginx[^/]*\.conf$"), "infrastructure", "Nginx"),
    (re.compile(r"(^|/)vercel\.json$"), "cloud", "Vercel"),
    (re.compile(r"(^|/)netlify\.toml$"), "cloud", "Netlify"),
    (re.compile(r"(^|/)app\.yaml$"), "cloud", "Google App Engine"),
    (re.compile(r"(^|/)fly\.toml$"), "cloud", "Fly.io"),
    (re.compile(r"(^|/)procfile$", re.I), "cloud", "Heroku"),
    (re.compile(r"^\.github/dependabot\.ya?ml$"), "security", "Dependabot"),
    (re.compile(r"^\.github/workflows/[^/]*codeql[^/]*\.ya?ml$", re.I), "security", "CodeQL scanning"),
    (re.compile(r"(^|/)\.snyk$"), "security", "Snyk"),
    (re.compile(r"(^|/)(openapi|swagger)[^/]*\.(ya?ml|json)$", re.I), "backend", "OpenAPI specification"),
    (re.compile(r"(^|/)(ios|android)/"), "mobile", "Native mobile project"),
]

LEGACY_VERSIONS: list[tuple[str, re.Pattern[str], str]] = [
    ("react", re.compile(r'"react"\s*:\s*"[\^~]?(1[0-6]|0)\.'), "React < 17"),
    ("angular", re.compile(r'"angular"\s*:\s*"[\^~]?1\.'), "AngularJS 1.x (end-of-life)"),
    ("django", re.compile(r"^django\s*[=<>~]=?\s*[12]\.", re.I | re.M), "Django < 3"),
    ("rails", re.compile(r"gem\s+['\"]rails['\"],\s*['\"][~>= ]*[2-5]\.", re.I), "Rails < 6"),
    ("laravel", re.compile(r'"laravel/framework"\s*:\s*"[\^~]?[4-7]\.'), "Laravel < 8"),
    ("jquery", re.compile(r'"jquery"\s*:\s*"[\^~]?[12]\.'), "jQuery 1.x/2.x"),
    ("python2", re.compile(r"python_requires\s*=\s*['\"][<>=~]*2\.", re.I), "Python 2"),
]


@dataclass
class TechHit:
    category: str
    name: str
    path: str
    token: str


def detect_from_dependencies(manifest_path: str, deps: list[str]) -> list[TechHit]:
    hits = []
    for d in deps:
        rule = DEPENDENCY_RULES.get(d)
        if rule is None and d.startswith("@") and "/" in d:
            # scoped / grouped packages e.g. "@sentry/browser" -> "@sentry/node" family
            base = d.split("/")[0]
            rule = next((v for k, v in DEPENDENCY_RULES.items() if k.startswith(base + "/")), None)
        if rule is None and (d.startswith("spring-boot-starter")):
            rule = ("backend", "Spring Boot")
        if rule:
            hits.append(TechHit(rule[0], rule[1], manifest_path, d))
    return hits


def detect_from_paths(paths: list[str]) -> list[TechHit]:
    seen: set[str] = set()
    hits = []
    for path in paths:
        if VENDORED.search(path):
            continue
        for pat, cat, name in PATH_RULES:
            if name not in seen and pat.search(path):
                seen.add(name)
                hits.append(TechHit(cat, name, path, PurePosixPath(path).name))
    return hits


def detect_architecture(fc: FileClass, tech_names: set[str], all_paths: list[str]) -> list[tuple[str, str]]:
    """Return ``[(style, rationale)]``."""
    out: list[tuple[str, str]] = []
    dirs = {p.lower() for path in all_paths for p in PurePosixPath(path).parts[:-1]}
    service_roots = {PurePosixPath(m).parts[0] + "/" + PurePosixPath(m).parts[1]
                     for m in fc.manifests + [i for i in fc.infra if "dockerfile" in i.lower()]
                     if len(PurePosixPath(m).parts) >= 3 and PurePosixPath(m).parts[0].lower()
                     in {"services", "apps", "packages", "microservices", "svc"}}
    if len(service_roots) >= 3 and any("docker" in t.lower() or "kubernetes" in t.lower() for t in tech_names):
        out.append(("Microservices", f"{len(service_roots)} independently packaged services ({', '.join(sorted(service_roots)[:5])})"))
    elif len(service_roots) >= 2:
        out.append(("Monorepo (multi-package)", f"packages: {', '.join(sorted(service_roots)[:5])}"))
    if {"Serverless Framework", "AWS SAM"} & tech_names or "functions" in dirs or "lambdas" in dirs:
        out.append(("Serverless", "serverless configuration or functions/ directory detected"))
    if {"Kafka", "RabbitMQ", "Celery", "BullMQ", "Sidekiq", "Bull"} & tech_names or {"events", "consumers", "subscribers"} & dirs:
        out.append(("Event-driven / async processing", "message broker or job-queue dependencies detected"))
    if {"controllers", "models", "views"} <= dirs or {"controllers", "models"} <= dirs:
        out.append(("MVC", "controllers/ and models/ directories"))
    if {"domain", "application", "infrastructure"} <= dirs or {"domain", "usecases"} <= dirs or {"domain", "use_cases"} <= dirs:
        out.append(("Clean / hexagonal architecture", "domain/, application/ and infrastructure/ layers"))
    elif {"services", "repositories"} <= dirs or {"api", "services", "models"} <= dirs:
        out.append(("Layered architecture", "service and repository/model layers"))
    frontends = {"React", "Next.js", "Vue", "Angular", "Svelte", "Nuxt", "AngularJS (legacy)"} & tech_names
    backends = {"Express", "Fastify", "NestJS", "Django", "Flask", "FastAPI", "Ruby on Rails", "Laravel",
                "Spring Boot", "Gin", ".NET / ASP.NET Core", "Phoenix", "Koa"} & tech_names
    if frontends and backends:
        out.append(("SPA frontend + API backend", f"{', '.join(sorted(frontends))} with {', '.join(sorted(backends))}"))
    if not any(s for s, _ in out if s in ("Microservices", "Serverless")) and backends:
        out.append(("Monolith", f"single deployable backend ({', '.join(sorted(backends))})"))
    return out
