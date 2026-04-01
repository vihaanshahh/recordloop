# RecordLoop — Video for Code Review

## Context

Code review is text-first. PRs describe *what* changed, but not *how it behaves*. A 30-second screen recording attached to a PR eliminates back-and-forth "does this work?" messages, accelerates review cycles, and creates a permanent visual artifact of bugs or behavior.

Existing solutions:
- **Loom** — Great product, expensive, not developer-centric, not PR-native
- **Vidtreo** — Cheap per-minute, good SDK, but general-purpose (interviews, support)
- **Cap** — Open source, self-hosted, desktop-centric
- **Claude Code / Cursor** — Local recording, no cloud sync or sharing

**No product targets developers who want to record browser/app behavior and attach it directly to their PR workflow.**

---

## Vision

> Record once. Attach anywhere. Review faster.

RecordLoop is a developer-first screen recording API that:
1. Records browser/app behavior via SDK (JS or Python)
2. Auto-uploads to cloud storage (S3/R2)
3. Returns a shareable URL you paste into a PR comment
4. Viewer can watch without installing anything or leaving GitHub

It feels like screenshot diff tools (e.g. Percy, Chromatic) but for video — integrated, not bolted on.

---

## Product Principles

1. **4-line integration** — Developer adds SDK, gets recording. No infrastructure to manage.
2. **MP4 only** — WebM is a support headache. Always output MP4 (H.264 + AAC).
3. **CI/CD native** — Works in local dev, CI pipelines, and GitHub Actions.
4. **Privacy-first** — Videos are short-lived by default (7-day expiry), deletable, no tracking.
5. **No accounts for viewers** — Anyone with the link can watch. No login required.

---

## Target Users

### Primary: Developers (B2D)
- Frontend engineers submitting PRs who want to show *behavior*, not just code
- QA engineers creating visual regression records
- Support engineers documenting bugs with reproduction steps

### Secondary: Teams
- Code review teams who are tired of "can you reproduce?"
- Design handoff teams (record "what this flow looks like")
- Sales demos (record feature walkthroughs)

---

## What We're Building

### Core Product: RecordLoop SDK

Two SDKs — JavaScript (web browsers) and Python (Playwright, CI/CD).

#### JS SDK (`@recordloop/js`)

```tsx
import { RecordLoop } from '@recordloop/js';

const recorder = new RecordLoop({ apiKey: 'rlk_xxxx' });

// Option A: Component (React)
function BugReport() {
  return (
    <RecordLoop.Button
      onComplete={(url) => addToPR(url)}
      label="Record Bug"
    />
  );
}

// Option B: Hook (vanilla JS)
const { start, stop, isRecording } = useRecorder({
  apiKey: 'rlk_xxxx',
  onComplete: (url) => copyToClipboard(url),
});

button.onclick = () => isRecording ? stop() : start();
```

#### Python SDK (`recordloop-python`)

```python
from recordloop import RecordLoop

client = RecordLoop(api_key="rlk_xxxx")

# Record via Playwright (already built in playwright-recorder)
with client.record() as rec:
    page = rec.start("https://app.example.com")
    page.click("#checkout-button")
    rec.stop()
    url = rec.upload()  # Returns shareable URL

# Or attach to existing Playwright tests
@recordloop(api_key="rlk_xxxx")
def test_checkout(page):
    page.goto("https://app.example.com/cart")
    page.click("#checkout")
    # video auto-uploaded on completion
```

### Backend API

```
POST /v1/recordings          # Upload video, returns { id, url, expires_at }
GET  /v1/recordings/:id     # Get metadata
DELETE /v1/recordings/:id    # Delete recording
GET  /v1/recordings/:id/video # Stream video
```

**Storage**: S3 or Cloudflare R2 (cheaper egress)
**CDN**: Cloudflare or Fastly for video streaming
**Auth**: API key per workspace, domain-restricted keys for SDKs

### Viewer Experience

- **No-login watch page**: `recordloop.io/w/:id` — minimal player, download button
- **GitHub App integration**: Post recording URL as PR comment automatically
- **Embed support**: `<iframe src="https://embed.recordloop.io/:id">` for embedding in Notion, Linear, etc.

---

## Feature Roadmap

### Phase 1: MVP (4-6 weeks)
- [ ] JS SDK (React component + vanilla hook)
- [ ] Python SDK (extend playwright-recorder)
- [ ] Video upload API (S3 + presigned URLs)
- [ ] Watch page (no-login, MP4 stream)
- [ ] Dashboard (list/delete recordings)
- [ ] API key auth

### Phase 2: CI/CD + GitHub (2-3 weeks)
- [ ] GitHub Action (`recordloop/action`)
- [ ] GitHub App (auto-post to PR)
- [ ] CLI (`npx recordloop record`)
- [ ] Webhook support (notify external services on upload)

### Phase 3: Polish + Growth (2-3 weeks)
- [ ] Custom branding (own domain for videos)
- [ ] Analytics (view count, watch duration)
- [ ] Video trimming (crop start/end)
- [ ] Team seats + roles
- [ ] Usage billing (per-minute)

