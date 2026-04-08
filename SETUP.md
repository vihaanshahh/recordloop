# SETUP.md — RecordLoop install runbook for AI agents

> **Audience**: An AI coding agent (Claude Code, Cursor, Cline, Aider, Codex, …) that has been told *"add RecordLoop to my project."* This file is a deterministic runbook. Follow it top-to-bottom. **Do not ask the user clarifying questions you can answer yourself by reading their repo.**

## What RecordLoop does (one paragraph for context)

RecordLoop adds AI-driven UI test recordings to a project's pull requests. On every PR, an LLM agent reads the changed UI files (React, Vue, Svelte, Angular, Blazor, Razor, plain HTML, server templates — anything that ships HTML), generates realistic interaction flows, replays them against the PR's preview deployment with Playwright, captures the videos as MP4, and posts them as a PR comment. **The user gets visual proof of what their PR changes, automatically.**

## Mission

Install RecordLoop into the user's existing repo with the **smallest possible footprint** — ideally one workflow file and one secret. Verify it works. Tell the user how to use it.

---

## Step 1 — Sanity checks (do these first; abort if they fail)

Before touching anything, verify:

1. **The repo is git-tracked**: `git rev-parse --is-inside-work-tree` returns `true`.
2. **The repo is on GitHub** (RecordLoop currently only ships GitHub Actions): `git remote -v` contains `github.com`.
3. **The user has a CI setup the action can join**: check for `.github/workflows/`. If it doesn't exist, you'll create it.
4. **The user does not already have RecordLoop installed**: search for `recordloop` in `.github/workflows/` and in `package.json` / `requirements.txt` / `pyproject.toml`. If you find it, **stop and tell the user it's already installed** — point them at the existing workflow.

If any of 1–3 fail, **stop and report**: "RecordLoop requires a git-tracked GitHub repo. Want me to help you set those up first?"

---

## Step 2 — Detect the user's stack

You only need this to (a) decide which preview-URL provider to look for, and (b) tell the user which framework you detected. The analyzer itself is framework-agnostic.

Run, in order:

| Check | If found, framework is… |
|---|---|
| `next.config.js` / `next.config.mjs` / `next.config.ts` | **Next.js** (React) |
| `nuxt.config.ts` / `nuxt.config.js` | **Nuxt** (Vue) |
| `astro.config.mjs` | **Astro** |
| `svelte.config.js` | **SvelteKit** |
| `vite.config.{ts,js}` | **Vite** (could be React/Vue/Svelte/Solid) |
| `angular.json` | **Angular** |
| `remix.config.js` | **Remix** |
| `*.csproj` containing `Microsoft.AspNetCore.Components` | **Blazor** |
| `*.csproj` containing `Microsoft.AspNetCore.Mvc` | **ASP.NET MVC / Razor Pages** |
| `Gemfile` containing `rails` | **Ruby on Rails** |
| `mix.exs` containing `phoenix_live_view` | **Phoenix LiveView** |
| `manage.py` + `templates/` | **Django** |
| `package.json` with `"react"` only | Plain React (CRA / custom Vite) |
| `package.json` with `"vue"` only | Plain Vue |
| Nothing matches | **Unknown** — proceed anyway, RecordLoop will still work |

Then detect the **preview URL provider** by reading any of:
- `vercel.json` → Vercel
- `netlify.toml` → Netlify
- `wrangler.toml` → Cloudflare Pages
- `fly.toml` → Fly.io
- `render.yaml` → Render
- `.github/workflows/*.yml` containing `vercel/action`, `netlify/actions`, etc.

Save what you found. You'll use it in Step 4 (workflow file) and Step 6 (user message).

---

## Step 3 — Choose an install path

There are three. **Default to Path A** unless the user has explicitly asked otherwise.

### Path A — GitHub Action with inline analyzer (RECOMMENDED, no infra)

