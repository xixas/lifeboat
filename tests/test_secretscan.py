import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from lifeboat import secretscan

AWS = "AKIA" + "IOSFODNN7EXAMPLE"
GH = "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"


@pytest.fixture(autouse=True)
def no_gitleaks(monkeypatch):
    monkeypatch.setattr(secretscan.shutil, "which", lambda name: None)


def git(cwd, *a, **kw):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", str(cwd), *a], check=True, capture_output=True, text=True, **kw).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    (r / "README.md").write_text("hello\n")
    git(r, "add", "-A")
    git(r, "commit", "-qm", "init")
    return r


def commit(repo, files):
    for name, text in files.items():
        p = repo / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "c")
    return git(repo, "rev-parse", "HEAD")


@pytest.mark.parametrize("text,rule", [
    (f"key = {AWS}", "aws-access-key"),
    (f"t {GH}", "github-token"),
    ("github_pat_" + "A" * 30, "github-token"),
    ("xoxb-" + "1234567890-abcdefghij", "slack-token"),
    ("-----BEGIN " + "OPENSSH PRIVATE KEY-----", "private-key"),
    ("AIza" + "A" * 35, "google-api-key"),
    ("sk-ant-" + "a" * 30, "anthropic-key"),
    ("sk-" + "b" * 30, "openai-key"),
    ("eyJhbGciOiJIUzI1NiJ9." + "eyJzdWIiOiIxMjM0NTY3ODkwIn0." + "abcDEF123_-", "jwt"),
    ('password: "hunter2hunter2hunter2"', "generic-secret"),
])
def test_rules_fire(repo, text, rule):
    base = git(repo, "rev-parse", "HEAD")
    head = commit(repo, {"notes.txt": f"line\n{text}\n"})
    found = secretscan.scan_range(repo, base, head)
    assert [(f["file"], f["line"], f["rule"]) for f in found] == [("notes.txt", 2, rule)]
    assert text not in str(found)  # findings never echo the secret


def test_clean_commit_passes(repo):
    base = git(repo, "rev-parse", "HEAD")
    head = commit(repo, {"skills/a/SKILL.md": "# A skill\nUse the token from the keyring.\napi key goes in env\n"})
    assert secretscan.scan_range(repo, base, head) == []


def test_only_added_lines_count(repo):
    old = commit(repo, {"a.txt": f"{AWS}\n"})
    head = commit(repo, {"a.txt": f"{AWS}\nsafe\n"})
    assert secretscan.scan_range(repo, old, head) == []


def test_token_in_skill_md_and_aws_blocked(repo):
    base = git(repo, "rev-parse", "HEAD")
    head = commit(repo, {"skills/x/SKILL.md": f"export GITHUB_TOKEN={GH}\n", "aws.txt": AWS + "\n"})
    assert {f["file"] for f in secretscan.scan_range(repo, base, head)} == {"skills/x/SKILL.md", "aws.txt"}


def test_no_base_scans_every_tracked_file(repo):
    head = commit(repo, {"deep/dir/f.txt": f"{AWS}\n"})
    assert [f["file"] for f in secretscan.scan_range(repo, None, head)] == ["deep/dir/f.txt"]


@pytest.mark.parametrize("name", [".env", ".env.local", "id_rsa", "id_rsa.pub", "server.pem", "k.key", "aws-credentials.json", "AuthKey.p8", "c.p12"])
def test_risky_file_names(repo, name):
    base = git(repo, "rev-parse", "HEAD")
    head = commit(repo, {f"x/{name}": "harmless\n"})
    found = secretscan.scan_range(repo, base, head)
    assert [(f["file"], f["rule"]) for f in found] == [(f"x/{name}", "risky-file-name")]


def test_gitleaks_findings_are_merged(repo, tmp_path, monkeypatch):
    fake = tmp_path / "bin" / "gitleaks"
    fake.parent.mkdir()
    fake.write_text('#!/bin/sh\nargs="$*"\nfor a; do case $a in --report-path=*) out=${a#--report-path=};; esac; done\n'
                    'echo "$args" > "$0.args"\n'
                    'echo \'[{"File":"deep.txt","StartLine":4,"RuleID":"custom-rule","Secret":"s3cret"}]\' > "$out"\nexit 0\n')
    fake.chmod(0o755)
    monkeypatch.setattr(secretscan.shutil, "which", lambda name: str(fake))
    base = git(repo, "rev-parse", "HEAD")
    head = commit(repo, {"a.txt": "fine\n"})
    found = secretscan.scan_range(repo, base, head)
    assert found == [{"file": "deep.txt", "line": 4, "rule": "custom-rule", "source": "gitleaks"}]
    assert f"--log-opts={base}..{head}" in Path(str(fake) + ".args").read_text()


