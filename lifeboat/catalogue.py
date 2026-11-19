"""What on this laptop is worth backing up: a catalogue of known folders and files plus a fast scan. No GTK."""
from __future__ import annotations

import os
import re
from collections.abc import Iterable
from pathlib import Path

from lifeboat.config import read_ignore

# Display order. "files" holds the standard folders (Documents ...); the rest come from the catalogue below.
GROUPS = {
    "files": "Files",
    "ai": "AI assistants",
    "shell": "Shell & terminal",
    "dev": "Git & dev tools",
    "editors": "Editors",
    "desktop": "Desktop",
    "keys": "Keys & logins",
    "skip": "Not backed up: rebuildable or huge",
    "other": "Other app settings",
}

# Agent folders: chat history, logins, caches and live state stay out of the backup.
AGENT_EXCLUDES = {
    ".claude": ["projects/**/*.jsonl", "projects/**/tool-results/**", "history.jsonl", "file-history/**", "paste-cache/**",
                ".credentials.json*", "backups/**", "sessions/**", "session-env/**", "state/**", "cache/**", "debug/**",
                "logs/**", "shell-snapshots/**", "telemetry/**", "statsig/**", "ide/**", "plugins/cache/**",
                "plugins/marketplaces/**", "security/**", "todos/**"],
    ".codex": ["auth.json", "logs_2.sqlite*", "state_5.sqlite*", "models_cache.json", "sessions/**", "shell_snapshots/**",
               "tmp/**", ".tmp/**"],
    ".gemini": ["oauth_creds.json", "google_accounts.json", "tmp/**"],
    ".agents": [".removed-*"],
}

# (key, label, group, path relative to home, secret). Dirs first, then single files. Groups "ai" are always secret + links.
DIRS = (
    ("claude", "Claude Code", "ai", ".claude", True), ("codex", "Codex", "ai", ".codex", True),
    ("gemini", "Gemini", "ai", ".gemini", True), ("agents", "Agents", "ai", ".agents", True),
    ("cursor", "Cursor", "ai", ".cursor", True), ("cline", "Cline", "ai", ".cline", True),
    ("continue", "Continue", "ai", ".continue", True), ("roo", "Roo", "ai", ".roo", True),
    ("kiro", "Kiro", "ai", ".kiro", True), ("qwen", "Qwen", "ai", ".qwen", True),
    ("copilot", "Copilot", "ai", ".copilot", True), ("opencode", "OpenCode", "ai", ".config/opencode", True),
    ("goose", "Goose", "ai", ".config/goose", True), ("skillshare", "Skillshare", "ai", ".config/skillshare", True),
    ("zsh", "Zsh", "shell", ".config/zsh", True), ("fish", "Fish", "shell", ".config/fish", False),
    ("kitty", "Kitty", "shell", ".config/kitty", False), ("wezterm", "WezTerm", "shell", ".config/wezterm", False),
    ("atuin", "Atuin", "shell", ".config/atuin", True),
    ("git", "Git", "dev", ".config/git", False), ("gh", "GitHub CLI", "dev", ".config/gh", True),
    ("uv", "uv", "dev", ".config/uv", False),
    ("vscode", "VS Code settings", "editors", ".config/Code/User", False), ("nvim", "Neovim", "editors", ".config/nvim", False),
    ("autostart", "Autostart", "desktop", ".config/autostart", False), ("gtk-4.0", "GTK 4", "desktop", ".config/gtk-4.0", False),
    ("gtk-3.0", "GTK 3", "desktop", ".config/gtk-3.0", False),
    ("ssh", "SSH keys", "keys", ".ssh", True), ("gnupg", "GPG keys", "keys", ".gnupg", True),
    ("aws", "AWS", "keys", ".aws", True), ("azure", "Azure", "keys", ".azure", True),
    ("cache", "Caches", "skip", ".cache", False), ("chrome", "Chrome", "skip", ".config/google-chrome", False),
    ("brave", "Brave", "skip", ".config/BraveSoftware", False), ("chromium", "Chromium", "skip", ".config/chromium", False),
    ("slack", "Slack", "skip", ".config/Slack", False), ("discord", "Discord", "skip", ".config/discord", False),
    ("steam", "Steam", "skip", ".local/share/Steam", False),
)
FILES = (
    ("claude-json", "Claude settings file", "ai", ".claude.json", True),
    ("zshrc", ".zshrc", "shell", ".zshrc", False), ("zshenv", ".zshenv", "shell", ".zshenv", False),
    ("p10k", ".p10k.zsh", "shell", ".p10k.zsh", False), ("tmux", ".tmux.conf", "shell", ".tmux.conf", False),
    ("gitconfig", ".gitconfig", "dev", ".gitconfig", False),
    ("npmrc", ".npmrc", "keys", ".npmrc", True),
)


