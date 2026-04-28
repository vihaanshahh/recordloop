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

import json
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
    """Patch get_pr_files + upsert_pr_comment so /trigger never touches GitHub."""

    async def fake_get_pr_files(repo, pr_number, token):
        return [{
            "filename": "src/components/SignupForm.tsx",
            "status": "modified",
            "content": '<form><button id="go">Sign up</button></form>',
            "patch": "",
        }]

    posted = []

    async def fake_upsert_pr_comment(repo, pr_number, body, token, **kwargs):
        posted.append({"repo": repo, "pr_number": pr_number, "body": body})

    with patch("api.main.get_pr_files", side_effect=fake_get_pr_files), \
         patch("api.main.upsert_pr_comment", side_effect=fake_upsert_pr_comment):
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
    result = analyze_pr(
        [{
            "filename": "src/components/SignupForm.tsx",
            "status": "modified",
            "content": '<form><button id="go">Sign up</button></form>',
        }],
        preview_url="http://localhost:3000",
    )
    flows = result.flows
    assert len(flows) == 1
    assert flows[0].name == "dry_run_smoke_flow"
    assert len(flows[0].steps) == 2
    assert flows[0].steps[0].action == "click"
    assert flows[0].steps[1].action == "assert_visible"
    # Dry-run must still populate cost (with model="(dry-run)") so the comment
    # renderer's cost-footer code path is exercised end-to-end.
    assert result.cost.model == "(dry-run)"


def test_analyzer_skips_non_component_files():
    flows = analyze_pr(
        [{"filename": "README.md", "status": "modified", "content": "# hi"}],
        preview_url="http://localhost:3000",
    ).flows
    assert flows == []


def test_analyzer_includes_test_and_story_files():
    """Test files and Storybook stories are valuable context — keep them."""
    flows = analyze_pr(
        [
            {"filename": "src/Foo.test.tsx", "status": "modified", "content": "<div/>"},
            {"filename": "src/Foo.stories.tsx", "status": "modified", "content": "<div/>"},
        ],
        preview_url="http://localhost:3000",
    ).flows
    # Dry-run returns one synthetic flow regardless of how many files came in,
    # but the important thing is the call succeeded (i.e. files were not filtered out).
    assert len(flows) == 1


def test_analyzer_skips_type_declaration_files():
    flows = analyze_pr(
        [{"filename": "src/types.d.ts", "status": "modified", "content": "export type X = number"}],
        preview_url="http://localhost:3000",
    ).flows
    assert flows == []


def test_analyzer_skips_jest_snapshots():
    flows = analyze_pr(
        [{"filename": "src/__snapshots__/Foo.test.tsx.snap", "status": "modified", "content": "snap"}],
        preview_url="http://localhost:3000",
    ).flows
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


# ---------------------------------------------------------------------------
# Repo context tests
# ---------------------------------------------------------------------------

from api.repo_context import parse_recordloop_md, RepoContext, _glob_match  # noqa: E402


@pytest.mark.parametrize("filename,pattern,expected", [
    # ** matches across / boundaries
    ("docs/guide.md", "docs/**", True),
    ("docs/deep/nested/file.md", "docs/**", True),
    ("generated/Bar.tsx", "generated/**", True),
    ("generated/deep/Bar.tsx", "generated/**", True),
    # * does NOT match across /
    ("src/App.tsx", "src/*.tsx", True),
    ("src/deep/App.tsx", "src/*.tsx", False),
    ("src/README.md", "*.md", False),       # star at root doesn't cross /
    # ** in the middle
    ("src/deep/App.tsx", "src/**/*.tsx", True),
    ("src/deep/nested/App.tsx", "src/**/*.tsx", True),   # triple-deep
    # ** at start (no leading dir)
    ("src/deep/nested/Foo.tsx", "**/*.tsx", True),
    ("Foo.tsx", "**/*.tsx", True),            # root-level file
    # ** boundary: must not match partial names
    ("xfoo.tsx", "**/foo.tsx", False),        # xfoo != foo
    ("src/foo.tsx", "**/foo.tsx", True),      # exact match after /
    ("foo.tsx", "**/foo.tsx", True),          # exact match at root
    # leading dot in directory
    (".github/recordloop.md", ".github/**", True),
    (".github/workflows/ci.yml", ".github/**", True),
    # bare * at top level
    ("README.md", "*.md", True),
    ("src/App.tsx", "*.md", False),
    # exact match (no wildcards)
    ("package.json", "package.json", True),
    ("src/package.json", "package.json", False),
    # ? wildcard
    ("src/App.tsx", "src/???.tsx", True),
    ("src/AppX.tsx", "src/???.tsx", False),   # 4 chars != 3 ?s
    # empty pattern
    ("anything.tsx", "", False),
    # no match
    ("other/File.tsx", "generated/**", False),
])
def test_glob_match(filename, pattern, expected):
    assert _glob_match(filename, pattern) == expected, (
        f"_glob_match({filename!r}, {pattern!r}) should be {expected}"
    )


