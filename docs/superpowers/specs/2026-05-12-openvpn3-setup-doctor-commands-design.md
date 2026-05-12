# Design: `setup` + `doctor` commands for `openvpn3-on-demand`

Date: 2026-05-12

## Problem

The `openvpn3-on-demand` plugin needs per-project (and per-machine) setup before it does
anything: a `.claude/openvpn3-on-demand.local.md` settings file, a `.gitignore` entry,
host packages (`openvpn3-client`, `python3-dbus`), the one-time `openvpn3-admin` netcfg
init, and — in BYO mode — an imported openvpn3 config. Today the only help is prose in the
README and `skills/vpn-on-demand/references/example-local-settings.md`; the skill's preflight
silently bails when the settings file is missing. We want the plugin to actively help a user
(or Claude on their behalf) get configured correctly.

## Scope

Add two slash commands to the plugin and a shared checklist they both draw on, plus small
wording changes to the existing skill so it points at them. No changes to the MCP server or
the teardown hook.

Out of scope: running privileged (`sudo`) commands automatically; importing the BYO profile
automatically; any automated test harness for command markdown (there is none today for the
skill either — these are prose, validated by `claude plugin validate` + manual smoke test).

## Components

### `setup-checklist.md` (plugin root) — shared reference

A single Markdown reference that both commands read via `${CLAUDE_PLUGIN_ROOT}/setup-checklist.md`.
It enumerates every check, how to run it, and the canonical remediation text. Keeping it in one
place means `setup` and `doctor` can't drift. It lives at the plugin root rather than under
`commands/` so `claude plugin validate` doesn't register it as a (frontmatter-less) slash command.

| # | Check | How (read-only) | Remediation text |
|---|---|---|---|
| 1 | `openvpn3-client` installed | `openvpn3 version` exits 0 | install the `openvpn3-client` system package (Debian/Ubuntu: `apt install openvpn3-client`) |
| 2 | `python3-dbus` installed | `python3 -c 'import dbus'` exits 0 | install the `python3-dbus` system package (Debian/Ubuntu: `apt install python3-dbus`) |
| 3 | Host netcfg initialized | `test -f /var/lib/openvpn3/netcfg.json` | the three `sudo openvpn3-admin …` lines from SKILL.md preflight + the `--config-show` verify line |
| 4 | Settings file present | `test -f .claude/openvpn3-on-demand.local.md` | run `/openvpn3-on-demand:setup` |
| 5 | Settings file valid | parse frontmatter: exactly one of `profile_name` / `ovpn_provision_cmd` non-empty | SKILL.md's existing both/neither wording; re-run `/openvpn3-on-demand:setup` to fix |
| 6 | (BYO only) profile imported | `openvpn3 configs-list` lists the configured `profile_name` | `openvpn3 config-import --config /path/to/file.ovpn --name <profile_name> --persistent` |
| 7 | `.gitignore` covers settings file | `.gitignore` matches `.claude/*.local.md` (or the explicit path) | add `.claude/*.local.md` to `.gitignore` |

The reference also states the standing facts the commands repeat: settings edits take effect
immediately (skill re-reads per turn, hook re-reads per fire) — no Claude Code restart needed;
only `.mcp.json` / `hooks.json` edits need a restart.

### `commands/doctor.md` → `/openvpn3-on-demand:doctor`

Read-only diagnostic. Runs checks 1–7 from the checklist, prints one PASS/FAIL line per check
(with the remediation text inline on failures), and a one-line summary, e.g.
`configured · BYO mode · profile "prod-vpn" imported · host ready` or
`not configured — run /openvpn3-on-demand:setup`. Writes nothing, runs nothing privileged.

### `commands/setup.md` → `/openvpn3-on-demand:setup`

Interactive configuration. Flow:

1. Run checks 1–3 (deps + netcfg). If any fail, print the remediation and **stop** — don't
   configure a plugin whose host isn't ready. (Instruct only; no `sudo`.)
2. If `.claude/openvpn3-on-demand.local.md` already exists: show its current contents and ask
   *keep / reconfigure / abort* (AskUserQuestion). On *keep* or *abort*, stop.
