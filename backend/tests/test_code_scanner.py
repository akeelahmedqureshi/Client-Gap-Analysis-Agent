from cip.core.code_scanner import (
    classify,
    detect_architecture,
    detect_from_dependencies,
    detect_from_paths,
    parse_dependencies,
    select_important_files,
)


def test_parse_manifests():
    assert set(parse_dependencies("package.json", '{"dependencies":{"react":"18"},"devDependencies":{"jest":"1"}}')) == {"react", "jest"}
    assert parse_dependencies("requirements.txt", "Django==4.2\n# c\nopenai>=1.0 ; python_version>'3'\n-e .\n") == ["django", "openai"]
    assert set(parse_dependencies("pyproject.toml", '[project]\ndependencies=["fastapi>=0.1","sqlalchemy[asyncio]"]\n')) == {"fastapi", "sqlalchemy"}
    assert parse_dependencies("Gemfile", "gem 'rails', '~> 7.0'\ngem \"devise\"\n") == ["rails", "devise"]
    assert "github.com/gin-gonic/gin" in parse_dependencies("go.mod", "require (\n\tgithub.com/gin-gonic/gin v1.9.1\n)\n")
    assert parse_dependencies("package.json", "{not json") == []


def test_technology_detection():
    hits = detect_from_dependencies("package.json", ["react", "@sentry/browser", "stripe", "openai", "github.com/foo/bar"])
    names = {h.name for h in hits}
    assert {"React", "Sentry", "Stripe", "OpenAI API"} <= names
    paths = detect_from_paths([".github/workflows/ci.yml", "Dockerfile", "infra/main.tf", "node_modules/x/Dockerfile"])
    assert {h.name for h in paths} == {"GitHub Actions", "Docker", "Terraform"}


def test_classification_skips_vendored_and_sensitive():
    fc = classify(["src/app.py", "node_modules/lib/index.js", ".env", "tests/test_app.py", "README.md",
                   "requirements.txt", "migrations/0001.py"])
    assert fc.sensitive == [".env"]
    assert "node_modules/lib/index.js" not in fc.source
    assert fc.tests == ["tests/test_app.py"]
    assert fc.manifests == ["requirements.txt"]
    selected = select_important_files(fc, 10)
    assert selected[0] == "README.md" and ".env" not in selected


def test_architecture_detection():
    paths = ["services/a/Dockerfile", "services/a/package.json", "services/b/Dockerfile", "services/b/go.mod",
             "services/c/Dockerfile", "services/c/requirements.txt"]
    arch = dict(detect_architecture(classify(paths), {"Docker", "Kafka"}, paths))
    assert "Microservices" in arch and "Event-driven / async processing" in arch
    mvc = ["app/controllers/x.rb", "app/models/y.rb", "app/views/z.erb"]
    arch = dict(detect_architecture(classify(mvc), {"Ruby on Rails"}, mvc))
    assert "MVC" in arch and "Monolith" in arch
