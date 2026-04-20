# GitHub Actions: CI + Semver Release Design

**Date:** 2026-04-19  
**Status:** Approved

## Problem

The repository has no automated CI or release process. Tests must be run manually, and version bumps are done by hand in `pyproject.toml`. This design introduces two GitHub Actions workflows to automate both.

## Scope

- **In:** CI workflow running tests on branch pushes, release workflow triggered on merge to main
- **Out:** Deployment, package publishing (PyPI), Docker images

---

## Workflow 1: CI (`ci.yml`)

### Trigger
Push to any branch **except** `main`.

### Purpose
Validate that tests pass before any code reaches `main`. Intended to gate PRs once branch protection is enabled on `main`.

### Steps
1. `actions/checkout` — checkout branch
2. `actions/setup-python` — Python 3.13
3. Install `uv` via `astral-sh/setup-uv`
4. `uv pip install -e .[dev]`
5. `uv run --with pytest --with pytest-asyncio python -m pytest tests/ -q`

### Notes
- Single job, no matrix — project targets one Python version (3.13)
- Fails fast on test errors

---

## Workflow 2: Release (`release.yml`)

### Trigger
Push to `main` (i.e., a PR is merged).

### Purpose
Automatically calculate the next semver version from conventional commits, update `pyproject.toml`, tag the commit, and create a GitHub Release with a changelog.

### Version Calculation
Uses `paulhatch/semantic-version@v5.4.0`, which walks git history from the last tag using conventional commit patterns:

| Commit prefix | Version bump |
|---|---|
| `feat:` / `feat(scope):` | minor |
| `feat!:`, `fix!:`, `BREAKING CHANGE:` in body | major |
| `fix:`, `chore:`, `refactor:`, `docs:`, etc. | patch |

No PR labels required — version intent is encoded in commit messages.

### Steps
1. `actions/checkout` with `fetch-depth: 0` (required for full tag history)
2. `paulhatch/semantic-version@v5.4.0` → outputs `version` (e.g. `1.2.3`) and `version_tag` (e.g. `v1.2.3`)
3. **Idempotency guard** — skip remaining steps if current commit is already tagged with `version_tag`
4. Update `pyproject.toml` version field with a Python one-liner (`re` module)
5. Commit bump back to `main`: `chore: bump version to vX.Y.Z [skip ci]`  
   (`[skip ci]` prevents the commit from re-triggering this workflow)
6. Push tag `vX.Y.Z`
7. Generate changelog from `git log` since previous tag, filtered to conventional commit prefixes
8. Create GitHub Release via `gh release create` with the changelog as body

### Permissions
Workflow needs `contents: write` permission to push commits, tags, and create releases.

### Git Identity
Commits are made as `github-actions[bot]` using the standard bot email.

---

## Conventions Required

For the release workflow to produce meaningful changelogs and correct version bumps, commit messages should follow [Conventional Commits](https://www.conventionalcommits.org/):

- `feat: add new thing` → minor bump
- `fix: correct broken thing` → patch bump
- `feat!: rename API` or body contains `BREAKING CHANGE:` → major bump
- `chore:`, `refactor:`, `docs:`, `test:` → patch bump

---

## File Layout

```
.github/
  workflows/
    ci.yml
    release.yml
```

The existing `dependabot.yml` already covers auto-updating action versions weekly.
