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


def test_infers_missing_fields_and_ignores_free_email_domains():
    csv = "Email,URL,Notes\njohn@gmail.com,https://www.acme-robotics.io,see https://gitlab.com/acme/core/-/tree/main and https://linkedin.com/company/acme\n"
    rec = parse_csv(csv).records[0]
    assert rec.client.domain == "acme-robotics.io"  # gmail ignored, website used
    assert rec.client.name == "Acme Robotics"
    assert rec.sources.gitlab == ["https://gitlab.com/acme/core"]
    assert rec.sources.linkedin == ["https://linkedin.com/company/acme"]
    assert any("inferred" in i for i in rec.issues)


def test_duplicates_and_invalid_urls():
    csv = (
        "Client Name,Project Name,Project URL,Repository URL\n"
        "Acme,Portal,https://acme.com,https://github.com/acme/portal\n"
        "ACME,portal,https://acme.com/,\n"
        "Acme,Mobile,not a url,https://example.org/repo\n"
    )
    result = parse_csv(csv)
    assert result.valid
    r2, r3, r4 = result.records
    assert r3.duplicate_of_row == 2
    assert any("Invalid project URL" in i for i in r4.issues)
    assert any("not a recognised GitHub/GitLab" in i for i in r4.issues)
    assert any("same client" in w for w in result.warnings)


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
