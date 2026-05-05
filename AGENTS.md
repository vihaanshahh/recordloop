# AGENTS.md

> Audience: AI coding agents joining this repo cold. Read this before making
> changes. It is a contract for preserving the current architecture.

## What RecordLoop Is

RecordLoop turns a GitHub PR diff into a short, recorded browser verification:

```text
PR opened
  -> fetch changed files
  -> analyzer agent loop reads only the PR diff/files
  -> submit exactly one focused Playwright flow
  -> replay that flow at selected viewport(s)
  -> build screenshot-based GIF previews + upload release assets
  -> update one PR comment with pass/fail, GIFs, and trace details
```

The AI step is itself an agent loop in `api/analyzer.py`. Do not replace it
with a single prompt call. The recorder step is in `api/cloud_recorder.py`.
The GitHub Action runner is `api/run_action.py` plus `action.yml`.

## Current Architecture

| Path | Purpose | Touch when |
|---|---|---|
| `api/analyzer.py` | LLM agent loop, file filter, tool schemas, provider routing, cost tracking. | Changing LLM behavior, providers, tools, prompt, file filtering. |
| `api/cloud_recorder.py` | Replays generated flows with Playwright, viewport matrix, assertions, screenshot GIF capture, selector normalization. | Recording behavior, viewport profiles, action execution, assertions, GIF/video result shape. |
| `api/gif_builder.py` | Builds inline PR GIFs from per-step screenshots with Pillow. | GIF size, frame timing, image encoding. |
| `api/run_action.py` | GitHub Action entry point: env parsing, analyzer call, recorder call, upload release assets, render PR comment. | Action runtime behavior, PR comment format, upload flow, output variables. |
| `action.yml` | Composite GitHub Action inputs/steps. | Adding/removing action inputs or runtime install behavior. |
| `api/main.py` | FastAPI service: `/health`, `/trigger`, `/jobs/{job_id}` and background job orchestration. | Hosted API routes, job status, API request handling. |
| `api/models.py` | Pydantic API request/response models (`LLMConfig`, `RecordingConfig`, etc.). | Hosted API schema changes. |
| `api/github_client.py` | GitHub REST helpers for PR files, comments, release asset uploads. | GitHub API behavior. |
| `api/login.py`, `api/login_capture.py` | Storage-state auth support. | Login/session behavior. |
| `api/repo_context.py` | `.github/recordloop.md` parsing and ignore-path matching. | Repo-local configuration. |
| `api/tests/test_smoke.py` | Tier-1 verification for API/analyzer/action helpers. Zero secrets, no browser. | Any change in `api/`, `action.yml`, or docs-sensitive behavior. |
| `src/recordloop/capture/recorder.py` | Lower-level `PlaywrightRecorder` and `RecorderConfig`. | Browser context options, viewport/mobile flags, session metadata. |
| `src/recordloop/core/session.py` | `Action`, `ActionType`, `SemanticKey`, `Session`. | New action types or selector strategies. |
| `README.md` | Public GitHub Action docs. | User-facing action inputs, install, architecture. |
| `api/README.md` | Hosted API docs. | `/trigger` schema, API examples, local server docs. |

## Non-Negotiable Invariants

1. Keep `analyze_pr()` backwards-compatible.

   Positional callers still pass through `api/main.py` and `api/run_action.py`.
   Add new analyzer parameters only as keyword-only defaults.

   ```python
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
   ) -> AnalyzeResult: ...
   ```

2. `RECORDLOOP_DRY_RUN=1` must skip the LLM entirely.

   Dry-run belongs near the top of `analyze_pr()` after lightweight filters.
   It must not build an LLM client, call a provider, fetch network resources,
   import Playwright, or require secrets.

3. Keep the cost caps unless adding an explicit budget/quota mechanism.

   Current constants in `api/analyzer.py`:

   ```python
   MAX_ITERATIONS = 10
   MAX_FILES_READ = 30
   MAX_TOTAL_INPUT_TOKENS = 50_000
   MAX_OUTPUT_TOKENS_PER_TURN = 2048
   ```

4. Playwright must stay lazy-imported.

   `api.main` must import successfully without Playwright installed. Keep
   `from recordloop.capture.recorder import ...` inside `_record_one()` and
   `from recordloop.core.session import ...` inside execution/selector helpers.

5. Tests, specs, and stories are intentionally kept.

   `_is_component()` keeps `.test.tsx`, `.spec.jsx`, and `.stories.tsx`.
   They often contain interaction examples. Only type declarations and
   snapshots are filtered as noise.

6. The analyzer emits one flow per PR.

   `_parse_flows()` returns `flows[:1]`. Responsive/multi-size support replays
   the same flow across viewports. Do not ask the LLM for a separate flow per
   viewport unless the product direction changes.

7. Responsive recording is recorder-side, not analyzer-side.

   The architecture is:

   ```text
   one LLM flow -> record_flows(..., viewports=...) -> N recordings
   ```

   This keeps LLM cost flat and keeps the prompt stable.

