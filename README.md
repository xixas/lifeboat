# Lifeboat

Lifeboat backs up a Linux laptop to Google Drive (or any [rclone](https://rclone.org) remote) and answers one
question: **is this laptop safe to lose?** It is a small GTK 4 + libadwaita app plus a command line, driven by one
config file. Anything a backup replaces or deletes is moved aside into a timestamped folder, so you can restore
old versions too.

![Overview](docs/screenshots/overview.png)

<!-- screenshot placeholder: add docs/screenshots/overview.png -->

**Status: early, personal-scale.** It is used daily by its author on one laptop. Expect rough edges; there is no
setup wizard yet (you edit a TOML file).

## Install

Needs Python 3.11+, rclone 1.60+, and the system packages for the app (PyGObject, GTK 4, libadwaita):

| Distro | Command |
|---|---|
| Fedora / Nobara | `sudo dnf install python3-gobject gtk4 libadwaita rclone` |
| Debian / Ubuntu | `sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 rclone` |
| Arch | `sudo pacman -S python-gobject gtk4 libadwaita rclone` |

```sh
git clone https://github.com/xixas/lifeboat && cd lifeboat
./install.sh            # user-level, no root; ./install.sh --uninstall to remove
```

The installer copies the package to `~/.local/share/lifeboat/lib`, adds `~/.local/bin/lifeboat`, a desktop entry,
a D-Bus service and the icon. It never touches your config or timers. Make sure `~/.local/bin` is on your `PATH`.

## Configure

1. Create an rclone remote for your storage: `rclone config` (e.g. a Google Drive remote named `gdrive`). For
   encrypted backups add a `crypt` remote layered on it (e.g. `gdrive-crypt`).
2. Run `lifeboat` and press **Create example config**, or copy [`data/config.example.toml`](data/config.example.toml)
   to `~/.config/lifeboat/config.toml` and edit it: one `[[jobs]]` block per folder you want backed up.
3. `lifeboat run --dry-run`, then `lifeboat run`.
4. `lifeboat install-timer` to back up daily.

## Command line

| Command | Does |
|---|---|
| `lifeboat` | Open the app |
| `lifeboat run [--dry-run]` | Settings command (if any), then every job |
| `lifeboat prune` | Delete old versions past `keep_days` |
| `lifeboat status [--json]` | Show the current state |
| `lifeboat check [--dry-run]` | Notify about problems (dry run: just print) |
| `lifeboat versions JOB` | List old versions of a job |
| `lifeboat restore JOB --to DIR [--version STAMP] [--dry-run]` | Copy the current backup or an old version into `DIR` |
| `lifeboat install-timer` / `uninstall-timer` | Daily backup + weekly prune as systemd user timers |
| `lifeboat --version` | Print the version |

## How it works

Each job runs `rclone sync SOURCE DEST --backup-dir OLD/<timestamp>/<job>`. The destination mirrors the source;
files that were changed or removed are moved into the timestamp folder instead of being lost. `prune` deletes
timestamp folders older than `keep_days`, judged by the folder name (not file age). `restore` uses `rclone copy`
into a folder you choose, so it never overwrites your live files. State (`last-run.json`, `prune.json`,
`settings.json`, `run.log`) lives in `$XDG_STATE_HOME/lifeboat` (default `~/.local/state/lifeboat`).

See [docs/usage.md](docs/usage.md) for the full guide.

## Tests

`pip install pytest ruff && pytest -q` (no network; uses rclone's local backend).

## License

MIT, (c) 2026 Vikas Kyatannawar. See [LICENSE](LICENSE).