def test_parse_recordloop_md_full():
    raw = """---
ignore_paths:
  - apps/legacy/**
  - docs/**
context_globs:
  - packages/ui/**
selector_convention: data-testid
default_navigate_to: /dashboard
login_config: storage-state
---

# Acme Web

This is a B2B analytics dashboard.
"""
    ctx = parse_recordloop_md(raw)
    assert ctx.ignore_paths == ["apps/legacy/**", "docs/**"]
    assert ctx.context_globs == ["packages/ui/**"]
    assert ctx.selector_convention == "data-testid"
    assert ctx.default_navigate_to == "/dashboard"
    assert ctx.login_config == "storage-state"
    assert "B2B analytics" in ctx.body


def test_parse_recordloop_md_no_frontmatter():
    raw = "Just a plain markdown file with some notes."
    ctx = parse_recordloop_md(raw)
    assert ctx.ignore_paths == []
    assert ctx.body == "Just a plain markdown file with some notes."


def test_parse_recordloop_md_empty():
    ctx = parse_recordloop_md("")
    assert ctx.body == ""
    assert ctx.ignore_paths == []


def test_parse_recordloop_md_bad_yaml():
    raw = """---
: invalid: yaml: [
---

Body text here.
"""
    ctx = parse_recordloop_md(raw)
    # Malformed YAML — treat entire file as body
    assert "Body text" in ctx.body or "invalid" in ctx.body


def test_parse_recordloop_md_partial_frontmatter():
    raw = """---
ignore_paths:
  - generated/**
---

Some context.
"""
    ctx = parse_recordloop_md(raw)
    assert ctx.ignore_paths == ["generated/**"]
    assert ctx.default_navigate_to == "/"
    assert ctx.body == "Some context."


def test_repo_context_apply_ignore_paths():
    ctx = RepoContext(ignore_paths=["docs/**", "*.md"])
    files = [
        {"filename": "src/App.tsx"},
        {"filename": "docs/guide.md"},
        {"filename": "docs/deep/nested/api.md"},
        {"filename": "README.md"},
    ]
    filtered = ctx.apply_ignore_paths(files)
    assert len(filtered) == 1
    assert filtered[0]["filename"] == "src/App.tsx"


def test_repo_context_to_system_message():
    empty = RepoContext(body="")
    assert empty.to_system_message() is None

    full = RepoContext(body="This is a Next.js app.")
    msg = full.to_system_message()
    assert msg is not None
    assert "Next.js app" in msg
    assert "Repository context" in msg


# ---------------------------------------------------------------------------
# Login tests
# ---------------------------------------------------------------------------

import base64  # noqa: E402
from api.login import decode_storage_state, resolve_storage_state  # noqa: E402


def test_decode_storage_state_valid():
    state = {"cookies": [{"name": "session", "value": "abc123"}], "origins": []}
    encoded = base64.b64encode(json.dumps(state).encode()).decode()
    result = decode_storage_state(encoded)
    assert result["cookies"][0]["name"] == "session"


def test_decode_storage_state_invalid_base64():
    with pytest.raises(ValueError, match="base64"):
        decode_storage_state("not-valid-base64!!!")


def test_decode_storage_state_invalid_json():
    encoded = base64.b64encode(b"not json").decode()
    with pytest.raises(ValueError, match="JSON"):
        decode_storage_state(encoded)


def test_decode_storage_state_missing_cookies_key():
    encoded = base64.b64encode(b'{"origins": []}').decode()
    with pytest.raises(ValueError, match="cookies"):
        decode_storage_state(encoded)


