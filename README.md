# RecordLoop

Drop a JS SDK into your frontend. It captures every interaction from inside the browser. Commit the session JSON, and a GitHub Action replays it with Playwright, records video, uploads to S3, and posts the link on your PR.

**Works with React, Vue, Next.js, Angular, Svelte, or any web app.**

## How it works

```
 Local dev                              CI/CD (GitHub Action)

 Your App + JS SDK                      PR opened
      │ captures clicks,                     │
      │ typing, navigation                   ▼
      ▼                                Finds .recordloop/sessions/*.json
 .recordloop/sessions/abc.json              │
      │ committed to repo (~3KB)            ▼
      │                                Runner: replays with Playwright
      │                                     │ records video
      │                                     ▼
      │                                S3: uploads video (pre-signed URL)
      │                                     │
      │                                     ▼
      │                                InstantDB: saves metadata (optional)
      │                                     │
      │                                     ▼
      └──────────────────────────> PR comment: "Watch recording" link

 Cost at 1000 recordings/month: ~$0.58
 (S3 storage + egress. GitHub Actions + InstantDB = free tier.)
```

## Quick start

### 1. Install

```bash
pip install playwright pytest watchdog
playwright install chromium

python -m recordloop init    # detects framework, writes .env, creates .recordloop/
```

### 2. Add the JS SDK to your frontend

**React:**

```jsx
import { RecordLoopProvider, useRecordLoop } from 'recordloop/react'

function App() {
  return (
    <RecordLoopProvider endpoint="http://localhost:8787">
      <YourApp />
      <RecordButton />
    </RecordLoopProvider>
  )
}

function RecordButton() {
  const { recording, actions, start, stop } = useRecordLoop()
  return (
    <button onClick={recording ? stop : start}>
      {recording ? `Stop (${actions.length} actions)` : 'Record'}
    </button>
  )
}
```

**Vue:**

```js
import { RecordLoopPlugin } from 'recordloop/vue'
app.use(RecordLoopPlugin, { endpoint: 'http://localhost:8787' })
```

```vue
<script setup>
import { useRecordLoop } from 'recordloop/vue'
const { recording, actions, start, stop } = useRecordLoop()
</script>
<template>
  <button @click="recording ? stop() : start()">
    {{ recording ? `Stop (${actions.length})` : 'Record' }}
  </button>
</template>
```

**Vanilla JS / Angular / Svelte / anything:**

```html
<script src="recordloop/dist/recordloop.js"></script>
<script>
  const rl = new RecordLoop({ endpoint: 'http://localhost:8787' })
  rl.start()
  // user interacts...
  rl.stop()  // POSTs session to bridge, saves to .recordloop/sessions/
</script>
```

### 3. Start the bridge server (local dev)

```bash
python -m recordloop serve
```

The bridge receives sessions from the JS SDK and:
- Saves to `.recordloop/sessions/` (commit these for CI/CD)
- Generates Playwright test code
- Converts browser events to replayable actions

### 4. Commit sessions, get videos on PR

```bash
git add .recordloop/sessions/
git commit -m "Add recording sessions"
git push
```