def install(repo):
    secretscan.install_pre_push_hook(repo)
    return repo / ".git" / "hooks" / "pre-push"


_SHIM = Path(tempfile.mkdtemp(prefix="lifeboat-shim-"))
(_SHIM / "lifeboat").write_text(f'#!/bin/sh\nPYTHONPATH="{Path(secretscan.__file__).parent.parent}" exec {sys.executable} -m lifeboat "$@"\n')
(_SHIM / "lifeboat").chmod(0o755)


_BIN = Path(tempfile.mkdtemp(prefix="lifeboat-bin-"))  # just what git and the hook need, so there is no other `lifeboat` on PATH
for _tool in ("git", "sh", "cat", "dirname"):
    (_BIN / _tool).symlink_to(shutil.which(_tool))


def push_env(on_path=True):
    """git's environment with a `lifeboat` on PATH (or without one), as it is where Lifeboat is installed."""
    return dict(os.environ, PATH=os.pathsep.join(([str(_SHIM)] if on_path else []) + [str(_BIN)]), PYTHONPATH="")


def test_hook_blocks_secret_push_and_allows_clean(repo, tmp_path):
    bare = tmp_path / "r.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
    git(repo, "remote", "add", "origin", str(bare))
    install(repo)
    git(repo, "push", "-q", "origin", "main", env=push_env())  # first push: whole tree is clean
    commit(repo, {"SKILL.md": f"token {GH}\n"})
    r = subprocess.run(["git", "-C", str(repo), "push", "origin", "main"], capture_output=True, text=True, env=push_env())
    assert r.returncode != 0 and "SKILL.md" in r.stderr and GH not in r.stderr
    assert git(bare, "rev-list", "--count", "main") == "1"
    git(repo, "reset", "-q", "--hard", "HEAD~1")
    commit(repo, {"ok.txt": "fine\n"})
    git(repo, "push", "-q", "origin", "main", env=push_env())
    assert git(bare, "rev-list", "--count", "main") == "2"


def test_hook_chains_existing_and_is_idempotent(repo):
    hook = repo / ".git" / "hooks" / "pre-push"
    hook.parent.mkdir(exist_ok=True)
    hook.write_text("#!/bin/sh\necho chained-ran >&2\nexit 7\n")
    hook.chmod(0o755)
    install(repo)
    install(repo)
    chained = repo / ".git" / "hooks" / "pre-push.lifeboat-chained"
    assert chained.read_text().endswith("exit 7\n")
    assert hook.read_text().count(secretscan.HOOK_MARK) == 1
    r = subprocess.run([str(hook), "origin", "x"], input="", capture_output=True, text=True, cwd=repo, env=push_env())
    assert r.returncode == 7 and "chained-ran" in r.stderr


def test_cli_pre_push_exit_codes(repo):
    base = git(repo, "rev-parse", "HEAD")
    head = commit(repo, {"a.txt": AWS + "\n"})
    line = f"refs/heads/main {head} refs/heads/main {base}\n"
    r = subprocess.run(["lifeboat", "secret-scan", "--pre-push"], input=line, capture_output=True, text=True, cwd=repo, env=push_env())
    assert r.returncode == 1


def test_working_tree_scan_covers_modified_and_untracked(repo):
    (repo / "README.md").write_text(f"hello\n{AWS}\n")
    (repo / "new").mkdir()
    (repo / "new" / "SKILL.md").write_text(f"token {GH}\n")
    (repo / ".env").write_text("A=1\n")
    found = secretscan.scan_working_tree(repo)
    assert {(f["file"], f["rule"]) for f in found} == {("README.md", "aws-access-key"), ("new/SKILL.md", "github-token"), (".env", "risky-file-name")}
    git(repo, "checkout", "--", "README.md")
    assert {f["file"] for f in secretscan.scan_working_tree(repo)} == {"new/SKILL.md", ".env"}


def test_a_secret_added_and_removed_in_later_commits_is_still_found(repo):
    """A adds a key, B removes it: the net diff is clean, but A would be pushed."""
    base = git(repo, "rev-parse", "HEAD")
    commit(repo, {"skills/x/SKILL.md": f"key {AWS}\n"})
    a = git(repo, "rev-parse", "HEAD")
    commit(repo, {"skills/x/SKILL.md": "clean\n"})
    head = git(repo, "rev-parse", "HEAD")
    found = secretscan.scan_range(repo, base, head)
    assert [(f["file"], f["line"], f["rule"], f["commit"]) for f in found] == [("skills/x/SKILL.md", 1, "aws-access-key", a[:7])]
    assert AWS not in str(found)


def test_a_risky_file_added_then_deleted_is_still_found(repo):
    base = git(repo, "rev-parse", "HEAD")
    commit(repo, {".env": "harmless\n"})
    git(repo, "rm", "-q", ".env")
    git(repo, "commit", "-qm", "rm")
    found = secretscan.scan_range(repo, base, "HEAD")
    assert [(f["file"], f["rule"]) for f in found] == [(".env", "risky-file-name")]