def test_resolve_storage_state_none_when_unset():
    old = os.environ.pop("RECORDLOOP_STORAGE_STATE", None)
    try:
        assert resolve_storage_state() is None
    finally:
        if old is not None:
            os.environ["RECORDLOOP_STORAGE_STATE"] = old


def test_resolve_storage_state_from_env():
    state = {"cookies": [{"name": "s", "value": "v"}], "origins": []}
    encoded = base64.b64encode(json.dumps(state).encode()).decode()
    os.environ["RECORDLOOP_STORAGE_STATE"] = encoded
    try:
        result = resolve_storage_state()
        assert result is not None
        assert result["cookies"][0]["name"] == "s"
    finally:
        del os.environ["RECORDLOOP_STORAGE_STATE"]


# ---------------------------------------------------------------------------
# Analyzer integration with new params
# ---------------------------------------------------------------------------


def test_analyze_pr_accepts_repo_context_body():
    """analyze_pr with repo_context_body kwarg must not break dry-run."""
    flows = analyze_pr(
        [{"filename": "src/Foo.tsx", "status": "modified", "content": "<div/>"}],
        preview_url="http://localhost:3000",
        repo_context_body="This is a Next.js app with data-testid selectors.",
    ).flows
    assert len(flows) == 1
    assert flows[0].name == "dry_run_smoke_flow"


def test_ignore_paths_filters_shallow():
    """ignore_paths with ** should filter files one level deep."""
    ctx = RepoContext(ignore_paths=["generated/**"])
    files = [
        {"filename": "src/Foo.tsx"},
        {"filename": "generated/Bar.tsx"},
    ]
    filtered = ctx.apply_ignore_paths(files)
    assert [f["filename"] for f in filtered] == ["src/Foo.tsx"]


def test_ignore_paths_filters_deeply_nested():
    """ignore_paths with ** must match files at arbitrary depth."""
    ctx = RepoContext(ignore_paths=["generated/**"])
    files = [
        {"filename": "src/Foo.tsx"},
        {"filename": "generated/Bar.tsx"},
        {"filename": "generated/deep/Baz.tsx"},
        {"filename": "generated/deep/nested/Qux.tsx"},
    ]
    filtered = ctx.apply_ignore_paths(files)
    assert [f["filename"] for f in filtered] == ["src/Foo.tsx"]


def test_ignore_paths_removes_all():
    """If ignore_paths removes every file, result is empty."""
    ctx = RepoContext(ignore_paths=["src/**"])
    files = [{"filename": "src/Foo.tsx"}, {"filename": "src/deep/Bar.tsx"}]
    filtered = ctx.apply_ignore_paths(files)
    assert filtered == []


def test_analyze_pr_ignore_paths_filters_in_dry_run():
    """analyze_pr honours ignore_paths even in dry-run mode."""
    flows = analyze_pr(
        [
            {"filename": "src/Foo.tsx", "status": "modified", "content": "<div/>"},
            {"filename": "generated/Bar.tsx", "status": "modified", "content": "<div/>"},
            {"filename": "generated/deep/Baz.tsx", "status": "modified", "content": "<div/>"},
        ],
        preview_url="http://localhost:3000",
        ignore_paths=["generated/**"],
    ).flows
    # generated/** filtered out, src/Foo.tsx survives -> dry-run flow returned
    assert len(flows) == 1
    assert flows[0].name == "dry_run_smoke_flow"


def test_analyze_pr_ignore_paths_removes_all_ui():
    """If ignore_paths removes all UI files, return empty even in dry-run."""
    flows = analyze_pr(
        [{"filename": "src/Foo.tsx", "status": "modified", "content": "<div/>"}],
        preview_url="http://localhost:3000",
        ignore_paths=["src/**"],
    ).flows
    assert flows == []


# ---------------------------------------------------------------------------
# Additional edge-case tests (from review)
# ---------------------------------------------------------------------------


def test_parse_recordloop_md_windows_line_endings():
    raw = "---\r\nignore_paths:\r\n  - docs/**\r\n---\r\n\r\nBody here.\r\n"
    ctx = parse_recordloop_md(raw)
    assert ctx.ignore_paths == ["docs/**"]
    assert ctx.body == "Body here."


def test_parse_recordloop_md_extra_fields_ignored():
    raw = """---
ignore_paths:
  - docs/**
version: 99
experimental_feature: true
---

Body.
"""
    ctx = parse_recordloop_md(raw)
    assert ctx.ignore_paths == ["docs/**"]
    assert ctx.body == "Body."


