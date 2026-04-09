"""
AI-powered component analyzer (agentic).

Instead of pre-truncating the PR diff to a fixed slice, this module hands an
LLM a lightweight overview of every changed file and lets it pull what it
actually needs via tool calls. The agent decides how deep to go, when to
stop, and what to ignore. Cost is bounded by hard caps on iterations, files
read, and cumulative input tokens.

Public API: ``analyze_pr(changed_files, preview_url, ...) -> list[InteractionFlow]``
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
    action: str          # click | fill | select | navigate | wait | hover
    selector: str
    value: Optional[str] = None
    description: str = ""


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

MAX_ITERATIONS = 10                 # safety net for runaway loops
MAX_FILES_READ = 30                 # cumulative read_file calls per PR
MAX_TOTAL_INPUT_TOKENS = 50_000     # cumulative across the whole loop
MAX_OUTPUT_TOKENS_PER_TURN = 2048


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
                }
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
    "generate ONE Playwright interaction flow that demonstrates the SPECIFIC "
    "UI change introduced by this PR — and nothing else.\n\n"
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
    "  4. Keep the flow short — 2 to 5 steps total including the initial "
    "navigate. A long flow that visits unrelated UI is WORSE than a short "
    "one focused on the diff.\n\n"
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
    "visible text. Only include steps that will succeed on the real page.\n\n"
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
                                                "enum": ["click", "fill", "select", "navigate", "wait", "hover"],
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
    """Return a configured OpenAI-compatible client and the model/deployment to call."""
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

    raise ValueError(f"Unknown provider: {provider!r} (expected 'openai' or 'azure')")


def _resolve_model(
    provider: str,
    model: Optional[str],
    azure_deployment: Optional[str],
) -> str:
    if (provider or "openai").lower() == "azure":
        return (
            azure_deployment
            or os.environ.get("AZURE_OPENAI_DEPLOYMENT")
            or model
            or DEFAULT_MODEL
        )
    return model or os.environ.get("OPENAI_MODEL") or DEFAULT_MODEL


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
) -> list[InteractionFlow]:
    """Generate interaction flows for the changed UI in a PR.

    Internally runs an agent loop: hands the LLM an overview of every changed
    file, exposes ``read_file`` / ``read_diff`` / ``list_files`` / ``submit_flows``
    tools, and lets the model decide how deep to go. Cost is bounded by
    MAX_ITERATIONS, MAX_FILES_READ, and MAX_TOTAL_INPUT_TOKENS.
    """
    # Quick exit: nothing UI-shaped in the PR.
    if not any(_is_component(f["filename"]) for f in changed_files):
        return []

    # Dry-run: skip the loop entirely.
    if _dry_run_enabled():
        return _parse_flows(_DRY_RUN_FLOWS_PAYLOAD)

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

    messages: list[dict] = [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": user_message},
    ]

    client = _build_client(provider, api_key, azure_endpoint, azure_api_version)
    model_name = _resolve_model(provider, model, azure_deployment)
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
            state["input_tokens"] += getattr(resp.usage, "prompt_tokens", 0) or 0

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

        for tc in tool_calls:
            name = tc.function.name
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}

            if name == "submit_flows":
                return _parse_flows(args)

            result = _dispatch_tool(name, args, index, state)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result,
                }
            )

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

    return []  # ran out of iterations without submit_flows


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
