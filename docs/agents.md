# Let an AI assistant manage your backups

Lifeboat's command line is built so an AI coding assistant (Claude Code, Codex, and similar) can discover your
setup, add or remove what gets backed up, run and verify backups, and restore files. You stay in charge: every
change can be previewed first. One thing to know: `lifeboat run` is a sync, so DEST is made to mirror SOURCE, and anything
else already in DEST is moved to the old folder and deleted later by `prune`. DEST must therefore be a new, empty folder or this
job's own earlier backup.

## The contract

| Command | Purpose | `--json` shape |
|---|---|---|
| `lifeboat config` | Is the config file there and valid? | `{"path", "exists", "ok", "error", "jobs"}` |
| `lifeboat init` | Write an example config (refuses if one exists) | no |
| `lifeboat remotes` | rclone remotes and their types | `{"remotes": [{"name": "gdrive:", "type": "drive"}]}` |
| `lifeboat jobs` | Configured jobs | `{"config_path", "jobs": [{name, source, dest, old, exclude, include, filter_file, encrypted}]}` |
| `lifeboat status` | Everything Lifeboat knows, with a verdict | the full status document |
| `lifeboat add-job NAME SOURCE DEST [--old] [--exclude]... [--include]... [--filter-file] [--encrypted] [--existing-dest] [--dry-run]` | Add a job to config.toml | `{"added": {...job}, "config_path", "dry_run"}` |
| `lifeboat remove-job NAME [--dry-run]` | Remove a job from config.toml | `{"removed", "config_path", "dry_run"}` |
| `lifeboat run [--dry-run]` | Back up every job | the run result plus `"settings_ok"` |
| `lifeboat versions JOB` | Old versions of a job | `{"job", "versions": [...]}` |
| `lifeboat restore JOB --to DIR [--version STAMP] [--dry-run]` | Copy files back into `DIR` | `{"ok", "rc", "job", "to", "version", "dry_run"}` |
| `lifeboat check` | Problems the timer would warn about | `{"problems": [...]}` (never sends a notification; exit 1 if the list is not empty) |
| `lifeboat prune` | Delete old versions past `keep_days` | the prune result |

- With `--json`, stdout is exactly one JSON document. Progress and rclone output go to stderr.
- Exit codes: `0` ok, `1` the operation ran and failed (or something is not ok), `2` config or usage error (the message is on stderr).
- `--dry-run` shows what would happen and changes nothing. `add-job` and `remove-job` edit `config.toml` as text
  (comments survive), validate the result before writing, write atomically, and keep the old file as `config.toml.bak`.
- `add-job` and `remove-job` only edit the config; they copy and delete nothing. `add-job` refuses a missing source folder,
  an unknown remote, a non-empty DEST (pass `--existing-dest` only when DEST holds this job's own earlier Lifeboat backup),
  a duplicate name, and bad combinations (include with exclude, `old` equal to `dest`). If the config file is a symlink, the edit is written to its target.

## Recipes

**Add a folder to the backup**

1. `lifeboat remotes --json`: pick a remote (or ask the user which one).
2. `lifeboat add-job NAME ~/Folder REMOTE:Backup/Folder --exclude 'node_modules/**' --dry-run`: show the user the block.
3. Same command without `--dry-run`.
4. `lifeboat run --dry-run`: check what rclone would copy.
5. `lifeboat run`, then `lifeboat status --json`: the verdict should be "Fully covered".

**Stop backing something up**

`lifeboat remove-job NAME --dry-run`, then without `--dry-run`. The backup copy and old versions stay on the remote
at `DEST` and `OLD/<timestamp>/NAME` until the user deletes them by hand.

**Verify a restore works**

```sh
lifeboat versions NAME --json
lifeboat restore NAME --to "$(mktemp -d)" --dry-run --json
```

A real restore into a fresh temporary folder is also safe; compare it with the source, then delete the folder.

**Check health**

`lifeboat check --json` lists problems (empty list = healthy). `lifeboat status --json` has the details: last run,
per-job results, missing rclone remotes, timers.

## Safety rules for assistants

1. Dry run first (`--dry-run`) for `add-job`, `remove-job`, `run` and `restore`, and show the user the result.
2. Show the user the job block before you add it.
3. Never restore into a live folder (the job's source, `~/Documents`, and so on) without asking. Restore into a new folder.
4. Secrets and keys (`~/.ssh`, `~/.gnupg`, tokens, password stores) go only into a job on a crypt remote. Check with `lifeboat remotes` that the remote's type is `crypt`, and use `--encrypted`.
5. Never set `settings.command` or `rclone.password_command` in the config unless the user asks: both run as shell commands.
6. Point DEST at a new, empty folder (or this job's own earlier backup): `run` mirrors SOURCE onto DEST. Never pass `--existing-dest` for a folder that holds anything else. Never delete remote data (`rclone purge`, `rclone delete`) and do not run `lifeboat prune` unless the user asks.
7. Do not edit `config.toml` by hand when `add-job` or `remove-job` can do it.

## Example prompts

- "Back up my ~/Projects folder to my Google Drive with Lifeboat, skipping node_modules and .venv. Show me the job first."
- "Add ~/.ssh to a backup, but only on an encrypted remote."
- "Is my laptop backup healthy? If not, tell me what is wrong."
- "Stop backing up the `photos` job. Don't delete anything on the drive."
- "Check I could restore `documents`: do a dry run into a temp folder and tell me what it would copy."
- "List my backup jobs and tell me what is not covered."

## Install the skill

The Lifeboat Agent Skill teaches an assistant these commands and rules. With no network, copy `skills/lifeboat/` to
`~/.claude/skills/lifeboat/`. Or use the skills CLI:

```sh
npx skills add xixas/lifeboat
```
