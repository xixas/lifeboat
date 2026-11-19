"""GitHub App sign-in (device flow) and the few API calls the config store needs. No GTK.
The app has Contents and Metadata access only and is installed on the store repo alone, so there is no repo creation:
the user creates the repo on github.com (new_repo_url), installs the app on it (install_url), then signs in.
All network goes through one injectable `http(method, url, headers, data) -> (status, json)`."""
from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request

CLIENT_ID = "Iv23liecZB4WN7Ij8DiP"
API = "https://api.github.com"
DEVICE_URL = "https://github.com/login/device/code"
TOKEN_URL = "https://github.com/login/oauth/access_token"
REFRESH_MARGIN = 300  # refresh when fewer than 5 minutes remain
HELPER = '!f() { echo username=x-access-token; echo "password=$LIFEBOAT_GH_TOKEN"; }; f'


class GitHubError(RuntimeError):
    pass


class NotInstalled(GitHubError):
    """The Lifeboat GitHub App is not installed on this account yet."""


def install_url() -> str:
    return "https://github.com/apps/lifeboat-backup/installations/new"


def new_repo_url(name: str = "skillshare-source") -> str:
    return "https://github.com/new?" + urllib.parse.urlencode({"name": name, "visibility": "private"})


def default_http(method: str, url: str, headers: dict | None = None, data: dict | None = None) -> tuple[int, dict]:
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, method=method, headers={"Accept": "application/json", "Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310 - https URLs only
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except ValueError:
            return e.code, {}
    except (OSError, ValueError) as e:
        raise GitHubError(f"network error: {e}") from e


class KeyringStore:
    """The desktop keyring through libsecret's `secret-tool`. The value goes over stdin, never argv, never to disk."""
    ATTRS = ["service", "lifeboat", "kind", "github"]

    def _run(self, args: list[str], input: str | None = None):
        try:
            return subprocess.run(["secret-tool", *args], input=input, capture_output=True, text=True, timeout=15, check=False)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise GitHubError("secret-tool (libsecret) is not available") from e

    def get(self) -> str | None:
        try:
            r = self._run(["lookup", *self.ATTRS])
        except GitHubError:
            return None
        return r.stdout or None if r.returncode == 0 else None

    def set(self, value: str) -> None:
        if self._run(["store", "--label=Lifeboat GitHub sign-in", *self.ATTRS], input=value).returncode != 0:
            raise GitHubError("could not save the token in the keyring")

    def clear(self) -> None:
        self._run(["clear", *self.ATTRS])


class GitHub:
    def __init__(self, http=default_http, store=None, now=time.time):
        self.http, self.store, self.now = http, store or KeyringStore(), now

    # ---- device flow
    def start_device_flow(self) -> dict:
        status, d = self.http("POST", DEVICE_URL, None, {"client_id": CLIENT_ID})
        if status != 200 or "device_code" not in d:
            raise GitHubError(d.get("error_description") or d.get("message") or f"device flow failed ({status})")
        return {k: d[k] for k in ("device_code", "user_code", "verification_uri", "interval")}

    def _save(self, d: dict) -> None:
        t = self.now()
        self.store.set(json.dumps({"access_token": d["access_token"], "refresh_token": d.get("refresh_token", ""),
                                   "expires_at": t + int(d.get("expires_in", 28800)),
                                   "refresh_expires_at": t + int(d.get("refresh_token_expires_in", 0))}))

    def poll_token(self, device_code: str) -> dict:
        """One poll. status: ok | pending | slow_down (with the new interval) | expired | denied | error."""
        _, d = self.http("POST", TOKEN_URL, None, {"client_id": CLIENT_ID, "device_code": device_code,
                                                   "grant_type": "urn:ietf:params:oauth:grant-type:device_code"})
        if "access_token" in d:
            self._save(d)
            return {"status": "ok"}
        err = d.get("error", "")
        if err == "slow_down":
            return {"status": "slow_down", "interval": d.get("interval", 10)}
        return {"authorization_pending": {"status": "pending"}, "expired_token": {"status": "expired"},
                "access_denied": {"status": "denied"}}.get(err, {"status": "error", "error": err or "unknown"})

    # ---- stored, expiring user-to-server token
    def _saved(self) -> dict | None:
        try:
            return json.loads(self.store.get() or "")
        except ValueError:
            return None

    def signed_in(self) -> bool:
        return self._saved() is not None

    def refresh(self) -> bool:
        saved = self._saved()
        if not saved or not saved.get("refresh_token"):
            return False
        _, d = self.http("POST", TOKEN_URL, None, {"client_id": CLIENT_ID, "grant_type": "refresh_token", "refresh_token": saved["refresh_token"]})
        if "access_token" not in d:
            return False
        self._save(d)
        return True

    def token(self) -> str | None:
        """A usable access token, refreshed first when under 5 minutes remain; None if signed out or the refresh failed."""
        saved = self._saved()
        if not saved:
            return None
        if saved["expires_at"] - self.now() < REFRESH_MARGIN:
            if not self.refresh():
                return None
            saved = self._saved()
        return saved["access_token"]

    def sign_out(self) -> None:
        self.store.clear()

    # ---- API
    def _get(self, path: str) -> dict:
        token = self.token()
        if not token:
            raise GitHubError("not signed in to GitHub")
        status, d = self.http("GET", API + path, {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                                                  "X-GitHub-Api-Version": "2022-11-28"}, None)
        if status != 200:
            raise GitHubError(d.get("message") or f"GitHub returned {status}")
        return d

    def installations(self) -> list[dict]:
        return [{"id": i["id"], "repository_selection": i.get("repository_selection")} for i in self._get("/user/installations").get("installations", [])]

    def installation_repos(self) -> list[dict]:
        """Repos the app can reach: [{full_name, clone_url, private}]. NotInstalled if the app is on no account yet."""
        installs = self.installations()
        if not installs:
            raise NotInstalled("install the Lifeboat GitHub App on your store repo first")
        return [{"full_name": r["full_name"], "clone_url": r["clone_url"], "private": r["private"]}
                for i in installs for r in self._get(f"/user/installations/{i['id']}/repositories").get("repositories", [])]


def push(repo_path, token: str, timeout: int = 120) -> dict:
    """`git push` with the token supplied to this one process through its environment: never argv, never any git config."""
    argv = ["git", "-C", str(repo_path), "-c", "credential.helper=", "-c", f"credential.helper={HELPER}", "push"]
    try:
        r = subprocess.run(argv, env={**os.environ, "LIFEBOAT_GH_TOKEN": token, "GIT_TERMINAL_PROMPT": "0"}, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as e:
        return {"ok": False, "error": str(e)}
    return {"ok": r.returncode == 0, "error": "" if r.returncode == 0 else (r.stderr or r.stdout).replace(token, "***").strip()[-500:]}
