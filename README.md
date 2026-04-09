# RecordLoop

**AI-driven UI test recordings on every PR.** A GitHub Action reads your pull request diff, an LLM agent generates realistic Playwright interaction flows targeted at exactly what changed, replays them against your preview URL, and posts the recording back as a PR comment.

No JS SDK. No bridge server. No S3 bucket. No committed session files. Twelve lines of YAML and one secret.

```
PR opened ──► Agent reads diff ──► Playwright replays ──► PR comment with GIF
```

- **12-line install** — one workflow file, one secret. `uses: vihaanshahh/recordloop@v1` and you're done.
- **AI reads your diff** — an agent loop calls `read_diff` / `read_file` / `list_files` and generates one focused flow per PR aimed at the actual changed lines.
- **30+ frameworks** — React, Vue, Next.js, Nuxt, Angular, Svelte, Astro, Solid, Qwik, SvelteKit, Remix, Blazor, Razor, Rails (ERB), Phoenix LiveView, Django/Jinja, Twig, Handlebars, Liquid, Pug, Nunjucks, PHP, plain HTML, HTMX. Anything that ships markup.
- **Bounded cost** — $0.001 to $0.005 per PR on `gpt-5.4`. The agent is hard-capped at 10 iterations, 30 files, and 50K input tokens, so worst-case is around $0.10 per PR even on expensive reasoning models.
- **MIT licensed, zero infra** — every line is auditable. No telemetry. Recordings are stored as release assets in your own repo.

## Quick start

### 1. Add the workflow

Drop this into `.github/workflows/recordloop.yml`:

```yaml
name: RecordLoop
on:
  pull_request:
    types: [opened, synchronize, reopened]
permissions:
  pull-requests: write
  contents: write
jobs:
  recordloop:
    runs-on: ubuntu-latest
    if: github.event.pull_request.head.repo.full_name == github.repository
    steps:
      - uses: vihaanshahh/recordloop@v1
        with:
          openai-api-key: ${{ secrets.OPENAI_API_KEY }}
```

That's the entire install — no `pip`, no `npm`, no bridge server. The action installs its own Python, Playwright, and ffmpeg dependencies on the runner.

The `if:` guard disables the job on PRs from forks so untrusted contributors can't access your OpenAI key.

### 2. Add your OpenAI key as a secret

```bash
gh secret set OPENAI_API_KEY
```

