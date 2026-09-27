# Workflow

How changes move from an idea to a merged PR, and the conventions that apply along the way.

## Flow

| Step | Who | What |
| --- | --- | --- |
| `/ship` | Claude, stopping for the human | Scope, worktree, commits, evidence, review, PR, CI, docs |
| Merge | Human, on GitHub | Always the human's act |
| `/land` | Claude, one confirmation | Sync the default branch, remove the worktree and both branches |

The gate-by-gate details live in `.claude/skills/ship/SKILL.md` and `.claude/skills/land/SKILL.md`.

## Human in the loop

The rule is defined in CLAUDE.md. In `/ship` it produces these stop points:

1. Scope: task, branch name, acceptance criteria.
2. Before the first push and PR creation.
3. Before any push after a CI failure.
4. Docs: the human approves, edits or rejects the docs change.
5. After a second failed review.

The permission allowlist in `.claude/settings.json` is a second safety net. Read-only commands run without prompts. `github.py push`, `pr-create`, `pr-edit`, `branch-delete` and the repo commands always prompt.

## Verification

- Acceptance criteria are agreed at the start of `/ship`. Each is tagged `runnable`, meaning a command proves it, or `judgment`.
- Runnable criteria are executed, and their command and output are kept as evidence.
- `/verify` runs only when the change touches a runnable app or UI.
- One fresh-context `code-reviewer` agent judges the criteria and the code together. It returns `VERDICT: PASS | FAIL`.
- `PASS` needs every criterion to pass and zero blocking findings. After two FAILs, the flow hands over to the human.

## Branches

- `<type>/<kebab-summary>`, with type one of `feat`, `fix`, `docs`, `chore`, `refactor`.
- Worktrees live in `.claude/worktrees/`, which is gitignored. They branch from `origin/<default-branch>`.

## Commits

- Conventional Commits, lowercase, scope optional: `feat(api): add card lookup`, `fix: close the pool on startup failure`.
- One coherent step per commit. `script/check.py` and `script/check_conventions.py --staged` must pass first.
- Add a body only when the reason is not visible in the diff. Add no trailers.

## Pull requests

- The body follows `.github/PULL_REQUEST_TEMPLATE.md`: Summary, Changes, Review, Verification, Docs and Merge checklist.
- The title is the lead commit subject.
- The body ends with the plain line `Generated with Claude Code`.

## Project checks

`script/check.py` runs the harness self-tests, then `PROJECT_COMMANDS`. Fill that list in once per repository. Examples:

```python
PROJECT_COMMANDS = [
    ["uv", "run", "ruff", "check", "."],
    ["uv", "run", "pytest", "-q"],
]
```

```python
PROJECT_COMMANDS = [
    ["npm", "run", "lint"],
    ["npm", "run", "build"],
    ["npm", "test"],
]
```

```python
PROJECT_COMMANDS = [
    ["go", "vet", "./..."],
    ["go", "test", "./..."],
]
```

CI runs the same script through `.github/workflows/harness.yml`, so local and CI checks cannot drift apart.

## Delegation

- Never verify your own work. Reviews and acceptance go to a fresh-context agent.
- Delegate large reads to a sub-agent and keep only its conclusions: sweeping many files, long logs, open-ended research.
- Give a sub-agent the goal, the acceptance criteria and the exact report format you need back.

## Lessons log

Topic files under `docs/` may end with a `## Lessons` section, one line per lesson:

```
- YYYY-MM-DD | Symptom: ... | Cause: ... | Rule: ...
```

Add a lesson when a mistake would likely repeat. Remove it once the rule has moved into the relevant doc or into CLAUDE.md.
