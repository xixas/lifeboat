"""Secret check before anything reaches GitHub: regexes plus risky file names, merged with gitleaks if installed.
Findings name the file, line and rule, never the secret itself. No GTK."""
from __future__ import annotations

import fnmatch
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

RULES = {
    "aws-access-key": r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b",
    "github-token": r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{22,})",
    "slack-token": r"\bxox[abposr]-[A-Za-z0-9-]{10,}",
    "private-key": r"-----BEGIN (?:[A-Z]+ )*PRIVATE KEY",
    "google-api-key": r"\bAIza[0-9A-Za-z_-]{35}",
    "anthropic-key": r"\bsk-ant-[A-Za-z0-9_-]{20,}",
    "openai-key": r"\bsk-[A-Za-z0-9_-]{20,}",
    "jwt": r"\beyJ[A-Za-z0-9_-]{5,}\.eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}",
    "generic-secret": r"""(?i)(?:api[_-]?key|secret|token|password)\s*[:=]\s*['"]?([A-Za-z0-9/+_\-]{16,})""",
}
PATTERNS = {name: re.compile(rx) for name, rx in RULES.items()}
RISKY_NAMES = (".env", ".env.*", "id_rsa*", "*.pem", "*.key", "*credentials*", "*.p8", "*.p12")
HOOK_MARK = "# lifeboat-secretscan"
ZERO = "0" * 40


def _git(repo, *args: str) -> str:
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=False, errors="replace")
    return r.stdout if r.returncode == 0 else ""


def _finding(file, line, rule, source="lifeboat") -> dict:
    return {"file": file, "line": line, "rule": rule, "source": source}


def _looks_random(value: str) -> bool:
    """A generic secret needs a digit and a letter: 'github_installation_token' is a name, not a value."""
    return any(c.isdigit() for c in value) and any(c.isalpha() for c in value)


ALLOW_FILE = ".lifeboat-secrets-allow"


def _allowlist(repo) -> set[tuple]:
    """Entries of <repo>/.lifeboat-secrets-allow: `rule:path` or `rule:path:line`, one per line, # for comments."""
    try:
        text = (Path(repo) / ALLOW_FILE).read_text(errors="replace")
    except OSError:
        return set()
    out = set()
    for ln in text.splitlines():
        ln = ln.strip()
        if ln and not ln.startswith("#"):
            rule, _, rest = ln.partition(":")
            path, _, line = rest.rpartition(":") if rest.rpartition(":")[2].isdigit() else (rest, "", "")
            out.add((rule, path, int(line) if line else None))
    return out


def _allowed(repo, found: list[dict]) -> list[dict]:
    allow = _allowlist(repo)
    return [f for f in found if (f["rule"], f["file"], None) not in allow and (f["rule"], f["file"], f["line"]) not in allow] if allow else found


def _scan_text(file: str, lines, first_line: int = 1) -> list[dict]:
    out = []
    for n, text in enumerate(lines, first_line):
        for rule, rx in PATTERNS.items():
            m = rx.search(text)
            if m and (rule != "generic-secret" or _looks_random(m.group(1))):
                out.append(_finding(file, n, rule))
                break  # one finding per line
    return out


def _risky_name(path: str) -> bool:
    name = path.rsplit("/", 1)[-1].lower()
    return any(fnmatch.fnmatch(name, p) for p in RISKY_NAMES)


def _added_lines(diff: str):
    """Yield (file, line number in the new file, text) for every added line of a unified diff."""
    file, n = None, 0
    for raw in diff.splitlines():
        if raw.startswith("+++ "):
            file = raw[6:] if raw.startswith("+++ b/") else None
        elif raw.startswith("@@"):
            m = re.match(r"@@ -\S+ \+(\d+)", raw)
            n = int(m.group(1)) if m else 0
        elif file and raw.startswith("+"):
            yield file, n, raw[1:]
            n += 1
        elif file and raw.startswith(" "):
            n += 1


def _gitleaks(repo, base, head) -> list[dict]:
    exe = shutil.which("gitleaks")
    if not exe:
        return []
    with tempfile.TemporaryDirectory() as tmp:
        report = Path(tmp) / "report.json"
        opts = f"{base}..{head}" if base else head
        cmd = [exe, "git", f"--log-opts={opts}", "--report-format=json", f"--report-path={report}", "--no-banner", "--redact", "--exit-code=0", str(repo)]
        try:
            subprocess.run(cmd, capture_output=True, text=True, timeout=120, check=False)
            data = json.loads(report.read_text() or "[]")
        except (OSError, ValueError, subprocess.TimeoutExpired):
            return []
    return [_finding(d.get("File", ""), d.get("StartLine", 0), d.get("RuleID", "gitleaks"), "gitleaks") for d in data]