Or via Settings → Secrets and variables → Actions. Azure OpenAI works too — see [provider configuration](#provider-configuration) below.

### 3. Open a PR — get a video comment

On every PR, the action will:

1. Fetch the diff via the GitHub API
2. Hand the diff to an agent loop with `read_diff` / `read_file` / `list_files` / `submit_flows` tools
3. Generate one short Playwright flow targeted at the changed lines
4. Auto-start your app on the runner (or use a `preview-url` you provide)
5. Replay the flow with Playwright, record it as MP4 + GIF
6. Upload the GIF to a `recordloop-recordings` release in your repo
7. Post a PR comment with the GIF rendered inline

## Inputs

| Input | Default | Description |
|---|---|---|
| `openai-api-key` | _(required)_ | OpenAI API key. Used by the analyzer agent. |
| `preview-url` | _(empty)_ | PR preview deployment URL. If empty AND `auto-start` is on, the action builds and runs your app on the runner. |
| `auto-start` | `true` | When `preview-url` is empty AND there's a `package.json`, auto-build and start your app on the runner so flows can record against `localhost`. |
| `start-command` | _(auto-detect)_ | Override the auto-start command. Default tries `npm ci` → `npm run build` → `npm start` (or `npm run dev`). |
| `start-port` | _(auto-probe)_ | Port to probe for the app. Default tries `3000, 3001, 4173, 5173, 4321, 8080`. |
| `node-version` | `20` | Node version to install when auto-start is enabled. |
| `python-version` | `3.12` | Python version to install for the runner. |
| `model` | `gpt-5.4` | Override the analyzer model. Try `gpt-4o-mini` for the cheapest setup. |
| `provider` | `openai` | `openai` (default) or `azure`. |
| `azure-openai-api-key` | _(empty)_ | Azure OpenAI API key. Required when `provider: azure`. |
| `azure-openai-endpoint` | _(empty)_ | Azure OpenAI resource endpoint. |
| `azure-openai-deployment` | _(empty)_ | Azure OpenAI deployment name (used as the model identifier). |
| `github-token` | `${{ github.token }}` | Token used to fetch the PR diff and post the comment. |

## Provider configuration

### OpenAI (default)

```yaml
- uses: vihaanshahh/recordloop@v1
  with:
    openai-api-key: ${{ secrets.OPENAI_API_KEY }}
    model: gpt-4o-mini  # optional cheaper override
```

### Azure OpenAI

For compliance-friendly routing where the diff never leaves your Azure tenant:

```yaml
- uses: vihaanshahh/recordloop@v1
  with:
    provider: azure
    azure-openai-api-key: ${{ secrets.AZURE_OPENAI_API_KEY }}
    azure-openai-endpoint: https://my-resource.openai.azure.com
    azure-openai-deployment: gpt-5.4
```

## How it works

```
┌─────────────────┐    ┌────────────────────┐    ┌────────────────────┐    ┌──────────────┐
│   PR opened     │ ─► │  Agent reads diff  │ ─► │  Playwright replay │ ─► │  PR comment  │
│                 │    │  (read_diff /      │    │  on auto-started   │    │  inline GIF  │
│  pull_request   │    │   read_file /      │    │  app or preview    │    │              │
│  workflow event │    │   list_files)      │    │  URL               │    │              │
└─────────────────┘    └────────────────────┘    └────────────────────┘    └──────────────┘
```

The agent sees a token-budgeted overview of every changed file in the PR and uses tools to drill into whichever ones look load-bearing. It generates exactly one short flow (2–5 steps) whose every step targets the changed region — no wandering through unchanged UI.

A Playwright worker on the runner replays the flow, ffmpeg converts the MP4 to a 15 fps palette-optimised GIF, and the action uploads it to a `recordloop-recordings` pre-release in your repo. The GIF renders inline in the PR comment.

## What the agent guarantees

- **Only one flow per PR** — picks the single most user-visible change.
- **Only what changed** — every step in the flow must touch an element on a `+` diff line or sit directly next to one. No generic smoke tests.
- **Bounded cost** — hard caps on iterations (10), files read (30), and total input tokens (50K).
- **Bounded surface** — the agent only sees files changed in this PR, never the rest of your repo.

## Privacy and security

- Only the files changed in the PR are sent to the LLM provider, capped at ~50K tokens of input context. Nothing else in your repo is read.
- The `if:` guard on the workflow disables the job entirely on PRs from forks, so untrusted contributors can't trigger runs against your OpenAI key.
- Generated Playwright flows run inside the GitHub runner against your preview URL — same-origin only, no shell access, no arbitrary network egress.
- Every line of the analyzer, the action, and the agent prompt is MIT licensed. Fork it, audit it, self-host it on your own runners.

## Self-hosted runners

RecordLoop is a composite GitHub Action — point it at a self-hosted runner via `runs-on: [self-hosted, linux]` and the action installs its own Playwright + ffmpeg dependencies on first run, then re-uses the cached layer on subsequent jobs. Your OpenAI key stays in your own secret store, network egress is whatever the runner allows, and recordings stay inside your GitHub org.

## Cost

| Setup | Per PR | Notes |
|---|---|---|
| `gpt-5.4` (default) | $0.001 – $0.005 | Most PRs land in this range. |
| `gpt-4o-mini` | ~$0.0003 | Override `model` for the cheapest config. |
| Worst case | ~$0.10 | Caps: 10 iterations × 50K input tokens. |

There are no seats, no quotas, no minimums. You pay your own LLM bill, RecordLoop marks nothing up.

## Local development

To work on RecordLoop itself:

```bash
git clone https://github.com/vihaanshahh/recordloop
cd recordloop
pip install -e '.[dev]'
pytest
```

The analyzer lives in `api/analyzer.py`, the recorder in `api/cloud_recorder.py`, and the action entry point in `api/run_action.py`.

## License

MIT. See [LICENSE](LICENSE).