3. Ask **BYO vs ephemeral** (AskUserQuestion).
   - **BYO** → ask for `profile_name`. Then run check 6; if the profile isn't in
     `openvpn3 configs-list`, print the `config-import … --persistent` line and note that
     setup will still write the settings file (the user runs the import themselves).
   - **Ephemeral** → ask for the `ovpn_provision_cmd` string, showing the README's examples
     (`vault read -field=config …`, `aws s3 cp s3://…/vpn.ovpn -`, `cat ~/.config/openvpn3/my.ovpn`,
     `make get_vpn_client_config OUTPUT=/dev/stdout`). Remind that it must write the `.ovpn`
     body to **stdout**.
4. Ask whether they want any optional fields (`trigger_patterns`, `post_connect_cmd`,
   `post_disconnect_cmd`); default skip.
5. **Write** `.claude/openvpn3-on-demand.local.md` from a template populated with the answers,
   including a commented "notes for humans" body stub.
6. If `.gitignore` doesn't already cover the settings file, append `.claude/*.local.md`
   (creating `.gitignore` if absent).
7. Print a summary: what was written, what the user still owes (profile import / netcfg if
   outstanding), and "no Claude Code restart needed — the skill re-reads this file every turn."

### Skill changes — `skills/vpn-on-demand/SKILL.md`

- Preflight step 1 ("no settings file → **stop**, do nothing"): keep the "do nothing / call no
  `vpn_*` tool" behaviour, but add — "tell the user the plugin isn't configured for this
  project and they can run `/openvpn3-on-demand:setup` (or offer to run it now)."
- Preflight step 3 (misconfigured mode, both/neither): after surfacing the config error, add
  "… re-run `/openvpn3-on-demand:setup` to fix it."
- "Failure modes" section: the "openvpn3 / dbus-python not installed" and "tunnel up but can't
  reach the host" entries gain "`/openvpn3-on-demand:doctor` prints the full host/config state."

## File layout

```
plugins/openvpn3-on-demand/
  setup-checklist.md              # shared reference, read via ${CLAUDE_PLUGIN_ROOT}
  commands/
    setup.md
    doctor.md
  skills/vpn-on-demand/SKILL.md   # edited
```

Commands are auto-discovered from `commands/` — no `plugin.json` change needed. The shared
checklist stays *out* of `commands/` (it has no frontmatter and would otherwise register as a
slash command). Command Markdown files do **not** get the AGPL SPDX header (the repo convention
is "new Python/shell *source* files"; these are prose, like the existing SKILL.md and references).

## Housekeeping

- Version bump `0.5.0 → 0.6.0` (new feature). Per this plugin's CLAUDE.md a bump touches:
  `.claude-plugin/plugin.json`, the plugin's entry in the repo-root
  `.claude-plugin/marketplace.json`, `servers/openvpn3/pyproject.toml`, and the stderr banner
  string in `servers/openvpn3/openvpn3_mcp/server.py`'s `main()` — even though no server code
  changed.
- README: replace the "Per-project setup" section's lead with "run `/openvpn3-on-demand:setup`"
  as the recommended path; keep the manual template as the documented fallback; mention
  `/openvpn3-on-demand:doctor` under Troubleshooting.
- Plugin `CLAUDE.md`: add a fourth bullet to "What this plugin is" (or a Commands note)
  describing the two commands + the shared checklist, and note they carry no code.

## Testing / verification

- `claude plugin validate plugins/openvpn3-on-demand` still passes (and `claude plugin
  validate .` for the marketplace).
- Manual smoke test via `claude --plugin-dir …` in three states:
  (a) an unconfigured repo — `doctor` reports "not configured", `setup` walks the full flow;
  (b) a BYO-configured repo with the profile imported — `doctor` is all-green, `setup` offers
  keep/reconfigure/abort;
  (c) a repo with a deliberately broken settings file (both fields set) — `doctor` flags
  check 5, `setup` offers to rewrite it.
- No automated tests added (consistent with the skill, which is also un-harnessed prose).

## Open items resolved during brainstorming

- Setup scope: **write the settings file + patch `.gitignore` only**; everything else
  (profile import, netcfg sudo lines) is instruct-only.
- Doctor vs setup: **shared checklist, two entry points** — `doctor` runs it read-only;
  `setup` runs the same checks then does the interactive write.
