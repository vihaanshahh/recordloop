"""
Tier-1 smoke tests for the RecordLoop API.

These tests:
  - Need ZERO secrets (no OPENAI_API_KEY, no GitHub token, no preview URL)
  - Need ZERO Playwright / chromium
  - Run in well under a second
  - Exercise the full HTTP -> background-job -> analyzer -> comment-render path

They rely on RECORDLOOP_DRY_RUN=1 to short-circuit the LLM call, and on
mocking api.main.get_pr_files / api.main.post_pr_comment to short-circuit
GitHub. Recording is skipped by passing preview_url="".
"""

import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


# Force dry-run BEFORE importing the app so analyzer reads it correctly.
os.environ.setdefault("RECORDLOOP_DRY_RUN", "1")

from api.main import app, _jobs  # noqa: E402
from api.analyzer import analyze_pr  # noqa: E402


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def fake_github():
    """Patch get_pr_files + post_pr_comment so /trigger never touches GitHub."""

    async def fake_get_pr_files(repo, pr_number, token):
        return [{
            "filename": "src/components/SignupForm.tsx",
            "status": "modified",
            "content": '<form><button id="go">Sign up</button></form>',
            "patch": "",
        }]

    posted = []

    async def fake_post_pr_comment(repo, pr_number, body, token):
        posted.append({"repo": repo, "pr_number": pr_number, "body": body})

    with patch("api.main.get_pr_files", side_effect=fake_get_pr_files), \
         patch("api.main.post_pr_comment", side_effect=fake_post_pr_comment):
        yield posted


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_analyzer_dry_run_returns_synthetic_flow():
    """The analyzer must return a canned flow without any LLM call."""
    flows = analyze_pr(
        [{
            "filename": "src/components/SignupForm.tsx",
            "status": "modified",
            "content": '<form><button id="go">Sign up</button></form>',
        }],
        preview_url="http://localhost:3000",
    )
    assert len(flows) == 1
    assert flows[0].name == "dry_run_smoke_flow"
    assert len(flows[0].steps) == 1
    assert flows[0].steps[0].action == "click"


def test_analyzer_skips_non_component_files():
    flows = analyze_pr(
        [{"filename": "README.md", "status": "modified", "content": "# hi"}],
        preview_url="http://localhost:3000",
    )
    assert flows == []


def test_analyzer_includes_test_and_story_files():
    """Test files and Storybook stories are valuable context — keep them."""
    flows = analyze_pr(
        [
            {"filename": "src/Foo.test.tsx", "status": "modified", "content": "<div/>"},
            {"filename": "src/Foo.stories.tsx", "status": "modified", "content": "<div/>"},
        ],
        preview_url="http://localhost:3000",
    )
    # Dry-run returns one synthetic flow regardless of how many files came in,
    # but the important thing is the call succeeded (i.e. files were not filtered out).
    assert len(flows) == 1


def test_analyzer_skips_type_declaration_files():
    flows = analyze_pr(
        [{"filename": "src/types.d.ts", "status": "modified", "content": "export type X = number"}],
        preview_url="http://localhost:3000",
    )
    assert flows == []


def test_analyzer_skips_jest_snapshots():
    flows = analyze_pr(
        [{"filename": "src/__snapshots__/Foo.test.tsx.snap", "status": "modified", "content": "snap"}],
        preview_url="http://localhost:3000",
    )
    assert flows == []


def test_trigger_end_to_end(client, fake_github):
    """POST /trigger -> background job runs to completion in dry-run mode."""
    r = client.post("/trigger", json={
        "repo": "vihaanshahh/recordloop",
        "pr_number": 42,
        "preview_url": "",  # skip Playwright recording
        "github_token": "fake-token",
    })
    assert r.status_code == 200
    job_id = r.json()["job_id"]

    # FastAPI's BackgroundTasks runs after the response is sent. With
    # TestClient that happens synchronously before .post() returns.
    job = _jobs[job_id]
    assert job["status"] == "done", f"job failed: {job.get('error')}"
    assert job["files_changed"] == 1
    assert job["flows_generated"] == 1

    # The PR comment was rendered and "posted" to our fake.
    assert len(fake_github) == 1
    assert "RecordLoop" in fake_github[0]["body"]
    assert "dry_run_smoke_flow" in fake_github[0]["body"]


def test_trigger_with_api_key_gating(client, fake_github):
    """When RECORDLOOP_API_KEY is set, wrong key 401s and right key 200s."""
    os.environ["RECORDLOOP_API_KEY"] = "secret-for-test"
    try:
        bad = client.post(
            "/trigger",
            json={"repo": "x/y", "pr_number": 1, "github_token": "t"},
            headers={"X-Api-Key": "wrong"},
        )
        assert bad.status_code == 401

        good = client.post(
            "/trigger",
            json={"repo": "x/y", "pr_number": 1, "github_token": "t"},
            headers={"X-Api-Key": "secret-for-test"},
        )
        assert good.status_code == 200
    finally:
        del os.environ["RECORDLOOP_API_KEY"]


def test_get_job_404_for_unknown_id(client):
    r = client.get("/jobs/does-not-exist")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Cross-framework coverage — ensures the file filter is framework-agnostic.
# ---------------------------------------------------------------------------

from api.analyzer import _is_component  # noqa: E402