def _item(key, label, group, path, kind, secret, exclude=(), links=False):
    return {"key": key, "label": label, "group": group, "path": str(path), "kind": kind, "secret": secret,
            "default_on": group != "skip" and group != "other", "exclude": list(exclude), "links": links}


LINK_DEPTH = 3


def _pruned(exclude: list[str]) -> set[str]:
    """Top-level folders an exclude list drops whole ('sessions/**'), so the link walk skips them."""
    return {p[:-3] for p in exclude if p.endswith("/**") and not any(c in p[:-3] for c in "*?[/")}


def _symlinks(folder: Path, exclude: list[str]):
    """Symlinks at most LINK_DEPTH levels below folder, never following any and never entering an excluded folder."""
    skip = _pruned(exclude)
    stack = [(folder, 0)]
    while stack:
        d, depth = stack.pop()
        try:
            entries = list(os.scandir(d))
        except OSError:
            continue
        for e in entries:
            if e.is_symlink():
                yield Path(e.path)
            elif e.is_dir(follow_symlinks=False) and depth < LINK_DEPTH - 1 and not (depth == 0 and e.name in skip):
                stack.append((Path(e.path), depth + 1))


def _link_root(target: Path, home: Path) -> Path | None:
    """What to back up for a link target: its git checkout, else its folder. None if that would be home or beyond it."""
    for parent in target.parents:
        if parent == home or home not in parent.parents:
            break
        if (parent / ".git").exists():
            return parent
    root = target.parent
    return root if home in root.parents else None


def _linked_items(home: Path, agents: list[dict]) -> list[dict]:
    covered = [Path(a["path"]).resolve() for a in agents]
    roots: dict[Path, int] = {}
    for a in agents:
        for link in _symlinks(Path(a["path"]), a["exclude"]):
            target = link.resolve()
            if not target.exists() or any(c == target or c in target.parents for c in covered):
                continue
            if (root := _link_root(target, home.resolve())) is not None:
                roots[root] = roots.get(root, 0) + 1
    return [{**_item("linked-" + re.sub(r"[^a-z0-9]+", "-", str(r).lower()).strip("-"), f"Linked: {r}", "ai", r, "dir", True,
                     links=True), "link_count": n} for r, n in sorted(roots.items())]


def scan(home: Path, ignore: Iterable[str] | None = None) -> list[dict]:
    """Every catalogue entry that exists under home, in group order. No size walks: this stays instant.
    Items whose key is in `ignore` (default: [scan] ignore from the config) come back with ignored=True."""
    home = Path(home)
    from lifeboat import setup  # late: setup imports this module
    items = [_item(name.lower(), label, "files", path, "dir", False) for name, label, path in setup.standard_folders(home)]
    for kind, table in (("dir", DIRS), ("file", FILES)):
        for key, label, group, rel, secret in table:
            path = home / rel
            if not (path.is_dir() if kind == "dir" else path.is_file()):
                continue
            agent = group == "ai"
            items.append(_item(key, label, group, path, kind, secret, AGENT_EXCLUDES.get(Path(rel).name, ()) if kind == "dir" else (),
                               links=agent and kind == "dir"))
    items += _linked_items(home, [i for i in items if i["group"] == "ai" and i["kind"] == "dir" and i["links"]])
    covered = [Path(i["path"]) for i in items]
    config = home / ".config"
    for d in sorted(config.iterdir(), key=lambda p: p.name.lower()) if config.is_dir() else []:
        if d.is_dir() and not any(c == d or d in c.parents for c in covered):
            items.append(_item("config-" + d.name.lower(), d.name, "other", d, "dir", False))
    hidden = set(read_ignore() if ignore is None else ignore)
    order = list(GROUPS)
    return sorted(({**i, "ignored": i["key"] in hidden} for i in items), key=lambda i: order.index(i["group"]))
