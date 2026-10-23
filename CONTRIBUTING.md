# Contributing

Lifeboat is early and small. Issues and pull requests are welcome.

**Set up:** Python 3.11+, rclone 1.60+, and for the app PyGObject, GTK 4 and libadwaita.

**Before you push:**

```sh
pip install pytest ruff && ruff check . && pytest -q
```

Tests use rclone's local backend and never touch the network. The GTK smoke test renders on a hidden Broadway
display (`gtk4-broadwayd`) and is skipped if that is missing.

**Rules:**

- Keep `lifeboat/engine.py` and `lifeboat/config.py` free of GTK imports, so they stay testable and scriptable.
- In the app, follow libadwaita guidelines: no deprecated widgets, symbolic icons, sentence case, never block the main loop.
- Write commit messages as [Conventional Commits](https://www.conventionalcommits.org/) (`feat:`, `fix:`, `docs:`).
- Keep personal values out of code. User-specific settings belong in the config file.

**Maintainer notes:**

- The Google OAuth client in `lifeboat/setup.py` is stored rclone-obscured (as rclone ships its own). Never change or
  rotate it: drive.file only sees files created by that client ID, so a new one orphans every user's backup. Plain text
  would be blocked by GitHub push protection and could be auto-revoked.
- Releases: merge the release-please PR; `release.yml` attaches the tarball, `SHA256SUMS` and an attestation, and Packit
  builds the RPM into the `xixas/lifeboat` COPR. Only `feat:`, `fix:` and `deps:` commits trigger a release.
- Run the app against a throwaway setup without touching your real one: set `HOME`, the `XDG_*` dirs,
  `LIFEBOAT_DRIVE_ROOT=Lifeboat-test` and `LIFEBOAT_APP_ID=io.github.xixas.LifeboatE2E` (a separate GApplication
  instance; otherwise launching only raises an already-open Lifeboat window).
