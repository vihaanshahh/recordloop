# RecordLoop AI Analyzer API

A FastAPI service that turns a GitHub PR diff into recorded Playwright sessions, automatically.

```
PR opened ──► /trigger ──► fetch changed files ──► LLM picks one flow ──► Playwright replays viewports ──► PR comment + job result
```

The LLM step is provider-agnostic: **OpenAI**, **Azure OpenAI**, or
**Anthropic**. Default model is `gpt-5.4` for OpenAI/Azure and
`claude-opus-4-7` for Anthropic.

---

## What it does

1. **Fetches the PR diff** via the GitHub API (`api/github_client.py`)
2. **Filters to UI surfaces** (`.tsx`, `.jsx`, `.vue`, `.svelte`, templates, HTML; keeps tests/stories for context)
3. **Runs an LLM agent loop** to generate exactly one focused interaction flow (`api/analyzer.py`)
4. **Replays that flow** with Playwright at the configured viewport(s) (`api/cloud_recorder.py`)
5. **Posts/updates a PR comment** with recording status, artifact paths/URLs, assertions, and cost

The GitHub Action path uploads GIF/video artifacts to a repo release before
rendering the PR comment. The hosted FastAPI path records local artifact paths
unless your deployment adds an upload/storage layer and populates `gif_url` /
`video_url`.

Jobs run in the background. `/jobs/{job_id}` returns status: `queued → analyzing → recording → done | failed`.

---

## Install

```bash
cd api
pip install -r requirements.txt
playwright install chromium
```

---

## Configure the LLM provider

Pick **one** of these provider setups.

### OpenAI

```bash
export OPENAI_API_KEY=sk-...
export OPENAI_MODEL=gpt-5.4          # optional — this is the default
```

### Azure OpenAI

```bash
export AZURE_OPENAI_API_KEY=...
export AZURE_OPENAI_ENDPOINT=https://my-resource.openai.azure.com
export AZURE_OPENAI_DEPLOYMENT=my-gpt-5-4-deployment
export AZURE_OPENAI_API_VERSION=2024-10-21   # optional
```

### Anthropic

```bash
export ANTHROPIC_API_KEY=...
export ANTHROPIC_BASE_URL=https://api.anthropic.com/v1  # optional
export ANTHROPIC_MODEL=claude-opus-4-7                  # optional
```

You can also pass provider settings per request in the `/trigger` body;
request values override env vars.

---

## Run the server

```bash
# from repo root
uvicorn api.main:app --reload --port 8080
```

Optional API key gating:

```bash
export RECORDLOOP_API_KEY=my-shared-secret
# or multiple, comma-separated:
export RECORDLOOP_VALID_KEYS=key1,key2,key3
```

If neither is set, the server is open (dev mode) and the `X-Api-Key` header is unnecessary.

### Dry-run mode

```bash
export RECORDLOOP_DRY_RUN=1
```

When set, the analyzer skips every LLM call and returns a canned flow. Useful for CI smoke tests, local dev without API keys, and as the cheapest possible health check on the full pipeline.

---

## Trigger a job

### Minimal (uses server env vars, OpenAI)

`X-Api-Key` is only required if `RECORDLOOP_API_KEY` / `RECORDLOOP_VALID_KEYS` is set on the server.

```bash
curl -X POST http://localhost:8080/trigger \
  -H "Content-Type: application/json" \
  -d '{
    "repo": "vihaanshahh/recordloop",
    "pr_number": 42,
    "preview_url": "https://pr-42.preview.example.com",
    "github_token": "ghp_..."
  }'
```

Response:

```json
{ "job_id": "a1b2c3d4", "status": "queued", "message": "Job a1b2c3d4 started for vihaanshahh/recordloop#42" }
```

### Per-request provider override (nested `llm` config)

