from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cip.config import Settings  # noqa: E402

# Tests never read the server's backend/.env: production settings (TLS, executor, proxies…) must not change them.
Settings.model_config["env_file"] = None
os.environ.setdefault("CIP_OPENROUTER_API_KEY", "")
os.environ.setdefault("CIP_SEARCH_PROVIDER", "none")
os.environ.setdefault("CIP_JWT_SECRET", "test-secret-with-at-least-32-bytes-of-entropy")

from cip.agents.base import RunContext  # noqa: E402
from cip.agents.csv_intake import parse_csv  # noqa: E402
from cip.connectors.research.web import WebFetcher  # noqa: E402
from cip.core.evidence import EvidenceLedger  # noqa: E402
from cip.core.llm import NullLLM  # noqa: E402

from fakes import FakeSearch, FakeSourceControl, web_transport  # noqa: E402

SAMPLE_CSV = """Client Name,Client Email,Project Name,Project URL,Description,Industry,Technology,Repository URL,Existing Features
ABC Healthcare,ops@abc-healthcare.com,ABC Patient Management,https://abc-healthcare.com,Healthcare appointment management platform,Healthcare,"React, Node",https://github.com/abc/project,"Online booking; email reminders"
"""


@pytest.fixture
def sample_record():
    return parse_csv(SAMPLE_CSV).records[0]


@pytest.fixture
def make_ctx(sample_record):
    def _make(llm=None, approvals=("external_research", "repository_access", "client_report"), **kw) -> RunContext:
        return RunContext(
            run_id="run_test", project_id="prj_test", record=kw.pop("record", sample_record),
            ledger=EvidenceLedger(), approvals=set(approvals), llm=llm or NullLLM(),
            fetcher=kw.pop("fetcher", None) or WebFetcher(transport=web_transport()),
            search=kw.pop("search", FakeSearch()),
            source_control_factory=kw.pop("factory", lambda ref, token: FakeSourceControl(ref, token)),
            **kw,
        )
    return _make
