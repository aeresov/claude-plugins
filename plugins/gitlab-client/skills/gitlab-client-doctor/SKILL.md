---
name: gitlab-client-doctor
description: "Diagnose the gitlab-client setup — python3, settings files, token_cmd resolves, instance reachable and its version, token scopes and expiry, project resolves from the git remote, .gitignore — and report what is missing. Runs your token_cmd (inside gl) and makes three read-only API calls."
---

Resolve `../..` from this SKILL.md's directory to the absolute plugin root. Substitute that path for `<plugin-root>` in the instructions and commands below; do not assume a plugin-root shell variable is set. Keep the working directory at the consuming project root.

In Codex, select `$gitlab-client:gitlab-client-setup` or `$gitlab-client:gitlab-client-doctor`. In Claude Code, the equivalent commands are `/gitlab-client:setup` and `/gitlab-client:doctor`.

You are running `gitlab-client-doctor`: a health check. Write no files, dispatch no agent, make no write calls. Checks 1–3 and 8 are static. Checks 4–7 are **live** — they run `<plugin-root>/scripts/gl`, which runs the user's `token_cmd` internally and calls `GET /metadata`, `GET /personal_access_tokens/self`, and `GET /projects/:path`. Those calls need network access to the instance, and `token_cmd` may need the user's keychain or home directory: follow the host client's approval policy for them (each may prompt once — that is expected). The settings files are inspected with silent `sed -n` + `grep` probes (see the checklist) so a `token_cmd` never enters the transcript; the only file you read is `.gitignore`.

## Steps

1. Read the shared checklist at `<plugin-root>/setup-checklist.md` — it defines checks 1–8 with exact commands and remediation text.

2. Run checks **1–8 in order** from the project root:
   - 1 `python3` ≥ 3.9
   - 2 user settings file present (`n/a` when `GITLAB_CLIENT_URL` + `GITLAB_CLIENT_TOKEN` are both exported)
   - 3 settings valid (SKIP if 2 failed; `n/a` when env-only — then 4–7 still run)
   - 4 `token_cmd` resolves — live (SKIP if 3 failed)
   - 5 instance reachable + version — live (SKIP if 4 failed; WARN on a non-15.x version)
   - 6 token scopes and expiry — live (SKIP if 5 failed; WARN on read-only or expiring tokens)
   - 7 project resolves — live (SKIP if 5 failed; `n/a` outside a GitLab clone; WARN when git isn't installed)
   - 8 `.gitignore` covers the project settings file (`n/a` if there is no project-level file)

   **Never print a token.** `gl` doesn't either; if you ever see one in output, stop and tell the user their `token_cmd` is misconfigured (it should print only to stdout, and `gl` swallows stdout).

3. Print one line per check:
   ```
   PASS  1. python3 3.9.25
   PASS  2. user settings file present
   PASS  3. settings valid
   PASS  4. token_cmd resolves
   PASS  5. instance reachable — GitLab 15.2.5 (CE)
   WARN  6. token scopes — read_api only (writes will 403); expires 2026-09-03
         → <remediation text from the checklist, verbatim>
   PASS  7. project resolves — platform/api (from remote:origin)
   n/a   8. .gitignore — no project-level settings file
   ```

4. End with a one-line summary:
   - All PASS/WARN/n-a → `OK — gitlab-client ready · <host> · GitLab <version> · scopes <list> · project <path or "pass --project">` (append ` · <n> warning(s) above` when any WARN).
   - Settings absent → `Not configured — run gitlab-client-setup`.
   - Otherwise → `<n> issue(s) — see the FAIL lines above`.

Keep it terse. Don't fix anything here — point at `gitlab-client-setup` only where the checklist's remediation text already does.
