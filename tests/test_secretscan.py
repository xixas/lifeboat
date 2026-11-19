import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from lifeboat import secretscan, store_install

AWS = "AKIA" + "IOSFODNN7EXAMPLE"
GH = "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"


_REAL_CMD = getattr(secretscan, "_lifeboat_cmd", None)


@pytest.fixture(autouse=True)
def no_gitleaks(monkeypatch):
    monkeypatch.setattr(secretscan.shutil, "which", lambda name: None)
    monkeypatch.setattr(store_install, "gitleaks_path", lambda: None)
    monkeypatch.setattr(secretscan, "_lifeboat_cmd", lambda: ["/nonexistent/bin/lifeboat"])  # no `lifeboat` of this machine's is found by absolute path


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


def test_a_changed_file_is_scanned_whole_and_an_already_pushed_commit_is_not(repo):
    old = commit(repo, {"a.txt": f"{AWS}\n"})
    head = commit(repo, {"a.txt": f"{AWS}\nsafe\n"})
    assert [(f["file"], f["line"], f["commit"]) for f in secretscan.scan_range(repo, old, head)] == [("a.txt", 1, head[:7])]
    git(repo, "update-ref", "refs/remotes/origin/main", head)  # on the remote already
    assert secretscan.scan_range(repo, None, head) == []


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


def test_gitleaks_findings_are_merged_with_lifeboats_own(repo, fake_gitleaks):
    fake_gitleaks(git=[{"File": "deep.txt", "StartLine": 4, "RuleID": "custom-rule", "Secret": "REDACTED", "Commit": "abcdef0123456789"}])
    base = git(repo, "rev-parse", "HEAD")
    head = commit(repo, {"a.txt": f"{AWS}\n"})
    found = secretscan.scan_range(repo, base, head)
    assert {(f["file"], f["line"], f["rule"], f["source"], f.get("commit")) for f in found} == {
        ("deep.txt", 4, "custom-rule", "gitleaks", "abcdef0"), ("a.txt", 1, "aws-access-key", "lifeboat", head[:7])}
    call = next(ln for ln in fake_gitleaks.log().splitlines() if ln.startswith("git|"))
    for want in ("--no-banner", "--report-format json", "--report-path ", "--redact", "--ignore-gitleaks-allow",
                 f"--log-opts={head} --not --remotes {base}"):  # ignore-allow: a repo's own comments cannot switch rules off
        assert want in call, call


