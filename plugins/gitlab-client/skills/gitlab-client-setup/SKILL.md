---
name: gitlab-client-setup
description: "Configure gitlab-client — record the GitLab URL and the command that prints your access token in ~/.claude/gitlab-client.local.md, optionally pin a project path for this repo, and add the project file to .gitignore. Never runs your token command and never contacts GitLab."
---

Resolve `../..` from this SKILL.md's directory to the absolute plugin root. Substitute that path for `<plugin-root>` in the instructions and commands below; do not assume a plugin-root shell variable is set. Keep the working directory at the consuming project root.

In Codex, select `$gitlab-client:gitlab-client-setup` or `$gitlab-client:gitlab-client-doctor`. In Claude Code, the equivalent commands are `/gitlab-client:setup` and `/gitlab-client:doctor`.

You are running `gitlab-client-setup`: an interactive configurator. You will write **only** `~/.claude/gitlab-client.local.md`, optionally `.claude/gitlab-client.local.md` in the project root, and (if needed) a line in the project's `.gitignore`. You will **not** run `token_cmd`, **not** call `gl`, and **not** contact GitLab — `gitlab-client-doctor` does the live checks afterwards. The user file lives outside the project (both clients read the same one), so writing it may need approval: follow the host client's approval policy.

First read the shared checklist at `<plugin-root>/setup-checklist.md` — it defines checks 1–8 and the remediation text. Use that text verbatim on failures.

## Flow

### 1. Prerequisite
Run check 1 (`python3` ≥ 3.9). If it fails, print its remediation text and **stop**.

### 2. Existing user settings?
Run check 2. If `~/.claude/gitlab-client.local.md` exists, run `grep -E '^url:' ~/.claude/gitlab-client.local.md` and `grep -qE '^token_cmd: *[^ ]' ~/.claude/gitlab-client.local.md; echo "token_cmd set: $?"` (0 = set). Show the URL and whether `token_cmd` is set — **don't read or print the file**; a hand-written `token_cmd` could hold the token itself. Ask the user: *Keep as-is* / *Reconfigure (overwrite)* / *Abort*. On Keep, jump to step 5; on Abort, stop.

### 3. GitLab URL
Ask for the instance URL (e.g. `https://gitlab.example.com`; relative-URL installs include the prefix, e.g. `https://intranet.example.com/gitlab`). Strip a trailing slash. It must start with `http://` or `https://`.

### 4. Where does the token come from?
Ask the user — "Where does your GitLab personal access token come from?":

- **Keeper Commander (`keeper`)** — "The PAT is the password of a record in your vault: `cd / && flock -w 50 -E 75 ~/.claude/gitlab-client.keeper.lock setsid -w keeper --batch-mode --fail-on-throttle get --format password -- <record-uid> </dev/null`"
- **pass / 1Password / other CLI** — "`pass show <path>`, `op read \"op://<vault>/<item>/credential\"`, or any command whose stdout is the token."
- **Environment variable** — "The token is already exported, e.g. `$GITLAB_TOKEN` → `printf '%s' \"$GITLAB_TOKEN\"`."

Keeper means Keeper Commander, the `keeper` CLI. Don't offer Keeper Secrets Manager (`ksm`, `keeper://` notation): it needs a Secrets Manager application, an enterprise feature an ordinary Keeper account doesn't have.

If the user offers to paste the token itself, decline: the file would then *be* the secret and later setup / doctor runs could echo it. Point them at the environment-variable form (export it from their shell profile or a secret-store hook).

Open `<plugin-root>/skills/gitlab-client/references/local-settings.md` and show the matching example. Ask for the specifics and assemble the final `token_cmd`. Restate it and confirm before writing.

For Keeper Commander, the only specific is the record's **UID**, never its title (`keeper search <words>` in the user's own terminal prints it); keep the rest of the line exactly as the reference gives it — each part is explained there. The line needs Linux (`flock` and `setsid` are util-linux); on macOS, recommend another token source. Then tell the user what Commander needs before the first `gl` call — setup checks and runs none of it:

- Signed in on this host, in their own terminal, as the user `gl` runs as, with persistent login on and a logout timeout longer than the gaps between `gl` uses: `keeper login`, `keeper this-device persistent-login on`, `keeper this-device timeout 30d`. The timeout is required — Commander's default logs the session out after an idle hour, and `gitlab-client-doctor` run straight after setup would not notice. `keeper this-device` shows the current settings.
- In a sandboxed client, `gl` must be allowed to write Commander's data directory (`~/.keeper`, else `~/.local/share/.keeper`) or the keychain it uses, granted before the first call: a blocked write lets that call succeed and breaks the next sign-in.

Enforce while assembling:

- The command's **stdout must be exactly one line: the token**. No banners, no log lines (`--quiet` flags where the tool has them).
- Record the command **verbatim** — exactly as the user runs it, concrete values and all. Don't invent env-var placeholders.
- Tell the user which scope the token needs: `read_api` for reading, `api` for creating MRs / commenting / retrying jobs / triggering pipelines.

Write `~/.claude/gitlab-client.local.md` (create `~/.claude/` if needed):

```markdown
---
# Generated by gitlab-client-setup. Edits take effect on the next gl call — no restart.
# token_cmd stdout must be exactly one line: the personal access token.
url: <url>
token_cmd: <value>
---

# GitLab notes (for humans — not read by the plugin)

Which instance, which token, who to ping when it breaks.
```

Multi-line `token_cmd` → YAML block scalar (`token_cmd: |` + indented lines).

### 5. Project-level file (optional)
Run `git remote get-url origin` in the project root. If it succeeds and looks like `<host>:group/name.git` or `https://<host>/group/name.git`, tell the user the project will be derived from it and ask the user whether to pin it anyway: *No — derive from the remote* / *Yes — pin `project:`*. A project file can only pin `project` (`gl` ignores `url`/`token_cmd` there); if the user needs another instance for this repo, point them at `GITLAB_CLIENT_URL` + `GITLAB_CLIENT_TOKEN`. If the remote is missing or doesn't look like a GitLab path, recommend pinning and ask for the `group/name` path. If the command can't run at all (`command not found`, exit 127), git isn't installed — say so, recommend pinning (without git, `gl` reads the project file from the current directory, so it should run from the project root), and ask for the `group/name` path.

If writing, create `.claude/gitlab-client.local.md`:

```markdown
---
# Generated by gitlab-client-setup. Only url, token_cmd, project are read.
project: <group/name>
---
```

Then run check 8; if `.gitignore` lacks `.claude/*.local.md`, append it (create `.gitignore` if absent) and say so.

### 6. Finish
Print what was written (paths only — never the token), then: "Run `gitlab-client-doctor` to verify the token, the connection, and the project."
