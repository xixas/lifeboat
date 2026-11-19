import os
import shutil
import subprocess

import pytest

from lifeboat import catalogue


def mk(home, *rels):
    for rel in rels:
        (home / rel).mkdir(parents=True, exist_ok=True)


def by_key(items):
    return {i["key"]: i for i in items}


def test_scan_lists_existing_seed_entries_in_group_order(fake_home):
    mk(fake_home, "Documents", ".claude", ".config/zsh", ".config/git", ".config/nvim", ".config/autostart", ".ssh", ".cache")
    (fake_home / ".zshrc").write_text("x")
    items = catalogue.scan(fake_home)
    groups = [i["group"] for i in items]
    order = list(catalogue.GROUPS)
    assert groups == sorted(groups, key=order.index)
    got = by_key(items)
    assert got["documents"]["group"] == "files" and got["documents"]["secret"] is False
    claude = got["claude"]
    assert (claude["group"], claude["kind"], claude["secret"], claude["links"], claude["default_on"]) == ("ai", "dir", True, True, True)
    assert claude["path"] == str(fake_home / ".claude")
    assert claude["label"] == "Claude Code"
    assert "/history.jsonl" in claude["exclude"] and "/.credentials.json*" in claude["exclude"]
    assert got["zshrc"]["kind"] == "file" and got["zshrc"]["path"] == str(fake_home / ".zshrc") and got["zshrc"]["group"] == "shell"
    assert got["ssh"]["group"] == "keys" and got["ssh"]["secret"] is True
    assert got["cache"]["group"] == "skip" and got["cache"]["default_on"] is False
    assert "gemini" not in got  # not on this laptop
    assert set(items[0]) >= {"key", "label", "group", "path", "kind", "secret", "default_on", "exclude", "links"}


def test_uncovered_config_dirs_become_off_by_default_other_items(fake_home):
    mk(fake_home, ".config/Code/User", ".config/git", ".config/inkscape", ".config/htop")
    (fake_home / ".config" / "stray.conf").write_text("x")  # only directories count
    got = by_key(catalogue.scan(fake_home))
    assert "vscode" in got and "git" in got
    assert "config-code" not in got  # ~/.config/Code is covered by the VS Code entry beneath it
    ink = got["config-inkscape"]
    assert (ink["group"], ink["default_on"], ink["kind"], ink["label"], ink["path"]) == (
        "other", False, "dir", "inkscape", str(fake_home / ".config" / "inkscape"))
    assert "config-htop" in got and "config-stray.conf" not in got and "config-git" not in got


def test_symlinks_leaving_an_agent_folder_become_linked_roots(fake_home):
    repo = fake_home / "work" / "skills-repo"
    mk(fake_home, repo / ".git", repo / "a", repo / "b")
    loose = fake_home / "notes" / "prompts"
    mk(fake_home, loose)
    (loose / "p.md").write_text("p")
    agents = fake_home / ".agents"
    mk(fake_home, agents / "inside", ".claude/skills", ".claude/cache")
    os.symlink(repo / "a", fake_home / ".claude" / "skills" / "a")
    os.symlink(repo / "b", agents / "b")  # a second agent folder pointing into the same checkout: one root
    os.symlink(loose / "p.md", fake_home / ".claude" / "skills" / "p.md")
    os.symlink(agents / "inside", fake_home / ".claude" / "skills" / "inside")  # lands in a folder that is backed up anyway
    os.symlink(fake_home / "gone", fake_home / ".claude" / "skills" / "dangling")  # nothing to back up there
    os.symlink(repo / "a", fake_home / ".claude" / "cache" / "a")  # excluded folders are not walked
    linked = [i for i in catalogue.scan(fake_home) if i["label"].startswith("Linked: ")]
    by_path = {i["path"]: i for i in linked}
    assert set(by_path) == {str(repo), str(loose)}
    git = by_path[str(repo)]
    assert git["label"] == f"Linked: {repo.name}" and git["link_count"] == 2 and git["kind"] == "dir"
    assert (git["group"], git["secret"], git["default_on"], git["links"]) == ("ai", True, True, True)
    assert by_path[str(loose)]["link_count"] == 1
    assert len({i["key"] for i in linked}) == 2


def test_ignored_items_are_marked_not_dropped(fake_home):
    mk(fake_home, ".claude", ".config/inkscape", "Documents")
    got = by_key(catalogue.scan(fake_home, ignore=["claude", "config-inkscape"]))
    assert got["claude"]["ignored"] is True and got["config-inkscape"]["ignored"] is True
    assert got["documents"]["ignored"] is False


def test_scan_reads_the_ignore_list_from_the_config_by_default(fake_home):
    mk(fake_home, ".claude", ".config/lifeboat")
    (fake_home / ".config" / "lifeboat" / "config.toml").write_text(
        '[scan]\nignore = ["claude"]\n[[jobs]]\nname = "a"\nsource = "~/a"\ndest = "r:a"\nold = "r:_old"\n')
    assert by_key(catalogue.scan(fake_home))["claude"]["ignored"] is True
    (fake_home / ".config" / "lifeboat" / "config.toml").write_text("not toml [")
    assert by_key(catalogue.scan(fake_home))["claude"]["ignored"] is False  # a broken config never breaks the scan


@pytest.mark.skipif(not shutil.which("rclone"), reason="rclone needed to apply the patterns")
def test_agent_excludes_only_match_at_the_top_of_the_folder(fake_home):
    """A skill called debug, logs or state must still be backed up; the agent's own debug/ folder must not."""
    base = fake_home / ".claude"
    for rel in ("skills/debug/SKILL.md", "skills/logs/SKILL.md", "plugins/state/x.md", "debug/log.txt", "history.jsonl",
                "projects/a/chat.jsonl", "skills/mine/notes.jsonl", "settings.json"):
        (base / rel).parent.mkdir(parents=True, exist_ok=True)
        (base / rel).write_text("x")
    item = next(i for i in catalogue.scan(fake_home) if i["key"] == "claude")
    args = [a for pat in item["exclude"] for a in ("--exclude", pat)]
    out = subprocess.run(["rclone", "lsf", "-R", "--files-only", str(base), *args], capture_output=True, text=True, check=True).stdout
    assert sorted(out.split()) == ["plugins/state/x.md", "settings.json", "skills/debug/SKILL.md", "skills/logs/SKILL.md",
                                   "skills/mine/notes.jsonl"]


def test_vscode_is_encrypted_and_leaves_out_extension_state_and_history(fake_home):
    mk(fake_home, ".config/Code/User", ".cursor", ".continue")
    got = by_key(catalogue.scan(fake_home))
    assert got["vscode"]["secret"] is True and got["vscode"]["default_on"] is True
    assert {"/workspaceStorage/**", "/globalStorage/**", "/History/**"} <= set(got["vscode"]["exclude"])
    assert "/extensions/**" in got["cursor"]["exclude"]
    assert {"/index/**", "/sessions/**", "/dev_data/**"} <= set(got["continue"]["exclude"])
