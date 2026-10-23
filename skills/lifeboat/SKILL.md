---
name: lifeboat
description: Back up and restore a Linux laptop with Lifeboat (rclone based). Use when the user wants to back up a folder, add or remove something from the backup, run or verify a backup, restore files, or check whether their backup is healthy.
---

# Lifeboat

Lifeboat backs up folders to any rclone remote. Everything is done with the `lifeboat` command. Add `--json` for
machine-readable output (one JSON document on stdout, progress on stderr). Exit codes: 0 ok, 1 failed or not ok, 2 config or usage error.

## Commands

- Discover: `lifeboat config --json`, `lifeboat remotes --json`, `lifeboat jobs --json`, `lifeboat status --json`
- No config yet: `lifeboat init`, then `add-job` your own folder FIRST, then `remove-job documents` (it refuses to remove the last job). The example remotes (`gdrive:...`) are placeholders: do not `run` until the example job is replaced
- Add: `lifeboat add-job NAME SOURCE DEST [--exclude PAT]... [--encrypted] --dry-run`, then again without `--dry-run`
- Remove: `lifeboat remove-job NAME --dry-run`, then without
- Run and verify: `lifeboat run --dry-run`, `lifeboat run --json`, `lifeboat check --json` (exit 1 if there are problems)
- Restore: `lifeboat versions JOB --json`, `lifeboat restore JOB --to DIR [--version STAMP] --dry-run`

## Add a folder

`remotes` -> `add-job --dry-run` (show the user the block) -> `add-job` -> `run --dry-run` -> `run` -> `status`.

## Safety rules

- Dry run first; show the user the job block before adding it.
- Never restore into a live folder without asking; restore into a new folder.
- Secrets and keys (`~/.ssh`, `~/.gnupg`, tokens) only into a job on a crypt remote (`lifeboat remotes` shows the type).
- Never set `settings.command` or `rclone.password_command` unless the user asks (they run shell commands).
- `run` is a sync: DEST becomes a mirror of SOURCE and other files there are moved to the old folder, then deleted by `prune`. DEST must be a new, empty folder or this job's own earlier backup; never use `--existing-dest` for anything else.
- Never delete remote data; `remove-job` keeps it. Do not run `prune` unless asked.

More detail and example prompts: `docs/agents.md` in the Lifeboat repository.