def test_gitleaks_uses_lifeboats_config_not_the_repos(repo, fake_gitleaks):
    fake_gitleaks()
    (repo / ".gitleaks.toml").write_text("[allowlist]\npaths = ['.*']\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "cfg")
    secretscan.scan_range(repo, None, "HEAD")
    call = next(ln for ln in fake_gitleaks.log().splitlines() if ln.startswith("git|"))
    assert " -c " in call or " --config" in call
    assert str(repo / ".gitleaks.toml") not in call


def test_the_index_and_working_tree_are_exported_and_scanned_by_gitleaks(repo, fake_gitleaks):
    fake_gitleaks(dir=[{"File": "index/staged.txt", "StartLine": 2, "RuleID": "generic-api-key", "Secret": "REDACTED"},
                       {"File": "./work/new.txt", "StartLine": 1, "RuleID": "generic-api-key", "Secret": "REDACTED"}])
    (repo / "staged.txt").write_text("a\nb\n")
    git(repo, "add", "staged.txt")
    (repo / "new.txt").write_text("fresh\n")
    found = secretscan.scan_working_tree(repo)
    assert {(f["file"], f["line"], f["rule"], f["source"]) for f in found} == {
        ("staged.txt", 2, "generic-api-key", "gitleaks"), ("new.txt", 1, "generic-api-key", "gitleaks")}
    log = fake_gitleaks.log()
    assert "FILE ./index/staged.txt" in log and "FILE ./work/new.txt" in log
    assert "--redact" in log


def test_a_missing_gitleaks_fails_closed_when_asked_to(repo):
    with pytest.raises(secretscan.ScannerUnavailable):
        secretscan.scan_working_tree(repo, strict=True)
    base = git(repo, "rev-parse", "HEAD")
    head = commit(repo, {"a.txt": "fine\n"})
    with pytest.raises(secretscan.ScannerUnavailable):
        secretscan.scan_range(repo, base, head, strict=True)
    assert secretscan.scan_range(repo, base, head) == []  # the hook's layer keeps Lifeboat's own rules


def test_a_gitleaks_that_the_path_offers_is_never_used(repo, tmp_path, monkeypatch):
    other = tmp_path / "pathbin"
    other.mkdir()
    (other / "gitleaks").write_text("#!/bin/sh\nexit 0\n")
    (other / "gitleaks").chmod(0o755)
    monkeypatch.setenv("PATH", f"{other}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setattr(secretscan.shutil, "which", lambda name: str(other / name))
    with pytest.raises(secretscan.ScannerUnavailable):
        secretscan.scan_working_tree(repo, strict=True)


@pytest.mark.parametrize("how", ["exit-2", "bad-json", "no-report"])
def test_a_gitleaks_that_fails_to_run_fails_closed(repo, fake_gitleaks, how):
    exe = fake_gitleaks()
    if how == "exit-2":
        fake_gitleaks(rc=2)
    elif how == "bad-json":
        Path(f"{exe}.dir.json").write_text("not json")
        Path(f"{exe}.git.json").write_text("not json")
    else:
        exe.write_text("#!/bin/sh\nexit 0\n")
    with pytest.raises(secretscan.ScannerUnavailable):
        secretscan.scan_working_tree(repo, strict=True)
    base = git(repo, "rev-parse", "HEAD")
    head = commit(repo, {"a.txt": "fine\n"})
    with pytest.raises(secretscan.ScannerUnavailable):
        secretscan.scan_range(repo, base, head, strict=True)


MCP = ('{"mcpServers": {"joplin": {"env": {"JOPLIN_TOKEN": "9f8a3c1d5e7b2a4c6e8f0a1b3c5d7e9f2a4b6c8d0e1f3a5b7c9d1e3f5a7b9c1d"}},'
       ' "x": {"api_key": "Zk3Jq9XvT2mP7LwN4RbY8sD1fHgC6aUe"}}}\n')


@pytest.mark.parametrize("name,text,rule", [
    ("mcp.json", MCP, "generic-secret"),
    ("mcp.json", '{"env": {"JOPLIN_TOKEN": "9f8a3c1d5e7b2a4c6e8f0a1b3c5d7e9f2a4b6c8d"}}\n', "generic-secret"),
    ("mcp.json", '{"api_key": "Zk3Jq9XvT2mP7LwN4RbY8sD1fHgC6aUe"}\n', "generic-secret"),
    ("h.txt", "Authorization: Bearer q8Zk3Jv9XtT2mP7LwN4RbY8sD1fHgC6aUeQ5\n", "bearer-token"),
    ("h.txt", 'curl -H "Authorization: Bearer q8Zk3Jv9XtT2mP7LwN4RbY8sD1fHgC6aUeQ5"\n', "bearer-token"),
])
def test_lifeboats_own_rules_catch_json_keys_and_bearer_headers(repo, name, text, rule):
    base = git(repo, "rev-parse", "HEAD")
    head = commit(repo, {name: text})
    found = secretscan.scan_range(repo, base, head)
    assert found and {f["rule"] for f in found} == {rule}
    assert "9f8a3c1d" not in str(found) and "q8Zk3Jv9" not in str(found)


def test_a_bearer_word_in_prose_is_not_a_secret(repo):
    base = git(repo, "rev-parse", "HEAD")
    head = commit(repo, {"d.md": "Send an Authorization: Bearer <token> header.\n"})
    assert secretscan.scan_range(repo, base, head) == []


@pytest.mark.skipif(not os.environ.get("LIFEBOAT_TEST_GITLEAKS"), reason="set LIFEBOAT_TEST_GITLEAKS to a gitleaks binary to run this offline check")
def test_the_real_gitleaks_finds_a_token_json_and_never_reports_the_secret(repo, monkeypatch):
    monkeypatch.setattr(store_install, "gitleaks_path", lambda: Path(os.environ["LIFEBOAT_TEST_GITLEAKS"]))
    head = commit(repo, {"mcp.json": MCP})
    found = secretscan.scan_range(repo, None, head, strict=True)
    assert any(f["source"] == "gitleaks" and f["file"] == "mcp.json" for f in found)
    assert "9f8a3c1d" not in str(found)


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
    assert "PYTHONPATH" not in text and "secret-scan --pre-push" in text


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


# ---------------------------------------------------------------- scan gaps
def test_a_secret_in_a_non_ascii_file_name_is_found_by_name(repo):
    base = git(repo, "rev-parse", "HEAD")
    head = commit(repo, {"nötes.md": f"line\n{AWS}\n", "clé.pem": "x\n"})
    found = secretscan.scan_range(repo, base, head)
    assert {(f["file"], f["rule"]) for f in found} == {("nötes.md", "aws-access-key"), ("clé.pem", "risky-file-name")}
    whole = secretscan.scan_range(repo, None, head)  # no base: every tracked file is read too
    assert ("nötes.md", 2) in {(f["file"], f["line"]) for f in whole}


def test_the_working_tree_scan_finds_a_secret_in_a_non_ascii_file_name(repo):
    (repo / "nötes.md").write_text(f"{AWS}\n")
    assert [(f["file"], f["rule"]) for f in secretscan.scan_working_tree(repo)] == [("nötes.md", "aws-access-key")]
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "n")
    (repo / "nötes.md").write_text(f"{AWS}\nmore {GH}\n")
    assert ("nötes.md", "github-token") in {(f["file"], f["rule"]) for f in secretscan.scan_working_tree(repo)}


def test_a_secret_added_only_in_a_merge_resolution_is_found(repo):
    git(repo, "checkout", "-qb", "feature")
    commit(repo, {"f.txt": "feature\n"})
    git(repo, "checkout", "-q", "main")
    commit(repo, {"m.txt": "main\n"})
    base = git(repo, "rev-parse", "HEAD")
    git(repo, "merge", "--no-commit", "--no-ff", "feature")
    (repo / "resolved.txt").write_text(f"key {AWS}\n")  # nobody but the merge commit adds this
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "merge feature")
    head = git(repo, "rev-parse", "HEAD")
    assert ("resolved.txt", "aws-access-key") in {(f["file"], f["rule"]) for f in secretscan.scan_range(repo, base, head)}


