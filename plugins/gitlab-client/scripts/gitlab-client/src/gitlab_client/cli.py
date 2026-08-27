# SPDX-License-Identifier: AGPL-3.0-only
"""argparse front-end for `gl`.

Subcommands: api, project, version (this task); log, diff, artifacts (Tasks 7–9).
All network access goes through `Context.client`; all inputs (cwd, home, env, the client
factory, stdio) are injectable so tests never touch the real environment.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.parse
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence, TextIO

from . import __version__, diff, log
from .errors import ConfigError, GlError, PolicyError
from .http import Client, check_write_policy, parse_params, project_fields, substitute_project
from .project import Project, git_toplevel, resolve_project
from .settings import Settings, load_settings, resolve_token

ClientFactory = Callable[[str, str], Client]
Command = Callable[["Context", argparse.Namespace, TextIO], int]


class Context:
    """Lazily resolves settings → token → client → project, each at most once per process."""

    def __init__(self, args: argparse.Namespace, *, cwd: Path, home: Path, env: Mapping[str, str],
                 client_factory: ClientFactory, stderr: TextIO):
        self.args = args
        self.cwd = cwd
        self.home = home
        self.env = env
        self.stderr = stderr
        self.root = git_toplevel(cwd) or cwd
        self._client_factory = client_factory
        self._settings: Settings | None = None
        self._client: Client | None = None
        self._project: Project | None = None

    @property
    def settings(self) -> Settings:
        if self._settings is None:
            self._settings = load_settings(
                cwd=self.root, home=self.home, env=self.env,
                url_flag=getattr(self.args, "url", None), project_flag=getattr(self.args, "project", None),
            )
        return self._settings

    @property
    def client(self) -> Client:
        if self._client is None:
            self._client = self._client_factory(self.settings.url, resolve_token(self.settings))
        return self._client

    @property
    def project(self) -> Project:
        if self._project is None:
            self._project = resolve_project(self.client, self.settings, remote=getattr(self.args, "remote", "origin"), cwd=self.cwd)
        return self._project

    @property
    def host(self) -> str:
        return urllib.parse.urlsplit(self.settings.url).netloc

    @property
    def cache_dir(self) -> Path:
        base = Path(self.env.get("XDG_CACHE_HOME") or (self.home / ".cache"))
        return base / "gitlab-client" / self.host

    def warn(self, message: str) -> None:
        if not getattr(self.args, "quiet", False):
            print(message, file=self.stderr)


def _dump(data: Any) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


# ---- commands --------------------------------------------------------------------------------


RAW_ROUTE = re.compile(r"/(raw|trace|artifacts(/.*)?|archive(\.[a-z0-9.]+)?)$")


def cmd_api(ctx: Context, args: argparse.Namespace, out: TextIO) -> int:
    method = args.method
    # Policy first, on the *unsubstituted* path: a refused write must never run token_cmd or touch the network.
    check_write_policy(method, args.path)
    params = parse_params(args.params)
    if args.all and method != "GET":
        raise ConfigError("--all only applies to GET")

    body: Any = None
    if args.json_body is not None:
        try:
            body = json.loads(args.json_body)
        except json.JSONDecodeError as e:
            raise ConfigError(f"--json: bad JSON: {e}") from None
    elif method != "GET":
        body = params
    query = params if method == "GET" else None
    if "sudo" in params or (isinstance(body, dict) and "sudo" in body):
        raise PolicyError("refused by gitlab-client write policy: the sudo parameter (impersonation) is not allowed")

    path = substitute_project(args.path, ctx.project.path) if ":project" in args.path else args.path
    accept_json = not RAW_ROUTE.search(path.split("?", 1)[0])

    if args.out:
        with open(args.out, "wb") as fh:
            resp = ctx.client.request(method, path, query=query, json_body=body, accept_json=False, stream_to=fh)
        out.write(f"wrote {resp.bytes_written} bytes to {args.out}\n")
        return 0

    if args.all:
        data = ctx.client.paginate(path, query, max_items=args.max, warn=ctx.warn)
    else:
        resp = ctx.client.request(method, path, query=query, json_body=body, accept_json=accept_json)
        try:
            data = resp.json()
        except ValueError:
            out.write(resp.body.decode("utf-8", errors="replace"))
            return 0
    if args.fields:
        data = project_fields(data, [f.strip() for f in args.fields.split(",") if f.strip()])
    out.write(_dump(data))
    return 0


def cmd_project(ctx: Context, args: argparse.Namespace, out: TextIO) -> int:
    p = ctx.project
    out.write(_dump({"id": p.id, "path_with_namespace": p.path, "default_branch": p.default_branch, "web_url": p.web_url, "resolved_from": p.resolved_from}))
    return 0


def cmd_version(ctx: Context, args: argparse.Namespace, out: TextIO) -> int:
    data = ctx.client.request("GET", "/metadata").json() or {}
    version = str(data.get("version", "?"))
    if version.split(".", 1)[0] != "15":
        ctx.warn(f"gl: warning: references are written for GitLab 15.11; this instance is {version}")
    out.write(_dump({"version": version, "revision": data.get("revision"), "enterprise": data.get("enterprise"), "url": ctx.settings.url}))
    return 0


def cmd_log(ctx: Context, args: argparse.Namespace, out: TextIO) -> int:
    job, path, size = log.fetch_trace(ctx.client, ctx.project.id, args.job_id, ctx.cache_dir, refresh=args.refresh)
    out.write(log.header_line(job, size) + "\n")
    raw = path.read_bytes().decode("utf-8", errors="replace")  # bytes: text mode would turn bare \r into newlines
    lines, sections = log.parse_trace(raw, strip_ansi=not args.raw)
    if args.sections:
        out.write("".join(s + "\n" for s in log.sections_summary(sections)))
        return 0
    if args.section:
        body = log.section_body(lines, sections, args.section)
        if body is None:
            raise GlError(f"no section named {args.section!r} (use --sections to list them)")
        lines = body
    if args.grep:
        out.write("".join(s + "\n" for s in log.grep(lines, args.grep, args.context)))
        return 0
    if args.head is not None:
        lines = log.head(lines, args.head)
    elif args.tail is not None:
        if args.tail == 0:
            ctx.warn(f"gl: warning: printing the whole log ({len(lines)} lines, {size} bytes)")
        lines = log.tail(lines, args.tail)
    elif not args.section:
        lines = log.tail(lines, log.DEFAULT_TAIL)
    out.write("".join(s + "\n" for s in lines))
    return 0


def cmd_diff(ctx: Context, args: argparse.Namespace, out: TextIO) -> int:
    if not (args.range or args.commit or args.mr_iid is not None):
        raise ConfigError("give MR_IID, --commit SHA, or --range FROM..TO")
    if args.range:
        frm, sep, to = args.range.partition("..")
        if not sep or not frm or not to:
            raise ConfigError("--range needs FROM..TO")
    pid = ctx.project.id  # resolved only after argument validation, so usage errors never hit the network
    if args.range:
        payload = diff.compare(ctx.client, pid, frm, to, args.straight)
        diffs = payload.get("diffs") or []
        if not args.files and not args.file:
            out.write(diff.render_compare(payload))
            return 0
    elif args.commit:
        diffs = diff.commit_diff(ctx.client, pid, args.commit)
    else:
        diffs = diff.mr_diffs(ctx.client, pid, args.mr_iid, ctx.warn)
    if args.file:
        diffs = [d for d in diffs if args.file in (d.get("new_path"), d.get("old_path"))]
        if not diffs:
            raise GlError(f"no file {args.file!r} in this diff")
    out.write(diff.render_files(diffs) if args.files else diff.render_diffs(diffs))
    return 0


def _not_implemented(ctx: Context, args: argparse.Namespace, out: TextIO) -> int:
    raise ConfigError(f"{args.command} is not implemented yet")


COMMANDS: dict[str, Command] = {
    "api": cmd_api,
    "project": cmd_project,
    "version": cmd_version,
    "log": cmd_log,
    "diff": cmd_diff,
    "artifacts": _not_implemented,
}


# ---- parser ----------------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--url", help="GitLab base URL (overrides settings)")
    common.add_argument("--project", help="group/name or a GitLab URL (overrides settings and the git remote)")
    common.add_argument("--remote", default="origin", help="git remote to derive the project from (default: origin)")
    common.add_argument("-q", "--quiet", action="store_true", help="suppress warnings on stderr (errors still print)")

    p = argparse.ArgumentParser(prog="gl", description="GitLab REST v4 client for the gitlab-client Claude Code plugin.")
    p.add_argument("--version", action="version", version=f"gl {__version__}")
    sub = p.add_subparsers(dest="command", metavar="{api,project,log,diff,artifacts,version}")

    a = sub.add_parser("api", parents=[common], help="call any REST v4 endpoint (GET/POST/PUT; writes are allow-listed)")
    a.add_argument("method", type=str.upper, choices=["GET", "POST", "PUT"])
    a.add_argument("path", help="path under /api/v4; ':project' expands to the URL-encoded project path")
    a.add_argument("params", nargs="*", help="key=value | key:=json | key[]=value (query for GET, JSON body for POST/PUT)")
    a.add_argument("--all", action="store_true", help="follow pagination and print one array (GET only)")
    a.add_argument("--max", type=int, default=1000, help="item cap for --all (default 1000)")
    a.add_argument("--fields", help="comma-separated dotted paths to keep in each object; `.N` indexes a list (author.username, notes.0.body)")
    a.add_argument("--out", help="stream the raw response body to FILE")
    a.add_argument("--json", dest="json_body", help="raw JSON request body (POST/PUT) instead of params")

    sub.add_parser("project", parents=[common], help="print the resolved project")
    sub.add_parser("version", parents=[common], help="GET /metadata (warns if not GitLab 15.x)")

    lg = sub.add_parser("log", parents=[common], help="job log: ANSI-stripped, bounded views")
    lg.add_argument("job_id", type=int)
    lg.add_argument("--tail", type=int, help="last N lines (default 150; 0 = whole log, with a warning)")
    lg.add_argument("--head", type=int, help="first N lines")
    lg.add_argument("--grep", metavar="REGEX", help="case-insensitive search; prints 'line: text'")
    lg.add_argument("-C", "--context", type=int, default=0, help="context lines around --grep hits")
    lg.add_argument("--sections", action="store_true", help="list runner sections with line counts and durations")
    lg.add_argument("--section", metavar="NAME", help="restrict to one section")
    lg.add_argument("--raw", action="store_true", help="keep ANSI escape sequences")
    lg.add_argument("--refresh", action="store_true", help="re-download even if cached")

    d = sub.add_parser("diff", parents=[common], help="unified diff of an MR, a commit, or a ref range")
    d.add_argument("mr_iid", nargs="?", type=int)
    d.add_argument("--commit", metavar="SHA")
    d.add_argument("--range", metavar="FROM..TO")
    d.add_argument("--straight", action="store_true", help="two-dot compare (default is merge-base)")
    d.add_argument("--files", action="store_true", help="list changed files instead of the diff")
    d.add_argument("--file", metavar="PATH", help="only this file")

    ar = sub.add_parser("artifacts", parents=[common], help="download/list/extract job artifacts")
    ar.add_argument("job_id", nargs="?", type=int)
    ar.add_argument("--ref", help="branch or tag (latest successful pipeline); needs --job")
    ar.add_argument("--job", help="job name, with --ref")
    ar.add_argument("--list", action="store_true", help="list archive entries")
    ar.add_argument("--extract", metavar="DIR", help="extract the archive into DIR")
    ar.add_argument("--file", metavar="PATH", help="fetch one file from the artifacts (no archive download)")
    ar.add_argument("--out", help="with --file: write to FILE instead of stdout")
    ar.add_argument("--refresh", action="store_true", help="re-download even if cached")
    return p


def main(
    argv: Sequence[str] | None = None,
    *,
    cwd: str | os.PathLike[str] | None = None,
    home: str | os.PathLike[str] | None = None,
    env: Mapping[str, str] | None = None,
    client_factory: ClientFactory | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    stdout = stdout or sys.stdout
    stderr = stderr or sys.stderr
    parser = build_parser()
    args, extra = parser.parse_known_args(argv)
    if extra:
        if args.command == "api" and not any(x.startswith("-") for x in extra):
            args.params.extend(extra)  # params given after options, e.g. `gl api GET /p --fields id state=opened`
        else:
            parser.error(f"unrecognized arguments: {' '.join(extra)}")
    if not args.command:
        parser.print_usage(stderr)
        return 2
    ctx = Context(
        args,
        cwd=Path(cwd or os.getcwd()),
        home=Path(home or Path.home()),
        env=os.environ if env is None else env,
        client_factory=client_factory or Client,
        stderr=stderr,
    )
    try:
        return COMMANDS[args.command](ctx, args, stdout)
    except GlError as e:
        print(f"gl: {e}", file=stderr)
        return e.exit_code