def test_parse_recordloop_md_body_with_triple_dash():
    raw = """---
selector_convention: data-testid
---

Some context.

---

More context after the horizontal rule.
"""
    ctx = parse_recordloop_md(raw)
    assert ctx.selector_convention == "data-testid"
    assert "---" in ctx.body
    assert "More context" in ctx.body


def test_parse_recordloop_md_string_ignore_paths():
    """If user writes ignore_paths as a string, it should be wrapped in a list."""
    raw = """---
ignore_paths: "*.md"
---

Body.
"""
    ctx = parse_recordloop_md(raw)
    assert ctx.ignore_paths == ["*.md"]


def test_decode_storage_state_missing_padding():
    """Base64 with stripped padding should still decode."""
    state = {"cookies": [{"name": "s", "value": "v"}], "origins": []}
    encoded = base64.b64encode(json.dumps(state).encode()).decode()
    stripped = encoded.rstrip("=")
    result = decode_storage_state(stripped)
    assert result["cookies"][0]["name"] == "s"


def test_decode_storage_state_empty_cookies_list():
    """Storage state with empty cookies list should be accepted."""
    state = {"cookies": [], "origins": []}
    encoded = base64.b64encode(json.dumps(state).encode()).decode()
    result = decode_storage_state(encoded)
    assert result["cookies"] == []


# ---------------------------------------------------------------------------
# Cost computation + comment-footer rendering
# ---------------------------------------------------------------------------

from api.analyzer import CostInfo, _price_for, _anthropic_url, _ANTHROPIC_TOOLS, _TOOLS  # noqa: E402
from api.run_action import _format_cost_footer, _render_comment  # noqa: E402


def test_price_for_known_models():
    assert _price_for("claude-opus-4-7")["input"] == 15.00
    assert _price_for("claude-opus-4-7")["output"] == 75.00
    assert _price_for("gpt-4o-mini")["input"] == 0.15
    # Family prefix match — model with a date suffix should resolve.
    assert _price_for("gpt-5.4-2026-01-15")["input"] == 5.00


def test_price_for_unknown_model_falls_back():
    p = _price_for("totally-made-up-model")
    assert p["input"] == 5.0 and p["output"] == 15.0


def test_cost_info_finalize_computes_usd():
    c = CostInfo(provider="anthropic", model="claude-opus-4-7")
    c.add_usage(input_t=10_000, output_t=2_000)
    c.finalize()
    # 10K * $15/M = $0.15 ; 2K * $75/M = $0.15 ; total $0.30
    assert abs(c.usd - 0.30) < 1e-6
    assert c.input_tokens == 10_000
    assert c.output_tokens == 2_000


def test_cost_info_finalize_zero_when_no_usage():
    c = CostInfo(provider="openai", model="gpt-4o-mini")
    c.finalize()
    assert c.usd == 0.0


def test_cost_info_accumulates_across_calls():
    c = CostInfo(provider="openai", model="gpt-4o-mini")
    c.add_usage(1000, 100)
    c.add_usage(500, 50, cached_t=200)
    assert c.input_tokens == 1500
    assert c.output_tokens == 150
    assert c.cached_input_tokens == 200


def test_format_cost_footer_with_real_usage():
    c = CostInfo(provider="anthropic", model="claude-opus-4-7")
    c.add_usage(12_300, 1_100)
    c.finalize()
    footer = _format_cost_footer(c)
    assert "claude-opus-4-7" in footer
    assert "$" in footer
    assert "12.3K in" in footer
    assert "1.1K out" in footer


def test_format_cost_footer_skips_when_no_tokens():
    """Non-dry-run with 0 tokens means no LLM call was made — skip footer."""
    c = CostInfo(provider="openai", model="gpt-4o-mini")
    c.finalize()
    assert _format_cost_footer(c) == ""


def test_format_cost_footer_shows_on_dry_run():
    """Dry-run should show the footer even with 0 tokens so the user can
    confirm the dry-run path fired correctly."""
    c = CostInfo(provider="openai", model="(dry-run)")
    c.finalize()
    footer = _format_cost_footer(c)
    assert "(dry-run)" in footer
    assert "$0" in footer


def test_format_cost_footer_handles_none():
    """Older callers that don't pass cost shouldn't break the renderer."""
    assert _format_cost_footer(None) == ""


