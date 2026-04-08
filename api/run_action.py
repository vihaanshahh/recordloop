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


def _render_comment(flows, preview_url: str, recordings: list | None) -> str:
    if not flows:
        return (
            "## RecordLoop\n\n"
            "_No recordable UI changes detected in this PR._"
        )

    lines = [
        "## RecordLoop",
        "",
        "_AI-generated test flows for the changed UI in this PR_",
        "",
    ]

    rec_by_name = {r.get("name"): r for r in (recordings or [])}
    has_video_without_url = False

    for f in flows:
        rec = rec_by_name.get(f.name) or {}
        actions_done = rec.get("actions") or 0
        status = rec.get("status") or ""

        lines.append(f"### {f.name}")

        # Change context — what the reviewer should look for
        if f.change_context:
            lines.append(f"> {f.change_context}")
            lines.append("")

        lines.append(f"_{f.description}_")
        lines.append("")

        after_gif  = rec.get("gif_url") or ""
        before_gif = rec.get("before_gif_url") or ""

        if before_gif and after_gif:
            # Side-by-side table — GitHub renders HTML tables in PR comments
            lines.append(
                "<table><tr>"
                "<th>Before (base branch)</th>"
                "<th>After (this PR)</th>"
                "</tr><tr>"
                f'<td><img src="{before_gif}" width="320" alt="before"/></td>'
                f'<td><img src="{after_gif}"  width="320" alt="after"/></td>'
                "</tr></table>"
            )
            lines.append("")
            lines.append(f"_🎥 {actions_done}/{len(f.steps)} steps recorded_")
        elif after_gif:
            lines.append(f"![{f.name}]({after_gif})")
            lines.append("")
            lines.append(f"_🎥 {actions_done}/{len(f.steps)} steps recorded_")
        elif rec.get("video_url"):
            lines.append(f"[▶ Watch recording]({rec['video_url']})")
            lines.append("")
            lines.append(f"_🎥 {actions_done}/{len(f.steps)} steps recorded_")
        elif rec.get("video"):
            has_video_without_url = True
            lines.append(f"_🎥 {actions_done}/{len(f.steps)} steps recorded (see artifact below)_")
        elif status == "failed":
            lines.append(f"_❌ recording failed: {rec.get('error', '')}_")
        else:
            lines.append("_📋 planned (not recorded)_")

        lines.append("")
        lines.append("<details><summary>Steps</summary>")
        lines.append("")
        for s in f.steps:
            extra = f" — `{s.value}`" if s.value else ""
            lines.append(f"1. **{s.action}** `{s.selector}`{extra}")
        lines.append("")
        lines.append("</details>")
        lines.append("")

    workflow_run_url = _env("WORKFLOW_RUN_URL")
    if has_video_without_url and workflow_run_url:
        lines.append("---")
        lines.append("")
        lines.append(
            f"📥 **[Download the recorded MP4s]({workflow_run_url}#artifacts)** "
            f"(artifact: `recordloop-videos-pr-{_env('PR_NUMBER')}`, retained 14 days)"
        )
    elif not preview_url and not _env("RECORDLOOP_DETECTED_URL"):
        lines.append("> ℹ️ No `preview-url` configured and `auto-start` is disabled — flows are listed but not recorded.")

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
    from api.github_client import get_pr_files, post_pr_comment, upload_pr_video

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
            await post_pr_comment(repo, pr_number, body, github_token)
        except Exception:
            pass
        return 1

    print(f"  analyzer produced {len(flows)} flow(s)")
    _emit_output("flows_count", str(len(flows)))

    # 3. Optionally record with Playwright (after = PR branch, before = base branch)
    recordings: list | None = None
    if flows and preview_url:
        try:
            from api.cloud_recorder import record_flows  # lazy: pulls in playwright
            recordings = await asyncio.to_thread(record_flows, flows, preview_url, base_url)
            ok = sum(1 for r in recordings if r.get("status") == "done")
            print(f"  recorded {ok}/{len(recordings)} flow(s) with Playwright")
        except Exception as e:
            print(f"RecordLoop: recording skipped — {e}", file=sys.stderr)

    # 3b. Upload all GIFs (after + before) to GitHub releases for inline display
    if recordings:
        upload_tasks: list = []
        upload_meta: list = []  # (rec_dict, url_key_to_set)
        for r in recordings:
            for path_key, url_key in [
                ("gif",         "gif_url"),
                ("video",       "video_url"),
                ("before_gif",  "before_gif_url"),
                ("before_video","before_video_url"),
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
                    label = "before GIF" if "before" in url_key else "after GIF"
                    print(f"  uploaded {label} for '{rec['name']}' → {url}")
                else:
                    print(f"  upload failed for '{rec['name']}' ({url_key}): {url}", file=sys.stderr)

    # 4. Post the comment
    body = _render_comment(flows, preview_url, recordings)
    try:
        await post_pr_comment(repo, pr_number, body, github_token)
        print("  posted PR comment")
    except Exception as e:
        print(f"RecordLoop: failed to post comment — {e}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
