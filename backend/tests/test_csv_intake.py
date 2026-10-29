from cip.agents.csv_intake import detect_columns, parse_csv


def test_detects_aliased_columns_and_normalizes(sample_record):
    rec = sample_record
    assert rec.client.name == "ABC Healthcare"
    assert rec.client.domain == "abc-healthcare.com"
    assert rec.project.url == "https://abc-healthcare.com"
    assert rec.sources.github == ["https://github.com/abc/project"]
    assert rec.project.technology == ["React", "Node"]
    assert rec.project.existing_features == ["Online booking", "email reminders"]


def test_column_aliases():
    mapping, unmapped = detect_columns(["Company", "Website", "Repo", "Tech Stack", "Random"])
    assert mapping == {"client_name": "Company", "project_url": "Website", "repository_url": "Repo",
                       "technology": "Tech Stack"}
    assert unmapped == ["Random"]


def test_mandatory_fields_are_never_guessed_and_free_email_domains_are_ignored():
    csv = "Email,URL,Notes\njohn@gmail.com,https://www.acme-robotics.io,see https://gitlab.com/acme/core/-/tree/main and https://linkedin.com/company/acme\n"
    result = parse_csv(csv)
    row, rec = result.rows[0], result.records[0]
    assert rec.client.domain == "acme-robotics.io"  # gmail ignored, website used
    assert rec.client.name == "" and row.errors == {"client_name": "Client name is required."}
    assert any("Client name" in w for w in result.warnings)   # no column for a mandatory field
    assert rec.sources.gitlab == ["https://gitlab.com/acme/core"]
    assert rec.sources.linkedin == ["https://linkedin.com/company/acme"]


def test_invalid_values_are_reported_per_field():
    csv = (
        "Client Name,Project Name,Project URL,Repository URL,Client Email,Start Date\n"
        "Acme,Portal,https://acme.com,https://github.com/acme/portal,ops@acme.com,2023-02-01\n"
        "Acme,Mobile,not a url,https://example.org/repo,not-an-email,Feb 2023\n"
        ",Shop,,,,\n"
    )
    result = parse_csv(csv)
    assert result.valid and len(result.rows) == 3
    ok, bad, empty = result.rows
    assert ok.errors == {}
    assert set(bad.errors) == {"project_url", "repository_url", "client_email", "start_date"}
    assert "not a GitHub or GitLab" in bad.errors["repository_url"].replace("Not", "not")
    assert set(empty.errors) == {"client_name", "project_url"}  # the two mandatory fields
    assert result.records[1].project.url is None and result.records[1].issues


def test_project_name_defaults_to_the_client_name():
    rec = parse_csv("Client Name,Website\nBeta GmbH,beta.de\n").records[0]
    assert rec.project.name == "Beta GmbH" and rec.project.url == "https://beta.de"


def test_a_repository_is_not_a_website():
    row = parse_csv("Client,Website\nAcme,https://github.com/acme/app\n").rows[0]
    assert "Repository URL" in row.errors["project_url"]


def test_rejects_csv_without_identifying_columns():
    result = parse_csv("foo,bar\n1,2\n")
    assert not result.valid
    assert "identifying column" in result.errors[0]


def test_semicolon_delimited_and_bom():
    data = "﻿Client;Project;URL\nBeta GmbH;Shop;beta.de\n".encode()
    rec = parse_csv(data).records[0]
    assert rec.client.name == "Beta GmbH"
    assert rec.project.url == "https://beta.de"


def test_github_pages_is_not_a_repository():
    rec = parse_csv("Client,URL\nX,https://x.github.io/site\n").records[0]
    assert rec.sources.github == []
    assert rec.project.url == "https://x.github.io/site"


MALFORMED = ["[2013-04-24]", "https://[2013-04-24]/x", "http://[::1", "http://a.com:99999/", "ftp://files.example.com",
             "http://", "[]", "//", "javascript:alert(1)", "https://exa mple.com", "\x00", "%%%", "😀.com",
             "a" * 3000, "http://[fe80::1%eth0]/"]


def test_malformed_values_never_crash_the_upload():
    """Regression: '[2013-04-24]' in a URL column raised ValueError (bracketed IPv6 host) -> HTTP 500."""
    import csv as _csv
    import io as _io

    from cip.agents.csv_intake import parse_csv

    out = _io.StringIO()
    w = _csv.writer(out)
    w.writerow(["Client Name", "Client Email", "Project Name", "Project URL", "Repository URL", "LinkedIn",
                "Description", "Notes"])
    for i, bad in enumerate(MALFORMED):
        w.writerow([f"Client {i}", bad, f"Project {i}", bad, bad, bad, f"see {bad}", bad])
    result = parse_csv(out.getvalue())
    assert not result.errors and len(result.records) == len(MALFORMED)
    first = result.records[0]
    assert first.project.url is None and "project_url" in result.rows[0].errors
    # Everything except the (valid, internationalised) emoji domain is rejected as a project URL.
    rejected = [r for r, bad in zip(result.records, MALFORMED) if not bad.endswith(".com") or " " in bad]
    assert all(r.project.url is None for r in rejected)


def test_malformed_links_on_a_page_are_ignored():
    from cip.connectors.research.web import parse_html

    html = ("<a href='http://[2013-04-24]/x'>a</a><a href='http://a.com:99999/'>b</a><a href='//[::1'>c</a>"
            "<a href='/about'>About</a><a href='https://partner.example.org/'>Partner</a>")
    page = parse_html("https://site.example.com/", 200, html)
    assert page.links == ["https://site.example.com/about"]
    assert page.external_links == ["https://partner.example.org/"]


def test_the_published_template_parses_cleanly():
    """examples/client-template.csv (also served by the UI) uses every recognised column and needs no inference."""
    from pathlib import Path

    from cip.agents.csv_intake import COLUMN_ALIASES

    root = Path(__file__).resolve().parents[2]
    data = (root / "examples" / "client-template.csv").read_bytes()
    assert (root / "frontend" / "public" / "client-template.csv").read_bytes() == data
    r = parse_csv(data)
    assert r.valid and not r.warnings and not r.unmapped_columns
    assert set(r.column_mapping) == set(COLUMN_ALIASES)
    assert all(not rec.issues for rec in r.records)