```bash
curl -X POST http://localhost:8080/trigger \
  -H "X-Api-Key: my-shared-secret" \
  -H "Content-Type: application/json" \
  -d '{
    "repo": "vihaanshahh/recordloop",
    "pr_number": 42,
    "preview_url": "https://pr-42.preview.example.com",
    "github_token": "ghp_...",
    "llm": {
      "provider": "azure",
      "model": "gpt-5.4",
      "azure": {
        "api_key": "...",
        "endpoint": "https://my-resource.openai.azure.com",
        "deployment": "my-gpt-5-4-deployment",
        "api_version": "2024-10-21"
      }
    }
  }'
```

For OpenAI overrides, use the flat `llm.api_key` instead of `llm.azure`:

```json
{ "llm": { "provider": "openai", "model": "gpt-5.4", "api_key": "sk-..." } }
```

### Responsive recording override

```json
{
  "recording": {
    "viewports": ["desktop", "mobile", "tall"],
    "settle_ms": 800
  }
}
```

The same generated flow is replayed at each viewport, so LLM cost does not
increase.

### Poll the job

```bash
curl http://localhost:8080/jobs/a1b2c3d4
```

```json
{
  "job_id": "a1b2c3d4",
  "status": "done",
  "files_changed": 7,
  "flows_generated": 1,
  "recordings": [
    {
      "name": "submit_signup_form",
      "viewport": "desktop",
      "viewport_width": 1280,
      "viewport_height": 720,
      "gif": "/tmp/recordloop-videos/signup-desktop.gif",
      "video": "/tmp/recordloop-videos/signup.webm",
      "gif_url": null,
      "video_url": null,
      "status": "passed"
    }
  ]
}
```

---

## Test it without GitHub, Playwright, or LLM tokens

### Option A — `RECORDLOOP_DRY_RUN=1` (zero secrets, zero cost)

The analyzer short-circuits to a synthetic flow when this env var is set. Use it for smoke tests, copy-pasteable docs, and local hacking.

```bash
export RECORDLOOP_DRY_RUN=1
python -c "
from api.analyzer import analyze_pr
flows = analyze_pr(
    [{'filename': 'Foo.tsx', 'status': 'modified', 'content': '<button id=go>Go</button>'}],
    'http://localhost:3000',
).flows
for f in flows:
    print(f.name, '—', f.description)
"
# dry_run_smoke_flow — Synthetic flow returned by RECORDLOOP_DRY_RUN — no LLM call was made
```

This still exercises the file filter, prompt builder, JSON parser, and dataclass mapping — just without the network call.

### Option B — Real LLM call (one API request, ~$0.001)

Self-contained — no files on disk required.

```python
# scratch_test.py
from api.analyzer import analyze_pr

fake_files = [{
    "filename": "SignupForm.tsx",
    "status": "modified",
    "content": '<form><input name="email"/><button id="go">Sign up</button></form>',
}]

flows = analyze_pr(
    changed_files=fake_files,
    preview_url="http://localhost:3000",
    provider="openai",   # or "azure" / "anthropic"
).flows

for f in flows:
    print(f.name, "—", f.description)
    for s in f.steps:
        print(" ", s.action, s.selector, s.value or "")
```

```bash
export OPENAI_API_KEY=sk-...
python scratch_test.py
```

### Option C — Unit-test parser/recorder helpers

The analyzer is an agent loop, not a single `_call_llm` helper. For zero-secret
tests, exercise helpers directly (`_parse_flows`, `_resolve_viewports`,
`_dispatch_tool`) or use `RECORDLOOP_DRY_RUN=1`. See `api/tests/test_smoke.py`
for the supported patterns.

---

## Test it in a CI runner

Drop this into `.github/workflows/api-smoke.yml`:

