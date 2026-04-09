"""
RecordLoop GitHub Action entry point.

Single-file runner the action.yml composite invokes. Reads everything from
env vars (the standard GitHub Actions contract), runs the analyzer agent
loop against the PR diff, optionally records videos with Playwright if a
preview URL is set, and posts a PR comment with the result.

Dependencies: only ``openai`` (analyzer) and Python stdlib (github_client).
Playwright is imported lazily inside cloud_recorder and only loaded when
PREVIEW_URL is non-empty.

Env vars consumed:
    OPENAI_API_KEY        — required (or AZURE_OPENAI_* if PROVIDER=azure)
    GITHUB_TOKEN          — required, must have pull-requests:write + contents:write
    REPO                  — owner/repo, e.g. "vihaanshahh/recordloop"
    PR_NUMBER             — integer
    PREVIEW_URL           — optional; empty = skip Playwright recording
    BASE_URL              — optional; base branch URL for before/after comparison
    PROVIDER              — "openai" (default) or "azure"
    MODEL                 — optional; defaults to gpt-5.4
    GITHUB_OUTPUT         — auto-set by GitHub Actions (output sink path)
"""

from __future__ import annotations

import asyncio
import os
import sys
import traceback


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default) or default


def _emit_output(key: str, value: str) -> None:
    """Append to $GITHUB_OUTPUT so the action exposes a step output."""
    out = os.environ.get("GITHUB_OUTPUT")
    if not out:
        return
    try:
        with open(out, "a", encoding="utf-8") as f:
            f.write(f"{key}={value}\n")
    except Exception:
        pass


def _flow_title(name: str) -> str:
    """Convert snake_case flow name to a readable Title Case heading."""
    return name.replace("_", " ").title()


_STATUS_LABELS = {
    "passed": "✅ Passed",
    "failed": "❌ Failed",
    "demo":   "▶ Demo (no assertions)",
}


def _render_comment(flows, preview_url: str, recordings: list | None) -> str:
    if not flows:
        return (
            "## RecordLoop\n\n"
            "_No recordable UI changes detected in this PR._"
        )

    lines = ["## RecordLoop", ""]

    rec_by_name = {r.get("name"): r for r in (recordings or [])}
    has_video_without_url = False

    for f in flows:
        rec = rec_by_name.get(f.name) or {}
        status = rec.get("status") or ""
        status_label = _STATUS_LABELS.get(status, "")

        # Heading: status badge + human title + source file
        file_label = f" · `{f.component_file}`" if f.component_file else ""
        heading_status = f"{status_label} · " if status_label else ""
        lines.append(f"### {heading_status}{_flow_title(f.name)}{file_label}")

        # Single-sentence context (the "why" for the reviewer)
        if f.change_context:
            lines.append(f.change_context)
        lines.append("")

        after_gif = rec.get("gif_url") or ""
        if after_gif:
            lines.append(f"![{_flow_title(f.name)}]({after_gif})")
        elif rec.get("video_url"):
            lines.append(f"[▶ Watch recording]({rec['video_url']})")
        elif rec.get("video"):
            has_video_without_url = True

        lines.append("")

        # Failure breakdown comes BEFORE the collapsed details so reviewers
        # see what broke without expanding anything.
        for af in rec.get("assertion_failures") or []:
            lines.append(
                f"> ❌ assertion failed — `{af['selector']}` "
                f"{('(' + af['value'] + ')') if af.get('value') else ''} — {af['reason']}"
            )
        if rec.get("assertion_failures"):
            lines.append("")

        if not rec and not preview_url and not _env("RECORDLOOP_DETECTED_URL"):
            lines.append("_📋 planned (no preview URL configured)_")
        elif status in ("passed", "failed", "demo"):
            asserts_passed = rec.get("assertions_passed", 0)
            asserts_total = rec.get("assertions_total", 0)
            assertion_summary = (
                f"{asserts_passed}/{asserts_total} assertions"
                if asserts_total
                else "no assertions"
            )
            lines.append(
                f"<details><summary>Flow trace · {len(f.steps)} steps · {assertion_summary}</summary>"
            )
            lines.append("")
            for s in f.steps:
                extra = f" — `{s.value}`" if s.value else ""
                marker = "🔍" if s.is_assertion else "•"
                lines.append(f"- {marker} **{s.action}** `{s.selector}`{extra}")
            lines.append("")
            lines.append("</details>")

        lines.append("")

    workflow_run_url = _env("WORKFLOW_RUN_URL")
    if has_video_without_url and workflow_run_url:
        lines.append(
            f"📥 [Download recordings]({workflow_run_url}#artifacts) "
            f"· artifact `recordloop-videos-pr-{_env('PR_NUMBER')}` · 14 days"
        )

    return "\n".join(lines)