# ---------------------------------------------------------------- content, not diff text
def test_a_line_that_starts_with_plus_plus_space_does_not_confuse_the_scan(repo):
    base = git(repo, "rev-parse", "HEAD")
    head = commit(repo, {"notes.txt": f"x\n++ b/other.txt\n{AWS}\n"})
    found = secretscan.scan_range(repo, base, head)
    assert [(f["file"], f["line"], f["rule"]) for f in found] == [("notes.txt", 3, "aws-access-key")]


@pytest.mark.parametrize("attr", ["binary", "-diff", "diff=none"])
def test_a_file_marked_binary_in_gitattributes_is_still_scanned(repo, attr):
    base = git(repo, "rev-parse", "HEAD")
    head = commit(repo, {".gitattributes": f"*.dat {attr}\n", "k.dat": f"line\n{AWS}\n"})
    assert [(f["file"], f["line"], f["rule"]) for f in secretscan.scan_range(repo, base, head)] == [("k.dat", 2, "aws-access-key")]
    (repo / "w.dat").write_text(f"{GH}\n")  # and in the working tree
    assert ("w.dat", "github-token") in {(f["file"], f["rule"]) for f in secretscan.scan_working_tree(repo)}


def test_real_binary_content_is_scanned_with_bad_bytes_replaced(repo):
    base = git(repo, "rev-parse", "HEAD")
    head = commit(repo, {"a.bin": b"\xff\xfe\x00junk\n".decode("latin-1") + AWS + "\n"})
    assert [f["rule"] for f in secretscan.scan_range(repo, base, head)] == ["aws-access-key"]


