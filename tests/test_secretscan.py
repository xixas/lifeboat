import os
import subprocess
import sys
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


def push_env():
    return dict(os.environ, PYTHONPATH=str(Path(secretscan.__file__).parent.parent))


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
    assert hook.read_text().count("lifeboat.secretscan") == 1
    r = subprocess.run([str(hook), "origin", "x"], input="", capture_output=True, text=True, cwd=repo, env=push_env())
    assert r.returncode == 7 and "chained-ran" in r.stderr


def test_cli_pre_push_exit_codes(repo):
    base = git(repo, "rev-parse", "HEAD")
    head = commit(repo, {"a.txt": AWS + "\n"})
    line = f"refs/heads/main {head} refs/heads/main {base}\n"
    r = subprocess.run([sys.executable, "-m", "lifeboat.secretscan", "--pre-push"], input=line, capture_output=True, text=True, cwd=repo, env=push_env())
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