8. Do not reintroduce ffmpeg as a required dependency.

   Inline PR previews are screenshot-based GIFs from `api/gif_builder.py`
   using Pillow. Playwright's raw video is still uploaded as a full recording
   link when available. The action installs Playwright + Pillow, not ffmpeg.

9. The `SemanticKey` bridge in `api/cloud_recorder.py:_to_key()` is order-sensitive.

   Check specific selector patterns (`#id`, `data-testid`, `name`,
   `aria-label`) before falling back to `xpath`. Playwright still receives the
   raw normalized selector; `_to_key()` only affects recorded session metadata.

10. `gpt-5.x` chat completions use `max_completion_tokens`.

    Do not change OpenAI/Azure calls back to `max_tokens`.

11. `_jobs` is intentionally in-memory for now.

    It is a known hosted-API limitation. Do not silently replace it with a DB.
    The intended future shape is a `JobStore` protocol plus Redis/Postgres
    behind a design change.

12. `JobStatus.job_id` is the field name.

    Do not rename it to `id` in models, docs, or `_jobs`.

13. `allow_origins=["*"]` is dev-mode behavior.

    Keep existing behavior unless explicitly asked to harden production auth.
    Do not copy this pattern into new security-sensitive code.

## How To Implement Common Changes

### Add or change a viewport profile

Goal: support a new replay size/device while keeping one LLM flow.

1. Edit `api/cloud_recorder.py`.
   - Add the profile to `_VIEWPORT_PRESETS`.
   - Add aliases to `_VIEWPORT_ALIASES` if useful.
   - Keep `MAX_VIEWPORTS = 4` unless adding quota/rate controls.
2. If the profile needs real mobile behavior, set `is_mobile`, `has_touch`,
   `device_scale_factor`, and `user_agent` in the `ViewportProfile`.
3. If the lower-level recorder needs a new browser-context option, add it to
   `RecorderConfig` in `src/recordloop/capture/recorder.py` and pass it inside
   `_setup_browser()`.
4. Add/adjust tests in `api/tests/test_smoke.py` around `_resolve_viewports()`.
5. Update `README.md` action docs and `api/README.md` request schema if the
   user-facing option changes.
6. Run the three required verification commands below.

Do not change `api/analyzer.py` for viewport work unless the model truly needs
new information in its prompt. Most responsive behavior should be tested by
replaying the same flow at different sizes.

### Add a new recorder action

1. Add the action name to the analyzer prompt and submit schema in
   `api/analyzer.py` only if the LLM should emit it.
2. Add execution behavior in `api/cloud_recorder.py:_execute()`.
3. Add an `ActionType` in `src/recordloop/core/session.py` only if the recorded
   session model needs to store it semantically.
4. Add smoke tests for parsing/dispatch or focused execution helpers where
   possible without importing Playwright.
5. If the prompt or tool schema changed, run one live LLM verification with a
   real key before merging.

### Add a new GitHub Action input

1. Add the input to `action.yml`.
2. Thread it into the `Run RecordLoop analyzer` step env.
3. Parse it in `api/run_action.py`.
4. Pass it to `analyze_pr()` or `record_flows()` as appropriate.
5. Update `README.md`.
6. If hosted API users need the same capability, add it to `api/models.py`,
   thread it through `api/main.py`, and update `api/README.md`.
7. Add smoke tests in `api/tests/test_smoke.py`.

### Add a hosted API request option

1. Add a nested model in `api/models.py`; do not flatten provider/recorder
   specific fields onto `TriggerRequest` unless they are truly universal.
2. Thread it through `api/main.py:_run_job`.
3. Update `api/README.md` schema and examples.
4. Add a smoke test with the existing `client` fixture.

### Add a new LLM provider

1. Add a branch to `_build_client()` or a provider-specific loop in
   `api/analyzer.py`.
2. Reuse OpenAI-compatible clients for vLLM/Ollama/OpenRouter-style APIs.
   Do not add SDK dependencies unless the provider is incompatible.
3. Add model/env resolution in `_resolve_model()`.
4. Add nested provider config in `api/models.py`.
5. Thread action inputs through `action.yml` and `api/run_action.py` if the
   GitHub Action should support it.
6. Add dry-run smoke tests and update `README.md` plus `api/README.md`.
7. Run live provider verification with a real key before shipping.

### Add a new analyzer tool

1. Append the OpenAI tool schema to `_TOOLS` in `api/analyzer.py`.
   `_ANTHROPIC_TOOLS` is derived from `_TOOLS`; keep it that way.
2. Add a branch in `_dispatch_tool()`.
3. Add a smoke test. Use `test_dispatch_tool_unknown_name` as the pattern.
4. Update `_SYSTEM` only if the tool is not self-explanatory from its schema.
5. Be conservative. New tools increase model freedom, runtime, and token cost.

### Add a new UI framework/file type

