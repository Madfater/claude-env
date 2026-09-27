# claude-env

A Claude Code harness template: shared rules, a `/ship` and `/land` workflow, a fresh-context code reviewer, a PR template, and Python scripts for checks and GitHub access. It saves rebuilding the same setup in every repository.

## What it contains

- **CLAUDE.md** holds the repository rules, including the Human in the loop rule, and a routing table into `docs/`.
- **`/ship`** takes a change from agreed scope through a worktree branch, checked commits, a fresh-context review and CI to an open PR. The human then judges the docs. It never merges.
- **`/issue`** works a GitHub issue: it triages it, reproduces a bug first, then runs the `/ship` gates to a PR that closes the issue on merge.
- **`/land`** cleans up after the human merges: it syncs the default branch and removes the worktree and both branches.
- **`playwright-cli` skill** drives a real browser for web UI and UX checks. `/ship` uses it as evidence for UI changes.
- **`code-reviewer` agent** is a read-only reviewer that returns a strict PASS or FAIL verdict.
- **`script/`** holds the scripts:
  - `check.py` runs the project checks.
  - `check_conventions.py` enforces the rules.
  - `github.py` is the GitHub API client and needs no `gh`.
  - `apply.py` installs the harness into existing repositories.

## Use it

- **New repository:** choose "Use this template" on GitHub.
- **Existing repository:** run `python3 script/apply.py /path/to/repo` from a clone of this repository.

See [docs/setup.md](docs/setup.md) for the steps, file ownership and credentials. See [docs/workflow.md](docs/workflow.md) for how changes ship.

## Requirements

- Python 3.10 or newer, with no third-party packages.
- git.
- Node.js and `@playwright/cli`, only for browser checks.
- A fine-grained GitHub token in `.env.skills` for the GitHub steps.
