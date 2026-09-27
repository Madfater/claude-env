# Setup

How to get the harness into a repository, keep it current, and give it credentials.

## New repository

1. On GitHub, open `Madfater/claude-env` and choose "Use this template", then clone the new repository.
2. Fill in the Project section of `CLAUDE.md`.
3. Fill in `PROJECT_COMMANDS` in `script/check.py`. See [workflow.md](workflow.md#project-checks).
4. Fill in `docs/architecture.md`.
5. Set up credentials, as described below.

## Existing repository

Run the apply script from a local clone of `claude-env`:

```bash
python3 script/apply.py /path/to/repo --dry-run   # show the plan
python3 script/apply.py /path/to/repo             # show the plan, ask, then write
```

Then do steps 2 to 5 above for any file that was created, and merge each `<file>.template` by hand.

## Updating

Pull the `claude-env` clone and run the apply script again. `.claude/harness-version` in the target records the template commit it was last synced from.

## File ownership

| Owner | Files | On apply |
| --- | --- | --- |
| Template | `.claude/skills/ship`, `.claude/skills/land`, `.claude/agents/code-reviewer.md`, `.github/PULL_REQUEST_TEMPLATE.md`, `.env.skills.example`, `docs/workflow.md`, `docs/setup.md`, `script/apply.py`, `script/check_conventions.py`, `script/github.py`, `script/test_harness.py` | Overwritten |
| Project | `CLAUDE.md`, `.claude/settings.json`, `.github/workflows/harness.yml`, `docs/README.md`, `docs/architecture.md`, `script/check.py` | Created if missing, otherwise `<file>.template` is written beside it |
| Shared | `.gitignore` | Missing harness entries are appended |

Change template-owned files in `claude-env` only. Local edits to them in a target repository are lost on the next apply.

## Credentials

Every skill that needs credentials reads them from `.env.skills`. That file sits at the root of the main checkout and is gitignored. Linked worktrees find it automatically.

```bash
cp .env.skills.example .env.skills
```

`GITHUB_TOKEN` is a fine-grained personal access token. Create it at GitHub, Settings, Developer settings, Fine-grained tokens. Limit it to the repository and give it these permissions:

| Permission | Access | Used by |
| --- | --- | --- |
| Contents | Read and write | push, sync, branch-delete |
| Pull requests | Read and write | pr-create, pr-view, pr-edit, pr-merged |
| Checks | Read | pr-checks |
| Commit statuses | Read | pr-checks |
| Administration | Read and write | repo-create, repo-set-template, only when needed |

`GITHUB_REPO` is optional and defaults to the `origin` remote.

`script/github.py` passes the token to git through a one-shot credential helper. The token never appears on a command line, and the global git config is not changed. The `gh` CLI is not required.
