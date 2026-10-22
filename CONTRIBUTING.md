# Contributing

Lifeboat is early and small. Issues and pull requests are welcome.

- Needs Python 3.11+, rclone 1.60+, and (for the app) PyGObject, GTK 4 and libadwaita.
- Run the checks before you push: `pip install pytest ruff && ruff check . && pytest -q`.
  Tests use rclone's local backend; they never touch the network. The GTK smoke test renders into a hidden
  Broadway display (`gtk4-broadwayd`) and is skipped if that is missing.
- Keep the engine (`lifeboat/engine.py`, `config.py`) free of GTK imports so it stays testable and scriptable.
- Follow libadwaita guidelines in the app: no deprecated widgets, symbolic icons, sentence case, never block the main loop.
- Commit messages use [Conventional Commits](https://www.conventionalcommits.org/) (`feat:`, `fix:`, `docs:` ...).
- No personal values in code: everything user-specific belongs in the config file.