def test_a_staged_file_with_no_commit_is_scanned_even_if_the_working_copy_was_cleaned(repo):
    (repo / "s.txt").write_text(f"{AWS}\n")
    git(repo, "add", "s.txt")
    (repo / "s.txt").write_text("clean\n")
    found = secretscan.scan_working_tree(repo)
    assert [(f["file"], f["line"], f["rule"]) for f in found] == [("s.txt", 1, "aws-access-key")] and "commit" not in found[0]


def test_ignored_untracked_files_are_not_scanned(repo):
    commit(repo, {".gitignore": "secret.txt\n"})
    (repo / "secret.txt").write_text(f"{AWS}\n")
    assert secretscan.scan_working_tree(repo) == []


def test_a_force_push_over_a_remote_sha_this_clone_never_fetched_is_still_scanned(repo):
    commit(repo, {"SKILL.md": f"key {AWS}\n"})
    commit(repo, {"SKILL.md": "clean\n"})
    head = git(repo, "rev-parse", "HEAD")
    unknown = "a" * 40
    found = secretscan.scan_pre_push(repo, f"refs/heads/main {head} refs/heads/main {unknown}\n")
    assert [(f["file"], f["rule"]) for f in found] == [("SKILL.md", "aws-access-key")]


def test_commits_already_on_a_remote_are_not_scanned_again(repo):
    commit(repo, {"SKILL.md": f"key {AWS}\n"})
    git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    head = commit(repo, {"b.md": "fine\n"})
    assert secretscan.scan_pre_push(repo, f"refs/heads/main {head} refs/heads/main {'0' * 40}\n") == []


# ---------------------------------------------------------------- the hook finds lifeboat without PATH
def _abs_shim(tmp_path, name="abs-bin"):
    d = tmp_path / name
    d.mkdir()
    exe = d / "lifeboat"
    exe.write_text(f'#!/bin/sh\nPATH="{_BIN}" PYTHONPATH="{Path(secretscan.__file__).parent.parent}" exec {sys.executable} -m lifeboat "$@"\n')
    exe.chmod(0o755)
    return exe


def _push_line(head):
    return f"refs/heads/main {head} refs/heads/main {'0' * 40}\n"


def test_the_hook_blocks_a_secret_with_an_empty_path_by_the_absolute_path_written_at_install(repo, tmp_path, monkeypatch):
    exe = _abs_shim(tmp_path)
    monkeypatch.setattr(secretscan.shutil, "which", lambda name: str(exe) if name == "lifeboat" else None)
    monkeypatch.setattr(secretscan, "_lifeboat_cmd", _REAL_CMD)
    hook = install(repo)
    assert str(exe) in hook.read_text()
    head = commit(repo, {"SKILL.md": f"key {AWS}\n"})
    r = subprocess.run([str(hook), "origin", "x"], input=_push_line(head), capture_output=True, text=True, cwd=repo, env={"PATH": ""})
    assert r.returncode == 1 and "SKILL.md" in r.stderr, r.stderr


def test_the_hook_falls_back_to_path_when_the_absolute_path_is_gone(repo, tmp_path, monkeypatch):
    exe = _abs_shim(tmp_path)
    monkeypatch.setattr(secretscan.shutil, "which", lambda name: str(exe) if name == "lifeboat" else None)
    monkeypatch.setattr(secretscan, "_lifeboat_cmd", _REAL_CMD)
    hook = install(repo)
    exe.unlink()
    head = commit(repo, {"SKILL.md": f"key {AWS}\n"})
    r = subprocess.run([str(hook), "origin", "x"], input=_push_line(head), capture_output=True, text=True, cwd=repo, env=push_env())
    assert r.returncode == 1 and "SKILL.md" in r.stderr, r.stderr
    r = subprocess.run([str(hook), "origin", "x"], input=_push_line(head), capture_output=True, text=True, cwd=repo, env=push_env(on_path=False))
    assert r.returncode == 0 and "not found" in r.stderr  # neither exists: fail open, with a warning