```yaml
name: API smoke test
on: [push, pull_request]

jobs:
  smoke:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }

      - run: pip install -r api/requirements.txt
      - run: playwright install --with-deps chromium

      # Boot the API in dry-run mode — no LLM key needed
      - run: uvicorn api.main:app --port 8080 &
        env:
          RECORDLOOP_DRY_RUN: "1"

      - run: |
          for i in {1..20}; do
            curl -sf http://localhost:8080/health && break
            sleep 1
          done

      # Trigger against this very PR
      - run: |
          curl -fsS -X POST http://localhost:8080/trigger \
            -H "Content-Type: application/json" \
            -d "{
              \"repo\": \"${{ github.repository }}\",
              \"pr_number\": ${{ github.event.pull_request.number }},
              \"preview_url\": \"\",
              \"github_token\": \"${{ secrets.GITHUB_TOKEN }}\"
            }"
```

No secrets required beyond the auto-provided `GITHUB_TOKEN`. To run the **real** LLM in CI, drop `RECORDLOOP_DRY_RUN`, add `OPENAI_API_KEY` (or the Azure trio), and set `PREVIEW_URL`.

---

## Request schema (`TriggerRequest`)

| Field          | Type        | Required | Notes                                      |
|----------------|-------------|----------|--------------------------------------------|
| `repo`         | string      | yes      | `"owner/repo"`                             |
| `pr_number`    | int         | yes      |                                            |
| `preview_url`  | string      | no       | If empty, recordings are skipped (dry run) |
| `github_token` | string      | yes      | Needs PR read + comment write              |
| `llm`          | `LLMConfig` | no       | Provider config — falls back to env vars   |
| `recording`    | `RecordingConfig` | no | Viewport/load config for Playwright replay |

**`LLMConfig`**

| Field      | Type          | Notes                                                |
|------------|---------------|------------------------------------------------------|
| `provider` | string        | `"openai"` (default), `"azure"`, or `"anthropic"` |
| `model`    | string        | Defaults to `gpt-5.4` or `claude-opus-4-7`        |
| `api_key`  | string        | Used when `provider == "openai"`. Overrides env var. |
| `azure`    | `AzureConfig` | Required only when `provider == "azure"`          |
| `anthropic` | `AnthropicConfig` | Required only when `provider == "anthropic"` |

**`RecordingConfig`**

| Field               | Type         | Default       | Notes |
|---------------------|--------------|---------------|-------|
| `viewports`         | list[string] | `["desktop"]` | Named profiles: `desktop`, `mobile`, `tablet`, `tall`; custom `WIDTHxHEIGHT` also works. |
| `wait_until`        | string       | `networkidle` | `networkidle`, `load`, or `domcontentloaded`. |
| `settle_ms`         | int          | `300`         | Extra wait after readiness before interactions begin. |

**`AzureConfig`**

| Field         | Type   | Notes                              |
|---------------|--------|------------------------------------|
| `api_key`     | string | Overrides `AZURE_OPENAI_API_KEY`   |
| `endpoint`    | string | Overrides `AZURE_OPENAI_ENDPOINT`  |
| `deployment`  | string | Overrides `AZURE_OPENAI_DEPLOYMENT`|
| `api_version` | string | Defaults to `2024-10-21`           |

**`AnthropicConfig`**

| Field         | Type   | Notes |
|---------------|--------|-------|
| `api_key`     | string | Overrides `ANTHROPIC_API_KEY` |
| `base_url`    | string | Native Anthropic or Azure AI Foundry project URL |
| `api_version` | string | Optional Foundry `?api-version=` query param |

---

## Endpoints

| Method | Path           | Description                                  |
|--------|----------------|----------------------------------------------|
| GET    | `/health`      | Liveness                                     |
| POST   | `/trigger`     | Start an analyze-and-record job (background) |
| GET    | `/jobs/{job_id}` | Job status + results                        |

Header on `/trigger`: `X-Api-Key: <your key>` (skipped if no key configured).

---

## Files

- `api/analyzer.py` — LLM provider switch + agentic flow generation (`analyze_pr`)
- `api/cloud_recorder.py` — Playwright replay, viewport profiles, screenshot GIF capture
- `api/github_client.py` — Fetch PR files, post comments
- `api/main.py` — FastAPI app, `/trigger`, `/jobs/{job_id}`, background runner
- `api/models.py` — Pydantic request/response models
