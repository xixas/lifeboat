# Lifeboat

Back up a Linux laptop to Google Drive or any [rclone](https://rclone.org) remote, and see at a glance whether the
laptop is safe to lose. A GTK 4 app plus a command line, driven by one config file.

Any folder can go to any remote. Files a backup replaces or deletes are kept in timestamped folders, so you can
restore old versions.

**Status: early, personal-scale.** The author uses it daily on one laptop. Expect rough edges. There is no setup
wizard: you edit a TOML file.

## Install

Needs Python 3.11+, rclone 1.60+, PyGObject, GTK 4 and libadwaita.

| Distro | Install the dependencies |
|---|---|
| Fedora / Nobara | `sudo dnf install python3-gobject gtk4 libadwaita rclone` |
| Debian / Ubuntu | `sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 rclone` |
| Arch | `sudo pacman -S python-gobject gtk4 libadwaita rclone` |

```sh
git clone https://github.com/xixas/lifeboat && cd lifeboat
./install.sh
```

The installer works at user level (no root). It adds `~/.local/bin/lifeboat`, a desktop entry, a D-Bus service and
the icon. It never touches your config or timers. Make sure `~/.local/bin` is on your `PATH`.

Update: `git pull && ./install.sh`. Remove: `./install.sh --uninstall`.

## First backup

1. Create an rclone remote: `rclone config` (for example a Google Drive remote named `gdrive`).
2. Write a config: `lifeboat init`, or press **Create example config** in the app.
3. Replace the example job with your own. The remotes in the example are placeholders.
   ```sh
   lifeboat add-job projects ~/Projects gdrive:Backup/Projects --exclude 'node_modules/**'
   lifeboat remove-job documents
   ```
4. Preview, then back up: `lifeboat run --dry-run`, then `lifeboat run`.
5. Back up every day: `lifeboat install-timer`.

**Before step 4, know this:**

- `run` is a sync. DEST becomes a mirror of SOURCE. Use a new, empty folder as DEST, or the job's own earlier backup.
- `settings.command` and `rclone.password_command` in the config run as shell commands. Only put commands you trust there.
- Put secrets (`~/.ssh`, `~/.gnupg`, tokens) only in a job on a crypt remote, and keep the crypt passphrase off the laptop.

Details are in the [usage guide](docs/usage.md).

## Use it with an AI assistant

Every command has `--json` output and a `--dry-run` preview. An assistant such as Claude Code or Codex can add
folders, run backups and check health for you. Read [docs/agents.md](docs/agents.md) for the commands and safety rules.

Install the skill with either:

- `cp -r skills/lifeboat ~/.claude/skills/`
- `npx skills add xixas/lifeboat`

## More

- [Usage guide](docs/usage.md): config, commands, restore, tips, troubleshooting.
- [Contributing](CONTRIBUTING.md): tests and conventions.

## Releases

Each [release](https://github.com/xixas/lifeboat/releases) has a source tarball, `SHA256SUMS` and a build
provenance attestation (proof that GitHub Actions built the tarball from that tag). To install a release instead of `main`:

```sh
gh release download -R xixas/lifeboat -p 'lifeboat-*.tar.gz' -p SHA256SUMS    # latest release, uploaded files only
sha256sum -c SHA256SUMS                                          # the download is intact
gh attestation verify lifeboat-*.tar.gz --repo xixas/lifeboat    # optional: it was built from this repo
tar xzf lifeboat-*.tar.gz && cd lifeboat-*/ && ./install.sh
```

## License

MIT, (c) 2026 Vikas Kyatannawar. See [LICENSE](LICENSE).