async def main() -> int:
    repo = _env("REPO")
    pr_number_str = _env("PR_NUMBER")
    github_token = _env("GITHUB_TOKEN")
    # Prefer the explicitly-passed preview-url. If empty, the auto-start step
    # in action.yml may have spun up the user's app inside the runner and
    # exported the localhost URL via $GITHUB_ENV.
    preview_url = _env("PREVIEW_URL") or _env("RECORDLOOP_DETECTED_URL")
    # Base URL for "before" recording — set by the base-branch auto-start step
    # or passed explicitly as the base-url action input.
    base_url = _env("BASE_URL") or _env("RECORDLOOP_BASE_URL")
    provider = _env("PROVIDER", "openai").lower()
    model = _env("MODEL") or None

    if not repo or not pr_number_str or not github_token:
        print("RecordLoop: missing required env (REPO, PR_NUMBER, GITHUB_TOKEN)", file=sys.stderr)
        return 1

    try:
        pr_number = int(pr_number_str)
    except ValueError:
        print(f"RecordLoop: invalid PR_NUMBER {pr_number_str!r}", file=sys.stderr)
        return 1

    print(
        f"RecordLoop → {repo}#{pr_number}  provider={provider}  "
        f"preview={preview_url or '(none)'}  base={base_url or '(none)'}"
    )

    # Lazy imports keep cold-start fast and prove the lazy chain still works.
    from api.analyzer import analyze_pr
    from api.github_client import get_pr_files, upsert_pr_comment, upload_pr_video

    # 1. Fetch the diff
    try:
        changed_files = await get_pr_files(repo, pr_number, github_token)
    except Exception as e:
        print(f"RecordLoop: failed to fetch PR files — {e}", file=sys.stderr)
        traceback.print_exc()
        return 1
    print(f"  fetched {len(changed_files)} changed files")

    # 2. Run the agent loop
    try:
        flows = analyze_pr(
            changed_files=changed_files,
            preview_url=preview_url,
            provider=provider,
            model=model,
        )
    except Exception as e:
        print(f"RecordLoop: analyzer failed — {e}", file=sys.stderr)
        traceback.print_exc()
        body = f"## RecordLoop\n\n⚠️ Analyzer failed: `{e}`"
        try:
            await upsert_pr_comment(repo, pr_number, body, github_token)
        except Exception:
            pass
        return 1

    print(f"  analyzer produced {len(flows)} flow(s)")
    _emit_output("flows_count", str(len(flows)))

    # 3. Record one clean flow with Playwright
    recordings: list | None = None
    if flows and preview_url:
        try:
            from api.cloud_recorder import record_flows  # lazy: pulls in playwright
            recordings = await asyncio.to_thread(record_flows, flows, preview_url)
            passed = sum(1 for r in recordings if r.get("status") == "passed")
            failed = sum(1 for r in recordings if r.get("status") == "failed")
            demo   = sum(1 for r in recordings if r.get("status") == "demo")
            print(
                f"  recorded {len(recordings)} flow(s): "
                f"{passed} passed · {failed} failed · {demo} demo"
            )
        except Exception as e:
            print(f"RecordLoop: recording skipped — {e}", file=sys.stderr)

    # 3b. Upload the GIF (and MP4 fallback link) to GitHub releases for inline display
    if recordings:
        upload_tasks: list = []
        upload_meta: list = []  # (rec_dict, url_key_to_set)
        for r in recordings:
            for path_key, url_key in [
                ("gif",   "gif_url"),
                ("video", "video_url"),
            ]:
                path = r.get(path_key)
                if path:
                    upload_tasks.append(upload_pr_video(repo, pr_number, path, github_token))
                    upload_meta.append((r, url_key))

        if upload_tasks:
            upload_results = await asyncio.gather(*upload_tasks, return_exceptions=True)
            for (rec, url_key), url in zip(upload_meta, upload_results):
                if isinstance(url, str):
                    rec[url_key] = url
                    print(f"  uploaded {url_key} for '{rec['name']}' → {url}")
                else:
                    print(f"  upload failed for '{rec['name']}' ({url_key}): {url}", file=sys.stderr)

    # 4. Post the comment
    body = _render_comment(flows, preview_url, recordings)
    try:
        await upsert_pr_comment(repo, pr_number, body, github_token)
        print("  posted PR comment")
    except Exception as e:
        print(f"RecordLoop: failed to post comment — {e}", file=sys.stderr)
        return 1

    # 5. Fail the workflow if any flow failed an assertion. This is what
    # makes RecordLoop a TEST and not just a demo recorder: a broken UI
    # change should turn the PR check red, not just leave a friendly GIF.
    if recordings and any(r.get("status") == "failed" for r in recordings):
        print("RecordLoop: at least one flow failed an assertion", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
