"""Secret check before anything reaches GitHub: regexes plus risky file names, merged with gitleaks if installed.
Findings name the file, line and rule, never the secret itself. No GTK."""
from __future__ import annotations

import fnmatch
import json
import os
import re
import shlex
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
    r = subprocess.run(["git", "-c", "core.quotePath=false", "-C", str(repo), *args], capture_output=True, text=True, check=False, errors="replace")
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


def _gitleaks(repo, base, head) -> list[dict]:
    exe = shutil.which("gitleaks")
    if not exe:
        return []
    with tempfile.TemporaryDirectory() as tmp:
        report = Path(tmp) / "report.json"
        opts = f"{base}..{head}" if base else f"{head} --not --remotes"
        cmd = [exe, "git", f"--log-opts={opts}", "--report-format=json", f"--report-path={report}", "--no-banner", "--redact", "--exit-code=0", str(repo)]
        try:
            subprocess.run(cmd, capture_output=True, text=True, timeout=120, check=False)
            data = json.loads(report.read_text() or "[]")
        except (OSError, ValueError, subprocess.TimeoutExpired):
            return []
    return [_finding(d.get("File", ""), d.get("StartLine", 0), d.get("RuleID", "gitleaks"), "gitleaks") for d in data]


def _git_bytes(repo, *args: str) -> bytes:
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, check=False)
    return r.stdout if r.returncode == 0 else b""


def _top(repo) -> str:
    """The repo's top folder: every path git prints below is relative to it."""
    return _git(repo, "rev-parse", "--show-toplevel").strip() or str(repo)


def _scan_bytes(file: str, data: bytes) -> list[dict]:
    """Scan a file's whole content, whatever .gitattributes says about it: bytes, decoded with bad bytes replaced."""
    return _scan_text(file, data.decode("utf-8", errors="replace").split("\n"))


def _paths(out: str) -> list[str]:
    return [p for p in out.split("\0") if p]


def _changed(repo, commit: str) -> list[tuple[str, str]]:
    """(blob sha, path) of every file `commit` adds or changes, a merge read against its first parent so what its resolution adds is seen too."""
    toks = _git(repo, "diff-tree", "-r", "-z", "--no-renames", "--root", "-m", "--first-parent", "--no-commit-id", commit).split("\0")
    out = []
    for meta, path in zip(toks[::2], toks[1::2], strict=False):
        _old_mode, new_mode, _old, blob, status = meta.lstrip(":").split(" ")[:5]
        if status[0] != "D" and new_mode != "160000":  # not a deletion, not a submodule
            out.append((blob, path))
    return out


def scan_range(repo, base: str | None = None, head: str = "HEAD") -> list[dict]:
    """Findings in the whole content of every file each unpushed commit adds or changes (`head`, minus whatever any remote-tracking
    ref and `base` already have), one commit at a time (`commit` is a short sha). It never needs to know what the remote holds."""
    top = _top(repo)
    found: list[dict] = []
    by_blob: dict[str, list[dict]] = {}
    for sha in _git(top, "rev-list", "--reverse", head, "--not", "--remotes", *([base] if base else [])).split():
        for blob, path in _changed(top, sha):
            if blob not in by_blob:
                by_blob[blob] = _scan_bytes("", _git_bytes(top, "cat-file", "blob", blob))
            found += [{**f, "file": path, "commit": sha[:7]} for f in by_blob[blob]]
            if _risky_name(path):
                found.append({**_finding(path, 0, "risky-file-name"), "commit": sha[:7]})
    return _allowed(top, _dedupe(found + _gitleaks(top, base, head)))


def _dedupe(found: list[dict]) -> list[dict]:
    seen, out = set(), []
    for f in found:
        key = (f["file"], f["line"], f["rule"])
        if key not in seen:
            seen.add(key)
            out.append(f)
    return out


def scan_working_tree(repo) -> list[dict]:
    """Findings in changes not committed yet, whole files: what is staged (read from the index, so a file cleaned after `git add` still counts),
    tracked files edited since, and untracked files that are not ignored (what `skillshare push` would commit)."""
    top = _top(repo)
    found, names = [], []
    for f in _paths(_git(top, "diff", "--cached", "--name-only", "-z", "--diff-filter=ACMRT", "--no-renames")):
        names.append(f)
        found += _scan_bytes(f, _git_bytes(top, "cat-file", "blob", f":{f}"))
    edited = _paths(_git(top, "diff", "--name-only", "-z", "--diff-filter=ACMRT", "--no-renames")) + _paths(_git(top, "ls-files", "--others", "--exclude-standard", "-z"))
    for f in edited:
        names.append(f)
        path = Path(top) / f
        try:
            if path.is_file() and not path.is_symlink():
                found += _scan_bytes(f, path.read_bytes())
        except OSError:
            pass
    return _allowed(top, _dedupe(found + [_finding(f, 0, "risky-file-name") for f in names if _risky_name(f)]))


def scan_pre_push(repo, stdin_text: str) -> list[dict]:
    """Scan what git is about to push: stdin lines are `<local ref> <local sha> <remote ref> <remote sha>`."""
    found = []
    for line in stdin_text.splitlines():
        parts = line.split()
        if len(parts) != 4 or parts[1] == ZERO:
            continue  # a deletion pushes nothing
        found += scan_range(repo, None, parts[1])  # the remote's sha (parts[3]) is not needed, and may not be in this clone
    return found