1. Edit `_is_component()` in `api/analyzer.py`.
   Longer compound suffixes must come before bare suffixes.
2. Add a case to `test_is_component_recognizes_framework`.
3. Run `PYTHONPATH=.:src .venv/bin/pytest api/tests/test_smoke.py -k is_component -v`.

## Responsive Recording Contract

Current public knobs:

```yaml
with:
  viewports: desktop,mobile,tall
  wait-until: networkidle
  settle-ms: "300"
```

Current hosted API shape:

```json
{
  "recording": {
    "viewports": ["desktop", "mobile", "tall"],
    "wait_until": "networkidle",
    "settle_ms": 300
  }
}
```

Implementation path:

```text
action.yml inputs
  -> api/run_action.py env parsing
  -> record_flows(..., viewports=..., wait_until=..., settle_ms=...)
  -> _resolve_viewports()
  -> _record_one(..., viewport=ViewportProfile)
  -> RecorderConfig(viewport_width, viewport_height, is_mobile, has_touch, ...)
  -> Playwright context
  -> screenshot frames
  -> api/gif_builder.py
  -> release asset upload
  -> grouped PR comment per flow + viewport
```

Hosted API path:

```text
api/models.py RecordingConfig
  -> api/main.py _run_job()
  -> record_flows(...)
  -> same recorder path
```

Do not:

- Generate separate LLM flows for each viewport.
- Add per-viewport prompts by default.
- Make ffmpeg required for inline previews.
- Increase `MAX_VIEWPORTS` without thinking through runtime and release asset
  upload volume.

## Verification

Run all three before considering an API/action change done:

```bash
PYTHONPATH=.:src .venv/bin/pytest api/tests/test_smoke.py -v
```

```bash
.venv/bin/python -c "
import sys
class Block:
    def find_module(self, name, path=None):
        if name.startswith('playwright'): return self
    def load_module(self, name): raise ImportError(f'BLOCKED: {name}')
sys.meta_path.insert(0, Block())
from api.main import app
print('OK - api.main imports without playwright')
"
```

```bash
RECORDLOOP_DRY_RUN=1 .venv/bin/python -c "
from api.analyzer import analyze_pr
flows = analyze_pr(
    [{'filename':'src/Foo.tsx','status':'modified','content':'<button id=go>Go</button>'}],
    preview_url='http://localhost:3000',
).flows
assert flows and flows[0].name == 'dry_run_smoke_flow'
print('OK - dry-run pipeline')
"
```

Also run `PYTHONPATH=.:src .venv/bin/python -m py_compile ...` on changed
Python modules when editing import-heavy files.

### When live LLM verification is required

Run a real provider call if you changed any of these:

- `_TOOLS`
- `_dispatch_tool()`
- `_SYSTEM`
- OpenAI/Azure/Anthropic message loop order
- provider routing/model resolution

Never commit real API keys.

## Hot Zones

- `api/analyzer.py:_FileIndex.overview()`: ordering and compact formatting
  influence model behavior.
- `api/analyzer.py:analyze_pr`: assistant tool-call messages must be appended
  before tool result messages.
- `api/cloud_recorder.py:_execute()`: action semantics must match the prompt
  vocabulary and recorder session model.
- `api/cloud_recorder.py:_resolve_viewports()`: accepts named profiles,
  aliases, and bounded custom `WIDTHxHEIGHT`; keep validation strict.
- `api/cloud_recorder.py:_to_key()`: selector matching order is intentional.
- `api/run_action.py:_render_comment()`: groups multiple viewport recordings
  under the same flow; preserve local-artifact fallback.
- `action.yml`: many inputs support self-hosted/corporate runners. Do not
  remove them while working on unrelated recorder changes.
- `api/login_capture.py`: secrets must never be logged.

## Out Of Scope Unless Explicitly Asked

- `js/` browser SDK.
- `src/recordloop/cli/` local CLI.
- `src/recordloop/bridge/` bridge server.
- `demo/`, `example.py`, `demo_test.py`.
- Generated/local output directories: `generated-tests/`, `.recordloop/`,
  `recordloop.egg-info/`.
- Personal/planning docs: `DISCUSSION.txt`, `PRODUCT.md`.

Read these for context if useful, but do not refactor them as part of API or
recorder work.

## Documentation Rule

Any user-facing input, API field, environment variable, provider, recording
behavior, auth behavior, or artifact behavior must be documented in the same
change:

- GitHub Action behavior: update `README.md` and `action.yml`.
- Hosted API behavior: update `api/README.md` and `api/models.py`.
- Agent-facing implementation behavior: update this file.

## One-Line Summary

RecordLoop is a PR-diff -> one AI-generated flow -> Playwright replay at one
or more viewport profiles -> screenshot GIF/full recording -> PR comment
pipeline. Keep LLM cost bounded, keep dry-run zero-secret, keep Playwright
lazy-imported, and implement responsive behavior in the recorder, not the
analyzer.