def test_render_comment_includes_cost_footer():
    from api.analyzer import InteractionFlow, InteractionStep
    flow = InteractionFlow(
        name="hero_cta_clicked",
        description="Click the hero CTA",
        component_file="src/Hero.tsx",
        navigate_to="/",
        change_context="The PR adds an href; this asserts it.",
        steps=[
            InteractionStep(action="click", selector="[data-testid=cta]"),
            InteractionStep(action="assert_attribute", selector="[data-testid=cta]", value="href=github"),
        ],
    )
    cost = CostInfo(provider="anthropic", model="claude-opus-4-7")
    cost.add_usage(5000, 500)
    cost.finalize()
    body = _render_comment([flow], "https://preview.example.com", recordings=None, cost=cost)
    assert "claude-opus-4-7" in body
    assert "RecordLoop" in body
    # The footer is appended AFTER the body content
    assert body.rstrip().splitlines()[-1].startswith("_Analyzed by")


def test_render_comment_no_flows_includes_cost_footer():
    cost = CostInfo(provider="anthropic", model="claude-opus-4-7")
    cost.add_usage(2000, 100)
    cost.finalize()
    body = _render_comment([], "", recordings=None, cost=cost)
    assert "No recordable UI changes" in body
    assert "claude-opus-4-7" in body


# ---------------------------------------------------------------------------
# Anthropic provider — URL resolution + agent loop
# ---------------------------------------------------------------------------


def test_anthropic_url_resolution():
    # Empty falls back to native Anthropic v1.
    assert _anthropic_url("") == "https://api.anthropic.com/v1/messages"
    assert _anthropic_url(None) == "https://api.anthropic.com/v1/messages"
    # Bare host gets /v1/messages appended (matches official SDK behavior).
    assert _anthropic_url("https://api.anthropic.com") == "https://api.anthropic.com/v1/messages"
    # URL already ending in /v1 just gets /messages appended.
    assert _anthropic_url("https://api.anthropic.com/v1") == "https://api.anthropic.com/v1/messages"
    # Foundry's Anthropic-passthrough route — must become /anthropic/v1/messages,
    # NOT /anthropic/messages (would 404 against the Foundry router).
    foundry = "https://vishahdev-resource.services.ai.azure.com/anthropic"
    assert _anthropic_url(foundry) == foundry + "/v1/messages"
    assert _anthropic_url(foundry + "/") == foundry + "/v1/messages"
    # Already-final URL passes through unchanged (any path).
    final = "https://api.anthropic.com/v1/messages"
    assert _anthropic_url(final) == final
    foundry_full = "https://X.services.ai.azure.com/anthropic/v1/messages"
    assert _anthropic_url(foundry_full) == foundry_full


def test_anthropic_tools_are_derived_from_openai_tools():
    """The two tool schemas must stay in sync — derived list keeps them so."""
    assert len(_ANTHROPIC_TOOLS) == len(_TOOLS)
    names_openai = {t["function"]["name"] for t in _TOOLS}
    names_anth = {t["name"] for t in _ANTHROPIC_TOOLS}
    assert names_openai == names_anth
    # Anthropic format: input_schema instead of parameters.
    for t in _ANTHROPIC_TOOLS:
        assert "input_schema" in t
        assert "type" not in t  # no OpenAI wrapper