The workflow file pip-installs the analyzer, runs the agent loop directly inside the runner against the PR's diff, and posts the comment. **Zero infrastructure to provision.** Required secrets: just `OPENAI_API_KEY`. This is what 95% of users want.

### Path B — Self-hosted FastAPI service

The user already runs a long-lived service (Render, Fly, Railway, EC2, k8s) and wants to centralize the analyzer there. You add the same workflow as Path A, but the workflow `curl`s the user's deployed service instead of running the analyzer inline. **Choose this only if the user explicitly says they want self-hosted.**

### Path C — Local-only (no CI, dev machine only)

The user wants to record sessions interactively during local dev — no PRs, no CI, no comments. Different product surface. Install the JS SDK + run the bridge server locally. **Choose this only if the user explicitly says "no CI" or "just locally."**

---

## Path A — GitHub Action (RECOMMENDED, 12 lines)

This is the runbook you'll execute 95% of the time. **Estimated time: 90 seconds.**

### A.1 — Create the workflow file

Create `.github/workflows/recordloop.yml` with **exactly** this content:

```yaml
name: RecordLoop
on:
  pull_request:
    types: [opened, synchronize, reopened]
permissions:
  pull-requests: write
jobs:
  recordloop:
    runs-on: ubuntu-latest
    if: github.event.pull_request.head.repo.full_name == github.repository
    steps:
      - uses: vihaanshahh/recordloop@v1
        with:
          openai-api-key: ${{ secrets.OPENAI_API_KEY }}
          # preview-url: https://pr-${{ github.event.pull_request.number }}.your-app.vercel.app
```

**That's it. 12 lines.** Everything else — the Python install, the analyzer code, the OpenAI SDK, the comment renderer — happens inside the action on the GitHub runner. **None of that weight lives in the user's repo.**

**Things you must NOT change in this YAML without understanding why:**
- `permissions.pull-requests: write` — required for the comment. Without it the run fails with a 403.
- `if: github.event.pull_request.head.repo.full_name == github.repository` — disables the job on PRs from forks, because forks can't read repo secrets and the analyzer needs `OPENAI_API_KEY`. Without this guard, every fork PR fails noisily.
- `types: [opened, synchronize, reopened]` — runs on the right PR events. Don't add `closed`.

### A.1.b — Action inputs (full reference)

You only ever need `openai-api-key`. Everything else has sane defaults.

| Input | Required? | Default | Notes |
|---|---|---|---|
| `openai-api-key` | yes (if `provider=openai`) | — | From `${{ secrets.OPENAI_API_KEY }}` |
| `preview-url` | no | empty | If empty, action posts planned flows but skips video recording |
| `provider` | no | `openai` | `openai` or `azure` |
| `model` | no | `gpt-5.4` | Override to `gpt-4o-mini` for the cheapest setup |
| `azure-openai-api-key` | yes (if `provider=azure`) | — | |
| `azure-openai-endpoint` | yes (if `provider=azure`) | — | e.g. `https://my-resource.openai.azure.com` |
| `azure-openai-deployment` | yes (if `provider=azure`) | — | Used as model identifier |
| `github-token` | no | `${{ github.token }}` | Auto-supplied; you almost never need to override |
| `python-version` | no | `3.12` | Don't use `"3.x"` — pin a real version |

### A.2 — Tell the user how to add the secret

