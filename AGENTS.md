# AGENTS.md

> **Audience**: AI coding agents (Claude Code, Cursor, Cline, Aider, Codex, Windsurf, …) joining this repo cold. Read this **before** making changes. It is a contract, not a tutorial.

## TL;DR — what this project is

RecordLoop turns a GitHub PR diff into recorded Playwright sessions. An AI agent reads the changed UI files, generates realistic interaction flows, a headless browser replays them, and the resulting MP4 is uploaded to S3 and linked in a PR comment. **The AI step is itself an agent loop with tool calls** — see `api/analyzer.py`.

```
PR opened ─► fetch diff ─► agent loop (LLM + read_file/read_diff/list_files/submit_flows)
         ─► Playwright replay ─► S3 upload ─► PR comment with video link
```

Three components, one repo:
1. **`api/`** — FastAPI service that runs the analyzer + cloud recorder. **Most recent work lives here.**
2. **`src/recordloop/`** — Python library with the local CLI, Playwright recorder, and session model.
3. **`js/`** — Browser SDK (React/Vue/vanilla bindings) that captures user interactions in a dev environment.

---

## Architecture map (where to look for what)

| Path | Purpose | Read this if you're touching… |
|---|---|---|
| `api/analyzer.py` | **The agent loop.** Builds a `_FileIndex` from PR files, hands the LLM tools (`read_file`, `read_diff`, `list_files`, `submit_flows`), runs a bounded iteration loop. | LLM behavior, tool schemas, cost caps, file filter, prompt engineering |
| `api/main.py` | FastAPI app. `/health`, `/trigger`, `/jobs/{job_id}`. Background-task runner that calls `analyze_pr` then `record_flows`. | HTTP surface, request routing, API key gating |
| `api/models.py` | Pydantic request/response models. `TriggerRequest` carries a nested `LLMConfig` with optional `AzureConfig`. | Request schema, provider config |
| `api/cloud_recorder.py` | Bridges LLM output to Playwright. Converts CSS-ish selectors to `SemanticKey` for the recorder. **Lazy-imports playwright** so Tier-1 tests don't need it. | Recording, selector conversion, video output |
| `api/github_client.py` | `get_pr_files`, `post_pr_comment`. Hits the GitHub REST API. | GitHub integration |
| `api/tests/test_smoke.py` | 55 tests covering everything in `api/`. Runs in <1s with zero secrets. **The verification gate.** | Anything in `api/` |
| `api/README.md` | Human-facing API docs (curl examples, schema tables, CI snippet) | Documentation only |
| `src/recordloop/capture/recorder.py` | `PlaywrightRecorder` + `RecorderConfig`. Used by `cloud_recorder.py`. | Local recording, video file format |
| `src/recordloop/core/session.py` | `Action`, `ActionType`, `SemanticKey`, `Session`. The session model. | Action types, selector strategies |
| `.github/workflows/recordloop-api-smoke.yml` | Tier-1 zero-secret CI job for the API. | CI |
| `js/`, `src/recordloop/cli/`, `src/recordloop/bridge/` | Browser SDK, local CLI, bridge server. | Out of scope for agent-loop work |

---

## Invariants — DO NOT BREAK THESE

These are not stylistic preferences. Each one was added in response to a real failure mode. Read **why** before changing **what**.

### 1. `analyze_pr()` signature must stay backwards-compatible
Callers in `api/main.py:_run_job` pass positional args. The internal architecture has been rewritten twice (single-shot → agentic) without changing the signature. **Keep it that way.** If you need new params, add them as keyword-only with defaults.

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
) -> list[InteractionFlow]: ...
```

### 2. `RECORDLOOP_DRY_RUN=1` must skip the LLM entirely
Checked at the **top** of `analyze_pr()`. If you add new code paths, make sure dry-run still short-circuits before any network call. The smoke tests rely on this — they run in CI with no `OPENAI_API_KEY`.

```python
if _dry_run_enabled():
    return _parse_flows(_DRY_RUN_FLOWS_PAYLOAD)
```

### 3. The agent loop's hard caps are not optional
`MAX_ITERATIONS=10`, `MAX_FILES_READ=30`, `MAX_TOTAL_INPUT_TOKENS=50_000`. These bound the worst-case cost per PR to ~$0.10 even on reasoning models. **Do not raise them without a corresponding budget mechanism.** If a user wants higher caps, that's a per-request override, not a default change.

### 4. Playwright must stay lazy-imported in `cloud_recorder.py`
`from recordloop.capture.recorder import ...` is **inside** `_record_one()` (and `from recordloop.core.session import ...` is inside `_execute()` and `_to_key()`). This lets `api.main` boot without `playwright` installed, which is what makes Tier-1 CI fast and dependency-light. **If you import playwright at module load, you break the entire CI workflow.**

### 5. Tests, specs, and stories are NOT filtered out
`_is_component()` deliberately keeps `.test.tsx`, `.spec.jsx`, and `.stories.tsx` files. They contain literal interaction examples that give the LLM crucial signal. The only things filtered are `.d.ts` (type-only) and `__snapshots__/` (auto-generated noise). See the comment in `_is_component()`.

### 6. The `SemanticKey` bridge in `cloud_recorder._to_key()` is order-sensitive
The new recorder takes `SemanticKey` objects, not raw CSS strings. `_to_key()` parses common shapes (`#id`, `[data-testid=…]`, `[name=…]`, `[aria-label=…]`) and falls back to `xpath`. **Playwright still receives the original raw selector** — `_to_key` only affects what gets stored in the recorded `Session`. Don't conflate the two.