@pytest.mark.parametrize("filename", [
    # JS / TS frameworks
    "src/components/Signup.jsx",
    "src/components/Signup.tsx",
    "src/components/Signup.vue",
    "src/components/Signup.svelte",
    "src/pages/index.astro",
    # Angular
    "src/app/login/login.component.ts",
    "src/app/login/login.component.html",
    # .NET
    "Pages/Login.razor",
    "Views/Account/Login.cshtml",
    "Views/Account/Login.vbhtml",
    # Server templates
    "app/views/users/new.html.erb",
    "app/views/users/new.erb",
    "lib/my_app_web/live/login_live.heex",
    "lib/my_app_web/live/login_live.eex",
    "templates/login.jinja",
    "templates/login.jinja2",
    "templates/login.j2",
    "templates/login.twig",
    "templates/login.hbs",
    "templates/login.handlebars",
    "templates/login.mustache",
    "sections/header.liquid",
    "templates/login.njk",
    "views/login.pug",
    "templates/login.php",
    "templates/login.phtml",
    # Plain HTML (HTMX, static, etc.)
    "public/index.html",
    "public/index.htm",
    # Tests / stories — explicitly INCLUDED for context
    "src/components/Signup.test.tsx",
    "src/components/Signup.spec.jsx",
    "src/components/Signup.stories.tsx",
])
def test_is_component_recognizes_framework(filename):
    assert _is_component(filename), f"{filename} should have been recognized"


@pytest.mark.parametrize("filename", [
    # Genuinely non-UI files
    "README.md",
    "src/utils/math.ts",
    "src/server/db.py",
    "package.json",
    "Cargo.toml",
    # Type declarations and snapshots
    "src/types.d.ts",
    "src/__snapshots__/Foo.test.tsx.snap",
])
def test_is_component_skips_non_ui(filename):
    assert not _is_component(filename), f"{filename} should have been filtered out"


# ---------------------------------------------------------------------------
# Agent infrastructure unit tests (no LLM call required)
# ---------------------------------------------------------------------------

from api.analyzer import (  # noqa: E402
    _FileIndex,
    _dispatch_tool,
    _parse_flows,
)


def _sample_files():
    return [
        {
            "filename": "src/components/SignupForm.tsx",
            "status": "modified",
            "content": "line1\nline2\nline3\nline4\nline5\n",
            "patch": "@@ -1,2 +1,3 @@\n+new line\n existing\n existing2\n",
        },
        {
            "filename": "src/lib/auth.ts",
            "status": "modified",
            "content": "export function login() {}\n",
        },
        {
            "filename": "README.md",
            "status": "modified",
            "content": "# hi\n",
        },
    ]


def test_file_index_overview_groups_ui_first():
    idx = _FileIndex(_sample_files())
    overview = idx.overview()
    sf_pos = overview.index("SignupForm.tsx")
    auth_pos = overview.index("auth.ts")
    readme_pos = overview.index("README.md")
    # SignupForm.tsx is the only UI file → must come before the non-UI ones
    assert sf_pos < auth_pos
    assert sf_pos < readme_pos
    assert "1 UI / 2 other" in overview


def test_file_index_overview_only_ui():
    idx = _FileIndex(_sample_files())
    overview = idx.overview(only="ui")
    assert "SignupForm.tsx" in overview
    assert "auth.ts" not in overview
    assert "README.md" not in overview


def test_file_index_read_file_full_and_sliced():
    idx = _FileIndex(_sample_files())
    full = idx.read_file("src/components/SignupForm.tsx")
    assert full.startswith("line1") and "line5" in full

    sliced = idx.read_file("src/components/SignupForm.tsx", start_line=2, end_line=4)
    assert sliced == "line2\nline3\nline4"


def test_file_index_read_file_unknown_path():
    idx = _FileIndex(_sample_files())
    assert "ERROR" in idx.read_file("nope.tsx")


def test_file_index_read_diff():
    idx = _FileIndex(_sample_files())
    diff = idx.read_diff("src/components/SignupForm.tsx")
    assert "@@" in diff and "new line" in diff


def test_dispatch_tool_enforces_read_file_budget():
    idx = _FileIndex(_sample_files())
    state = {"files_read": 30, "input_tokens": 0}  # already at the cap
    result = _dispatch_tool("read_file", {"path": "src/components/SignupForm.tsx"}, idx, state)
    assert "budget exhausted" in result
    assert state["files_read"] == 30  # not incremented


def test_dispatch_tool_unknown_name():
    idx = _FileIndex(_sample_files())
    state = {"files_read": 0, "input_tokens": 0}
    result = _dispatch_tool("nuke_database", {}, idx, state)
    assert "unknown tool" in result


def test_parse_flows_handles_partial_garbage():
    """The parser must skip malformed flows/steps without raising."""
    payload = {
        "flows": [
            {"name": "good", "description": "ok", "component_file": "x.tsx", "navigate_to": "/", "steps": [
                {"action": "click", "selector": "#go"},
                {"selector": "no action"},   # missing action — should skip
                "not a dict",                  # garbage — should skip
            ]},
            {"description": "no name"},      # missing name — should skip whole flow
            "totally bogus",                  # garbage — should skip
        ]
    }
    flows = _parse_flows(payload)
    assert len(flows) == 1
    assert flows[0].name == "good"
    assert len(flows[0].steps) == 1
    assert flows[0].steps[0].action == "click"
