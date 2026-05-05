# RecordLoop

**Stop pulling branches to verify "the button has the right hover state."** RecordLoop is a GitHub Action that reads every PR's diff, generates a Playwright flow targeted at exactly what changed, runs real assertions against the live page, and posts a pass/fail comment with an inline GIF of the interaction. The PR check turns red when an assertion fails.

It's a UI test that writes itself, every PR, scoped to the diff.

```
PR opened ──► Agent reads diff ──► Playwright runs flow + assertions ──► ✅ / ❌ PR comment
```

## Why

You already know the pain. Someone opens a PR titled "fix: nav CTA href." You have to:

1. Pull the branch
2. `npm install`, `npm run dev`, wait
3. Click around the nav, squint, decide it looks right
4. Maybe forget to test the mobile breakpoint and ship a regression

RecordLoop does steps 1-3 automatically on every PR, generates an assertion derived from the diff (`[data-testid='nav-cta']` should have `href` containing `github.com`), records the click as a GIF, and either marks the check green or red. You review the GIF in the PR comment instead of pulling the branch.

- **Real assertions** — `assert_text`, `assert_attribute`, `assert_url`, `assert_visible`. The PR check fails when an assertion fails. Not a screensaver.
- **Scoped to the diff** — every step in the flow must touch a `+` line or sit within ~5 lines of one. No wandering smoke tests.
- **One clean comment per PR** — re-runs on push **edit the same comment in place**. Your PR thread doesn't fill up with bot noise.
- **12-line install** — one workflow file, one secret. `uses: vihaanshahh/recordloop@v1`.
- **MIT, zero infra** — no JS SDK, no bridge server, no S3 bucket. Recordings live as release assets in your own repo.
- **Bounded cost** — $0.001-$0.005 per PR in LLM tokens (worst case ~$0.10). Runner minutes are typically 1-2 minutes per PR.

## Prerequisites

You need exactly two things before you can install:

1. **An OpenAI account with a payment method on file.** Get one at [platform.openai.com](https://platform.openai.com). Add at least $5 of credit. RecordLoop bills against your own key — there's no RecordLoop SaaS, no markup, no proxy.
2. **A GitHub repo where you can add a workflow file and a secret.** That's it.

If you'd rather use Azure OpenAI (compliance-friendly, your code stays inside your Azure tenant), see [Azure setup](#azure-openai) below.

## Quick start

### 1. Add the workflow

Drop this into `.github/workflows/recordloop.yml`:

```yaml
name: RecordLoop
on:
  pull_request:
    types: [opened, synchronize, reopened]
permissions:
  pull-requests: write   # to post & edit the PR comment in place
  contents: write        # to upload recording assets on your repo
jobs:
  recordloop:
    runs-on: ubuntu-latest
    if: github.event.pull_request.head.repo.full_name == github.repository
    steps:
      - uses: vihaanshahh/recordloop@v1
        with:
          openai-api-key: ${{ secrets.OPENAI_API_KEY }}
```

That's the entire install. No `pip`, no `npm`, no bridge server.

**About the permissions:**
- `pull-requests: write` — to post and edit the PR comment.
- `contents: write` — to create a hidden pre-release named `recordloop-recordings` in your own repo and upload the recorded GIF/video assets there. Inline-rendering GIFs in markdown comments requires the GIF to live somewhere addressable; release assets are the cheapest GitHub-native answer. We never write to your code, branches, or tags.

**About the `if:` guard:** by default this disables RecordLoop on PRs from forks, so untrusted contributors can't trigger runs against your OpenAI key. If you maintain an open-source project and need RecordLoop on contributor PRs, see [the OSS workflow](#oss-maintainers-fork-prs-with-a-label-gate) below — it uses the standard `pull_request_target` + label-gated pattern.

### 2. Add your OpenAI key as a secret

```bash
gh secret set OPENAI_API_KEY
```

Or via Settings → Secrets and variables → Actions. Azure OpenAI works too — see [provider configuration](#provider-configuration) below.

### 3. Open a PR — get a recording comment

On every PR, the action will:

1. Fetch the diff via the GitHub API
2. Hand the diff to an agent loop with `read_diff` / `read_file` / `list_files` / `submit_flows` tools
3. Generate one short Playwright flow targeted at the changed lines
4. Auto-start your app on the runner (or use a `preview-url` you provide)
5. Replay the flow with Playwright at the selected viewport(s), capture a screenshot GIF plus full browser video
6. Upload the GIF/video assets to a `recordloop-recordings` release in your repo
7. Post a PR comment with the GIF(s) rendered inline

## OSS maintainers: fork PRs with a label gate

The default install in [Quick start](#quick-start) uses an `if:` guard that disables RecordLoop on PRs from forks. That's the right default for private repos, but it kills the entire use case for OSS maintainers — drive-by visual PRs are *exactly* where you want a recording before pulling the branch.

For OSS use the standard GitHub `pull_request_target` + label-gated pattern. Create a label called `recordloop-ok` in your repo, then use this workflow:

```yaml
name: RecordLoop
on:
  pull_request_target:
    types: [labeled, synchronize]
permissions:
  pull-requests: write
  contents: write
jobs:
  recordloop:
    if: contains(github.event.pull_request.labels.*.name, 'recordloop-ok')
    runs-on: ubuntu-latest
    steps:
      # Check out the PR HEAD (not the base) so we record the contributor's
      # actual changes — but only because the maintainer applied the label.
      - uses: actions/checkout@v4
        with:
          ref: ${{ github.event.pull_request.head.sha }}
      - uses: vihaanshahh/recordloop@v1
        with:
          openai-api-key: ${{ secrets.OPENAI_API_KEY }}
```

Workflow:

1. A contributor opens a PR from their fork.
2. You glance at the diff (30 seconds — same as you do today).
3. If it's not malicious, you apply the `recordloop-ok` label.
4. RecordLoop runs against the contributor's fork code, posts the GIF + assertions to the PR.
5. You review the GIF in the comment instead of pulling the branch.

The label is the human-in-the-loop. RecordLoop never runs on unlabeled fork PRs, so a hostile contributor can't exfiltrate your OpenAI key by submitting `prompt_injection.tsx`. The label-applier is recorded in the GitHub audit log automatically.

Re-running on `synchronize` keeps the same comment (RecordLoop edits in place), so the label only needs to be applied once per PR.

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
| `viewports` | `desktop` | Comma-separated recording profiles: `desktop`, `mobile`, `tablet`, `tall`, or custom `WIDTHxHEIGHT`. |
| `wait-until` | `networkidle` | Page readiness state before actions begin: `networkidle`, `load`, or `domcontentloaded`. |
| `settle-ms` | `300` | Extra wait after the page is ready before actions begin. |
| `model` | `gpt-5.4` | Override the analyzer model. Try `gpt-4o-mini` for the cheapest setup. |
| `provider` | `openai` | `openai` (default), `azure`, or `anthropic`. |
| `azure-openai-api-key` | _(empty)_ | Azure OpenAI API key. Required when `provider: azure`. |
| `azure-openai-endpoint` | _(empty)_ | Azure OpenAI resource endpoint. |
| `azure-openai-deployment` | _(empty)_ | Azure OpenAI deployment name (used as the model identifier). |
| `anthropic-api-key` | _(empty)_ | Anthropic API key. Required when `provider: anthropic`. Works for native Anthropic and Azure AI Foundry. |
| `anthropic-base-url` | _(empty)_ | Base URL. Native: `https://api.anthropic.com/v1`. Foundry: `https://<resource>.services.ai.azure.com/api/projects/<project>` (action appends `/messages`). |
| `anthropic-api-version` | _(empty)_ | Optional `?api-version=` query param (some Foundry routes need it, e.g. `2024-12-01-preview`). |
| `github-token` | `${{ github.token }}` | Token used to fetch the PR diff and post the comment. |
| `storage-state` | _(empty)_ | Pre-captured Playwright storage state (base64 JSON or raw JSON). Use this for SSO/Auth0 flows the built-in login can't drive. |
| `login-username` | _(empty)_ | Username/email for built-in login. Setting this together with `login-password` activates login. |
| `login-password` | _(empty)_ | Password for built-in login. Required together with `login-username`. |
| `login-url` | `/login` | Path or absolute URL to the login page. |
| `login-username-selector` | _(heuristic)_ | CSS selector for the username/email input. Defaults match `input[type="email"]` and common variants — only override when the heuristic mis-targets your form. |
| `login-password-selector` | _(heuristic)_ | CSS selector for the password input. Default is `input[type="password"]`. |
| `login-submit-selector` | _(heuristic)_ | CSS selector for the submit button. Defaults match `button[type="submit"]` / "Sign in" / "Log in". |
| `login-success-url` | _(auto)_ | URL glob the page must reach for success. When unset, success = the page leaves the login URL within 30s. |
| `storybook-config-dir` | _(auto-detect)_ | Path to a `.storybook` config directory. Set to disambiguate monorepos with multiple, or to `false` to disable. |
| `storybook-port` | `6006` | Port for the static-served Storybook bundle. |
| `npm-registry` | _(empty)_ | Optional npm registry URL (e.g. internal Artifactory mirror). Skipped if a `.npmrc` already exists. |
| `npm-auth-token` | _(empty)_ | Auth token for `npm-registry`. Cleaned up at end of run. |
| `pip-index-url` | _(empty)_ | Optional pip index URL. Skipped if `PIP_INDEX_URL` or a `pip.conf` is already set. |

### Responsive recordings

The analyzer still generates one focused flow, then the recorder can replay it
at multiple sizes without extra LLM cost:

```yaml
- uses: vihaanshahh/recordloop@v1
  with:
    openai-api-key: ${{ secrets.OPENAI_API_KEY }}
    viewports: desktop,mobile,tall
```

`mobile` uses a touch-enabled mobile browser context at `390x844`. `tall`
records a `1280x1600` viewport for longer pages. Custom breakpoints use
`WIDTHxHEIGHT`, for example `414x896`.

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

### Anthropic (native + Azure AI Foundry)

Use Claude as the analyzer. The same provider works against `api.anthropic.com`
or against Azure AI Foundry's Anthropic-compatible endpoint — Foundry keeps
the diff inside your Azure tenant *and* lets you route to Claude.

```yaml
# Native Anthropic
- uses: vihaanshahh/recordloop@v1
  with:
    provider: anthropic
    anthropic-api-key: ${{ secrets.ANTHROPIC_API_KEY }}
    anthropic-base-url: https://api.anthropic.com/v1
    model: claude-opus-4-7

# Azure AI Foundry
- uses: vihaanshahh/recordloop@v1
  with:
    provider: anthropic
    anthropic-api-key: ${{ secrets.AZURE_FOUNDRY_KEY }}
    anthropic-base-url: https://my-resource.services.ai.azure.com/api/projects/my-project
    anthropic-api-version: 2024-12-01-preview
    model: claude-opus-4-7
```

The action appends `/messages` to `anthropic-base-url` automatically (unless
you already included it). Both `api-key` and `x-api-key` headers are sent so
the same input works on either backend.

## Gated apps (login required)

If your app routes the meaningful UI behind a standard email/password form,
just hand RecordLoop the credentials:

```yaml
- uses: vihaanshahh/recordloop@v1
  with:
    openai-api-key: ${{ secrets.OPENAI_API_KEY }}
    login-username: ${{ secrets.E2E_USER }}
    login-password: ${{ secrets.E2E_PASS }}
```

That's the whole config. RecordLoop navigates to `/login`, finds the email
and password inputs by heuristic, submits, and waits for the page to leave
the login URL. The captured Playwright storage state is reused when the
agent's flow replays, so the recording shows the gated UI, not the login
screen. Credentials are read from env only and never echoed.

Override anything when the defaults don't fit:

```yaml
- uses: vihaanshahh/recordloop@v1
  with:
    openai-api-key: ${{ secrets.OPENAI_API_KEY }}
    login-username: ${{ secrets.E2E_USER }}
    login-password: ${{ secrets.E2E_PASS }}
    login-url: /auth/sign-in           # default: /login
    login-success-url: '**/dashboard**' # default: any URL change from login-url
    # login-username-selector / login-password-selector / login-submit-selector
    # are also overridable but the heuristics work for almost every form.
```

**Limitations.** This is for first-party email/password forms. Auth0
Universal Login, SAML SSO, and any cross-origin redirect flow won't work —
those need a `storage-state` you've captured offline and pass via the
existing `storage-state` input.

## Storybook

Repos with a `.storybook/main.{js,ts,mjs,cjs}` config directory are detected
automatically: the action runs `npx storybook build`, serves the static
bundle on port 6006, and points the recorder at it. No extra config:

```yaml
- uses: vihaanshahh/recordloop@v1
  with:
    openai-api-key: ${{ secrets.OPENAI_API_KEY }}
```

Use `storybook-config-dir` to disambiguate in monorepos with multiple
`.storybook/` directories, or set it to `false` to force the action to serve
your main app instead.

## Corporate / self-hosted runners

For runs on a bare RHEL/Alpine container, behind an Artifactory mirror, with
no `git`/`node` pre-installed:

```yaml
runs-on: [self-hosted, linux]
container:
  image: registry.internal/workbench:latest
steps:
  - uses: vihaanshahh/recordloop@v1
    with:
      provider: anthropic
      anthropic-api-key: ${{ secrets.AZURE_FOUNDRY_KEY }}
      anthropic-base-url: https://my-resource.services.ai.azure.com/api/projects/my-project
      model: claude-opus-4-7
      # Only set these when you actually need an internal mirror.
      # Skipped automatically if you already have a .npmrc / pip.conf.
      npm-registry: https://artifactory.internal/api/npm/npm-virtual/
      npm-auth-token: ${{ secrets.ARTIFACTORY_TOKEN }}
      pip-index-url: https://artifactory.internal/api/pypi/pypi-virtual/simple
```

The action probes the container on every run and only installs what's
missing: `git`, `node 20`, `npm`, `curl`, and `python3`. On Ubuntu runners
this is a no-op. If `python3`, Playwright, Chromium, and Pillow are pre-baked
into your image, the action skips those steps automatically.
The `~/.npmrc` it writes is `chmod 600` and removed at end of run.

## Advanced

These inputs exist for niche pre-baked images and override the auto-detection
above. **You almost never need them** — set them only if the auto-skip
logic is wrong for your environment.

| Input | Default | Notes |
|---|---|---|
| `harden-container` | `auto` | `auto` / `true` / `false`. Force the probe-and-install on/off. `auto` no-ops on Ubuntu. |
| `skip-python-setup` | `false` | Force-skip `setup-python`. By default the action skips it automatically when the container's `python3` is at or above `python-version`. |
| `skip-runtime-install` | `false` | Force-skip the pip-install of `openai`/`pyyaml`/`httpx`. Auto-skipped when those modules are already importable. |
| `skip-playwright-install` | `false` | Force-skip Playwright + Chromium install. Auto-skipped when `playwright` and a Chromium browser cache are already present. |

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

A Playwright worker on the runner replays the flow at each selected viewport.
RecordLoop snapshots the page after load and after every step, stitches those
frames into a compact inline GIF with Pillow, and uploads both the GIF and the
full Playwright browser video to a `recordloop-recordings` pre-release in your
repo.

## What the agent guarantees

- **Only one flow per PR** — picks the single most user-visible change.
- **Only what changed** — every step in the flow must touch an element on a `+` diff line or sit directly next to one. No generic smoke tests.
- **At least one real assertion** — the agent is required to emit at least one assertion derived from the diff. A flow without assertions is rendered as `▶ Demo` and does not turn the check green.
- **Bounded cost** — hard caps on iterations (10), files read (30), and total input tokens (50K).
- **Bounded surface** — the agent only sees files changed in this PR, never the rest of your repo.

### Assertion vocabulary

The agent picks from four oracle types, derived from the diff:

| Action | Selector | Value | What it checks |
|---|---|---|---|
| `assert_text` | CSS selector | expected substring | The element's `textContent` contains the substring. |
| `assert_attribute` | CSS selector | `attr=expected` | The named attribute contains the expected substring. |
| `assert_url` | _(unused)_ | expected substring | `page.url` contains the substring. |
| `assert_visible` | CSS selector | _(unused)_ | The element is present and visible. |

When an assertion fails, the failure reason appears at the top of the PR comment, the GIF still renders (so you can see the broken state), and the workflow exits non-zero — turning the PR check red.

## Privacy and security

- Only the files changed in the PR are sent to the LLM provider, capped at ~50K tokens of input context. Nothing else in your repo is read.
- The `if:` guard on the workflow disables the job entirely on PRs from forks, so untrusted contributors can't trigger runs against your OpenAI key.
- Generated Playwright flows run inside the GitHub runner against your preview URL — same-origin only, no shell access, no arbitrary network egress.
- Every line of the analyzer, the action, and the agent prompt is MIT licensed. Fork it, audit it, self-host it on your own runners.

## Self-hosted runners

RecordLoop is a composite GitHub Action — point it at a self-hosted runner via `runs-on: [self-hosted, linux]` and the action installs its own Playwright + Pillow dependencies on first run, then re-uses the cached layer on subsequent jobs. Your OpenAI key stays in your own secret store, network egress is whatever the runner allows, and recordings stay inside your GitHub org.

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
