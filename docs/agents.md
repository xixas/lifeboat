# Use Lifeboat with an AI assistant

For an AI assistant (Claude Code, Codex and similar) and the person who asks it to manage backups. The CLI is
built for this: `--json` output, `--dry-run` previews, stable exit codes.

**One fact to know:** `lifeboat run` is a sync. DEST is made to mirror SOURCE. Anything else in DEST is moved to the
old folder and deleted later by `prune`. DEST must be a new, empty folder, or this job's own earlier backup.

Install the skill (copy `skills/lifeboat` to `~/.claude/skills/`, or `npx skills add xixas/lifeboat`) so the assistant knows this page.

## Contract

| Command | Purpose | `--json` shape |
|---|---|---|
| `lifeboat config` | Is the config there and valid? | `{path, exists, ok, error, jobs}` |
| `lifeboat init` | Write an example config. Refuses if one exists | none |
| `lifeboat remotes` | rclone remotes and types | `{remotes: [{name: "gdrive:", type: "drive"}]}` |
| `lifeboat jobs` | Configured jobs | `{config_path, jobs: [{name, source, dest, old, exclude, include, filter_file, encrypted, links}]}` |
| `lifeboat status` | Everything known, with a verdict | the full status document |
| `lifeboat add-job NAME SOURCE DEST [--old] [--exclude]... [--include]... [--filter-file] [--encrypted] [--links] [--existing-dest] [--dry-run]` | Add a job | `{added: {...job}, config_path, dry_run}` |
| `lifeboat remove-job NAME [--dry-run]` | Remove a job | `{removed, config_path, dry_run}` |
| `lifeboat run [--dry-run]` | Back up every job | the run result plus `settings_ok` |
| `lifeboat versions JOB` | Old versions, newest first | `{job, versions: [...]}` |
| `lifeboat restore JOB --to DIR [--version STAMP] [--dry-run]` | Copy files into `DIR` | `{ok, rc, job, to, version, dry_run}` |
| `lifeboat check` | Problems the timer would warn about | `{problems: [...]}`. Never notifies. Exit 1 if not empty |
| `lifeboat check --ack-dropped` | Clear the "No longer backed up" reminder (`status --json` has `dropped`) | none |
| `lifeboat scan` | What on this laptop could be backed up | `{items: [{key, label, group, path, kind, secret, default_on, exclude, links, ignored, link_count?}]}` |
| `lifeboat ignore KEY` / `unignore KEY [--dry-run]` | Hide or unhide a scan item (`[scan] ignore`) | `{ignored or unignored, config_path, dry_run}` |
| `lifeboat prune` | Delete old versions past `keep_days` | the prune result |
| `lifeboat store-push` | Scan and push the config store (the skills repo) | `{time, ok, pushed, blocked_findings: [{file, line, rule, commit?}], error, signed_out, left_alone?}` (`left_alone`: changed files outside skill folders, never committed). Exit 1 if not ok |

- **JSON:** with `--json`, stdout is exactly one JSON document. Progress and rclone output go to stderr.
- **Exit codes:** `0` ok. `1` the operation ran and failed, or something is not ok. `2` config or usage error (message on stderr).
- **`--dry-run`** shows what would happen and changes nothing.
- **`add-job` and `remove-job`** only edit `config.toml`. They copy and delete nothing. They keep comments, validate before
  writing, write atomically and save the old file as `config.toml.bak`.
- **`add-job` refuses** a missing source, an unknown remote, a non-empty DEST, a duplicate name, `include` with `exclude`,
  and `old` equal to `dest`. Pass `--existing-dest` only when DEST holds this job's own earlier Lifeboat backup.
- **`--encrypted`** is a display flag. Encryption comes from the remote.
- **`--links`** (job key `links`) stores symlinks as links instead of following them. Use it for agent folders such as
  `~/.claude`, whose skills are symlinks into a git checkout.
- **`scan`** groups what it finds. Items with `secret: true` belong only on a crypt remote. A `Linked: <path>` item is the
  checkout or folder that an agent folder's symlinks point to, with `link_count` links into it; it needs its own job or
  those links would restore dangling.

