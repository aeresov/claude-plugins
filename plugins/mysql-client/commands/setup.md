---
description: Configure mysql-client connection discovery for this project — record the command that prints the database's connection URL, write .claude/mysql-client.local.md, and add it to .gitignore. Read-only against the host and database; runs nothing privileged and never connects.
allowed-tools: Bash(mysql --version), Bash(test -f *), Bash(grep *), Read, Glob, Write, Edit, AskUserQuestion
---

Read and follow `${CLAUDE_PLUGIN_ROOT}/skills/mysql-client-setup/SKILL.md`. Resolve `<plugin-root>` there to `${CLAUDE_PLUGIN_ROOT}`.