# ---------------------------------------------------------------- the hook must never brick pushes
def _remote(repo, tmp_path):
    bare = tmp_path / "r.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
    git(repo, "remote", "add", "origin", str(bare))
    return bare


def test_the_hook_does_not_pin_a_python_path_to_a_checkout(repo):
    text = install(repo).read_text()
    assert "PYTHONPATH" not in text and "python3 -m" not in text and "lifeboat secret-scan --pre-push" in text


def test_the_hook_fails_open_with_a_warning_when_lifeboat_is_not_installed(repo, tmp_path):
    bare = _remote(repo, tmp_path)
    install(repo)
    commit(repo, {"SKILL.md": f"token {GH}\n"})
    r = subprocess.run(["git", "-C", str(repo), "push", "origin", "main"], capture_output=True, text=True, env=push_env(on_path=False))
    assert r.returncode == 0 and "lifeboat" in r.stderr and "not found" in r.stderr
    assert git(bare, "rev-list", "--count", "main") == "2"


def test_the_hook_still_blocks_when_lifeboat_is_on_path(repo, tmp_path):
    _remote(repo, tmp_path)
    install(repo)
    commit(repo, {"SKILL.md": f"token {GH}\n"})
    r = subprocess.run(["git", "-C", str(repo), "push", "origin", "main"], capture_output=True, text=True, env=push_env())
    assert r.returncode != 0 and "SKILL.md" in r.stderr


# ---------------------------------------------------------------- fewer false positives
@pytest.mark.parametrize("text,flagged", [
    ("token: github_installation_token", False),
    ("api_key = my-long-kebab-case-name", False),
    ("password: " + "a" * 20, False),          # letters only
    ("secret: 1234567890123456", False),        # digits only
    ("token: a8F3k2" + "Zq9Lm4Xp7R", True),     # 16+ chars with a digit and a letter
    ('password: "hunter2hunter2hunter2"', True),
])
def test_generic_secret_needs_a_digit_and_a_letter(repo, text, flagged):
    base = git(repo, "rev-parse", "HEAD")
    head = commit(repo, {"n.md": text + "\n"})
    assert bool(secretscan.scan_range(repo, base, head)) is flagged


def test_the_allowlist_silences_a_rule_in_a_file_or_a_line(repo):
    base = git(repo, "rev-parse", "HEAD")
    head = commit(repo, {"docs/a.md": f"x\n{AWS}\n", "docs/b.md": f"{AWS}\n{AWS}\n"})
    assert len(secretscan.scan_range(repo, base, head)) == 3
    (repo / ".lifeboat-secrets-allow").write_text("# example keys\naws-access-key:docs/a.md\naws-access-key:docs/b.md:2\n\n")
    assert [(f["file"], f["line"]) for f in secretscan.scan_range(repo, base, head)] == [("docs/b.md", 1)]
    assert secretscan.scan_working_tree(repo) == []


def test_the_hook_goes_where_core_hookspath_points_repo_level(repo, tmp_path):
    hooks = tmp_path / "my-hooks"
    hooks.mkdir()
    (hooks / "pre-push").write_text("#!/bin/sh\necho theirs >&2\nexit 0\n")
    (hooks / "pre-push").chmod(0o755)
    git(repo, "config", "core.hooksPath", str(hooks))
    hook = secretscan.install_pre_push_hook(repo)
    assert hook == hooks / "pre-push" and secretscan.HOOK_MARK in hook.read_text()
    assert "theirs" in (hooks / "pre-push.lifeboat-chained").read_text()  # their hook still runs
    assert not (repo / ".git" / "hooks" / "pre-push").exists()
    _remote(repo, tmp_path)
    commit(repo, {"SKILL.md": f"token {GH}\n"})
    r = subprocess.run(["git", "-C", str(repo), "push", "origin", "main"], capture_output=True, text=True, env=push_env())
    assert r.returncode != 0 and "SKILL.md" in r.stderr


