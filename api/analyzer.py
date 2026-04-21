"""
AI-powered component analyzer (agentic).

Instead of pre-truncating the PR diff to a fixed slice, this module hands an
LLM a lightweight overview of every changed file and lets it pull what it
actually needs via tool calls. The agent decides how deep to go, when to
stop, and what to ignore. Cost is bounded by hard caps on iterations, files
read, and cumulative input tokens.

Three providers are supported:
  - openai     OpenAI chat-completions, function-calling tools.
  - azure      Azure OpenAI chat-completions, function-calling tools.
  - anthropic  Anthropic Messages API (works against api.anthropic.com OR
               Azure AI Foundry's Anthropic-compatible endpoint by setting
               ANTHROPIC_BASE_URL to the Foundry project URL).

Public API: ``analyze_pr(changed_files, preview_url, ...) -> AnalyzeResult``
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Public dataclasses (consumed by api/cloud_recorder.py)
# ---------------------------------------------------------------------------


@dataclass
class InteractionStep:
    # Interaction actions: click | fill | select | navigate | wait | hover
    # Assertion actions:   assert_text | assert_attribute | assert_url | assert_visible
    action: str
    selector: str
    value: Optional[str] = None
    description: str = ""

    @property
    def is_assertion(self) -> bool:
        return self.action.lower().startswith("assert_")


@dataclass
class InteractionFlow:
    name: str
    description: str
    component_file: str
    navigate_to: str = "/"
    steps: list[InteractionStep] = field(default_factory=list)
    change_context: str = ""  # 2-sentence plain-English summary of the diff change this flow tests


# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------

DEFAULT_MODEL = "gpt-5.4"
DEFAULT_ANTHROPIC_MODEL = "claude-opus-4-7"

MAX_ITERATIONS = 10                 # safety net for runaway loops
MAX_FILES_READ = 30                 # cumulative read_file calls per PR
MAX_TOTAL_INPUT_TOKENS = 50_000     # cumulative across the whole loop
MAX_OUTPUT_TOKENS_PER_TURN = 2048


# ---------------------------------------------------------------------------
# Pricing table (USD per 1M tokens) — used to compute the cost footer in the
# PR comment. Numbers are approximate; users with custom contracts can ignore
# them. Anything not listed falls back to _FALLBACK_PRICE.
# ---------------------------------------------------------------------------

_FALLBACK_PRICE = {"input": 5.0, "output": 15.0}

_PRICE_TABLE: dict[str, dict[str, float]] = {
    # OpenAI
    "gpt-5.4":            {"input": 5.00, "output": 15.00},
    "gpt-5":              {"input": 5.00, "output": 15.00},
    "gpt-4o":             {"input": 2.50, "output": 10.00},
    "gpt-4o-mini":        {"input": 0.15, "output": 0.60},
    "gpt-4.1":            {"input": 2.00, "output": 8.00},
    "gpt-4.1-mini":       {"input": 0.40, "output": 1.60},
    # Anthropic (Claude 4.x family)
    "claude-opus-4-7":    {"input": 15.00, "output": 75.00},
    "claude-opus-4":      {"input": 15.00, "output": 75.00},
    "claude-sonnet-4-6":  {"input":  3.00, "output": 15.00},
    "claude-sonnet-4":    {"input":  3.00, "output": 15.00},
    "claude-haiku-4-5":   {"input":  1.00, "output":  5.00},
}


def _price_for(model: str) -> dict[str, float]:
    if not model:
        return _FALLBACK_PRICE
    key = model.lower()
    if key in _PRICE_TABLE:
        return _PRICE_TABLE[key]
    # Match on family prefix (e.g. "gpt-5.4-2026-01-15" -> "gpt-5.4")
    for k, v in _PRICE_TABLE.items():
        if key.startswith(k):
            return v
    return _FALLBACK_PRICE


@dataclass
class CostInfo:
    """Per-PR LLM cost breakdown surfaced in the PR comment footer."""

    provider: str = ""
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0  # informational; not subtracted from input_tokens
    usd: float = 0.0

    def add_usage(self, input_t: int, output_t: int, cached_t: int = 0) -> None:
        self.input_tokens += int(input_t or 0)
        self.output_tokens += int(output_t or 0)
        self.cached_input_tokens += int(cached_t or 0)

    def finalize(self) -> None:
        price = _price_for(self.model)
        self.usd = round(
            (self.input_tokens / 1_000_000) * price["input"]
            + (self.output_tokens / 1_000_000) * price["output"],
            6,
        )


@dataclass
class AnalyzeResult:
    """Return type for ``analyze_pr``. Carries the flows AND a cost breakdown
    so the PR-comment renderer can show the user what the run actually cost."""

    flows: list[InteractionFlow] = field(default_factory=list)
    cost: CostInfo = field(default_factory=CostInfo)


# ---------------------------------------------------------------------------
# Dry-run support
# ---------------------------------------------------------------------------

_DRY_RUN_FLOWS_PAYLOAD: dict = {
    "flows": [
        {
            "name": "dry_run_smoke_flow",
            "description": "Synthetic flow returned by RECORDLOOP_DRY_RUN — no LLM call was made",
            "component_file": "dry-run.tsx",
            "navigate_to": "/",
            "change_context": "Dry-run flow — no real diff was analysed. This synthetic flow always navigates to the root page.",
            "steps": [
                {
                    "action": "click",
                    "selector": "[data-testid=dry-run]",
                    "description": "Synthetic click",
                },
                {
                    "action": "assert_visible",
                    "selector": "[data-testid=dry-run]",
                    "description": "Synthetic assertion",
                },
            ],
        }
    ]
}


def _dry_run_enabled() -> bool:
    return os.environ.get("RECORDLOOP_DRY_RUN", "").lower() in ("1", "true", "yes")


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

_SYSTEM = (
    "You are a frontend QA expert reviewing a pull request. Your job is to "
    "generate ONE Playwright interaction flow that demonstrates AND VERIFIES "
    "the SPECIFIC UI change introduced by this PR — and nothing else. The "
    "flow must contain real assertions, not just a navigate-and-click "
    "screensaver.\n\n"
    "The PR may touch React, Vue, Svelte, Angular, Astro, Blazor, Razor / "
    "ASP.NET, plain HTML, or any server-rendered template (ERB, Jinja, Twig, "
    "Handlebars, Liquid, Phoenix LiveView, PHP, …). Recording is browser-only, "
    "so reason about the rendered HTML — not the source language.\n\n"
    "ABSOLUTE RULES — only what changed and what immediately surrounds it:\n"
    "  1. Every step in your flow must touch an element that EITHER appears "
    "on a `+` line in the diff OR sits directly next to one (its parent, "
    "label, or sibling within ~5 lines). No exceptions.\n"
    "  2. Do NOT write generic smoke tests, do NOT navigate through unchanged "
    "menus, do NOT click unrelated buttons, do NOT scroll the page just to "
    "show off other sections. If the diff only touched the hero, your flow "
    "must stay in the hero.\n"
    "  3. Pick the SINGLE most user-visible change. If the PR adds a copy "
    "button, the flow clicks the copy button. If it changes a form field, "
    "the flow fills that field. If it removes a CTA, the flow navigates and "
    "waits to confirm the page renders without it.\n"
    "  4. Keep the flow short — 3 to 6 steps total including the initial "
    "navigate AND at least one assertion. A long flow that visits unrelated "
    "UI is WORSE than a short one focused on the diff.\n"
    "  5. EVERY flow must contain at least one assertion step that verifies "
    "the diff actually does what the PR claims. A flow with no assertions "
    "is rejected — it's a demo, not a test.\n\n"
    "Available step actions:\n"
    "  Interaction: navigate, click, fill, select, wait, hover\n"
    "  Assertions:  assert_text, assert_attribute, assert_url, assert_visible\n"
    "    • assert_text       — selector + value (substring of element text)\n"
    "    • assert_attribute  — selector + value formatted as 'attr=expected'\n"
    "                          e.g. selector=\"[data-testid='nav-link']\",\n"
    "                          value=\"href=https://example.com\" verifies\n"
    "                          the href attribute contains the substring.\n"
    "    • assert_url        — value = expected substring of page.url\n"
    "    • assert_visible    — selector only; verifies element is visible.\n"
    "  Pick assertions derived from the diff. If the PR adds an `href` to a\n"
    "  link, assert_attribute on that href. If it adds visible text, "
    "assert_text on that text. If it adds a route, assert_url on it.\n\n"
    "Workflow:\n"
    "  1. Call read_diff on every UI file listed. Identify the specific "
    "lines that were added (+) or removed (-) and what they render.\n"
    "  2. Call read_file only if the diff alone doesn't give you a stable "
    "selector for a changed element (e.g. you need to find its data-testid "
    "or surrounding wrapper).\n"
    "  3. Submit exactly ONE flow whose every step targets the changed "
    "region. change_context must be ONE sentence (max 20 words) naming the "
    "specific diff change and what the flow confirms about it.\n\n"
    "Selector priority: data-testid > id (#foo) > name attr > aria-label > "
    "visible text. For visible text use Playwright text-engine syntax: "
    "`text=Exact visible label`. For CSS use `[data-testid='foo']` or `#id`. "
    "Never emit a bare string like `Click me` — it will be parsed as CSS and "
    "silently fail. Only include steps that will succeed on the real page.\n\n"
    "For navigate steps: selector must be a URL path (e.g. '/' or '/pricing'), "
    "NOT a CSS selector like 'body'. Use wait or hover for element interactions."
)


# ---------------------------------------------------------------------------
# File index
# ---------------------------------------------------------------------------


class _FileIndex:
    """Wraps the changed_files list with O(1) path lookup and overview rendering."""

    def __init__(self, changed_files: list[dict]) -> None:
        self._files = list(changed_files)
        self._by_path: dict[str, dict] = {f["filename"]: f for f in self._files}

    def __len__(self) -> int:
        return len(self._files)

    def get(self, path: str) -> Optional[dict]:
        return self._by_path.get(path)

    def overview(self, only: str = "all", offset: int = 0, limit: int = 200) -> str:
        """Render a compact, tab-aligned summary of the changed files.

        ``only="ui"`` shows just files _is_component recognises; ``"all"`` shows
        everything but groups UI files first. The agent can re-call this with
        a different offset to page through huge PRs.
        """
        ui = [f for f in self._files if _is_component(f["filename"])]
        non_ui = [f for f in self._files if not _is_component(f["filename"])]

        if only == "ui":
            ordered = ui
        else:
            ordered = ui + non_ui

        page = ordered[offset : offset + limit]
        total = len(ordered)

        lines: list[str] = []
        if only == "ui":
            lines.append(f"UI files only — showing {len(page)} of {total}")
        else:
            lines.append(
                f"All changed files — showing {len(page)} of {total} "
                f"({len(ui)} UI / {len(non_ui)} other)"
            )
        lines.append("")
        lines.append(f"{'STATUS':<8}{'± LINES':<12}{'TOTAL':<8}FILE")

        for f in page:
            status_letter = (f.get("status") or "?")[0].upper()
            adds = f.get("additions")
            dels = f.get("deletions")
            if adds is None and dels is None and f.get("patch"):
                adds, dels = _count_patch(f["patch"])
            total_lines = _line_count(f)
            change = f"+{adds or 0} -{dels or 0}"
            lines.append(
                f"{status_letter:<8}{change:<12}{str(total_lines or '?'):<8}{f['filename']}"
            )

        if offset + limit < total:
            lines.append("")
            lines.append(
                f"... {total - offset - limit} more — call list_files(offset={offset + limit}) to page"
            )

        return "\n".join(lines)

    def read_file(
        self,
        path: str,
        start_line: Optional[int] = None,
        end_line: Optional[int] = None,
    ) -> str:
        f = self._by_path.get(path)
        if not f:
            return f"ERROR: {path!r} is not in this PR"

        content = f.get("content")
        if content is None:
            patch = f.get("patch")
            if patch:
                return f"NO FULL CONTENT AVAILABLE — showing diff hunks instead:\n\n{patch}"
            return f"ERROR: no content or patch available for {path!r}"

        if start_line is None and end_line is None:
            return content

        lines = content.splitlines()
        s = max(0, (start_line or 1) - 1)
        e = min(len(lines), end_line or len(lines))
        return "\n".join(lines[s:e])

    def read_diff(self, path: str) -> str:
        f = self._by_path.get(path)
        if not f:
            return f"ERROR: {path!r} is not in this PR"
        patch = f.get("patch")
        if not patch:
            return f"ERROR: no patch hunks available for {path!r} (file may be added or binary)"
        return patch


def _line_count(f: dict) -> Optional[int]:
    content = f.get("content")
    if content is None:
        return None
    return content.count("\n") + 1


def _count_patch(patch: str) -> tuple[int, int]:
    adds = sum(1 for ln in patch.splitlines() if ln.startswith("+") and not ln.startswith("+++"))
    dels = sum(1 for ln in patch.splitlines() if ln.startswith("-") and not ln.startswith("---"))
    return adds, dels


# ---------------------------------------------------------------------------
# Tool schemas (OpenAI / Azure function-calling format)
# ---------------------------------------------------------------------------

_TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": (
                "Read the full contents of a file changed in this PR. Optionally "
                "slice by 1-based line range for very long files."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path exactly as shown in the overview."},
                    "start_line": {"type": "integer", "description": "Optional 1-based first line to include."},
                    "end_line": {"type": "integer", "description": "Optional 1-based last line to include."},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_diff",
            "description": "Read just the diff hunks for a file in this PR. Cheaper than read_file when you only care about what changed.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "Re-list the changed files, optionally filtered or paginated.",
            "parameters": {
                "type": "object",
                "properties": {
                    "only": {"type": "string", "enum": ["ui", "all"], "description": "ui = only UI surfaces; all = everything"},
                    "offset": {"type": "integer"},
                    "limit": {"type": "integer"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "submit_flows",
            "description": "TERMINAL ACTION. Submit your final list of interaction flows for recording. Call this when you have enough context.",
            "parameters": {
                "type": "object",
                "properties": {
                    "flows": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string", "description": "snake_case identifier"},
                                "description": {"type": "string"},
                                "component_file": {"type": "string"},
                                "navigate_to": {"type": "string"},
                                "change_context": {
                                    "type": "string",
                                    "description": (
                                        "One concise plain-English sentence (max 20 words) describing "
                                        "the specific diff change this flow exercises and what it verifies."
                                    ),
                                },
                                "steps": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "action": {
                                                "type": "string",
                                                "enum": [
                                                    "click", "fill", "select",
                                                    "navigate", "wait", "hover",
                                                    "assert_text", "assert_attribute",
                                                    "assert_url", "assert_visible",
                                                ],
                                            },
                                            "selector": {"type": "string"},
                                            "value": {"type": "string"},
                                            "description": {"type": "string"},
                                        },
                                        "required": ["action", "selector"],
                                    },
                                },
                            },
                            "required": ["name", "description", "component_file", "navigate_to", "change_context", "steps"],
                        },
                    }
                },
                "required": ["flows"],
            },
        },
    },
]


# ---------------------------------------------------------------------------
# Tool schemas (Anthropic format) — same tools, different wrapper. Built by
# stripping the OpenAI ``function`` wrapper and renaming ``parameters`` to
# ``input_schema``. Kept as a derived constant so the two schemas can never
# drift.
# ---------------------------------------------------------------------------

_ANTHROPIC_TOOLS: list[dict] = [
    {
        "name": t["function"]["name"],
        "description": t["function"]["description"],
        "input_schema": t["function"]["parameters"],
    }
    for t in _TOOLS
]


# ---------------------------------------------------------------------------
# Tool dispatch
# ---------------------------------------------------------------------------


def _dispatch_tool(name: str, args: dict, index: _FileIndex, state: dict) -> str:
    """Execute one tool call. Returns the string result that goes back to the model."""
    if name == "read_file":
        if state["files_read"] >= MAX_FILES_READ:
            return f"ERROR: read_file budget exhausted ({MAX_FILES_READ} files). Submit flows now."
        state["files_read"] += 1
        return index.read_file(
            path=args.get("path", ""),
            start_line=args.get("start_line"),
            end_line=args.get("end_line"),
        )

    if name == "read_diff":
        return index.read_diff(path=args.get("path", ""))

    if name == "list_files":
        return index.overview(
            only=args.get("only", "all"),
            offset=int(args.get("offset", 0) or 0),
            limit=int(args.get("limit", 200) or 200),
        )

    return f"ERROR: unknown tool {name!r}"


# ---------------------------------------------------------------------------
# Flow parsing
# ---------------------------------------------------------------------------


def _parse_flows(payload: dict) -> list[InteractionFlow]:
    flows: list[InteractionFlow] = []
    for fd in payload.get("flows", []) or []:
        if not isinstance(fd, dict) or not fd.get("name"):
            continue
        steps = []
        for s in fd.get("steps", []) or []:
            if not isinstance(s, dict) or not s.get("action"):
                continue
            steps.append(
                InteractionStep(
                    action=s["action"],
                    selector=s.get("selector", ""),
                    value=s.get("value"),
                    description=s.get("description", ""),
                )
            )
        flows.append(
            InteractionFlow(
                name=fd["name"],
                description=fd.get("description", ""),
                component_file=fd.get("component_file", ""),
                navigate_to=fd.get("navigate_to", "/"),
                change_context=fd.get("change_context", ""),
                steps=steps,
            )
        )
    # Hard cap: we only want one clean recording per PR.
    return flows[:1]


# ---------------------------------------------------------------------------
# LLM client (provider switch)
# ---------------------------------------------------------------------------


def _build_client(
    provider: str,
    api_key: Optional[str],
    azure_endpoint: Optional[str],
    azure_api_version: Optional[str],
):
    """Return a configured OpenAI-compatible client.

    NOTE: only used for ``provider in {"openai", "azure"}``. The anthropic
    provider has its own loop in ``_run_anthropic_loop`` because the request
    shape, response shape, and tool-use protocol are all different.
    """
    provider = (provider or "openai").lower()

    if provider == "openai":
        from openai import OpenAI

        key = api_key or os.environ.get("OPENAI_API_KEY")
        if not key:
            raise RuntimeError("OPENAI_API_KEY not set")
        return OpenAI(api_key=key)

    if provider == "azure":
        from openai import AzureOpenAI

        key = api_key or os.environ.get("AZURE_OPENAI_API_KEY")
        endpoint = azure_endpoint or os.environ.get("AZURE_OPENAI_ENDPOINT")
        api_version = (
            azure_api_version
            or os.environ.get("AZURE_OPENAI_API_VERSION")
            or "2024-10-21"
        )
        if not key or not endpoint:
            raise RuntimeError("AZURE_OPENAI_API_KEY and AZURE_OPENAI_ENDPOINT must be set")
        return AzureOpenAI(api_key=key, azure_endpoint=endpoint, api_version=api_version)

    raise ValueError(
        f"Unknown provider: {provider!r} (expected 'openai', 'azure', or 'anthropic')"
    )


def _resolve_model(
    provider: str,
    model: Optional[str],
    azure_deployment: Optional[str],
) -> str:
    p = (provider or "openai").lower()
    if p == "azure":
        return (
            azure_deployment
            or os.environ.get("AZURE_OPENAI_DEPLOYMENT")
            or model
            or DEFAULT_MODEL
        )
    if p == "anthropic":
        return (
            model
            or os.environ.get("ANTHROPIC_MODEL")
            or DEFAULT_ANTHROPIC_MODEL
        )
    return model or os.environ.get("OPENAI_MODEL") or DEFAULT_MODEL


# ---------------------------------------------------------------------------
# Anthropic Messages call — works against api.anthropic.com OR Azure AI
# Foundry's Anthropic-compatible endpoint. We use raw httpx (instead of the
# anthropic SDK) so we can:
#   1. Send `api-key` in addition to `x-api-key` (Foundry uses the former).
#   2. Append the right path to whatever base URL the user gave us.
#   3. Pass arbitrary api-version query params for Foundry without fighting
#      the SDK.
# This keeps the dependency surface to httpx (which the openai SDK pulls in
# transitively anyway) — no extra wheel to install on the runner.
# ---------------------------------------------------------------------------


def _anthropic_url(base_url: str) -> str:
    """Resolve the full POST URL, mirroring the official Anthropic SDK's
    path scheme (``base_url + /v1/messages``).

    Resolution rules:
      - URL already ends with ``/messages``       → use as-is.
      - URL already ends with ``/v1``             → append ``/messages``.
      - URL ends in anything else (incl. empty)   → append ``/v1/messages``.

    Examples:
      "" or None                                              → https://api.anthropic.com/v1/messages
      "https://api.anthropic.com"                             → .../v1/messages
      "https://api.anthropic.com/v1"                          → .../v1/messages
      "https://X.services.ai.azure.com/anthropic"             → .../anthropic/v1/messages
      "https://X.services.ai.azure.com/anthropic/v1/messages" → unchanged
    """
    u = (base_url or "").rstrip("/")
    if not u:
        return "https://api.anthropic.com/v1/messages"
    if u.endswith("/messages"):
        return u
    if u.endswith("/v1"):
        return u + "/messages"
    return u + "/v1/messages"


def _anthropic_post(
    base_url: str,
    api_key: str,
    api_version: Optional[str],
    payload: dict,
) -> dict:
    import httpx  # transitive dep of openai; always present on the runner

    url = _anthropic_url(base_url)
    params: dict[str, str] = {}
    if api_version:
        # Foundry routes use ?api-version=YYYY-MM-DD-preview
        params["api-version"] = api_version
    headers = {
        # Send both auth header styles: Foundry expects `api-key`, native
        # Anthropic expects `x-api-key`. Sending both is harmless and means
        # the same workflow input works against either backend.
        "api-key": api_key,
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    resp = httpx.post(
        url,
        params=params or None,
        headers=headers,
        json=payload,
        timeout=httpx.Timeout(120.0, connect=15.0),
    )
    if resp.status_code >= 400:
        # Mirror the OpenAI SDK's behavior of raising with the body included
        # so debugging from CI logs is possible.
        raise RuntimeError(
            f"Anthropic endpoint returned {resp.status_code}: {resp.text[:500]}"
        )
    return resp.json()


def _run_anthropic_loop(
    base_url: str,
    api_key: str,
    api_version: Optional[str],
    model: str,
    system_blocks: list[str],
    user_message: str,
    index: "_FileIndex",
    cost: CostInfo,
) -> list[InteractionFlow]:
    """Anthropic-format agent loop.

    Mirrors the OpenAI loop but speaks the Messages API: assistant turns
    return ``content`` blocks containing ``text`` and ``tool_use`` items;
    tool results go back as a single user-role message whose ``content`` is
    a list of ``tool_result`` blocks. ``stop_reason == "tool_use"`` means
    keep going; anything else terminates the conversation.
    """
    # Anthropic's `system` is a top-level param (not a role). Two stable
    # blocks (base prompt + repo context) keep cache reuse possible.
    system: list[dict] = [{"type": "text", "text": s} for s in system_blocks if s]

    # Conversation messages — alternating user/assistant turns. Tool results
    # are user-role messages with content blocks; assistant turns are
    # whatever the model returned (text + tool_use blocks).
    messages: list[dict] = [{"role": "user", "content": user_message}]

    state = {"files_read": 0, "input_tokens": 0}

    for _iteration in range(MAX_ITERATIONS):
        payload = {
            "model": model,
            "max_tokens": MAX_OUTPUT_TOKENS_PER_TURN,
            "system": system,
            "messages": messages,
            "tools": _ANTHROPIC_TOOLS,
        }
        resp = _anthropic_post(base_url, api_key, api_version, payload)

        usage = resp.get("usage") or {}
        in_t = int(usage.get("input_tokens", 0) or 0)
        out_t = int(usage.get("output_tokens", 0) or 0)
        cached_t = int(usage.get("cache_read_input_tokens", 0) or 0)
        cost.add_usage(in_t, out_t, cached_t)
        state["input_tokens"] += in_t

        content_blocks = resp.get("content") or []
        # Append the assistant turn verbatim; Anthropic requires the prior
        # assistant turn to be present before the tool_result reply.
        messages.append({"role": "assistant", "content": content_blocks})

        tool_uses = [b for b in content_blocks if b.get("type") == "tool_use"]
        stop_reason = resp.get("stop_reason")

        if not tool_uses:
            # Model finished without calling submit_flows — no flows.
            break

        tool_results: list[dict] = []
        terminated = False
        for tu in tool_uses:
            name = tu.get("name", "")
            args = tu.get("input") or {}
            tu_id = tu.get("id", "")

            if name == "submit_flows":
                # Terminal — return immediately. No need to send tool_result
                # because we don't take another turn.
                return _parse_flows(args)

            result = _dispatch_tool(name, args, index, state)
            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": tu_id,
                    "content": result,
                }
            )

        if terminated:
            break

        messages.append({"role": "user", "content": tool_results})

        # Anthropic sets stop_reason="end_turn" when the model is done — but
        # if it called tools, it set "tool_use" instead. Our loop only cares
        # about whether there were tool calls (handled above).
        del stop_reason

        if state["input_tokens"] >= MAX_TOTAL_INPUT_TOKENS:
            messages.append(
                {
                    "role": "user",
                    "content": (
                        f"Token budget reached ({state['input_tokens']} / "
                        f"{MAX_TOTAL_INPUT_TOKENS}). Submit your flows now."
                    ),
                }
            )

    return []


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def analyze_pr(
    changed_files: list[dict],
    preview_url: str,
    provider: str = "openai",
    api_key: Optional[str] = None,
    model: Optional[str] = None,
    azure_endpoint: Optional[str] = None,
    azure_deployment: Optional[str] = None,
    azure_api_version: Optional[str] = None,
    *,
    repo_context_body: str = "",
    ignore_paths: Optional[list[str]] = None,
    anthropic_base_url: Optional[str] = None,
    anthropic_api_version: Optional[str] = None,
) -> AnalyzeResult:
    """Generate interaction flows for the changed UI in a PR.

    Internally runs an agent loop: hands the LLM an overview of every changed
    file, exposes ``read_file`` / ``read_diff`` / ``list_files`` / ``submit_flows``
    tools, and lets the model decide how deep to go. Cost is bounded by
    MAX_ITERATIONS, MAX_FILES_READ, and MAX_TOTAL_INPUT_TOKENS.

    Returns an ``AnalyzeResult`` with the flows AND a populated ``CostInfo``.
    """
    provider = (provider or "openai").lower()
    cost = CostInfo(provider=provider)

    # Pre-filter ignored paths using _glob_match which correctly handles
    # ``**`` across ``/`` boundaries (fnmatch and PurePosixPath.match do not).
    if ignore_paths:
        from .repo_context import _glob_match as _gm
        changed_files = [
            f for f in changed_files
            if not any(_gm(f["filename"], pat) for pat in ignore_paths)
        ]

    # Quick exit: nothing UI-shaped in the PR.
    if not any(_is_component(f["filename"]) for f in changed_files):
        return AnalyzeResult(flows=[], cost=cost)

    # Dry-run: skip the LLM loop but still honour filters above so tests
    # can verify ignore_paths and component filtering.
    if _dry_run_enabled():
        cost.model = "(dry-run)"
        return AnalyzeResult(flows=_parse_flows(_DRY_RUN_FLOWS_PAYLOAD), cost=cost)

    index = _FileIndex(changed_files)
    overview = index.overview(only="all", offset=0, limit=200)

    user_message = (
        f"Pull request opened against preview URL: {preview_url or '(none)'}\n\n"
        f"{overview}\n\n"
        "Start by calling read_diff on the UI files above. Identify exactly "
        "which lines were added or changed. Then generate EXACTLY ONE short "
        "flow (2–5 steps) whose every step touches the changed region — no "
        "wandering into unchanged parts of the page. Call submit_flows when done."
    )

    # ---- Anthropic branch (own loop, own request shape) -----------------
    if provider == "anthropic":
        key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        base_url = anthropic_base_url or os.environ.get("ANTHROPIC_BASE_URL", "")
        api_ver = anthropic_api_version or os.environ.get("ANTHROPIC_API_VERSION") or None
        if not key:
            raise RuntimeError("ANTHROPIC_API_KEY not set")
        cost.model = _resolve_model(provider, model, azure_deployment)
        flows = _run_anthropic_loop(
            base_url=base_url,
            api_key=key,
            api_version=api_ver,
            model=cost.model,
            system_blocks=[_SYSTEM, repo_context_body],
            user_message=user_message,
            index=index,
            cost=cost,
        )
        cost.finalize()
        return AnalyzeResult(flows=flows, cost=cost)

    # ---- OpenAI / Azure OpenAI branch (chat-completions tool-calls) -----
    messages: list[dict] = [
        {"role": "system", "content": _SYSTEM},
    ]
    # Repo context goes as a SECOND system message — keeps the prefix stable
    # across PRs for prompt-cache reuse (OpenAI caches identical prefixes
    # ≥1024 tokens at 50% discount; Anthropic gives 90% with cache_control).
    if repo_context_body:
        messages.append({"role": "system", "content": repo_context_body})
    messages.append({"role": "user", "content": user_message})

    client = _build_client(provider, api_key, azure_endpoint, azure_api_version)
    model_name = _resolve_model(provider, model, azure_deployment)
    cost.model = model_name
    state = {"files_read": 0, "input_tokens": 0}

    for iteration in range(MAX_ITERATIONS):
        resp = client.chat.completions.create(
            model=model_name,
            messages=messages,
            tools=_TOOLS,
            tool_choice="auto",
            max_completion_tokens=MAX_OUTPUT_TOKENS_PER_TURN,
        )

        if getattr(resp, "usage", None):
            in_t = getattr(resp.usage, "prompt_tokens", 0) or 0
            out_t = getattr(resp.usage, "completion_tokens", 0) or 0
            cached_t = 0
            details = getattr(resp.usage, "prompt_tokens_details", None)
            if details is not None:
                cached_t = getattr(details, "cached_tokens", 0) or 0
            cost.add_usage(in_t, out_t, cached_t)
            state["input_tokens"] += in_t

        msg = resp.choices[0].message
        tool_calls = msg.tool_calls or []

        # Append the assistant turn (must come before tool results in the next turn).
        assistant_entry: dict = {"role": "assistant", "content": msg.content or ""}
        if tool_calls:
            assistant_entry["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments or "{}",
                    },
                }
                for tc in tool_calls
            ]
        messages.append(assistant_entry)

        if not tool_calls:
            # Model stopped without submitting — bail with whatever we have (nothing).
            break

        submitted: Optional[list[InteractionFlow]] = None
        for tc in tool_calls:
            name = tc.function.name
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}

            if name == "submit_flows":
                submitted = _parse_flows(args)
                break

            result = _dispatch_tool(name, args, index, state)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result,
                }
            )

        if submitted is not None:
            cost.finalize()
            return AnalyzeResult(flows=submitted, cost=cost)

        # Cost guards
        if state["input_tokens"] >= MAX_TOTAL_INPUT_TOKENS:
            messages.append(
                {
                    "role": "user",
                    "content": (
                        f"Token budget reached ({state['input_tokens']} / "
                        f"{MAX_TOTAL_INPUT_TOKENS}). Submit your flows now."
                    ),
                }
            )

    cost.finalize()
    return AnalyzeResult(flows=[], cost=cost)



# ---------------------------------------------------------------------------
# File filter (also used as a hint by _FileIndex.overview)
# ---------------------------------------------------------------------------


def _is_component(filename: str) -> bool:
    """True if a changed file is a renderable UI surface worth analyzing.

    Framework-agnostic — covers anything that ultimately produces HTML over
    HTTP. The actual replay is browser-only, so server-side templates count
    as long as they render markup the LLM can reason about.
    """
    name = filename.lower()

    # Order matters: longer / compound suffixes first so e.g.
    # ".component.html" wins over the bare ".html" branch.
    exts = (
        # JS/TS frameworks
        ".jsx", ".tsx",                       # React, Next, Solid, Qwik
        ".vue",                               # Vue, Nuxt
        ".svelte",                            # Svelte, SvelteKit
        ".astro",                             # Astro
        # Angular (compound suffixes match before .ts/.html)
        ".component.ts", ".component.html",
        # .NET / Blazor / Razor
        ".razor", ".cshtml", ".vbhtml",
        # Server templates
        ".html.erb", ".erb",                  # Rails
        ".heex", ".leex", ".eex",             # Phoenix LiveView / EEx
        ".jinja", ".jinja2", ".j2",           # Jinja / Django
        ".twig",                              # Twig (Symfony, Drupal)
        ".hbs", ".handlebars", ".mustache",   # Handlebars / Mustache
        ".liquid",                            # Liquid (Shopify, Jekyll)
        ".njk",                               # Nunjucks
        ".pug", ".jade",                      # Pug
        ".php", ".phtml",                     # PHP templates
        ".html", ".htm",                      # plain HTML / HTMX (keep last)
    )
    # NOTE: we deliberately do NOT ignore .test / .spec / .stories files —
    # they often contain the clearest description of what a component does
    # (literal click/fill examples in tests, prop matrices in stories) and
    # give the LLM crucial signal about the changed behavior. Only filter
    # out things with no UI semantics.
    ignores = (
        ".d.ts",            # type-only declaration files
        "__snapshots__/",   # auto-generated jest snapshots, pure noise
    )

    return (
        any(name.endswith(e) for e in exts)
        and not any(i in name for i in ignores)
    )
