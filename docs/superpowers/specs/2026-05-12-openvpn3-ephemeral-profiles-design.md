# openvpn3-on-demand: BYO vs. ephemeral profiles

- **Date:** 2026-05-12
- **Status:** approved (design); pending implementation plan
- **Affects:** `plugins/openvpn3-on-demand/` — bumps the plugin to **0.5.0**
- **Breaking:** yes — see [Migration](#migration)

## Context

Today the per-project settings file `.claude/openvpn3-on-demand.local.md` *requires* `profile_name`, and `ovpn_provision_cmd` is an optional companion: if `vpn_connect(profile_name)` fails because the named openvpn3 config doesn't exist, the skill runs `ovpn_provision_cmd` to produce an `.ovpn` file, calls `vpn_config_import(ovpn_path, profile_name)`, and retries. The imported config is **persistent** (`Import(name, body, single_use=False, persistent=True)`), so it lingers on the machine indefinitely; "refreshing" it is a manual `vpn_disconnect` → `vpn_config_remove` → reconnect cycle the skill only runs on explicit request.

That conflates two genuinely different use cases:

1. **"Use a VPN profile I already manage."** The user has imported a long-lived openvpn3 config (`openvpn3 config-import --persistent --name …`); the plugin should just start/stop sessions for it and otherwise leave it alone. In this case `profile_name` is the *identity* of an existing thing.
2. **"Generate a throwaway profile for this session."** The user has a command (`vault read …`, `aws s3 cp s3://…/vpn.ovpn -`, `cat secrets/vpn.ovpn`) that emits a fresh `.ovpn`; the plugin should provision it, use it, and clean it up — and the *name* is an internal detail the user neither picks nor sees.

This design splits those into two explicit, mutually exclusive modes and makes `profile_name` optional.

## Goals

- `profile_name` becomes optional. A valid settings file declares **exactly one** of `profile_name` (BYO mode) or `ovpn_provision_cmd` (ephemeral mode).
- Ephemeral mode generates a short-lived, self-cleaning openvpn3 config whose name the user never has to choose or know.
- The Stop/SessionEnd safety-net hook keeps working in both modes, scoped to exactly the one profile the plugin is responsible for — never blanket-disconnecting.
- Zero new files in the plugin; the change is edits to the skill, the MCP server, the teardown hook, and docs.

## Non-goals

- Changing the fact that the Stop hook fires at every turn boundary (a known wart, out of scope).
- Supporting interactive-credential profiles, encrypted PKCS#12, or non-Linux hosts.
- Touching the host `netcfg`/`systemd-resolved` preflight check in the skill.
- A "keep the ephemeral tunnel up across the whole session" mode — explicitly per-turn for now (see [Known limitations](#known-limitations)).

## Mode selection & validation

The skill's preflight (and `teardown.py`) read the YAML frontmatter of `.claude/openvpn3-on-demand.local.md`:

| Field | Mode | Requirement | Meaning |
|---|---|---|---|
| `profile_name` | BYO | one-of | Name of an **already-imported** openvpn3 config. |
| `ovpn_provision_cmd` | ephemeral | one-of | Shell command whose **stdout is the `.ovpn` body**. |
| `trigger_patterns` | both | optional | Extra regex patterns; unchanged. |
| `post_connect_cmd` | both | optional | Runs after a fresh `vpn_connect`; unchanged. |
| `post_disconnect_cmd` | both | optional | Runs after a fresh `vpn_disconnect` (incl. from the hook); unchanged. |

**Validation rule:** exactly one of `{profile_name, ovpn_provision_cmd}` must be present and non-empty.

- **Neither** → config error: tell the user the settings file must declare either `profile_name` or `ovpn_provision_cmd`.
- **Both** → config error: tell the user the two fields are mutually exclusive — `profile_name` for an existing config, `ovpn_provision_cmd` for an ephemeral one.

On a config error the skill surfaces the message to the user, runs **no** `vpn_*` tool, and proceeds with the user's request as normal (it may fail if it needed the tunnel — that's a consequence of the misconfiguration, not something the skill papers over). `teardown.py` treats the same misconfig as a silent no-op (a safety net doesn't guess).

If the file doesn't exist at all, the plugin is inert for this project (unchanged from today).

## BYO mode (`profile_name`)

Essentially today's behavior, **minus auto-provisioning**:

1. On a VPN-gated command, `vpn_connect(profile_name)`. Idempotent — `already_connected` is fine.
2. If it returns `status: error` indicating the config is unknown, **stop**: tell the user the profile isn't imported and to import it once, e.g.
   `openvpn3 config-import --config /path/to/file.ovpn --name <profile_name> --persistent`.
   The skill does **not** run any provisioning command in this mode.
3. On a fresh connect, run `post_connect_cmd` if set (non-fatal on failure).
4. Run the user's command; reuse the tunnel for subsequent VPN-gated commands in the same task.
5. At task end, `vpn_disconnect(profile_name)`; on a fresh disconnect, run `post_disconnect_cmd` if set.

The teardown hook disconnects the named profile's session if still active but **never removes the config** — it's the user's.

This deletes the skill's current "config missing → run `ovpn_provision_cmd` → `vpn_config_import` → retry" branch entirely.

## Ephemeral mode (`ovpn_provision_cmd`)

### The ephemeral name

`N = "ovpn3-od-" + ${CLAUDE_CODE_SESSION_ID}`.

`CLAUDE_CODE_SESSION_ID` is present in the Bash tool environment (verified), so the skill builds `N` by plain string concatenation — no hashing, no `uuidgen`, no helper script. It's session-bound by construction: two Claude sessions running in the same project directory get different IDs → different `N` → they can't disconnect each other's ephemeral tunnel.

If `CLAUDE_CODE_SESSION_ID` is unset/empty in the skill's environment (a Claude Code old enough not to set it), the skill reports that ephemeral mode can't run safely without it and proceeds **without** bringing up the tunnel. (Since nothing was started, the hook has nothing to clean up — consistent.)

`teardown.py` recovers the same value from `os.environ["CLAUDE_CODE_SESSION_ID"]`; if absent, it parses `session_id` from the JSON every Stop/SessionEnd hook receives on stdin (the hook doesn't read stdin today — this adds a few lines). If neither is available, the hook no-ops (safety net, bails silently).

### Per-turn flow (skill)

For each VPN-gated command (a "turn" here = the unit of work the skill is processing; the Stop hook fires at its end):

1. `N = ovpn3-od-$CLAUDE_CODE_SESSION_ID` (or stop, per above, if the var is missing).
2. `vpn_connect(N)`.
   - `already_connected` → an earlier VPN-gated command this turn already brought it up (the `single_use` config is gone but the session persists and `vpn_connect` finds it). Skip to step 4.
   - `connected` → there was still a config under `N` from earlier this turn; it's now consumed. Go to step 3's post-connect tail (run `post_connect_cmd`), then step 4.
   - `error` (no session, no config) → provision, step 3.
3. **Provision + import + connect:**
   1. `vpn_config_remove(N)` — defensive, best-effort; clears a config still registered under `N` from an earlier turn this session whose `NewTunnel` threw before consuming it. `already_removed` is the normal case; an `error` result here isn't fatal — the subsequent `vpn_config_import` will surface any real problem.
   2. Create a fresh temp file: `tmp="$(mktemp --suffix=.ovpn)"` (mode 600 by default).
   3. Run the provision command with its stdout redirected into that file: e.g. `{ <ovpn_provision_cmd verbatim> ; } > "$tmp"`. The *requirement* (not the exact mechanism): the command's standard output lands in `$tmp`, and its stdout bytes never appear in the conversation transcript; stderr may (it's the command's diagnostics).
   4. If the command exited non-zero, or `$tmp` is empty → provisioning failure: `rm -f "$tmp"`, report to the user, do **not** connect. (The VPN-gated command will fail; that's on the broken provision command.)
   5. `vpn_config_import(ovpn_path="$tmp", profile_name="$N", single_use=True)`. On `status: error`, report it, `rm -f "$tmp"`, stop.
   6. `rm -f "$tmp"` — the config body is now inside openvpn3; the file is no longer needed.
   7. `vpn_connect("$N")` → on `status: connected`, run `post_connect_cmd` if set (non-fatal). On `status: error`, report it and stop.
4. Run the user's command.
5. At task end (no further VPN-gated step queued), `vpn_disconnect(N)`; on a fresh disconnect, run `post_disconnect_cmd` if set.

`ovpn_provision_cmd` therefore runs on **every turn that touches the VPN** — see [Known limitations](#known-limitations).

### Lifecycle / cleanup

- `single_use=True` ⇒ openvpn3 itself drops the config object once `NewTunnel` consumes it, so within a turn there's normally nothing to remove.
- The Stop/SessionEnd hook disconnects the session for `N` and, belt-and-suspenders, removes any config still registered under `N` (covers a turn that imported but whose `NewTunnel` threw before consuming the config).
- The skill's own `vpn_disconnect(N)` at task end is the primary teardown; the hook is the backstop for "the model forgot."
- Reconnecting later in the same session re-runs the full provision → import → connect sequence (the previous `single_use` config no longer exists) — accepted, consistent with the per-turn-aggressive design.

## MCP server change

`vpn_config_import` gains an optional parameter:

```python
@mcp.tool()
def vpn_config_import(ovpn_path: str, profile_name: str, single_use: bool = False) -> dict:
    ...
    cfg = _get_config_mgr().Import(
        profile_name,
        cfg_str,
        single_use,        # was: hardcoded False
        not single_use,    # persistent: True for normal imports, False for ephemeral
    )
    ...
```

Rationale for `persistent = not single_use`: an ephemeral, self-deleting config has no reason to be written to openvpn3's on-disk config store, and not writing the secret-bearing body to disk is the better default. If a live openvpn3 backend rejects the `single_use=True, persistent=False` combination, fall back to `persistent=True` and rely on the explicit + defensive `vpn_config_remove` calls (decide this when validating against a real backend during implementation).

No other server changes. The server stays name-addressed and stateless; it does not run shell commands, does not pick names, does not touch the filesystem beyond reading the `ovpn_path` it's handed. `vpn_config_import` continues to take a *path*, not the config text — passing the body as a tool argument would route secret material through MCP tool args and the transcript.

The existing regression test `test_vpn_config_import_passes_raw_ovpn_contents` (asserts the default call passes `single_use=False, persistent=True`) is unchanged. Add `test_vpn_config_import_single_use`: a `vpn_config_import(..., single_use=True)` call passes `single_use=True, persistent=False` to `Import` and returns `status: imported`.

## Teardown hook change (`hooks/scripts/teardown.py`)

Current behavior: read `profile_name` (+ `post_disconnect_cmd`) from the settings file's frontmatter, disconnect sessions whose config name matches `profile_name`, run `post_disconnect_cmd` if anything was disconnected, never raise.

New behavior:

1. Read `profile_name`, `ovpn_provision_cmd`, `post_disconnect_cmd` from the frontmatter.
2. Both `profile_name` and `ovpn_provision_cmd` set → misconfig → return 0 (no-op).
3. `profile_name` set → `target = profile_name`; do **not** remove the config afterward.
4. else `ovpn_provision_cmd` set → resolve the session id (`$CLAUDE_CODE_SESSION_ID`, else `session_id` from stdin JSON, else give up → return 0); `target = "ovpn3-od-" + session_id`; remove the config for `target` afterward (best-effort).
5. else (neither) → return 0.
6. Disconnect sessions for `target` (existing `_disconnect_via_dbus`, which looks up by config name in the session manager).
7. If step 4's mode, also best-effort remove configs for `target` (a new `_remove_config_via_dbus(name)` mirroring `_disconnect_via_dbus` but against `ConfigurationManager`).
8. If anything was disconnected and `post_disconnect_cmd` is set, run it (5 s timeout, swallow failures — unchanged).

Reading stdin: only if the env var was absent; wrap `json.load(sys.stdin)` in a try/except returning `None` on any failure (empty stdin, non-JSON, missing key).

New test file `tests/test_teardown.py` (none exists today): cover frontmatter parsing, mode detection, `target` computation under each env/stdin combination, no-op on missing/malformed settings file, no-op when both fields are set, and that D-Bus interactions are made only for the resolved `target`. Mock the D-Bus helpers so it runs on a stock CI runner (same approach as `tests/conftest.py` for `test_server.py`).

## `ovpn_provision_cmd` contract (summary)

A shell command whose **standard output is the contents of a `.ovpn` file**. It writes nothing the plugin cares about anywhere else; the plugin captures stdout into a private temp file (mode 600), imports it, and deletes it. Stderr is for the command's own diagnostics. A non-zero exit, or empty stdout, is a provisioning failure.

(The field keeps the name `ovpn_provision_cmd` for continuity even though its semantics change from "writes a file to a path you choose / prints the path" to "writes the body to stdout"; the migration note calls this out loudly. Renaming it — e.g. to `ovpn_source_cmd` — is an open option if preferred at review time.)

## Security considerations

- The generated ephemeral `.ovpn` exists on disk only as a `mktemp` file (mode 600), only between provisioning and `vpn_config_import` returning, then `rm -f`'d. Its bytes are never echoed into the conversation transcript — the provision command's stdout goes straight to the temp file, not through the skill's view.
- `vpn_config_import` takes a path, not config text, specifically so secret material doesn't pass through MCP tool arguments / the transcript.
- Ephemeral configs are imported `single_use` (openvpn3 drops them after one tunnel) and `persistent=False` (not written to openvpn3's on-disk config store).
- `.claude/openvpn3-on-demand.local.md` remains git-ignored; `ovpn_provision_cmd` may reference internal infrastructure or secret-store paths.
- Teardown hook scoping is unchanged in spirit: BYO mode touches only the named profile; ephemeral mode touches only `ovpn3-od-<this session's id>`. It never blanket-disconnects, and the session-binding means a sibling Claude session in the same directory is unaffected.

## Docs, versioning, migration

### Docs to update

- `plugins/openvpn3-on-demand/README.md` — modes, the new `ovpn_provision_cmd` contract, the migration note.
- `plugins/openvpn3-on-demand/skills/vpn-on-demand/SKILL.md` — preflight (mode detection + validation), BYO flow, ephemeral flow, the `ovpn_provision_cmd` contract, the configuration table.
- `plugins/openvpn3-on-demand/skills/vpn-on-demand/references/example-local-settings.md` — two example blocks (BYO, ephemeral), the env-var/stdout contract.
- `plugins/openvpn3-on-demand/CLAUDE.md` — the two-mode summary, the ephemeral-name fact, the `single_use` flag.

### Version

Bump to **0.5.0** in: `plugins/openvpn3-on-demand/.claude-plugin/plugin.json`, the `openvpn3-on-demand` entry in `.claude-plugin/marketplace.json`, `plugins/openvpn3-on-demand/servers/openvpn3/pyproject.toml`, the stderr banner string in `server.py`'s `main()`, and version mentions in `servers/openvpn3/README.md` / `scripts/launch.sh` comments.

### Migration

For anyone with an existing `.claude/openvpn3-on-demand.local.md`:

- **Had only `profile_name`** → no change; keep importing the config yourself (`openvpn3 config-import --persistent`).
- **Had both `profile_name` and `ovpn_provision_cmd`** → now a config error. Pick one: drop `ovpn_provision_cmd` and pre-import the config yourself once, **or** drop `profile_name` to switch to ephemeral mode (and update the command per the next point).
- **`ovpn_provision_cmd` semantics changed**: it must now write the `.ovpn` body to **stdout** (e.g. `cat ~/.config/openvpn3/my.ovpn`, `vault read -field=config secret/vpn`, `aws s3 cp s3://…/vpn.ovpn -`), not write a file to a path it picks / print that path. A command like `make get_vpn_client_config OUTPUT=…` becomes `make get_vpn_client_config OUTPUT=/dev/stdout` or similar.

## Known limitations

- **`ovpn_provision_cmd` runs every VPN-gated turn.** If the command is slow or hits a remote secret store, that cost recurs each turn. If it ever bites, the mitigation is a future opt-in "keep the ephemeral tunnel up for the session" flag — out of scope here.
- **Ephemeral mode needs `CLAUDE_CODE_SESSION_ID`.** Absent it (very old Claude Code), the skill declines to bring up the tunnel rather than guessing a name the hook couldn't recompute.
- **Reconnect within a session re-provisions.** The `single_use` config is gone after the first tunnel, so a later reconnect re-runs the whole provision sequence. Accepted; consistent with the aggressive design.

## Alternatives considered

- **Project-dir-derived ephemeral name + a shared helper script.** Rejected: needs either a new script file or the same hash algorithm duplicated in skill prose and `teardown.py`, *and* it collides for two Claude sessions in the same directory. Session-binding via `CLAUDE_CODE_SESSION_ID` is simpler and has no collision.
- **`uuidgen` ephemeral name + a `.claude/openvpn3-on-demand.local.json` state file** for the hook to read. Rejected: extra per-turn file write and a second per-project config file, for uniqueness that session-binding gives for free.
- **Keep today's "named, auto-provisioned, persistent" combo** alongside the new modes. Rejected per the strict-either/or decision — it's the behavior being replaced; covered by the migration note.
- **A `vpn_config_import` variant taking the config text as an argument** (so the skill never writes a temp file). Rejected: routes secret material through MCP tool args / the transcript.
- **Whole-session ephemeral lifetime** (provision once, keep the tunnel up across turns, remove at SessionEnd). Rejected per the per-turn-aggressive decision.
