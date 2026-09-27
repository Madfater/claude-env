"""GitHub client for the harness skills, using only the Python standard library.

- Reads credentials from `.env.skills` at the repository root; process environment overrides it.
- `GITHUB_TOKEN` is required for every API call and for pushing over HTTPS.
- `GITHUB_REPO` (owner/name) is optional; the default comes from the `origin` remote URL.
- Pushes and pulls pass the token to git through a one-shot credential helper that reads it
  from the child process environment, so the token never appears on a command line and the
  user's global git config is untouched.
- Subcommands print JSON or plain text to stdout.
- Exit codes: 0 success; 1 negative answer (CI failed, PR not merged); 2 CI still pending at
  timeout; 3 error (missing credentials, API or git failure).

Usage:
    python3 script/github.py default-branch
    python3 script/github.py push
    python3 script/github.py sync --path <worktree>
    python3 script/github.py pr-create --title T --body-file F [--base B] [--draft]
    python3 script/github.py pr-view [--number N | --branch B]
    python3 script/github.py pr-edit --number N --body-file F
    python3 script/github.py pr-checks [--number N | --branch B] [--wait] [--timeout 900]
    python3 script/github.py pr-merged [--number N | --branch B]
    python3 script/github.py branch-delete --branch B
    python3 script/github.py repo-create --name N [--private] [--description D]
    python3 script/github.py repo-set-template [--off]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

API_ROOT = "https://api.github.com"
ENV_FILE = ".env.skills"
CREDENTIAL_HELPER = '!f() { echo username=x-access-token; echo "password=$GITHUB_TOKEN"; }; f'


class GitHubError(Exception):
    """Error raised for any failed GitHub or git operation."""


def repo_root() -> Path:
    """Return the top level of the current git worktree.

    - Uses `git rev-parse --show-toplevel`, so it works from any subdirectory or worktree.
    """
    out = run_git(["rev-parse", "--show-toplevel"])
    return Path(out.strip())


def main_worktree_root() -> Path:
    """Return the root of the main worktree.

    - `.env.skills` is gitignored, so it only exists in the main checkout, not in linked worktrees.
    - Resolves the common git directory and takes its parent.
    """
    common = Path(run_git(["rev-parse", "--path-format=absolute", "--git-common-dir"]).strip())
    return common.parent


def parse_env_file(text: str) -> dict[str, str]:
    """Parse `KEY=VALUE` lines.

    - Skips blank lines and lines starting with `#`.
    - Accepts an optional leading `export `.
    - Strips one pair of matching single or double quotes around the value.
    """
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key:
            values[key] = value
    return values


def load_env() -> dict[str, str]:
    """Load `.env.skills` from the current worktree, falling back to the main worktree.

    - Process environment variables take precedence over file values.
    """
    values: dict[str, str] = {}
    for root in (repo_root(), main_worktree_root()):
        path = root / ENV_FILE
        if path.is_file():
            values = parse_env_file(path.read_text(encoding="utf-8"))
            break
    for key in ("GITHUB_TOKEN", "GITHUB_REPO", "GITHUB_API_URL"):
        if os.environ.get(key):
            values[key] = os.environ[key]
    return values


def parse_remote_url(url: str) -> str | None:
    """Extract `owner/name` from a GitHub remote URL.

    - Handles `https://github.com/o/r(.git)`, `git@github.com:o/r(.git)` and `ssh://git@github.com/o/r`.
    - Returns None for non-GitHub remotes.
    """
    match = re.match(
        r"^(?:https?://(?:[^@/]+@)?github\.com/|git@github\.com:|ssh://git@github\.com/)"
        r"([^/]+)/([^/]+?)(?:\.git)?/?$",
        url.strip(),
    )
    if not match:
        return None
    return f"{match.group(1)}/{match.group(2)}"


def run_git(args: list[str], env: dict[str, str] | None = None, cwd: Path | None = None) -> str:
    """Run git and return stdout.

    - Raises GitHubError with stderr when git exits non-zero.
    """
    proc = subprocess.run(
        ["git", *args], capture_output=True, text=True, env=env, cwd=cwd, check=False
    )
    if proc.returncode != 0:
        raise GitHubError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout


class Client:
    """Thin REST client bound to one repository."""

    def __init__(self, env: dict[str, str]):
        self.token = env.get("GITHUB_TOKEN", "")
        self.api = env.get("GITHUB_API_URL", API_ROOT).rstrip("/")
        self._repo = env.get("GITHUB_REPO")

    @property
    def repo(self) -> str:
        """Return `owner/name`, resolved from `GITHUB_REPO` or the origin remote on first use.

        - Resolution is lazy so `repo-create` works before any remote exists.
        """
        if not self._repo:
            try:
                url = run_git(["remote", "get-url", "origin"]).strip()
            except GitHubError:
                url = ""
            self._repo = parse_remote_url(url)
        if not self._repo:
            raise GitHubError("Cannot derive owner/name from origin. Set GITHUB_REPO in .env.skills.")
        return self._repo

    def require_token(self) -> None:
        """Stop with setup instructions when no token is configured."""
        if not self.token:
            raise GitHubError(
                "GITHUB_TOKEN is missing. Copy .env.skills.example to .env.skills in the main "
                "checkout and fill in a fine-grained token. See docs/setup.md."
            )

    def request(self, method: str, path: str, body: dict | None = None) -> dict | list | None:
        """Send one API request and decode the JSON response.

        - Paths starting with `/repos/{repo}` are expanded with the bound repository.
        - Raises GitHubError with the API message on HTTP errors.
        """
        self.require_token()
        if "{repo}" in path:
            path = path.replace("{repo}", self.repo)
        url = self.api + path
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", f"Bearer {self.token}")
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("X-GitHub-Api-Version", "2022-11-28")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")
            try:
                detail = json.loads(detail).get("message", detail)
            except (ValueError, AttributeError):
                pass
            raise GitHubError(f"{method} {path} -> HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise GitHubError(f"{method} {path} failed: {exc.reason}") from exc
        return json.loads(raw) if raw else None

    def git_env(self) -> dict[str, str]:
        """Return an environment that lets the credential helper read the token."""
        self.require_token()
        env = dict(os.environ)
        env["GITHUB_TOKEN"] = self.token
        env["GIT_TERMINAL_PROMPT"] = "0"
        return env

    def git_with_token(self, args: list[str], cwd: Path | None = None) -> str:
        """Run a git network command with the token helper.

        - Clears inherited credential helpers with an empty `credential.helper` first.
        """
        return run_git(
            ["-c", "credential.helper=", "-c", f"credential.helper={CREDENTIAL_HELPER}", *args],
            env=self.git_env(),
            cwd=cwd,
        )

    def current_branch(self) -> str:
        """Return the checked-out branch name, failing on a detached HEAD."""
        branch = run_git(["branch", "--show-current"]).strip()
        if not branch:
            raise GitHubError("Detached HEAD. Check out a branch first.")
        return branch

    def find_pr(self, number: int | None, branch: str | None = None) -> dict:
        """Return the PR by number, else the latest PR whose head is `branch` or the current branch."""
        if number:
            return self.request("GET", f"/repos/{{repo}}/pulls/{number}")
        branch = branch or self.current_branch()
        owner = self.repo.split("/")[0]
        head = urllib.parse.quote(f"{owner}:{branch}")
        prs = self.request("GET", f"/repos/{{repo}}/pulls?head={head}&state=all")
        if not prs:
            raise GitHubError(f"No PR found for branch {branch}.")
        return prs[0]


def local_default_branch() -> str | None:
    """Read the default branch from `refs/remotes/origin/HEAD` without network access."""
    try:
        ref = run_git(["symbolic-ref", "--quiet", "refs/remotes/origin/HEAD"]).strip()
    except GitHubError:
        return None
    prefix = "refs/remotes/origin/"
    return ref[len(prefix):] if ref.startswith(prefix) else None


def summarize_checks(runs: list[dict], statuses: list[dict]) -> dict:
    """Combine check runs and commit statuses into one verdict.

    - `pending` while any run is not completed or any status is pending.
    - `failure` when any run concluded failure, cancelled, timed_out or action_required,
      or any status is failure or error.
    - `success` otherwise, or `none` when nothing reported.
    """
    bad_conclusions = {"failure", "cancelled", "timed_out", "action_required", "startup_failure"}
    items = []
    pending = failed = False
    for run in runs:
        state = run.get("conclusion") if run.get("status") == "completed" else run.get("status")
        items.append({"name": run.get("name"), "state": state, "url": run.get("html_url")})
        if run.get("status") != "completed":
            pending = True
        elif run.get("conclusion") in bad_conclusions:
            failed = True
    for status in statuses:
        state = status.get("state")
        items.append({"name": status.get("context"), "state": state, "url": status.get("target_url")})
        if state == "pending":
            pending = True
        elif state in {"failure", "error"}:
            failed = True
    if not items:
        overall = "none"
    elif pending:
        overall = "pending"
    elif failed:
        overall = "failure"
    else:
        overall = "success"
    return {"overall": overall, "checks": items}


def cmd_default_branch(client: Client, _args) -> None:
    """Print the default branch, preferring local refs over the API."""
    branch = local_default_branch()
    if not branch:
        branch = client.request("GET", "/repos/{repo}")["default_branch"]
    print(branch)


def cmd_push(client: Client, _args) -> None:
    """Push the current branch to origin and set upstream."""
    branch = client.current_branch()
    client.git_with_token(["push", "-u", "origin", branch])
    print(f"pushed {branch}")


def cmd_sync(client: Client, args) -> None:
    """Fetch with pruning and fast-forward the branch checked out at `--path`."""
    path = Path(args.path) if args.path else main_worktree_root()
    client.git_with_token(["fetch", "--prune", "origin"], cwd=path)
    client.git_with_token(["pull", "--ff-only"], cwd=path)
    print(run_git(["log", "--oneline", "-1"], cwd=path).strip())


def cmd_pr_create(client: Client, args) -> None:
    """Open a PR from the current branch and print its number and URL."""
    base = args.base or local_default_branch() or client.request("GET", "/repos/{repo}")["default_branch"]
    body = Path(args.body_file).read_text(encoding="utf-8")
    pr = client.request(
        "POST",
        "/repos/{repo}/pulls",
        {"title": args.title, "body": body, "head": client.current_branch(), "base": base, "draft": args.draft},
    )
    print(json.dumps({"number": pr["number"], "url": pr["html_url"]}))


def cmd_pr_view(client: Client, args) -> None:
    """Print the key fields of a PR as JSON."""
    pr = client.find_pr(args.number, args.branch)
    print(json.dumps({
        "number": pr["number"],
        "url": pr["html_url"],
        "state": pr["state"],
        "merged": bool(pr.get("merged_at")),
        "draft": pr.get("draft", False),
        "title": pr["title"],
        "head": pr["head"]["ref"],
        "head_sha": pr["head"]["sha"],
        "base": pr["base"]["ref"],
        "body": pr.get("body") or "",
    }, indent=2))


def cmd_pr_edit(client: Client, args) -> None:
    """Replace the body of a PR with the contents of `--body-file`."""
    body = Path(args.body_file).read_text(encoding="utf-8")
    pr = client.request("PATCH", f"/repos/{{repo}}/pulls/{args.number}", {"body": body})
    print(json.dumps({"number": pr["number"], "url": pr["html_url"]}))


def cmd_pr_checks(client: Client, args) -> None:
    """Report CI status for a PR head commit.

    - With `--wait`, polls every 15 seconds until nothing is pending or `--timeout` elapses.
    - A `none` result is retried for the first 60 seconds, since CI can take a moment to register.
    - Exits 0 on success or none, 1 on CI failure, 2 on timeout while pending.
    """
    pr = client.find_pr(args.number, args.branch)
    sha = pr["head"]["sha"]
    deadline = time.monotonic() + args.timeout
    grace_end = time.monotonic() + 60
    while True:
        runs = client.request("GET", f"/repos/{{repo}}/commits/{sha}/check-runs?per_page=100")["check_runs"]
        statuses = client.request("GET", f"/repos/{{repo}}/commits/{sha}/status")["statuses"]
        result = summarize_checks(runs, statuses)
        waiting = result["overall"] == "pending" or (result["overall"] == "none" and time.monotonic() < grace_end)
        if not args.wait or not waiting or time.monotonic() >= deadline:
            break
        time.sleep(15)
    result["sha"] = sha
    print(json.dumps(result, indent=2))
    if result["overall"] == "failure":
        sys.exit(1)
    if result["overall"] == "pending":
        sys.exit(2)


def cmd_pr_merged(client: Client, args) -> None:
    """Print `true` or `false`; exit 1 when the PR is not merged."""
    pr = client.find_pr(args.number, args.branch)
    merged = bool(pr.get("merged_at"))
    print("true" if merged else "false")
    if not merged:
        sys.exit(1)


def cmd_branch_delete(client: Client, args) -> None:
    """Delete a remote branch; report `absent` when it is already gone."""
    ref = urllib.parse.quote(args.branch, safe="/")
    try:
        client.request("DELETE", f"/repos/{{repo}}/git/refs/heads/{ref}")
        print(f"deleted {args.branch}")
    except GitHubError as exc:
        if "HTTP 422" in str(exc) or "HTTP 404" in str(exc):
            print(f"absent {args.branch}")
        else:
            raise


def cmd_repo_create(client: Client, args) -> None:
    """Create a repository under the authenticated user and print its URLs."""
    repo = client.request(
        "POST",
        "/user/repos",
        {"name": args.name, "private": args.private, "description": args.description or ""},
    )
    print(json.dumps({"full_name": repo["full_name"], "url": repo["html_url"], "clone_url": repo["clone_url"]}))


def cmd_repo_set_template(client: Client, args) -> None:
    """Turn the repository's template flag on, or off with `--off`."""
    repo = client.request("PATCH", "/repos/{repo}", {"is_template": not args.off})
    print(json.dumps({"full_name": repo["full_name"], "is_template": repo.get("is_template")}))