### 7. `gpt-5.4` requires `max_completion_tokens`, not `max_tokens`
The gpt-5 family rejects `max_tokens` with a 400. Already fixed; don't accidentally revert it. Same applies to any future Azure deployment of a gpt-5-class model.

### 8. The in-memory `_jobs: dict` is a known limitation, not a target
Yes, it's not production-grade. Yes, it loses state on restart. The plan is a `JobStore` protocol with a Redis impl behind it. **Do not silently swap it for a database** — that's a structural change that needs design discussion. If you're tempted, file an issue first.

### 9. CORS is currently `allow_origins=["*"]` for dev convenience
**This is a known prod risk** flagged in the security review. Keep it `*` for now (dev mode), but **do not** copy this pattern to any new endpoint without explicit approval.

### 10. `JobStatus.id` is `JobStatus.job_id` — don't accidentally rename it back
The model field and the dict key in `_jobs[...]` are both `job_id`. Old code (and docs) used `id`. Don't reintroduce the inconsistency.

---

## Quick recipes

Common tasks, with the exact files to touch and the verification step.

### Add a new framework to the file filter

1. Edit `_is_component()` in `api/analyzer.py:exts`. **Order matters** — longer compound suffixes (e.g. `.html.erb`) must come before bare ones (`.html`).
2. Add a parametrize case to `test_is_component_recognizes_framework` in `api/tests/test_smoke.py`.
3. Run `pytest api/tests/test_smoke.py -k is_component -v`.

That's it. No prompt change needed — the system prompt already says "any framework that produces HTML."

### Add a new LLM provider (e.g. AWS Bedrock, vLLM)

1. Add a branch to `_build_client()` in `api/analyzer.py`.
2. If the API surface is OpenAI-compatible (vLLM, Ollama, OpenRouter), reuse the `OpenAI` client with a custom `base_url`. **Do not add a new SDK dependency** unless the provider is genuinely incompatible.
3. Add an env-var fallback in `_resolve_model()`.
4. Extend `LLMConfig` in `api/models.py` if the provider needs new request fields. Nest them in a sub-model like `AzureConfig`, do **not** flatten onto `LLMConfig`.
5. Add a smoke test that uses dry-run + the new provider name.
6. Update the `LLMConfig` table in `api/README.md`.

### Add a new tool to the agent loop

1. Append the schema to `_TOOLS` in `api/analyzer.py`. Use the OpenAI function-calling JSON-schema format.
2. Add a branch to `_dispatch_tool()`.
3. Add a unit test in `api/tests/test_smoke.py` (look at `test_dispatch_tool_unknown_name` for the pattern).
4. Update the system prompt only if the new tool is non-obvious — the model usually figures out tool semantics from the description field.
5. **Be conservative.** Each new tool increases the agent's degrees of freedom and the iteration count. The current 4 tools are sufficient for 95% of cases.

### Tighten or relax the cost caps

1. The constants are `MAX_ITERATIONS`, `MAX_FILES_READ`, `MAX_TOTAL_INPUT_TOKENS`, `MAX_OUTPUT_TOKENS_PER_TURN` at the top of `api/analyzer.py`.
2. **Do not** make them per-call function arguments unless you're also adding a billing/quota mechanism. They're constants because they're guardrails, not dials.
3. The unit test `test_dispatch_tool_enforces_read_file_budget` locks in the read-file cap behavior. If you change `MAX_FILES_READ`, update that test.

### Add a new HTTP endpoint

1. Add a route in `api/main.py`. Follow the existing `/trigger` pattern: Pydantic body model + `Optional[Header]` for the API key + background task.
2. Add the request/response models to `api/models.py`.
3. Add the endpoint to the request schema table in `api/README.md`.
4. Add a smoke test in `api/tests/test_smoke.py` using the existing `client` fixture.

### Generate new test data for the smoke tests

Use the `_sample_files()` helper in `api/tests/test_smoke.py`. **Do not** rely on real files in `src/components/` — those don't exist in CI checkouts and tests must be self-contained.

---

## Verification — how to know you didn't break anything

Run **all** of these before considering a change done:

```bash
# 1. The smoke tests (zero secrets, <1s, must be 100% green)
PYTHONPATH=.:src .venv/bin/pytest api/tests/test_smoke.py -v

# 2. Import-cleanliness check (proves Tier-1 doesn't need playwright)
.venv/bin/python -c "
import sys
class Block:
    def find_module(self, name, path=None):
        if name.startswith('playwright'): return self
    def load_module(self, name): raise ImportError(f'BLOCKED: {name}')
sys.meta_path.insert(0, Block())
from api.main import app
print('OK — api.main imports without playwright')
"

# 3. Dry-run end-to-end (still no LLM call, exercises the full HTTP path)
RECORDLOOP_DRY_RUN=1 .venv/bin/python -c "
from api.analyzer import analyze_pr
flows = analyze_pr(
    [{'filename':'src/Foo.tsx','status':'modified','content':'<button id=go>Go</button>'}],
    preview_url='http://localhost:3000',
)
assert flows and flows[0].name == 'dry_run_smoke_flow'
print('OK — dry-run pipeline')
"
```

All three must pass. If any of them fail, **revert your change** and reconsider.

### When you also need real-LLM verification

If you touched anything in the agent loop (`_TOOLS`, `_dispatch_tool`, the iteration logic, the system prompt), you must also do at least one real run with a real key:

```bash
OPENAI_API_KEY=sk-... .venv/bin/python << 'PY'
from api.analyzer import analyze_pr
flows = analyze_pr(
    [{
        "filename": "src/components/SignupForm.tsx",
        "status": "modified",
        "content": '<form><input data-testid="email"/><button data-testid="go">Go</button></form>',
    }],
    preview_url="http://localhost:3456",
)
print(f"got {len(flows)} flow(s)")
for f in flows:
    print(f"  {f.name}: {len(f.steps)} steps")
PY
```

Expected: 1–3 flows, each with 2–5 steps targeting the `data-testid` selectors. Cost: ~$0.005.

**Never commit a real API key.** Use env vars only.

---

## Hot zones — where to be extra careful

Code that has subtle behavior, where a "small fix" can quietly break something far away.

### `api/analyzer.py:_FileIndex.overview()`
Renders the overview the agent sees first. Ordering (UI files first), pagination, and the column layout all matter. Changing the format will silently degrade the LLM's behavior because models learn to expect specific structures from the system prompt.

### `api/analyzer.py:analyze_pr` → message loop
The order of operations inside the for-loop is load-bearing. **The assistant message with `tool_calls` MUST be appended before the `tool` result messages** — OpenAI's API rejects out-of-order messages with a 400. Don't refactor this loop without re-running the live OpenAI test.

### `api/cloud_recorder.py:_to_key()`
Order of regex matches matters. `data-testid` is checked **before** the bare `id` branch because `[data-testid=foo]` would otherwise be matched as if it started with `[`. If you add a new selector strategy, add it in the right place.

### `api/main.py:_check_api_key()`
Currently allows missing key in dev mode (when no `RECORDLOOP_API_KEY` / `RECORDLOOP_VALID_KEYS` is set). **The Ops review flagged this** — eventually we should fail closed in prod. Don't replicate the dev-mode pattern in any new auth check.

### `_DRY_RUN_FLOWS_PAYLOAD` in `api/analyzer.py`
This canned JSON shape is used by ~10 tests. If you change the shape, you'll need to update every test that asserts against `dry_run_smoke_flow`.

---

## Out of scope (don't touch unless explicitly asked)

These directories work, are independently tested, and have nothing to do with the agent loop. Don't refactor them to "match the api/ patterns":

- `js/` — Browser SDK. Has its own build pipeline.
- `src/recordloop/cli/` — Local CLI. Used by `python -m recordloop`.
- `src/recordloop/bridge/` — Bridge server that the JS SDK posts to.
- `demo/`, `example.py`, `demo_test.py` — Standalone demos.
- `generated-tests/` — Output directory.
- `recordloop.egg-info/` — Build artifact.
- `.recordloop/` — Local user data.
- `DISCUSSION.txt`, `PRODUCT.md` — Personal notes / planning docs.

You may **read** any of these for context. Just don't modify them as part of api/ work.

---

## License

MIT (see `LICENSE` once it's added — `pyproject.toml` already declares `license = { text = "MIT" }`). Project is being prepared for open-source release. **Never commit secrets**, especially `.env` files, GitHub tokens, OpenAI keys, AWS credentials. If you find one in git history, flag it immediately rather than deleting silently.

---

## One-line summary for your context window

> RecordLoop is a PR-diff → AI-flow → Playwright-video pipeline. The agent loop lives in `api/analyzer.py`, runs OpenAI/Azure with bounded cost (`MAX_ITERATIONS=10`, `MAX_FILES_READ=30`, `MAX_TOTAL_INPUT_TOKENS=50K`), supports `RECORDLOOP_DRY_RUN=1` for zero-cost CI, and is verified by `api/tests/test_smoke.py` (55 tests, <1s, zero secrets). Don't break the dry-run path, the lazy playwright import, or the public `analyze_pr()` signature.