def _global_hooks(tmp_path, monkeypatch):
    hooks = tmp_path / "global-hooks"
    hooks.mkdir()
    for name, body in (("pre-push", "echo global-pre-push-ran >&2\nexit 0"), ("commit-msg", "echo global-commit-msg-ran >&2\nexit 0")):
        (hooks / name).write_text(f"#!/bin/sh\n{body}\n")
        (hooks / name).chmod(0o755)
    gitconfig = tmp_path / "gitconfig"
    gitconfig.write_text(f"[core]\n\thooksPath = {hooks}\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(gitconfig))
    return hooks, gitconfig


def test_a_global_core_hookspath_is_never_written_to(repo, tmp_path, monkeypatch):
    hooks, gitconfig = _global_hooks(tmp_path, monkeypatch)
    before = {p.name: p.read_text() for p in hooks.iterdir()}
    hook = secretscan.install_pre_push_hook(repo)
    assert {p.name: p.read_text() for p in hooks.iterdir()} == before  # not one file added or changed there
    assert hook == repo / ".git" / "lifeboat-hooks" / "pre-push" and secretscan.HOOK_MARK in hook.read_text()
    assert git(repo, "config", "--local", "core.hooksPath") == str(hook.parent)
    assert "lifeboat" not in gitconfig.read_text()


def test_the_repo_hooks_dir_chains_to_the_global_hooks(repo, tmp_path, monkeypatch):
    _global_hooks(tmp_path, monkeypatch)
    _remote(repo, tmp_path)
    secretscan.install_pre_push_hook(repo)
    clean = subprocess.run(["git", "-C", str(repo), "push", "origin", "main"], capture_output=True, text=True, env=push_env())
    assert clean.returncode == 0 and "global-pre-push-ran" in clean.stderr
    (repo / "m.txt").write_text("x\n")
    git(repo, "add", "-A")
    r = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", str(repo), "commit", "-qm", "m"], capture_output=True, text=True, env=push_env())
    assert r.returncode == 0 and "global-commit-msg-ran" in r.stderr  # the other global hooks keep working
    commit(repo, {"SKILL.md": f"token {GH}\n"})
    blocked = subprocess.run(["git", "-C", str(repo), "push", "origin", "main"], capture_output=True, text=True, env=push_env())
    assert blocked.returncode != 0 and "SKILL.md" in blocked.stderr


def test_a_failing_global_pre_push_still_blocks(repo, tmp_path, monkeypatch):
    hooks, _ = _global_hooks(tmp_path, monkeypatch)
    (hooks / "pre-push").write_text("#!/bin/sh\nexit 9\n")
    _remote(repo, tmp_path)
    secretscan.install_pre_push_hook(repo)
    r = subprocess.run(["git", "-C", str(repo), "push", "origin", "main"], capture_output=True, text=True, env=push_env())
    assert r.returncode != 0


def test_a_relative_hookspath_is_relative_to_the_repo(repo):
    git(repo, "config", "core.hooksPath", ".githooks")
    assert secretscan.install_pre_push_hook(repo) == repo / ".githooks" / "pre-push"


# ---------------------------------------------------------------- only a clean scan lets a push through
def _fake_lifeboat(tmp_path, scan_exit, probe="lifeboat-secret-scan-1"):
    d = tmp_path / "fake-bin"
    d.mkdir(exist_ok=True)
    (d / "lifeboat").write_text(f'#!/bin/sh\nfor a in "$@"; do [ "$a" = --probe ] && {{ echo "{probe}"; exit 0; }}; done\nexit {scan_exit}\n')
    (d / "lifeboat").chmod(0o755)
    return dict(os.environ, PATH=os.pathsep.join([str(d), str(_BIN)]), PYTHONPATH="")


def _run_hook(repo, env):
    return subprocess.run([str(install(repo)), "origin", "x"], input="", capture_output=True, text=True, cwd=repo, env=env)


@pytest.mark.parametrize("code,allowed", [(0, True), (1, False), (2, False), (3, False), (126, False), (137, False)])
def test_only_exit_0_from_the_real_scan_allows_the_push(repo, tmp_path, code, allowed):
    r = _run_hook(repo, _fake_lifeboat(tmp_path, code))
    assert (r.returncode == 0) is allowed


def test_a_lifeboat_without_the_probe_is_treated_as_absent(repo, tmp_path):
    r = _run_hook(repo, _fake_lifeboat(tmp_path, 2, probe="nope"))  # 0.4.0: no secret-scan subcommand, exits 2
    assert r.returncode == 0 and "lifeboat" in r.stderr


def test_no_lifeboat_on_path_warns_and_allows(repo):
    r = _run_hook(repo, push_env(on_path=False))
    assert r.returncode == 0 and "not found" in r.stderr


def test_the_probe_flag_prints_the_marker():
    r = subprocess.run(["lifeboat", "secret-scan", "--probe"], capture_output=True, text=True, env=push_env())
    assert r.returncode == 0 and r.stdout.strip() == "lifeboat-secret-scan-1"


def test_installing_in_a_folder_that_is_not_a_repo_raises_and_writes_nothing(tmp_path, monkeypatch):
    plain, cwd = tmp_path / "plain", tmp_path / "cwd"
    plain.mkdir()
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    with pytest.raises(RuntimeError, match="not a git repository"):
        secretscan.install_pre_push_hook(plain)
    assert list(cwd.iterdir()) == [] and list(plain.iterdir()) == []