def test_anthropic_loop_threads_tool_use_to_submit_flows(monkeypatch):
    """Drive the anthropic agent loop end-to-end with a fake Foundry response.

    Sequence:
      1. Model calls read_diff(SignupForm.tsx)
      2. Tool result is threaded back, model calls submit_flows
      3. submit_flows terminates the loop; flows are returned with cost.
    """
    from api import analyzer as az

    # Disable dry-run for this single test so the anthropic loop actually runs.
    monkeypatch.delenv("RECORDLOOP_DRY_RUN", raising=False)

    call_count = {"n": 0}

    def fake_post(base_url, api_key, api_version, payload):
        call_count["n"] += 1
        # First turn: model decides to read the diff.
        if call_count["n"] == 1:
            return {
                "id": "msg_1",
                "model": payload["model"],
                "role": "assistant",
                "stop_reason": "tool_use",
                "type": "message",
                "content": [
                    {"type": "text", "text": "Let me look at the diff."},
                    {
                        "type": "tool_use",
                        "id": "toolu_001",
                        "name": "read_diff",
                        "input": {"path": "src/components/SignupForm.tsx"},
                    },
                ],
                "usage": {"input_tokens": 1500, "output_tokens": 30},
            }
        # Second turn: model submits flows.
        return {
            "id": "msg_2",
            "model": payload["model"],
            "role": "assistant",
            "stop_reason": "tool_use",
            "type": "message",
            "content": [
                {
                    "type": "tool_use",
                    "id": "toolu_002",
                    "name": "submit_flows",
                    "input": {
                        "flows": [
                            {
                                "name": "signup_button_click",
                                "description": "Click the new signup button",
                                "component_file": "src/components/SignupForm.tsx",
                                "navigate_to": "/",
                                "change_context": "The PR adds a Sign up button; flow asserts it is visible.",
                                "steps": [
                                    {"action": "click", "selector": "#go"},
                                    {"action": "assert_visible", "selector": "#go"},
                                ],
                            }
                        ]
                    },
                }
            ],
            "usage": {"input_tokens": 2200, "output_tokens": 180},
        }

    monkeypatch.setattr(az, "_anthropic_post", fake_post)

    result = az.analyze_pr(
        [{
            "filename": "src/components/SignupForm.tsx",
            "status": "modified",
            "content": '<form><button id="go">Sign up</button></form>',
            "patch": "@@ -1,1 +1,1 @@\n+<button id=\"go\">Sign up</button>",
        }],
        preview_url="http://localhost:5173",
        provider="anthropic",
        api_key="fake-key",
        model="claude-opus-4-7",
        anthropic_base_url="https://vishahdev-resource.services.ai.azure.com/api/projects/vishahdev",
    )

    assert call_count["n"] == 2, "loop should call _anthropic_post twice (read_diff + submit_flows)"
    assert len(result.flows) == 1
    assert result.flows[0].name == "signup_button_click"
    assert result.flows[0].steps[0].action == "click"
    # Cost was accumulated across both turns and finalized.
    assert result.cost.input_tokens == 1500 + 2200
    assert result.cost.output_tokens == 30 + 180
    assert result.cost.model == "claude-opus-4-7"
    assert result.cost.usd > 0


def test_anthropic_loop_terminates_on_no_tool_use(monkeypatch):
    """If the model returns text-only with no tool_use blocks, the loop must
    stop gracefully and return zero flows (with cost still tracked)."""
    from api import analyzer as az

    monkeypatch.delenv("RECORDLOOP_DRY_RUN", raising=False)

    def fake_post(base_url, api_key, api_version, payload):
        return {
            "id": "msg_x",
            "model": payload["model"],
            "role": "assistant",
            "stop_reason": "end_turn",
            "type": "message",
            "content": [{"type": "text", "text": "I have no opinion."}],
            "usage": {"input_tokens": 800, "output_tokens": 20},
        }

    monkeypatch.setattr(az, "_anthropic_post", fake_post)

    result = az.analyze_pr(
        [{"filename": "src/Foo.tsx", "status": "modified", "content": "<div/>"}],
        preview_url="",
        provider="anthropic",
        api_key="fake-key",
        anthropic_base_url="https://api.anthropic.com/v1",
    )
    assert result.flows == []
    # Cost still tracked even on bail-out.
    assert result.cost.input_tokens == 800
    assert result.cost.output_tokens == 20