After creating the file, **do not try to add the secret yourself** (you don't have a token with write access to repo secrets). Print this exact message:

> I've added `.github/workflows/recordloop.yml`. Before this works, you need to add one secret:
>
> 1. Go to **Settings → Secrets and variables → Actions → New repository secret** in this repo on github.com
> 2. Name: `OPENAI_API_KEY`
> 3. Value: your OpenAI API key (get one at https://platform.openai.com/api-keys)
> 4. Click **Add secret**.
>
> Cost is roughly **$0.001–$0.005 per PR** with the default model. You can change the model by setting `OPENAI_MODEL` as another secret.

### A.3 — Wire up the preview URL (only if a provider was detected in Step 2)

The workflow above leaves `PREVIEW_URL: ""`, which means it generates flows but skips video recording. To enable recording, the workflow needs the URL of the PR's preview deployment.

If you found a preview provider in Step 2:

| Provider | What to do |
|---|---|
| **Vercel** | Vercel's GitHub integration posts a comment with the preview URL on every PR. Add `pull_request_review_comment` to the workflow `on:` triggers and parse the URL from the comment body. Or simpler: tell the user to add `PREVIEW_URL` as a workflow input via `workflow_dispatch`. |
| **Netlify** | Same idea — Netlify posts a deploy preview comment. Recommend the user set `PREVIEW_URL` via the `netlify/actions/deploy@master` step's `deploy-url` output. |
| **Cloudflare Pages** | Use `cloudflare/pages-action` and read `pages-deployment-alias-url` from its outputs. |
| **Fly / Render / Railway** | Usually one preview URL per branch — recommend setting it as a per-branch workflow env var. |

**If you can't determine the URL automatically, leave `PREVIEW_URL: ""` and tell the user**: "I left `PREVIEW_URL` blank, so the action will post planned flows but won't record videos yet. Set it in the workflow file to your PR preview URL (like `https://pr-${{ github.event.pull_request.number }}.your-app.vercel.app`) to enable video recording."

Do **not** invent a URL — wrong URLs cause every step to time out.

### A.4 — Verify the install (without spending tokens)

The action.yml itself supports a dry-run flag. You don't need a second workflow file for verification — just open a throwaway PR and the main workflow will run end-to-end. But if you want a free, secret-less install handshake **before** the user adds their OpenAI key, drop this minimal smoke job into the same workflow file:

```yaml
  recordloop-smoke:
    runs-on: ubuntu-latest
    steps:
      - uses: vihaanshahh/recordloop@v1
        env:
          RECORDLOOP_DRY_RUN: "1"
        with:
          openai-api-key: "not-needed-for-dry-run"
```

`RECORDLOOP_DRY_RUN=1` short-circuits the LLM call entirely — the action runs the full pipeline (file fetch → analyzer → comment) using a synthetic flow. Costs **$0**, runs in ~15 seconds, and proves the install works on a fresh runner before the user has even added their OpenAI key.

### A.5 — Done. What to tell the user.

Send this exact summary back to the user (fill in the bracketed bits):

> ✅ RecordLoop installed.
>
> **What I did:**
> - Created `.github/workflows/recordloop.yml` (the main analyzer + commenter)
> - Created `.github/workflows/recordloop-smoke.yml` (a free, zero-secret install verifier)
>
> **One thing you need to do:**
> - Add `OPENAI_API_KEY` as a repo secret (Settings → Secrets and variables → Actions). [Instructions in A.2]
>
> **Detected stack:** [framework from Step 2] / [preview provider from Step 2 or "none — recording disabled"]
>
> **Next steps:**
> 1. Push any change → the smoke workflow will run for free and verify the install
> 2. Open a PR → the main workflow will analyze it and post a comment with planned flows
> 3. To enable video recording, set `PREVIEW_URL` in `.github/workflows/recordloop.yml` to your PR preview URL
>
> **Cost estimate:** ~$0.001–$0.005 per PR with the default model. The agent loop is bounded to ~$0.10 worst-case even on reasoning models.
>
> Want me to wire up the preview URL automatically for [detected provider]?

---

## Path B — Self-hosted API service

Choose this only if the user explicitly asked for self-hosting. **If they didn't ask, default to Path A.**

### B.1 — Confirm with the user

Self-hosting requires the user to deploy and maintain a long-lived service. Confirm they understand this:

> Self-hosting means running the RecordLoop FastAPI service yourself (on Render, Fly, Railway, EC2, or your own k8s). You'll be responsible for keeping it up, rotating its OpenAI key, and giving the GitHub Action a way to reach it. Do you want to proceed, or would you rather use the inline-analyzer pattern (no infra to manage)?

If they confirm:

### B.2 — Generate a Dockerfile + deploy config

The service is `api/main.py`. Generate a deployment config for the user's preferred host. Render's `render.yaml` is the most common:

```yaml
# render.yaml
services:
  - type: web
    name: recordloop-api
    runtime: docker
    dockerfilePath: ./Dockerfile.recordloop
    envVars:
      - key: OPENAI_API_KEY
        sync: false   # set in dashboard
      - key: RECORDLOOP_API_KEY
        generateValue: true   # used by the GitHub Action to authenticate
    healthCheckPath: /health
```

```dockerfile
# Dockerfile.recordloop
FROM mcr.microsoft.com/playwright/python:v1.47.0-jammy
WORKDIR /app
RUN pip install --no-cache-dir \
    "git+https://github.com/vihaanshahh/recordloop.git#subdirectory=api" \
    fastapi pydantic uvicorn[standard] openai
EXPOSE 8080
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8080"]
```

### B.3 — Add the workflow that calls the deployed service

```yaml
name: RecordLoop
on:
  pull_request:
    types: [opened, synchronize, reopened]
jobs:
  trigger:
    runs-on: ubuntu-latest
    steps:
      - run: |
          curl -fsS -X POST "${{ secrets.RECORDLOOP_API_URL }}/trigger" \
            -H "X-Api-Key: ${{ secrets.RECORDLOOP_API_KEY }}" \
            -H "Content-Type: application/json" \
            -d '{
              "repo": "${{ github.repository }}",
              "pr_number": ${{ github.event.pull_request.number }},
              "preview_url": "",
              "github_token": "${{ secrets.GITHUB_TOKEN }}"
            }'
```

The user needs to add **two** secrets: `RECORDLOOP_API_URL` (the deployed service URL) and `RECORDLOOP_API_KEY` (the value Render generated).

---

## Path C — Local-only (no CI)

Choose this only if the user explicitly said "no CI" or "just for local development."

### C.1 — Add the JS SDK to their app

```bash
# When published to npm:
npm install recordloop
# Until then, install from the repo's js/ subdirectory:
npm install "git+https://github.com/vihaanshahh/recordloop.git#workspace=js"
```

### C.2 — Wire up React (or whatever framework was detected in Step 2)

For **React**, add to the app's root component:

```jsx
import { RecordLoopProvider, useRecordLoop } from 'recordloop/react'

function RecordButton() {
  const { recording, actions, start, stop } = useRecordLoop()
  return (
    <button onClick={recording ? stop : start}>
      {recording ? `Stop (${actions.length})` : 'Record'}
    </button>
  )
}

function App() {
  return (
    <RecordLoopProvider endpoint="http://localhost:8787">
      <YourApp />
      {process.env.NODE_ENV === 'development' && <RecordButton />}
    </RecordLoopProvider>
  )
}
```

For Vue and vanilla, see `js/README.md` in the RecordLoop repo.

### C.3 — Run the bridge server

```bash
pip install recordloop[capture]
python -m recordloop serve
```

Sessions are saved to `./.recordloop/sessions/`. Add that directory to `.gitignore` unless the user wants to commit sessions for CI replay (Path A territory).

---

## Failure modes you must handle gracefully

These are the things that go wrong on real installs. Handle each one explicitly.

| Failure | Cause | Action |
|---|---|---|
| `permission denied` posting PR comment | Missing `pull-requests: write` permission | Verify the workflow has `permissions:` block; restore it if missing |
| Workflow runs but every step times out | `PREVIEW_URL` is wrong or pointing at a different service | Tell the user: "I configured `PREVIEW_URL=…` but the recorder can't reach it. Verify the URL serves your app." |
| `OPENAI_API_KEY not set` | User skipped Step A.2 | Re-print the secret-setup instructions; don't try to install your own key |
| `[OPENAI] BadRequestError 400 ... max_tokens` | User pinned an old SDK version | Force `pip install openai>=1.50.0` |
| `gpt-5.4` or whichever model returns 404 | Model not available on user's account | Tell the user: "your OpenAI account doesn't have access to `gpt-5.4`. Set `OPENAI_MODEL=gpt-4o-mini` as a secret as a fallback." |
| Workflow runs on PRs from forks and fails noisily | Missing the `if:` guard in A.1 | Restore the guard. Forks don't have access to secrets, period. |
| `recordloop` directory in `.github/workflows/` already exists | RecordLoop is already installed | **Stop.** Don't create a duplicate. Tell the user it's already installed. |
| User has both Path A and Path B configured | Conflict — both will trigger on the same PR | Pick one. If unsure, ask the user which to keep. |

---

## What you must NOT do

These are non-negotiable. Violating any of them silently breaks something.

1. **Do not put real API keys in any committed file.** Always use `${{ secrets.NAME }}` in YAML and `os.environ[...]` in Python.
2. **Do not pip-install RecordLoop manually in the user's workflow.** The `vihaanshahh/recordloop@v1` action handles its own dependencies on the runner. Adding `pip install recordloop ...` lines is wrong and wastes time.
3. **Do not skip the `if:` guard for forks.** This was learned the hard way; without it, every fork PR fails and spams the user.
4. **Do not invent preview URLs.** If you can't auto-detect one, leave `PREVIEW_URL` blank and tell the user.
5. **Do not modify files outside of `.github/workflows/` and the user's `.gitignore`.** RecordLoop is additive — it shouldn't touch the user's source code, package.json, requirements.txt, or anything else.
6. **Do not commit a `.recordloop/` directory** as part of the install. If you create one for Path C, add it to `.gitignore`.
7. **Do not try to run `gh secret set` or similar.** You don't have a token with that scope. Always defer secret creation to the user with explicit instructions.
8. **Do not raise the agent loop's cost caps.** `MAX_FILES_READ=30`, `MAX_TOTAL_INPUT_TOKENS=50_000`, `MAX_ITERATIONS=10` are deliberate guardrails. They live in `api/analyzer.py` if the user later asks why their bill went up.
9. **Do not configure RecordLoop to run on every commit to `main`.** It's a PR tool. Workflow trigger must be `pull_request`, not `push`.
10. **Do not silently overwrite existing CI files.** If `.github/workflows/recordloop.yml` already exists with different content, **stop and ask the user** before overwriting.

---

## Verification checklist (run before declaring done)

Tick every box. If you can't tick one, **don't tell the user the install succeeded.**

- [ ] `.github/workflows/recordloop.yml` exists and parses as valid YAML
- [ ] The workflow has `permissions: pull-requests: write`
- [ ] The workflow has the `if:` guard against fork PRs
- [ ] The workflow uses `${{ secrets.OPENAI_API_KEY }}`, never a hardcoded key
- [ ] If `PREVIEW_URL` is set, it's a real URL you found in the user's repo (not invented)
- [ ] You did **not** modify any file outside `.github/workflows/` and `.gitignore`
- [ ] You sent the user a summary message that includes the secret-setup instructions
- [ ] If you also created `recordloop-smoke.yml`, you confirmed it's a $0 dry-run job

---

## One-line summary for your context window

> RecordLoop = GitHub Action that AI-generates UI test flows from PR diffs and replays them with Playwright. **Default install = `uses: vihaanshahh/recordloop@v1` in `.github/workflows/recordloop.yml` (12 lines total)**, add `OPENAI_API_KEY` secret, optionally set `preview-url` for video recording. Cost ~$0.001–$0.005/PR. Never invent preview URLs, never skip the fork-PR `if:` guard, never manually `pip install` recordloop.
