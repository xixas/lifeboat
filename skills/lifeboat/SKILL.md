---
name: lifeboat
description: Back up and restore a Linux laptop with Lifeboat (rclone based). Use when the user wants to back up a folder, add or remove something from the backup, run or verify a backup, restore files, or check whether their backup is healthy.
---

# Lifeboat

Lifeboat backs up folders to any rclone remote with the `lifeboat` command. Add `--json` for one JSON document on
stdout (progress goes to stderr). Exit codes: 0 ok, 1 failed or not ok, 2 config or usage error.

## Commands

- Discover: `lifeboat config --json`, `remotes --json`, `jobs --json`, `status --json`
- No config yet: `lifeboat init`. Its example job and `gdrive:` remotes are placeholders, so do not `run` yet. `add-job` the user's folder first, then `remove-job documents` (the last job cannot be removed).
- Add: `lifeboat add-job NAME SOURCE DEST [--exclude PAT]... [--encrypted] --dry-run`, then again without `--dry-run`
- Remove: `lifeboat remove-job NAME --dry-run`, then without
- Run and verify: `lifeboat run --dry-run`, `lifeboat run --json`, `lifeboat check --json` (exit 1 if there are problems)
- Restore: `lifeboat versions JOB --json`, then `lifeboat restore JOB --to DIR [--version STAMP] --dry-run`

To add a folder: `remotes`, `add-job --dry-run` (show the user the block), `add-job`, `run --dry-run`, `run`, `status`.

## Safety rules

- Dry run first. Show the user the job block before adding it.
- `run` is a sync: DEST mirrors SOURCE, and other files there go to the old folder, then `prune` deletes them. DEST must be a new, empty folder or this job's own earlier backup. Use `--existing-dest` for nothing else.
- Restore into a new folder, never a live folder without asking.
- Put secrets (`~/.ssh`, `~/.gnupg`, tokens) only in a job on a crypt remote (`lifeboat remotes` shows the type).
- Do not set `settings.command` or `rclone.password_command` unless asked. They run shell commands.
- Never delete remote data. `remove-job` keeps it. Do not run `prune` unless asked.

More detail and example prompts: `docs/agents.md` in the Lifeboat repository.
