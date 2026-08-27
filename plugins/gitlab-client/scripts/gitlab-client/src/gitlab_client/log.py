# SPDX-License-Identifier: AGPL-3.0-only
"""Job log (trace) handling: download to a cache file, strip ANSI, parse runner sections,
and expose bounded views. The full log is never returned to the caller as one blob.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .http import Client

# CSI sequences (colours, erase-line, cursor moves) and OSC sequences (titles).
ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
# section_start:<unix ts>:<name>[collapsed=true]  /  section_end:<unix ts>:<name>
SECTION_RE = re.compile(r"section_(start|end):(\d+):([A-Za-z0-9_.-]+)(?:\[[^\]]*\])?")
TERMINAL = {"success", "failed", "canceled", "skipped"}
DEFAULT_TAIL = 150


@dataclass
class Section:
    name: str
    start_ts: int
    end_ts: int | None
    start_line: int  # index into the cleaned lines (first body line)
    end_line: int  # exclusive

    @property
    def duration(self) -> int | None:
        return None if self.end_ts is None else self.end_ts - self.start_ts


def clean_line(raw: str, *, strip_ansi: bool = True) -> str:
    text = ANSI_RE.sub("", raw) if strip_ansi else raw
    if "\r" in text:
        segments = [s for s in text.split("\r") if s]  # terminal overwrite: last non-empty wins
        text = segments[-1] if segments else ""
    return text.rstrip("\n")


def parse_trace(raw: str, *, strip_ansi: bool = True) -> tuple[list[str], list[Section]]:
    lines: list[str] = []
    sections: list[Section] = []
    open_sections: dict[str, Section] = {}
    # Split on "\n" only: str.splitlines() would also split on the bare "\r" that clean_line must see.
    raw_lines = raw.split("\n")
    if raw_lines and raw_lines[-1] == "":
        raw_lines.pop()  # a trailing newline, not an empty last line
    for raw_line in raw_lines:
        for m in SECTION_RE.finditer(raw_line):
            kind, ts, name = m.group(1), int(m.group(2)), m.group(3)
            if kind == "start":
                sec = Section(name, ts, None, len(lines), len(lines))
                open_sections[name] = sec
                sections.append(sec)
            elif name in open_sections:
                sec = open_sections.pop(name)
                sec.end_ts, sec.end_line = ts, len(lines)
        stripped = SECTION_RE.sub("", raw_line)
        cleaned = clean_line(stripped, strip_ansi=strip_ansi)
        if stripped != raw_line and not clean_line(stripped).strip():
            continue  # the line held only markers
        lines.append(cleaned)
    for sec in open_sections.values():
        sec.end_line = len(lines)
    return lines, sections


def tail(lines: list[str], n: int) -> list[str]:
    return lines[-n:] if n else lines


def head(lines: list[str], n: int) -> list[str]:
    return lines[:n] if n else lines


def grep(lines: list[str], pattern: str, context: int = 0) -> list[str]:
    rx = re.compile(pattern, re.IGNORECASE)
    keep: set[int] = set()
    for i, line in enumerate(lines):
        if rx.search(line):
            keep.update(range(max(0, i - context), min(len(lines), i + context + 1)))
    out: list[str] = []
    prev: int | None = None
    for i in sorted(keep):
        if prev is not None and i != prev + 1:
            out.append("--")
        out.append(f"{i + 1}: {lines[i]}")
        prev = i
    return out


def sections_summary(sections: list[Section]) -> list[str]:
    return [f"{s.name} · {s.end_line - s.start_line} lines · " + (f"{s.duration}s" if s.duration is not None else "unterminated") for s in sections]


def section_body(lines: list[str], sections: list[Section], name: str) -> list[str] | None:
    for s in sections:
        if s.name == name:
            return lines[s.start_line : s.end_line]
    return None


def cache_path(cache_dir: Path, job_id: int) -> Path:
    return cache_dir / f"job-{job_id}.log"


def fetch_trace(client: Client, project_id: int, job_id: int, cache_dir: Path, *, refresh: bool = False) -> tuple[dict, Path, int]:
    """Return (job json, cached log path, size). Terminal jobs are cached; others re-download."""
    job = client.request("GET", f"/projects/{project_id}/jobs/{job_id}").json() or {}
    path = cache_path(cache_dir, job_id)
    if refresh or job.get("status") not in TERMINAL or not path.is_file():
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as fh:
            client.request("GET", f"/projects/{project_id}/jobs/{job_id}/trace", accept_json=False, stream_to=fh)
    return job, path, path.stat().st_size


def header_line(job: dict[str, Any], size: int) -> str:
    duration = job.get("duration")
    dur = f"{duration:.0f}s" if isinstance(duration, (int, float)) else "-"
    return f"job {job.get('id')} {job.get('name')} · stage {job.get('stage')} · {job.get('status')} · {dur} · {size} bytes · {job.get('web_url')}"
