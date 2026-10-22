# Lifeboat: usage guide

Lifeboat answers one question: **is this laptop safe to lose?** It shows the state of your backup and lets you run
it and restore from it. The app is optional: once the timer is installed, the backup runs whether Lifeboat is open
or not.

Open it from the app grid ("Lifeboat") or run `lifeboat`.

## How the backup works

You describe backup sets in `~/.config/lifeboat/config.toml`. Each `[[jobs]]` entry copies one folder to one rclone
location (`dest`). Anything a run deletes or overwrites is moved to `<old>/<timestamp>/<job name>/` and kept for
`keep_days` (default 90). If `dest` is on a crypt remote, files are encrypted before upload.

An optional `[settings]` command (for example a script that snapshots your dotfiles) runs before the jobs.

**Schedule** (after `lifeboat install-timer`, systemd user timers):
- `lifeboat.timer`: daily at `daily_at` (or at the next login if the laptop was off), then `lifeboat check`, which
  notifies you only on failure.
- `lifeboat-prune.timer`: weekly, deletes old-version folders past `keep_days`.

**Not backed up:** anything not inside a job's `source`, and files matched by a job's `exclude` or filter file.

## The pages

| Page | What it shows | Buttons |
|---|---|---|
| Overview | One verdict: Fully / Partly / Not covered, and the weakest part | Back up now |
| Backups | Last and next run, each job's result, the old-versions safety net | Back up now, Dry run |
| Restore | Pick a job, "Latest" or an old version, and a target folder | Restore |
| Settings snapshot | Only if `[settings].command` is set: last result | Run now |
| Skills | Only if `~/.claude/skills` exists (or `claude_skills = "on"`): your own vs externally installed Claude Code skills | Update external skills |
| How it works | Short explanation | |
| Preferences | Config file path, daily timer status | Open config file, Install/Remove daily timer |

Status marks: check = fine, warning = needs attention, cross = failed.

## Restoring

In the app: Restore page. On the command line:

```sh
lifeboat versions documents                       # list old versions (timestamps)
lifeboat restore documents --to ~/restored        # the current backup
lifeboat restore documents --to ~/restored --version 2026-01-31_130000
lifeboat restore documents --to ~/restored --dry-run
```

Restore copies files into the folder you give; it never deletes anything and never touches your live folders unless
you point `--to` at one.

## If the laptop dies

1. Install your distro, then Lifeboat and rclone (see the README).
2. Recreate your rclone remotes with the same names (`rclone config`); keep the crypt passwords somewhere safe
   outside this laptop, such as a password manager and on paper.
3. Put your `config.toml` back, or write a new one pointing at the same `dest` paths.
4. `lifeboat restore JOB --to ~/restored` for each job.

## Command line

| Command | Does |
|---|---|
| `lifeboat` | Open the app |
| `lifeboat run [--dry-run]` | One backup pass |
| `lifeboat prune` | Delete old versions past `keep_days` |
| `lifeboat status [--json]` | Everything the app knows (`--dump` is an alias for `status --json`) |
| `lifeboat check [--dry-run]` | Notify about problems (dry run: just print) |
| `lifeboat versions JOB` | List old versions |
| `lifeboat restore JOB --to DIR ...` | Restore |
| `lifeboat install-timer` / `uninstall-timer` | Manage the systemd timers |

Logs and last results: `~/.local/state/lifeboat/` (`last-run.json`, `prune.json`, `settings.json`, `run.log`).

Debug: `LIFEBOAT_CYCLE=1` visits every page and quits; add `LIFEBOAT_SNAP=DIR` to save a PNG of each.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Backup failed, "token expired" / auth error | `rclone config reconnect gdrive:` (replace with your remote) |
| Failed with a keyring/password error | Make sure `password_command` works in a timer context (session unlocked), then run again |
| "rclone remote not configured" | Create the remote with `rclone config`, using the name in your config |
| App shows old data after an update | Close it fully (`pkill -f "python3 -m lifeboat"`) and reopen |

---
Lifeboat 0.2.0 · (c) 2026 Vikas Kyatannawar ([xixas](https://github.com/xixas)) · MIT License