def test_without_a_lifeboat_executable_the_hook_runs_this_python_isolated_with_the_package_folder(repo, monkeypatch):
    monkeypatch.setattr(secretscan.shutil, "which", lambda name: None)
    cmd = _REAL_CMD()
    assert cmd[:3] == [sys.executable, "-I", "-c"] and str(secretscan._package_parent()) in cmd[3]
    monkeypatch.setattr(secretscan, "_lifeboat_cmd", _REAL_CMD)
    assert f"{sys.executable} -I -c" in install(repo).read_text()


def test_a_lifeboat_folder_in_the_pushed_repo_is_never_run_by_the_hook(repo, tmp_path, monkeypatch):
    marker = tmp_path / "ran"
    (repo / "lifeboat").mkdir()
    (repo / "lifeboat" / "__init__.py").write_text("")
    (repo / "lifeboat" / "__main__.py").write_text(f"open({str(marker)!r}, 'w').write('x')\nprint('lifeboat-secret-scan-1')\n")
    monkeypatch.setattr(secretscan.shutil, "which", lambda name: None)
    monkeypatch.setattr(secretscan, "_lifeboat_cmd", _REAL_CMD)  # the python -m fallback
    hook = install(repo)
    head = commit(repo, {"a.md": "fine\n"})
    env = dict(os.environ, PATH=str(_BIN), PYTHONPATH="")
    subprocess.run([str(hook), "origin", "x"], input=_push_line(head), capture_output=True, text=True, cwd=repo, env=env)
    assert not marker.exists()


# ---------------------------------------------------------------- an install.sh install: the launcher's PYTHONPATH is not the hook's
def _install_sh_layout(tmp_path):
    """install.sh puts the package in <share>/lifeboat/lib and a launcher on PATH that sets PYTHONPATH for `python3 -m lifeboat`."""
    lib = tmp_path / "share" / "lifeboat" / "lib"
    shutil.copytree(Path(secretscan.__file__).parent, lib / "lifeboat", ignore=shutil.ignore_patterns("__pycache__"))
    return lib


def test_the_hook_blocks_a_manual_push_of_an_aws_key_on_an_install_sh_install_without_lifeboat_on_path(repo, tmp_path, monkeypatch):
    lib = _install_sh_layout(tmp_path)
    monkeypatch.setattr(secretscan, "_package_parent", lambda: lib, raising=False)
    monkeypatch.setattr(secretscan, "_lifeboat_cmd", _REAL_CMD)  # `lifeboat` was not on PATH when the hook was written: which() finds nothing
    hook = install(repo)
    assert str(lib) in hook.read_text()
    head = commit(repo, {"SKILL.md": f"key {AWS}\n"})
    r = subprocess.run([str(hook), "origin", "x"], input=_push_line(head), capture_output=True, text=True, cwd=repo, env=push_env(on_path=False))
    assert r.returncode == 1 and "SKILL.md" in r.stderr, r.stderr


def test_the_hook_tries_lifeboat_on_path_when_the_command_fixed_at_install_fails_the_probe(repo, monkeypatch):
    monkeypatch.setattr(secretscan, "_lifeboat_cmd", lambda: [sys.executable, "-I", "-m", "lifeboat"])  # cannot import lifeboat: the probe fails
    hook = install(repo)
    head = commit(repo, {"SKILL.md": f"key {AWS}\n"})
    r = subprocess.run([str(hook), "origin", "x"], input=_push_line(head), capture_output=True, text=True, cwd=repo, env=push_env())
    assert r.returncode == 1 and "SKILL.md" in r.stderr, r.stderr


def test_the_console_script_path_is_preferred_over_the_python_command(monkeypatch):
    monkeypatch.setattr(secretscan.shutil, "which", lambda name: "/opt/x/bin/lifeboat")
    assert _REAL_CMD() == ["/opt/x/bin/lifeboat"]
