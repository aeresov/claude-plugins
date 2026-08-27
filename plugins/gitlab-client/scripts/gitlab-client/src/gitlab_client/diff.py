# SPDX-License-Identifier: AGPL-3.0-only
"""Render GitLab's per-file diff JSON (MR diffs, commit diff, compare) as unified diff text.

GitLab already supplies the `@@` hunks in `diff`; we add the git-style file headers. An
empty `diff` on a file that is neither new, deleted, nor renamed means the server omitted it
because of the instance diff limits (15.11 has no collapsed/too_large flags). A renamed-and-modified
file over the limit therefore renders as a pure rename — a known simplification.
"""
from __future__ import annotations

from typing import Any, Callable

from .http import Client, encode_path_segment

OMITTED = "[diff omitted by server — exceeds the instance's diff limits]"


def file_status(d: dict[str, Any]) -> str:
    if d.get("new_file"):
        return "A"
    if d.get("deleted_file"):
        return "D"
    if d.get("renamed_file"):
        return "R"
    return "M"


def render_file(d: dict[str, Any]) -> str:
    old, new = d.get("old_path") or "", d.get("new_path") or ""
    lines = [f"diff --git a/{old} b/{new}"]
    if d.get("new_file"):
        lines.append(f"new file mode {d.get('b_mode') or '100644'}")
    if d.get("deleted_file"):
        lines.append(f"deleted file mode {d.get('a_mode') or '100644'}")
    if d.get("renamed_file"):
        lines += [f"rename from {old}", f"rename to {new}"]
    lines.append("--- /dev/null" if d.get("new_file") else f"--- a/{old}")
    lines.append("+++ /dev/null" if d.get("deleted_file") else f"+++ b/{new}")
    body = (d.get("diff") or "").rstrip("\n")
    if body:
        lines.append(body)
    elif not (d.get("new_file") or d.get("deleted_file") or d.get("renamed_file")):
        lines.append(OMITTED)  # an empty diff on a plain modification = server hit the diff limits
    return "\n".join(lines) + "\n"


def render_diffs(diffs: list[dict[str, Any]]) -> str:
    return "".join(render_file(d) for d in diffs)


def render_files(diffs: list[dict[str, Any]]) -> str:
    rows = []
    for d in diffs:
        row = f"{file_status(d)} {d.get('new_path') or d.get('old_path')}"
        if d.get("renamed_file"):
            row += f" (from {d.get('old_path')})"
        rows.append(row)
    rows.append(f"{len(diffs)} file(s)")
    return "\n".join(rows) + "\n"


def render_compare(payload: dict[str, Any]) -> str:
    commits = payload.get("commits") or []
    out = "".join(f"{c.get('short_id') or str(c.get('id', ''))[:8]} {c.get('title', '')}\n" for c in commits)
    if out:
        out += "\n"
    if payload.get("compare_timeout"):
        out += "[compare timed out on the server — result may be partial]\n"
    return out + render_diffs(payload.get("diffs") or [])


def mr_diffs(client: Client, project_id: int, iid: int, warn: Callable[[str], None]) -> list[dict[str, Any]]:
    return client.paginate(f"/projects/{project_id}/merge_requests/{iid}/diffs", None, max_items=3000, warn=warn)


def commit_diff(client: Client, project_id: int, sha: str) -> list[dict[str, Any]]:
    return client.request("GET", f"/projects/{project_id}/repository/commits/{encode_path_segment(sha)}/diff").json() or []


def compare(client: Client, project_id: int, frm: str, to: str, straight: bool = False) -> dict[str, Any]:
    return client.request("GET", f"/projects/{project_id}/repository/compare", query={"from": frm, "to": to, "straight": straight}).json() or {}
