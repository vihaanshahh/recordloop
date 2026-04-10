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

from .models import TriggerRequest, TriggerResponse, JobStatus, LLMConfig
from .analyzer import analyze_pr
from .github_client import get_pr_files, post_pr_comment
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
            print(f"[{job_id}] WARNING: storage state error (login may fail): {e}")
            import traceback; traceback.print_exc()

        # 2. AI analysis → interaction flows
        provider = (llm.provider or "openai").lower()
        azure = llm.azure
        api_key = llm.api_key if provider == "openai" else (azure.api_key if azure else None)

        flows = await asyncio.to_thread(
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
            # ignore_paths already applied to changed_files above (step 1b)
        )
        job["flows_generated"] = len(flows)
        print(f"[{job_id}] {provider} generated {len(flows)} flow(s)")

        if not flows:
            job["status"] = "done"
            job["note"] = "No recordable UI component changes detected in this PR"
            await _post_comment(repo, pr_number, github_token, job)
            return

        # 3. Record with Playwright (skip if no preview URL)
        if preview_url:
            job["status"] = "recording"
            results = await asyncio.to_thread(
                record_flows, flows, preview_url, storage_state=storage_state,
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

        if status == "planned":
            lines += [f"**{name}** _(planned — no preview URL)_", f"> {desc}", ""]
        elif r.get("video_url"):
            lines += [f"**{name}**", f"> {desc}", f"[▶ Watch recording]({r['video_url']})", ""]
        elif r.get("video"):
            lines += [f"**{name}**", f"> {desc}", f"Video: `{r['video']}`", ""]
        elif status == "failed":
            lines += [f"**{name}** _(recording failed: {r.get('error', '')})_", ""]
        else:
            lines += [f"**{name}**", f"> {desc}", ""]

    if not recordings and not error and not note:
        lines.append("No recordings generated.")

    comment = "\n".join(lines)
    await post_pr_comment(repo, pr_number, comment, token)


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