def _history(repo, base: str | None, head: str):
    """(sha, file, line, text) for every line ANY commit in base..head adds, and (sha, file) for every file such a commit adds
    or changes. A secret added by one commit and removed by the next is invisible to a diff of the endpoints, but the first
    commit is still pushed."""
    out = _git(repo, "log", "--no-color", "-p", "-U0", "--no-ext-diff", "--format=LBCOMMIT:%H", f"{base}..{head}" if base else head)
    sha, file, n, lines, files = "", None, 0, [], []
    pending_file = None
    for raw in out.splitlines():
        if raw.startswith("LBCOMMIT:"):
            sha, file, pending_file = raw[9:], None, None
        elif raw.startswith("diff --git "):
            m = re.match(r"diff --git a/.* b/(.*)$", raw)
            pending_file = m.group(1) if m else None
            if pending_file:
                files.append((sha, pending_file))
        elif raw.startswith("deleted file mode") and pending_file:
            files.remove((sha, pending_file))
        elif raw.startswith("+++ "):
            file = raw[6:] if raw.startswith("+++ b/") else None
        elif raw.startswith("@@"):
            m = re.match(r"@@ -\S+ \+(\d+)", raw)
            n = int(m.group(1)) if m else 0
        elif file and raw.startswith("+"):
            lines.append((sha, file, n, raw[1:]))
            n += 1
    return lines, files


def scan_range(repo, base: str | None, head: str = "HEAD") -> list[dict]:
    """Findings in everything the commits in `base..head` add, one commit at a time (each is `commit`, a short sha). With no base
    that is the whole history, plus every file tracked at head."""
    found: list[dict] = []
    lines, files = _history(repo, base, head)
    for sha, file, n, text in lines:
        found += [{**f, "commit": sha[:7]} for f in _scan_text(file, [text], n)]
    found += [{**_finding(f, 0, "risky-file-name"), "commit": sha[:7]} for sha, f in files if _risky_name(f)]
    if not base:
        for f in _git(repo, "ls-tree", "-r", "--name-only", head).splitlines():
            found += _scan_text(f, _git(repo, "show", f"{head}:{f}").splitlines())
    return _allowed(repo, _dedupe(found + _gitleaks(repo, base, head)))


def _dedupe(found: list[dict]) -> list[dict]:
    seen, out = set(), []
    for f in found:
        key = (f["file"], f["line"], f["rule"])
        if key not in seen:
            seen.add(key)
            out.append(f)
    return out


def scan_working_tree(repo) -> list[dict]:
    """Findings in changes not committed yet: edits to tracked files plus untracked files (what `skillshare push` would commit)."""
    found = []
    for file, n, text in _added_lines(_git(repo, "diff", "--no-color", "-U0", "--no-ext-diff", "HEAD")):
        found += _scan_text(file, [text], n)
    untracked = _git(repo, "ls-files", "--others", "--exclude-standard", "-z").split("\0")
    for f in filter(None, untracked):
        try:
            found += _scan_text(f, (Path(repo) / f).read_text(errors="replace").splitlines())
        except OSError:
            pass
    changed = _git(repo, "diff", "--name-only", "HEAD").splitlines() + [f for f in untracked if f]
    return _allowed(repo, _dedupe(found + [_finding(f, 0, "risky-file-name") for f in changed if _risky_name(f)]))


def scan_pre_push(repo, stdin_text: str) -> list[dict]:
    """Scan what git is about to push: stdin lines are `<local ref> <local sha> <remote ref> <remote sha>`."""
    found = []
    for line in stdin_text.splitlines():
        parts = line.split()
        if len(parts) != 4 or parts[1] == ZERO:
            continue  # a deletion pushes nothing
        found += scan_range(repo, None if parts[3] == ZERO else parts[3], parts[1])
    return found


HOOK = f"""#!/bin/sh
{HOOK_MARK}: blocks a push that carries a secret. Edits here are overwritten.
input=$(cat)
if command -v lifeboat >/dev/null 2>&1; then
  printf '%s\\n' "$input" | lifeboat secret-scan --pre-push "$@"
  rc=$?
  if [ "$rc" -eq 1 ]; then exit 1; fi
  if [ "$rc" -ne 0 ]; then echo "lifeboat: the secret check could not run (exit $rc); not blocking this push" >&2; fi
else
  echo "lifeboat: not found on PATH, so the secret check was skipped for this push" >&2
fi
chained="$(dirname "$0")/pre-push.lifeboat-chained"
if [ -x "$chained" ]; then
  printf '%s\\n' "$input" | "$chained" "$@" || exit $?
fi
exit 0
"""


def install_pre_push_hook(repo) -> Path:
    """Write .git/hooks/pre-push. Any hook already there is kept as pre-push.lifeboat-chained and still runs."""
    git_dir = Path(_git(repo, "rev-parse", "--absolute-git-dir").strip())
    hook = git_dir / "hooks" / "pre-push"
    chained = hook.with_name("pre-push.lifeboat-chained")
    hook.parent.mkdir(parents=True, exist_ok=True)
    if hook.exists() and HOOK_MARK not in hook.read_text(errors="replace"):
        if chained.exists():
            raise RuntimeError(f"{chained} already exists; not overwriting a hook")
        hook.rename(chained)
    hook.write_text(HOOK)
    hook.chmod(0o755)
    return hook


def format_findings(found: list[dict]) -> str:
    return "\n".join(f"  {f['file']}:{f['line']}  {f['rule']} ({f['source']})" for f in found)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if "--pre-push" not in argv:
        print("usage: python3 -m lifeboat.secretscan --pre-push  (reads git's pre-push lines on stdin)", file=sys.stderr)
        return 2
    try:
        found = scan_pre_push(os.getcwd(), sys.stdin.read())
    except Exception as e:  # noqa: BLE001 - a crash must not look like "blocked"
        print(f"lifeboat: the secret check failed ({type(e).__name__})", file=sys.stderr)
        return 3
    if found:
        print("lifeboat: push blocked, possible secrets found:\n" + format_findings(found), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
