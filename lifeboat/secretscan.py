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
    "generic-secret": r"""(?i)(?:api[_-]?key|secret|token|password)\s*[:=]\s*['"]?[A-Za-z0-9/+_\-]{16,}""",
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


def _scan_text(file: str, lines, first_line: int = 1) -> list[dict]:
    out = []
    for n, text in enumerate(lines, first_line):
        for rule, rx in PATTERNS.items():
            if rx.search(text):
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


def scan_range(repo, base: str | None, head: str = "HEAD") -> list[dict]:
    """Findings in what `base..head` adds; with no base, in every file tracked at head."""
    found: list[dict] = []
    if base:
        for file, n, text in _added_lines(_git(repo, "diff", "--no-color", "-U0", "--no-ext-diff", f"{base}..{head}")):
            found += _scan_text(file, [text], n)
        files = _git(repo, "diff", "--name-only", "--diff-filter=AMR", f"{base}..{head}").splitlines()
    else:
        files = _git(repo, "ls-tree", "-r", "--name-only", head).splitlines()
        for f in files:
            found += _scan_text(f, _git(repo, "show", f"{head}:{f}").splitlines())
    found += [_finding(f, 0, "risky-file-name") for f in files if _risky_name(f)]
    return _dedupe(found + _gitleaks(repo, base, head))


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
    return _dedupe(found + [_finding(f, 0, "risky-file-name") for f in changed if _risky_name(f)])


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
printf '%s\\n' "$input" | PYTHONPATH="{{pythonpath}}${{PYTHONPATH:+:$PYTHONPATH}}" python3 -m lifeboat.secretscan --pre-push "$@" || exit 1
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
    hook.write_text(HOOK.replace("{pythonpath}", str(Path(__file__).resolve().parent.parent)))
    hook.chmod(0o755)
    return hook


def format_findings(found: list[dict]) -> str:
    return "\n".join(f"  {f['file']}:{f['line']}  {f['rule']} ({f['source']})" for f in found)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if "--pre-push" not in argv:
        print("usage: python3 -m lifeboat.secretscan --pre-push  (reads git's pre-push lines on stdin)", file=sys.stderr)
        return 2
    found = scan_pre_push(os.getcwd(), sys.stdin.read())
    if found:
        print("lifeboat: push blocked, possible secrets found:\n" + format_findings(found), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