def build_parser() -> argparse.ArgumentParser:
    """Define the subcommands and their arguments."""
    parser = argparse.ArgumentParser(description="GitHub client for the harness skills.")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("default-branch").set_defaults(func=cmd_default_branch)
    sub.add_parser("push").set_defaults(func=cmd_push)

    p = sub.add_parser("sync")
    p.add_argument("--path")
    p.set_defaults(func=cmd_sync)

    p = sub.add_parser("pr-create")
    p.add_argument("--title", required=True)
    p.add_argument("--body-file", required=True)
    p.add_argument("--base")
    p.add_argument("--draft", action="store_true")
    p.set_defaults(func=cmd_pr_create)

    for name, func in (("pr-view", cmd_pr_view), ("pr-merged", cmd_pr_merged)):
        p = sub.add_parser(name)
        p.add_argument("--number", type=int)
        p.add_argument("--branch")
        p.set_defaults(func=func)

    p = sub.add_parser("pr-edit")
    p.add_argument("--number", type=int, required=True)
    p.add_argument("--body-file", required=True)
    p.set_defaults(func=cmd_pr_edit)

    p = sub.add_parser("pr-checks")
    p.add_argument("--number", type=int)
    p.add_argument("--branch")
    p.add_argument("--wait", action="store_true")
    p.add_argument("--timeout", type=int, default=900)
    p.set_defaults(func=cmd_pr_checks)

    p = sub.add_parser("branch-delete")
    p.add_argument("--branch", required=True)
    p.set_defaults(func=cmd_branch_delete)

    p = sub.add_parser("repo-create")
    p.add_argument("--name", required=True)
    p.add_argument("--private", action="store_true")
    p.add_argument("--description")
    p.set_defaults(func=cmd_repo_create)

    p = sub.add_parser("repo-set-template")
    p.add_argument("--off", action="store_true")
    p.set_defaults(func=cmd_repo_set_template)

    return parser


def main(argv: list[str] | None = None) -> int:
    """Parse arguments, build the client and run the chosen subcommand."""
    args = build_parser().parse_args(argv)
    try:
        client = Client(load_env())
        args.func(client, args)
    except GitHubError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