---

## Pricing Model

| Tier | Price | Includes |
|------|-------|----------|
| **Free** | $0 | 30 mins/month, 7-day retention, 100 recordings |
| **Pro** | $20/mo | 5 hrs/month, 30-day retention, unlimited recordings |
| **Team** | $50/mo | 15 hrs/month, 90-day retention, team management |
| **Enterprise** | Custom | Unlimited, SSO, SLA, custom retention |

**Competitors**: Loom starts at $15/user/month. We're per-minute, not per-seat.

---

## Technical Architecture

```
┌─────────────────────────────────────────────────────────┐
│                        Client                            │
│  ┌──────────────┐         ┌────────────────────────┐   │
│  │  JS SDK      │         │  Python SDK             │   │
│  │  (browser)   │         │  (Playwright/CI)        │   │
│  └──────┬───────┘         └───────────┬────────────┘   │
│         │                               │                │
│         │   POST /v1/recordings/presign  │                │
│         │   (get presigned S3 URL)      │                │
│         └───────────────┬───────────────┘                │
│                         ▼                                 │
│              ┌──────────────────────┐                    │
│              │    RecordLoop API    │                    │
│              │    (Node.js/Bun)     │                    │
│              └──────────┬───────────┘                    │
│                         │                                 │
│         ┌───────────────┼───────────────────┐          │
│         ▼               ▼                   ▼           │
│  ┌────────────┐  ┌─────────────┐  ┌─────────────────┐   │
│  │  S3 / R2   │  │  PostgreSQL │  │  Cloudflare CDN │   │
│  │  (videos)  │  │  (metadata) │  │  (streaming)    │   │
│  └────────────┘  └─────────────┘  └─────────────────┘   │
└─────────────────────────────────────────────────────────┘

Video flow:
1. Client starts recording (WebCodecs API in browser, Playwright on backend)
2. Client stops → chunks sent to S3 via presigned URL (direct upload, not through API server)
3. Client calls POST /v1/recordings to register the upload
4. API returns shareable URL (recordloop.io/w/:id)
5. Viewer requests → CDN streams from S3
```

### Tech Stack
- **API**: Bun or Node.js + Hono (fast, lightweight)
- **Database**: PostgreSQL (recordings metadata) + Redis (rate limiting, sessions)
- **Storage**: AWS S3 or Cloudflare R2
- **CDN**: Cloudflare Stream or Fastly
- **Auth**: API keys (simple) + optional OAuth for dashboard

---

## Differentiation

| | RecordLoop | Loom | Vidtreo | Cap |
|--|--|--|--|--|
| **Target** | Developers / PRs | All teams | Developers | Self-hosted teams |
| **Integration** | 4-line SDK | 20-line SDK | 4-line SDK | Self-hosted |
| **CI/CD** | Native | ❌ | ❌ | ❌ |
| **GitHub App** | ✅ | ❌ | ❌ | ❌ |
| **Pricing** | Per-minute | Per-user | Per-minute | Free (self-hosted) |
| **MP4 only** | ✅ | ❌ | ❌ | ❌ |
| **Expiry/Privacy** | ✅ | ❌ | ❌ | ✅ |

---

## Open Questions

1. **Recording size**: Browser recordings via WebCodecs are efficient (~10MB/min). Playwright recordings are larger (~50MB/min). Do we transcode server-side or client-side?
2. **Concurrent recordings**: How many simultaneous recordings per API key?
3. **Retention**: Auto-delete after expiry or warn-and-keep?
4. **Theming**: Dark/light player? Custom player colors for enterprise?
5. **Analytics**: What metrics matter? Views, watch time, completion rate?

---

## Next Steps

1. **Prototype JS SDK** — React component that uses `mediaDevices.getDisplayMedia()` and uploads chunks to S3
2. **Extend Python SDK** — Add `upload_to_cloud()` to playwright-recorder's `stop_recording()`
3. **Build minimal API** — `POST /v1/recordings/presign` + `POST /v1/recordings` + `GET /v1/recordings/:id`
4. **Deploy on Zo** — Use Zo's hosting to run the API and stream videos

---

## Appendix: Code Locations

All current `playwright-recorder` code lives in:
```
/home/workspace/playwright-recorder/
├── __init__.py
├── recorder.py        # Core: PlaywrightRecorder, RecorderConfig, RecordedAction
├── decorators.py      # @recordable, @video_capture, @watch_changes
├── test_runner.py     # TestRunner, VideoRecorder, TestResult
├── example.py
├── requirements.txt
└── test-videos/
```

**Changes needed for RecordLoop**:
- `recorder.py` → Add `upload_to_cloud()` method to `PlaywrightRecorder`
- `decorators.py` → Add `@recordloop` decorator that auto-uploads
- New: `sdk/python/recordloop.py` — Standalone SDK package
- New: `sdk/js/` — React component + vanilla hook