PROBE_MARKER = "lifeboat-secret-scan-1"
# Shell builtins only before lifeboat runs (no cat, dirname): the hook has to work even with an empty PATH.
HOOK = f"""#!/bin/sh
{HOOK_MARK}: blocks a push that carries a secret. Edits here are overwritten.
input=""
while IFS= read -r line || [ -n "$line" ]; do
  input="$input$line
"
done
lb() {{
  if [ -x @EXE@ ]; then @EXE@ @ARGS@"$@"
  elif command -v lifeboat >/dev/null 2>&1; then lifeboat "$@"
  else return 127
  fi
}}
if [ "$(lb secret-scan --probe 2>/dev/null)" = "{PROBE_MARKER}" ]; then
  printf '%s' "$input" | lb secret-scan --pre-push "$@"
  rc=$?
  if [ "$rc" -ne 0 ]; then
    echo "lifeboat: push blocked (the secret check exited $rc)" >&2
    exit 1
  fi
else
  echo "lifeboat: not found, or too old to have the secret check; skipped it for this push" >&2
fi
chained="${{0%/*}}/pre-push.lifeboat-chained"
if [ -x "$chained" ]; then
  printf '%s' "$input" | "$chained" "$@" || exit $?
fi
exit 0
"""


def _lifeboat_cmd() -> list[str]:
    """How the hook runs Lifeboat, fixed at install time: the absolute path of this machine's `lifeboat`, else this Python in isolated mode (-I: ignores the cwd, which is the pushed repo, and PYTHON* variables)."""
    exe = shutil.which("lifeboat")
    return [os.path.abspath(exe)] if exe else [sys.executable, "-I", "-m", "lifeboat"]  # -I: not the pushed repo (the cwd) nor PYTHON* env


def hook_text() -> str:
    exe, *rest = _lifeboat_cmd()
    return HOOK.replace("@EXE@", shlex.quote(exe)).replace("@ARGS@", "".join(shlex.quote(a) + " " for a in rest))


CHAIN_MARK = "# lifeboat-global-chain"


def _hooks_path(repo) -> tuple[str, str]:
    """(scope, absolute path) of core.hooksPath as git resolves it for this repo; ("", "") if unset."""
    out = _git(repo, "config", "--type=path", "--show-scope", "--get", "core.hooksPath").strip()
    scope, _, value = out.partition("\t")
    if not value:
        return "", ""
    return scope, str(Path(value) if Path(value).is_absolute() else Path(repo) / value)


def _own_hooks_dir(repo, global_dir: Path) -> Path:
    """A hooks folder owned by Lifeboat inside this repo's .git, made the repo-local core.hooksPath, so a global hooksPath is never
    written to. Every other hook there keeps running through a one-line wrapper that calls it by name."""
    own = Path(_git(repo, "rev-parse", "--path-format=absolute", "--git-path", "lifeboat-hooks").strip())
    own.mkdir(parents=True, exist_ok=True)
    wrappers = {p.name for p in global_dir.iterdir() if p.is_file()} if global_dir.is_dir() else set()
    for name in wrappers | {"pre-push"}:
        target = own / ("pre-push.lifeboat-chained" if name == "pre-push" else name)
        target.write_text(f'#!/bin/sh\n{CHAIN_MARK}\nif [ -x "{global_dir}/{name}" ]; then exec "{global_dir}/{name}" "$@"; fi\nexit 0\n')
        target.chmod(0o755)
    subprocess.run(["git", "-C", str(repo), "config", "--local", "core.hooksPath", str(own)], check=True, capture_output=True)
    return own


def install_pre_push_hook(repo) -> Path:
    """Write pre-push into the hooks folder git uses: .git/hooks, or core.hooksPath if the repo sets it. When core.hooksPath comes
    from global or system config, a Lifeboat-owned folder in this repo takes its place (see _own_hooks_dir) and chains to the global
    hooks. Any hook already in the folder is kept as pre-push.lifeboat-chained and still runs."""
    if not _git(repo, "rev-parse", "--git-dir").strip():
        raise RuntimeError(f"{repo} is not a git repository")
    scope, configured = _hooks_path(repo)
    if scope in ("global", "system"):
        hooks = _own_hooks_dir(repo, Path(configured))
    else:
        hooks = Path(_git(repo, "rev-parse", "--path-format=absolute", "--git-path", "hooks").strip())
    hook = hooks / "pre-push"
    chained = hook.with_name("pre-push.lifeboat-chained")
    hook.parent.mkdir(parents=True, exist_ok=True)
    if hook.exists() and HOOK_MARK not in hook.read_text(errors="replace"):
        if chained.exists():
            raise RuntimeError(f"{chained} already exists; not overwriting a hook")
        hook.rename(chained)
    hook.write_text(hook_text())
    hook.chmod(0o755)
    return hook


def format_findings(found: list[dict]) -> str:
    return "\n".join(f"  {f['file']}:{f['line']}  {f['rule']} ({f['source']})" for f in found)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if "--probe" in argv:
        print(PROBE_MARKER)
        return 0
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
