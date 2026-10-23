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