## Recipes

**Add a folder**

1. `lifeboat remotes --json`. Pick a remote, or ask the user.
2. `lifeboat add-job NAME ~/Folder REMOTE:Backup/Folder --exclude 'node_modules/**' --dry-run`. Show the user the job block.
3. Run the same command without `--dry-run`.
4. `lifeboat run --dry-run`. Check what rclone would copy.
5. `lifeboat run`, then `lifeboat status --json`. The verdict should be "Fully covered".

**No config yet**

Run `lifeboat init`. The example job and its `gdrive:` remotes are placeholders. Do not `run` yet.
`add-job` the user's own folder first, then `lifeboat remove-job documents` (it refuses to remove the last job).

**Stop backing something up**

`lifeboat remove-job NAME --dry-run`, then without `--dry-run`. The backup and old versions stay on the remote at
`DEST` and `OLD/<timestamp>/NAME` until the user deletes them by hand.

**Verify a restore**

```sh
lifeboat versions NAME --json
lifeboat restore NAME --to "$(mktemp -d)" --dry-run --json
```

A real restore into a fresh temporary folder is also safe. Compare it with the source, then delete the folder.

**Find what is not backed up**

`lifeboat scan --json`, then compare each item's `path` with `lifeboat jobs --json`. Offer the user the missing items, and
`lifeboat ignore KEY` for any they never want. Agent folders (`group: "ai"`) come with an `exclude` list that leaves out chat
history, logins and caches: pass it to `add-job` as `--exclude` and add `--links --encrypted`.

**Check health**

`lifeboat check --json`: an empty `problems` list means healthy. `lifeboat status --json` has the details: last run,
per-job results, missing rclone remotes, timers.

## Safety rules

1. Use `--dry-run` first for `add-job`, `remove-job`, `run` and `restore`. Show the user the result and the job block.
2. Never restore into a live folder (a job's source, `~/Documents`) without asking. Restore into a new folder.
3. Put secrets and keys (`~/.ssh`, `~/.gnupg`, tokens, password stores) only in a job on a crypt remote. Check that
   `lifeboat remotes` shows type `crypt`, and pass `--encrypted`.
4. Never set `settings.command` or `rclone.password_command` unless the user asks. Both run shell commands.
5. Never delete remote data (`rclone purge`, `rclone delete`). Do not run `lifeboat prune` unless the user asks.
6. Use `add-job` and `remove-job` instead of editing `config.toml` by hand.

## Config store

The config store is a git repo of skills pushed to GitHub. Set it up in the app (Config store page): it installs pinned, hash-checked
skillshare and gitleaks, adopts or creates the repo and signs in to GitHub. There is no CLI setup.

- `lifeboat store-push` scans, then pushes with Lifeboat's own GitHub token. `blocked_findings` lists file, line, rule and commit,
  never the secret. Fix the file (move the value to the keyring or an environment variable); do not allowlist a real secret.
- The scanner is Lifeboat's pinned gitleaks plus Lifeboat's own rules, over every commit not yet pushed by Lifeboat, the index and
  the working tree. If gitleaks is missing or fails, the push fails closed with "Secret scanner unavailable". Never install a
  gitleaks on `PATH` to work around it; rerun the app's store setup.
- Never push the store by other means (`skillshare push`, `git push` with other credentials), edit the pre-push hook, or add
  `.lifeboat-secrets-allow` entries without the user's say-so.
- Never pushed: any flagged content, and files such as `.env`, `id_rsa*`, `*.pem`, `*.key`, `*credentials*`.

## Example prompts

- "Back up my ~/Projects folder to my Google Drive with Lifeboat, skipping node_modules and .venv. Show me the job first."
- "Add ~/.ssh to a backup, but only on an encrypted remote."
- "Is my laptop backup healthy? If not, tell me what is wrong."
- "Stop backing up the `photos` job. Don't delete anything on the drive."
- "Check I could restore `documents`: do a dry run into a temp folder and tell me what it would copy."
- "List my backup jobs and tell me what is not covered."
