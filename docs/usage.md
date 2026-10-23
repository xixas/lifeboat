# Usage guide

For someone who has installed Lifeboat (see the [README](../README.md)). Open the app from the app grid, or run
`lifeboat`. The app is optional: once the timer is installed, backups run whether it is open or not.

## Read this first

- **`run` is a sync.** DEST is made to mirror SOURCE. Anything else in DEST is moved to the old folder and deleted
  later by `prune`. DEST must be a new, empty folder, or this job's own earlier backup.
- **Replaced and deleted files are kept** in `<old>/<timestamp>/<job>` for `keep_days` (default 90). `prune` then
  deletes them. `prune` judges by the timestamp in the folder name, not by file age.
- **Restore only copies.** It never deletes anything.
- **Secrets go only in a job on a crypt remote** (rclone encrypts before upload): `~/.ssh`, `~/.gnupg`, tokens. Keep the
  passphrase in a password manager and on paper; without it the backup is unreadable.
- **`settings.command` and `rclone.password_command` run shell commands** from your config. Only put commands you trust there.

## Set up jobs

A job copies one folder to one rclone location. Add jobs with the CLI. It keeps your comments, validates before
writing and saves the old file as `config.toml.bak`.

```sh
lifeboat add-job NAME SOURCE DEST [--exclude PAT]... --dry-run   # preview the job
lifeboat add-job NAME SOURCE DEST [--exclude PAT]...             # write it
```

`add-job` only edits the config. It copies nothing. It refuses a missing source, an unknown remote, a non-empty DEST,
a duplicate name, and `include` mixed with `exclude`. Pass `--existing-dest` only when DEST holds this job's own earlier backup.

You can also edit `~/.config/lifeboat/config.toml` by hand. [`data/config.example.toml`](../data/config.example.toml)
documents every key.

| Key | Meaning |
|---|---|
| `[[jobs]]` `name`, `source`, `dest`, `old` | One backup set. Letters, digits, `.`, `_`, `-` in names. `old` is required when editing by hand; `add-job` fills in `_old` next to `dest` |
| `exclude` / `include` | rclone patterns. Do not combine them |
| `filter_file` | An rclone `--filter-from` file, relative to the config folder. Use it for long rule lists |
| `encrypted` | Display only. Encryption comes from the remote |
| `[schedule]` `daily_at`, `keep_days` | Daily run time (default 13:00) and how long old versions live (default 90) |
| `[rclone]` `config`, `password_command` | A custom `rclone.conf`, and a command that prints its password if it is encrypted |
| `[settings]` `command` | Runs before the jobs. Good for a dotfiles snapshot |
| `[modules]` `claude_skills` | `auto`, `on` or `off`: show the Skills page |

Dotfiles example, with [chezmoi](https://www.chezmoi.io/) and a private git repo:

```toml
[settings]
command = "chezmoi re-add && chezmoi git -- add -A && chezmoi git -- commit -qm 'snapshot' ; chezmoi git -- push -q"
```

To remove a job: `lifeboat remove-job NAME`. The backup and old versions stay on the remote until you delete them by hand.
`remove-job` refuses to remove the last job.

## Schedule

`lifeboat install-timer` creates two systemd user timers. `lifeboat uninstall-timer` removes them.

| Timer | When | Does |
|---|---|---|
| `lifeboat.timer` | Daily at `daily_at`, up to 10 minutes late. Runs at the next start if the laptop was off | `lifeboat run`, then `lifeboat check`, which notifies only on problems |
| `lifeboat-prune.timer` | Weekly | `lifeboat prune` |

## Restore

```sh
lifeboat versions documents                        # list old versions
lifeboat restore documents --to ~/restored         # current backup
lifeboat restore documents --to ~/restored --version 2026-01-31_130000
lifeboat restore documents --to ~/restored --dry-run
```

Restore into a new folder. Files already in `--to` can be overwritten, and a live folder is only touched if you
point `--to` at it. In the app, use the Restore page.

**If the laptop dies:**

1. Install your distro, then Lifeboat and rclone.
2. Recreate your rclone remotes with the same names (`rclone config`), using the crypt passphrase from your password manager or paper.
3. Put `config.toml` back, or write a new one with the same `dest` paths.
4. Run `lifeboat restore JOB --to ~/restored` for each job.

## Commands

| Command | Does |
|---|---|
| `lifeboat` | Open the app |
| `lifeboat init` / `config` / `remotes` / `jobs` | Write an example config / check it (exit 2 if bad) / list rclone remotes / list jobs |
| `lifeboat add-job`, `remove-job` | Edit jobs (see above). `add-job` also takes `--old`, `--include`, `--filter-file`, `--encrypted` |
| `lifeboat run` | Settings command, then every job. Also `--dry-run`, `--settings-only`, `--no-settings` |
| `lifeboat status` / `check [--dry-run]` | Verdict and per-job results / notify about problems (`--dry-run` prints instead) |
| `lifeboat versions`, `restore`, `prune` | See Restore above; delete old versions past `keep_days` |
| `lifeboat install-timer` / `uninstall-timer` | Manage the systemd timers |

Most commands take `--json`: stdout is then one JSON document and other output goes to stderr. `check --json` never
notifies and exits 1 if there are problems. Exit codes: 0 ok, 1 failed or not ok, 2 config or usage error.
Assistants: see [agents.md](agents.md). `lifeboat COMMAND --help` lists every flag.

## The app

Pages: Overview (one verdict: Fully / Partly / Not covered), Backups (**Back up now**, **Dry run**), Restore,
Settings snapshot (only if `[settings].command` is set), Skills (only if `~/.claude/skills` exists, or
`claude_skills = "on"`), How it works, Preferences (config path, timer, **Open config file**).

State lives in `~/.local/state/lifeboat/` (`last-run.json`, `prune.json`, `settings.json`, `run.log`).
Debug: `LIFEBOAT_CYCLE=1 lifeboat` visits every page and quits; add `LIFEBOAT_SNAP=DIR` to save a PNG of each.

## Tips

- **Back up** `~/Documents`, `~/Projects`, `~/Pictures`, `~/.ssh`, `~/.gnupg` and app data you cannot recreate. **Skip**
  `~/.cache`, `node_modules`, `.venv`, `target/`, Steam libraries and Flatpak caches.
- **One job per folder.** One failing job (an unmounted drive, a bad pattern) then does not stop the others.
- **Test a restore monthly:** `lifeboat restore JOB --to "$(mktemp -d)" --dry-run`. See what the timer would warn
  about: `lifeboat check --dry-run`.

## Troubleshooting

| Symptom | Fix |
|---|---|
| "token expired" or auth error | `rclone config reconnect gdrive:` (use your remote name) |
| Keyring or password error | Make sure `password_command` works from a timer (session unlocked), then run again |
| "rclone remote not configured" | Create the remote with `rclone config`, using the name in your config |
| App shows old data after an update | Close it fully (`pkill -f "python3 -m lifeboat"`) and reopen |
