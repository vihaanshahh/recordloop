"""
RecordLoop Cloud API  —  api.recordloop.dev

POST /trigger   → analyze PR + record → post comment
GET  /jobs/{id} → check job status
GET  /health    → liveness check
"""

import asyncio
import os
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, BackgroundTasks, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware

from .models import TriggerRequest, TriggerResponse, JobStatus, LLMConfig, RecordingConfig
from .analyzer import analyze_pr
from .github_client import get_pr_files, upsert_pr_comment
from .cloud_recorder import record_flows

app = FastAPI(title="RecordLoop API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory job store (swap for Redis in production)
_jobs: dict[str, dict] = {}


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    return {"ok": True, "service": "api.recordloop.dev"}


@app.post("/trigger", response_model=TriggerResponse)
async def trigger(
    req: TriggerRequest,
    background_tasks: BackgroundTasks,
    x_api_key: Optional[str] = Header(None, alias="X-Api-Key"),
):
    _check_api_key(x_api_key)

    job_id = uuid.uuid4().hex[:8]
    _jobs[job_id] = {
        "job_id": job_id,
        "status": "queued",
        "repo": req.repo,
        "pr_number": req.pr_number,
        "preview_url": req.preview_url or "",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    background_tasks.add_task(
        _run_job,
        job_id=job_id,
        repo=req.repo,
        pr_number=req.pr_number,
        preview_url=req.preview_url or "",
        github_token=req.github_token,
        llm=req.llm,
        recording=req.recording,
        pr_head_sha=req.pr_head_sha or "",
    )

    return TriggerResponse(
        job_id=job_id,
        status="queued",
        message=f"Job {job_id} started for {req.repo}#{req.pr_number}",
    )


@app.get("/jobs/{job_id}", response_model=JobStatus)
def get_job(job_id: str):
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


# ---------------------------------------------------------------------------
# Background job
# ---------------------------------------------------------------------------

async def _run_job(
    job_id: str,
    repo: str,
    pr_number: int,
    preview_url: str,
    github_token: str,
    llm: LLMConfig,
    recording: RecordingConfig,
    pr_head_sha: str = "",
):
    job = _jobs[job_id]

    try:
        # 1. Fetch changed files
        job["status"] = "analyzing"
        changed_files = await get_pr_files(repo, pr_number, github_token)
        job["files_changed"] = len(changed_files)
        print(f"[{job_id}] Fetched {len(changed_files)} changed files")

        # 1b. Fetch repo context + resolve login state
        repo_context_body = ""
        storage_state: dict | None = None
        try:
            from .repo_context import fetch_recordloop_md
            repo_ctx = await fetch_recordloop_md(repo, github_token, ref=pr_head_sha)
            if repo_ctx:
                repo_context_body = repo_ctx.to_system_message() or ""
                changed_files = repo_ctx.apply_ignore_paths(changed_files)
        except Exception as e:
            print(f"[{job_id}] repo context fetch failed: {e}")

        try:
            from .login import resolve_storage_state
            storage_state = resolve_storage_state()
        except Exception as e:
            # User explicitly configured auth — continuing without it would
            # just record a login page.  Fail the job.
            job["status"] = "failed"
            job["error"] = f"storage state error: {e}"
            print(f"[{job_id}] FATAL: storage state error — {e}")
            return

        # 2. AI analysis → interaction flows
        provider = (llm.provider or "openai").lower()
        azure = llm.azure
        anthropic = llm.anthropic
        if provider == "openai":
            api_key = llm.api_key
        elif provider == "azure":
            api_key = azure.api_key if azure else None
        elif provider == "anthropic":
            api_key = anthropic.api_key if anthropic else None
        else:
            api_key = None

        result = await asyncio.to_thread(
            analyze_pr,
            changed_files,
            preview_url,
            provider,
            api_key,
            llm.model,
            azure.endpoint if azure else None,
            azure.deployment if azure else None,
            azure.api_version if azure else None,
            repo_context_body=repo_context_body,
            anthropic_base_url=anthropic.base_url if anthropic else None,
            anthropic_api_version=anthropic.api_version if anthropic else None,
            # ignore_paths already applied to changed_files above (step 1b)
        )
        flows = result.flows
        job["flows_generated"] = len(flows)
        job["cost"] = {
            "provider": result.cost.provider,
            "model": result.cost.model,
            "input_tokens": result.cost.input_tokens,
            "output_tokens": result.cost.output_tokens,
            "usd": result.cost.usd,
        }
        print(
            f"[{job_id}] {provider} generated {len(flows)} flow(s) "
            f"(model={result.cost.model} · ${result.cost.usd:.6f})"
        )

        if not flows:
            job["status"] = "done"
            job["note"] = "No recordable UI component changes detected in this PR"
            await _post_comment(repo, pr_number, github_token, job)
            return

        # 3. Record with Playwright (skip if no preview URL)
        if preview_url:
            job["status"] = "recording"
            results = await asyncio.to_thread(
                record_flows,
                flows,
                preview_url,
                storage_state=storage_state,
                viewports=recording.viewports,
                wait_until=recording.wait_until,
                settle_ms=recording.settle_ms,
            )
            job["recordings"] = results
            print(f"[{job_id}] Recorded {len(results)} flow(s)")
        else:
            # No preview URL — post the planned flows as a dry run comment
            job["recordings"] = [
                {"name": f.name, "description": f.description, "status": "planned"}
                for f in flows
            ]
            job["note"] = "No preview URL provided — showing planned recordings"

        job["status"] = "done"
        await _post_comment(repo, pr_number, github_token, job)

    except Exception as e:
        job["status"] = "failed"
        job["error"] = str(e)
        print(f"[{job_id}] FAILED: {e}")
        # Still try to post a comment so the PR isn't silent
        try:
            await _post_comment(repo, pr_number, github_token, job)
        except Exception:
            pass


async def _post_comment(repo: str, pr_number: int, token: str, job: dict):
    lines = [
        "## RecordLoop",
        "",
        "_Automatically recorded from changed components_",
        "",
    ]

    recordings = job.get("recordings") or []
    note = job.get("note", "")
    error = job.get("error", "")

    if error:
        lines += [f"> ⚠️ {error}", ""]
    elif note:
        lines += [f"> {note}", ""]

    for r in recordings:
        status = r.get("status", "")
        name = r.get("name", "recording")
        desc = r.get("description", "")
        viewport = r.get("viewport_label") or r.get("viewport") or ""
        width = r.get("viewport_width")
        height = r.get("viewport_height")
        viewport_suffix = ""
        if viewport:
            viewport_suffix = f" · {viewport}"
            if width and height:
                viewport_suffix += f" ({width}x{height})"

        if status == "planned":
            lines += [f"**{name}** _(planned — no preview URL)_", f"> {desc}", ""]
        elif r.get("video_url"):
            lines += [
                f"**{name}{viewport_suffix}**",
                f"> {desc}",
                f"[▶ Watch recording]({r['video_url']})",
                "",
            ]
        elif r.get("video"):
            lines += [f"**{name}{viewport_suffix}**", f"> {desc}", f"Video: `{r['video']}`", ""]
        elif status == "failed":
            lines += [f"**{name}{viewport_suffix}** _(recording failed: {r.get('error', '')})_", ""]
        else:
            lines += [f"**{name}{viewport_suffix}**", f"> {desc}", ""]

    if not recordings and not error and not note:
        lines.append("No recordings generated.")

    cost = job.get("cost") or {}
    if cost and (cost.get("input_tokens") or cost.get("output_tokens")):
        in_t = int(cost.get("input_tokens", 0) or 0)
        out_t = int(cost.get("output_tokens", 0) or 0)
        usd = float(cost.get("usd", 0.0) or 0.0)
        usd_str = f"${usd:.4f}" if usd >= 0.01 else f"${usd:.6f}"
        lines += [
            "",
            "---",
            f"_Analyzed by `{cost.get('model', 'unknown')}` — "
            f"{usd_str} ({in_t} in / {out_t} out)_",
        ]

    comment = "\n".join(lines)
    await upsert_pr_comment(repo, pr_number, comment, token)


# ---------------------------------------------------------------------------
# API key validation
# ---------------------------------------------------------------------------

def _check_api_key(key: str):
    """
    Validate the X-Api-Key header.

    In production: check against a database of valid keys.
    Here: check against RECORDLOOP_VALID_KEYS env var (comma-separated)
    or RECORDLOOP_API_KEY for single-key setups.
    """
    valid = set(filter(None, [
        os.environ.get("RECORDLOOP_API_KEY"),
        *os.environ.get("RECORDLOOP_VALID_KEYS", "").split(","),
    ]))

    if valid and key not in valid:
        raise HTTPException(status_code=401, detail="Invalid API key")
    # If no keys configured (dev mode), allow all
