# RecordLoop

Drop a JS SDK into your frontend app. It captures every click, keystroke, and navigation from inside the browser. The Python bridge replays them with Playwright, generates test code, and records video.

**Works with React, Vue, Next.js, Angular, Svelte, Astro, or any web app.** No framework lock-in — the core is vanilla JS.

## How it works

```
Your App (React/Vue/etc.)     Bridge Server       Playwright
┌─────────────────────┐     ┌──────────────┐     ┌──────────────┐
│  JS SDK captures     │────>│  Receives    │────>│  Replays     │
│  clicks, typing,     │POST │  sessions,   │     │  actions,    │
│  navigation          │     │  converts to │     │  records     │
│                      │     │  actions     │     │  video       │
└─────────────────────┘     └──────────────┘     └──────────────┘
                                   │
                              Generates test code
```

## Quick start

### 1. Install

```bash
# Python side
pip install playwright pytest watchdog
playwright install chromium

# In your project
python -m recordloop init    # detects framework, writes .env
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

**Vanilla JS / any framework:**

```html
<script src="recordloop/dist/recordloop.js"></script>
<script>
  const rl = new RecordLoop({ endpoint: 'http://localhost:8787' })
  rl.start()
  // ... user interacts with your app ...
  const session = rl.stop()  // captures everything
</script>
```

Or as an ES module:

```js
import { RecordLoop } from 'recordloop'

const rl = new RecordLoop({ endpoint: 'http://localhost:8787' })
rl.start()
```

### 3. Start the bridge server

```bash
python -m recordloop serve
```

This receives sessions from the JS SDK and:
- Converts browser events to Playwright actions
- Generates runnable test code (`generated-tests/test_session_*.py`)
- Saves the recording as JSON

### 4. (Optional) Replay with video

```python
from recordloop import replay_session
import json

session = json.load(open("generated-tests/session_abc123_raw.json"))
result = replay_session(session)
# result = { "video": "test-videos/...", "test": "generated-tests/...", "recording": "..." }
```

### 5. View everything

```bash
python -m recordloop report
# opens recordloop-report.html — action timelines, code preview, video playback
```

## JS SDK API

### `RecordLoop` (core)

```js
const rl = new RecordLoop({
  endpoint: 'http://localhost:8787',  // where to POST (null = collect only)
  clicks: true,          // capture clicks
  input: true,           // capture typing
  navigation: true,      // capture SPA navigation (pushState)
  scroll: true,          // capture scroll position
  ignore: ['[data-recordloop-ignore]'],  // selectors to skip
  debounceMs: 150,       // debounce scroll/resize
  meta: {},              // custom metadata
})

rl.start()               // begin recording
const session = rl.stop() // stop and get session object
rl.getSession()          // get session without stopping
rl.length                // number of recorded actions
```

### Selector strategy

The JS SDK generates stable selectors by priority:
1. `data-testid` / `data-test-id` attributes (best)
2. `id` attributes
3. `name` attributes (forms)
4. `aria-label` attributes
5. `:has-text()` for buttons/links
6. CSS path (fallback)

### React hooks

```jsx
// With provider
import { RecordLoopProvider, useRecordLoop } from 'recordloop/react'

// Standalone (no provider needed)
import { useRecorder } from 'recordloop/react'
const { recording, actions, start, stop } = useRecorder({ endpoint: '...' })
```

### Vue composables

```js
// With plugin
import { RecordLoopPlugin, useRecordLoop } from 'recordloop/vue'

// Standalone
import { useRecorder } from 'recordloop/vue'
const { recording, actions, start, stop } = useRecorder({ endpoint: '...' })
```

## CLI

```bash
python -m recordloop init              # Detect framework, generate .env
python -m recordloop serve             # Start bridge (receives JS SDK sessions)
python -m recordloop serve --port 9000 # Custom port
python -m recordloop report            # Generate HTML report
python -m recordloop config            # Print current config
```

## Environment variables

All Python-side config reads from `RECORDLOOP_*` env vars or `.env`:

| Variable | Default | Description |
|---|---|---|
| `RECORDLOOP_FRAMEWORK` | (auto-detected) | react, vue, next, vite, angular, svelte, etc. |
| `RECORDLOOP_BASE_URL` | `http://localhost:3000` | Your dev server URL |
| `RECORDLOOP_PORT` | `3000` | Dev server port |
| `RECORDLOOP_VIDEO_DIR` | `test-videos` | Video output directory |
| `RECORDLOOP_TEST_OUTPUT_DIR` | `generated-tests` | Generated test output |
| `RECORDLOOP_HEADLESS` | `true` | Headless browser |
| `RECORDLOOP_SLOW_MO` | `0` | Slow down replay (ms) |

## Framework detection

RecordLoop reads `package.json` and sets the right port:

| Framework | Port | Detection |
|---|---|---|
| React (CRA) | 3000 | `react` in deps |
| React + Vite | 5173 | `react` + `vite` |
| Next.js | 3000 | `next` |
| Vue + Vite | 5173 | `vue` + `vite` |
| Angular | 4200 | `@angular/core` |
| Svelte | 5173 | `svelte` |
| Gatsby | 8000 | `gatsby` |
| Remix | 3000 | `@remix-run/react` |
| Astro | 4321 | `astro` |

## Python API (direct Playwright recording)

You can also skip the JS SDK and record directly with Playwright:

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

Decorators:

```python
from recordloop import recordable, video_capture

@recordable()
def test_login(page):
    page.goto("http://localhost:3000/login")
    page.fill("#username", "user@example.com")
    page.click("#login")
```