The GitHub Action picks up the sessions, replays with Playwright, and posts video links on the PR. See [CI/CD setup](#cicd--github-action) below.

### 5. (Optional) Run locally

```bash
python -m recordloop run           # replay all sessions
python -m recordloop run abc123    # replay specific session
python -m recordloop report        # HTML report with timelines + video
```

## CI/CD — GitHub Action

### Setup

1. Create an S3 bucket:
```bash
# Set your env vars first (see .env.example)
python -m recordloop setup-s3
```

2. Add secrets to your GitHub repo:
   - `RECORDLOOP_S3_BUCKET`
   - `AWS_ACCESS_KEY_ID`
   - `AWS_SECRET_ACCESS_KEY`

3. Add the workflow (`.github/workflows/recordloop.yml`):

```yaml
name: RecordLoop
on:
  pull_request:
    paths: ['.recordloop/sessions/**']

jobs:
  replay:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
      - uses: vihaanshahh/recordloop@main
        with:
          s3-bucket: ${{ secrets.RECORDLOOP_S3_BUCKET }}
          aws-access-key-id: ${{ secrets.AWS_ACCESS_KEY_ID }}
          aws-secret-access-key: ${{ secrets.AWS_SECRET_ACCESS_KEY }}
```

The action will:
- Find all session JSONs in `.recordloop/sessions/`
- Replay each with Playwright, record video
- Upload video to S3 (pre-signed URL, auto-expires in 30 days)
- Post a PR comment with "Watch recording" links

### Optional: InstantDB dashboard

Add these secrets for real-time dashboard sync:
- `RECORDLOOP_INSTANTDB_APP_ID`
- `RECORDLOOP_INSTANTDB_ADMIN_TOKEN`

## JS SDK API

```js
const rl = new RecordLoop({
  endpoint: 'http://localhost:8787',  // bridge server (local dev)
  instantdb: { appId: '...' },       // optional: real-time dashboard sync
  clicks: true,
  input: true,
  navigation: true,     // captures SPA pushState/replaceState
  scroll: true,
  ignore: ['[data-recordloop-ignore]'],
  debounceMs: 150,
  meta: {},              // custom metadata attached to session
})

rl.start()               // begin recording
const session = rl.stop() // stop, POST to bridge, return session
rl.getSession()          // get session without stopping
rl.download()            // download session as JSON file
rl.length                // action count
```

**Selector strategy** (stable selectors for Playwright replay):
1. `data-testid` / `data-test-id` (best)
2. `id`
3. `name` (forms)
4. `aria-label`
5. `:has-text()` (buttons/links)
6. CSS nth-child path (fallback)

### React

```jsx
import { RecordLoopProvider, useRecordLoop } from 'recordloop/react'
// or standalone:
import { useRecorder } from 'recordloop/react'
const { recording, actions, start, stop } = useRecorder({ endpoint: '...' })
```

### Vue

```js
import { RecordLoopPlugin, useRecordLoop } from 'recordloop/vue'
// or standalone:
import { useRecorder } from 'recordloop/vue'
const { recording, actions, start, stop } = useRecorder({ endpoint: '...' })
```

## CLI

```bash
python -m recordloop init              # detect framework, generate .env, create .recordloop/
python -m recordloop serve             # start bridge server (receives JS SDK)
python -m recordloop run [session_id]  # replay sessions → video + test code
python -m recordloop run --output json # JSON output (for CI)
python -m recordloop report            # generate HTML report
python -m recordloop setup-s3          # create + configure S3 bucket
python -m recordloop config            # show resolved config
```

## Environment variables

```bash
# Framework (auto-detected from package.json)
RECORDLOOP_FRAMEWORK=react
RECORDLOOP_BASE_URL=http://localhost:3000
RECORDLOOP_PORT=3000

# Recording
RECORDLOOP_VIDEO_DIR=test-videos
RECORDLOOP_TEST_OUTPUT_DIR=generated-tests
RECORDLOOP_HEADLESS=true
RECORDLOOP_SLOW_MO=0
RECORDLOOP_VIEWPORT_WIDTH=1280
RECORDLOOP_VIEWPORT_HEIGHT=720

# Cloud — S3 (optional, enables CI uploads)
RECORDLOOP_S3_BUCKET=my-recordloop-videos
RECORDLOOP_S3_REGION=us-east-1
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...

# Cloud — InstantDB (optional, enables dashboard)
RECORDLOOP_INSTANTDB_APP_ID=...
RECORDLOOP_INSTANTDB_ADMIN_TOKEN=...
```

## Architecture — why it's cheap

| Component | What it does | Cost |
|---|---|---|
| **S3** | Store videos | ~$0.12/mo (5GB) |
| **S3 pre-signed URLs** | Serve videos (no CDN/server) | ~$0.45/mo egress |
| **InstantDB** | Session metadata + dashboard | Free tier |
| **GitHub Actions** | The runner (replay + upload) | Free tier |
| **Sessions in repo** | Storage + version control | Free (just git) |

No API server. No database server. No CDN. Videos served directly from S3 via pre-signed URLs that auto-expire.

## Python API (direct Playwright, no JS SDK)

```python
from recordloop import PlaywrightRecorder, RecordLoopConfig

config = RecordLoopConfig()
with PlaywrightRecorder(config.to_recorder_config()) as recorder:
    page = recorder.start_recording(config.base_url)
    page.fill("#search", "query")
    page.click("#submit")
    recorder.stop_recording()
    print(recorder.generate_test_code())
```