def test_anthropic_missing_api_key_raises(monkeypatch):
    """Anthropic provider with no key must raise a clear error, not crash deep
    inside httpx."""
    from api import analyzer as az
    monkeypatch.delenv("RECORDLOOP_DRY_RUN", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        az.analyze_pr(
            [{"filename": "src/Foo.tsx", "status": "modified", "content": "<div/>"}],
            preview_url="",
            provider="anthropic",
            api_key=None,
        )


# ---------------------------------------------------------------------------
# Storybook framework detection
# ---------------------------------------------------------------------------

def _write_pkg(dir_path, deps=None, dev_deps=None):
    pkg = {"name": "fixture", "version": "0.0.0"}
    if deps:
        pkg["dependencies"] = deps
    if dev_deps:
        pkg["devDependencies"] = dev_deps
    (dir_path / "package.json").write_text(json.dumps(pkg))


def test_detect_framework_storybook_via_config_dir(tmp_path):
    """An explicit .storybook/main.ts wins over @storybook/* in deps."""
    from recordloop.config.settings import detect_framework
    _write_pkg(tmp_path, deps={"react": "^18.0.0"}, dev_deps={"@storybook/react": "^8.0.0"})
    sb = tmp_path / "apps" / "storybook" / ".storybook"
    sb.mkdir(parents=True)
    (sb / "main.ts").write_text("export default {};")
    assert detect_framework(str(tmp_path)) == "storybook"


def test_detect_framework_storybook_ignored_when_no_config_dir(tmp_path):
    """A repo with @storybook/* in deps but no .storybook config must NOT be
    detected as storybook — many apps embed Storybook for component dev but
    want their main app served in CI."""
    from recordloop.config.settings import detect_framework
    _write_pkg(tmp_path, deps={"react": "^18.0.0", "next": "^14.0.0"}, dev_deps={"@storybook/react": "^8.0.0"})
    # No .storybook/main.* — falls back to next.
    assert detect_framework(str(tmp_path)) == "next"


def test_detect_framework_storybook_skips_node_modules(tmp_path):
    """A .storybook/main.ts under node_modules is library code, not a real
    config — must not match."""
    from recordloop.config.settings import detect_framework
    _write_pkg(tmp_path, deps={"react": "^18.0.0"})
    fake = tmp_path / "node_modules" / "@storybook" / "react" / ".storybook"
    fake.mkdir(parents=True)
    (fake / "main.ts").write_text("export default {};")
    # Falls back to react, not storybook.
    assert detect_framework(str(tmp_path)) == "react"


def test_framework_defaults_includes_storybook():
    from recordloop.config.settings import FRAMEWORK_DEFAULTS
    assert FRAMEWORK_DEFAULTS["storybook"]["port"] == 6006


# ---------------------------------------------------------------------------
# login_capture: env-validation + URL resolution (no browser needed)
# ---------------------------------------------------------------------------

def test_login_capture_resolve_absolute_url():
    from api.login_capture import _resolve_login_url
    assert _resolve_login_url("https://app.example.com/login", "") == \
        "https://app.example.com/login"


def test_login_capture_resolve_relative_url():
    from api.login_capture import _resolve_login_url
    assert _resolve_login_url("/login", "http://localhost:3000") == \
        "http://localhost:3000/login"


def test_login_capture_resolve_relative_no_base_raises():
    from api.login_capture import _resolve_login_url
    with pytest.raises(SystemExit, match="login-url is relative"):
        _resolve_login_url("/login", "")


def test_login_capture_main_missing_credentials_returns_2(monkeypatch):
    """If username or password is missing, login_capture should exit 2 with a
    clear message (and not even import playwright)."""
    from api import login_capture
    for var in (
        "RECORDLOOP_LOGIN_URL",
        "RECORDLOOP_LOGIN_USERNAME",
        "RECORDLOOP_LOGIN_PASSWORD",
        "RECORDLOOP_LOGIN_USERNAME_SELECTOR",
        "RECORDLOOP_LOGIN_PASSWORD_SELECTOR",
        "RECORDLOOP_LOGIN_SUBMIT_SELECTOR",
        "RECORDLOOP_LOGIN_SUCCESS_URL",
    ):
        monkeypatch.delenv(var, raising=False)
    rc = login_capture.main()
    assert rc == 2


def test_login_capture_main_username_only_returns_2(monkeypatch):
    """Setting username without password is a partial config and must fail."""
    from api import login_capture
    monkeypatch.setenv("RECORDLOOP_LOGIN_USERNAME", "alice@example.com")
    monkeypatch.delenv("RECORDLOOP_LOGIN_PASSWORD", raising=False)
    rc = login_capture.main()
    assert rc == 2


def test_login_capture_defaults_present():
    """Smart-default selectors are non-empty and look like CSS — this is what
    makes the login-* selector inputs optional."""
    from api import login_capture
    assert "input[type=\"email\"]" in login_capture._DEFAULT_USERNAME_SELECTOR
    assert "input[type=\"password\"]" in login_capture._DEFAULT_PASSWORD_SELECTOR
    assert "button[type=\"submit\"]" in login_capture._DEFAULT_SUBMIT_SELECTOR
    assert login_capture._DEFAULT_LOGIN_URL == "/login"
