# GitHub Actions CI + Semver Release Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a CI workflow that runs tests on non-main branch pushes and a release workflow that auto-bumps semver and creates a GitHub Release on every merge to main.

**Architecture:** Two independent workflow files in `.github/workflows/`. The CI workflow is stateless — checkout, install, test, done. The release workflow is stateful — it reads git history via `paulhatch/semantic-version`, writes back to `pyproject.toml`, commits, tags, and creates a GitHub Release.

**Tech Stack:** GitHub Actions, `paulhatch/semantic-version@v5.4.0`, `astral-sh/setup-uv`, `actions/setup-python@v5`, `actions/checkout@v4`, Python `re` module for version rewriting, `gh` CLI for release creation.

---

## File Structure

| File | Action | Purpose |
|---|---|---|
| `.github/workflows/ci.yml` | Create | Run pytest on all non-main branch pushes |
| `.github/workflows/release.yml` | Create | Bump version, tag, create GitHub Release on merge to main |

---

## Task 1: Create CI Workflow

**Files:**
- Create: `.github/workflows/ci.yml`

- [ ] **Step 1: Create the workflows directory**

```bash
mkdir -p .github/workflows
```

- [ ] **Step 2: Create `.github/workflows/ci.yml`**

```yaml
name: CI

on:
  push:
    branches-ignore:
      - main

jobs:
  test:
    runs-on: ubuntu-latest

    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.13"

      - name: Install uv
        uses: astral-sh/setup-uv@v5

      - name: Install dependencies
        run: uv pip install -e ".[dev]" --system

      - name: Run tests
        run: uv run --with pytest --with pytest-asyncio python -m pytest tests/ -q
```

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: add CI workflow to run tests on branch pushes

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

---

## Task 2: Create Release Workflow

**Files:**
- Create: `.github/workflows/release.yml`

- [ ] **Step 1: Create `.github/workflows/release.yml`**

```yaml
name: Release

on:
  push:
    branches:
      - main

permissions:
  contents: write

jobs:
  release:
    runs-on: ubuntu-latest

    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - name: Calculate next version
        id: version
        uses: paulhatch/semantic-version@v5.4.0
        with:
          tag_prefix: "v"
          major_pattern: "(BREAKING CHANGE|feat!|fix!|refactor!)"
          minor_pattern: "feat:"
          version_format: "${major}.${minor}.${patch}"
          bump_each_commit: false

      - name: Check if already tagged
        id: check_tag
        run: |
          if git rev-parse "v${{ steps.version.outputs.version }}" >/dev/null 2>&1; then
            echo "already_tagged=true" >> "$GITHUB_OUTPUT"
          else
            echo "already_tagged=false" >> "$GITHUB_OUTPUT"
          fi

      - name: Update pyproject.toml version
        if: steps.check_tag.outputs.already_tagged == 'false'
        run: |
          python -c "
          import re, pathlib
          p = pathlib.Path('pyproject.toml')
          content = p.read_text()
          content = re.sub(
              r'^version\s*=\s*\"[^\"]+\"',
              'version = \"${{ steps.version.outputs.version }}\"',
              content,
              flags=re.MULTILINE
          )
          p.write_text(content)
          "

      - name: Commit version bump
        if: steps.check_tag.outputs.already_tagged == 'false'
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
          git add pyproject.toml
          git commit -m "chore: bump version to v${{ steps.version.outputs.version }} [skip ci]"
          git push

      - name: Push tag
        if: steps.check_tag.outputs.already_tagged == 'false'
        run: |
          git tag "v${{ steps.version.outputs.version }}"
          git push origin "v${{ steps.version.outputs.version }}"

      - name: Generate changelog
        if: steps.check_tag.outputs.already_tagged == 'false'
        id: changelog
        run: |
          PREV_TAG=$(git describe --tags --abbrev=0 HEAD^ 2>/dev/null || echo "")
          if [ -z "$PREV_TAG" ]; then
            LOG=$(git log --pretty=format:"- %s" | grep -E "^- (feat|fix|chore|refactor|docs|test|perf|ci|style|build)(\(|:|!)" || true)
          else
            LOG=$(git log "${PREV_TAG}..HEAD^" --pretty=format:"- %s" | grep -E "^- (feat|fix|chore|refactor|docs|test|perf|ci|style|build)(\(|:|!)" || true)
          fi
          if [ -z "$LOG" ]; then
            LOG="- chore: version bump"
          fi
          echo "CHANGELOG<<EOF" >> "$GITHUB_OUTPUT"
          echo "$LOG" >> "$GITHUB_OUTPUT"
          echo "EOF" >> "$GITHUB_OUTPUT"

      - name: Create GitHub Release
        if: steps.check_tag.outputs.already_tagged == 'false'
        env:
          GH_TOKEN: ${{ github.token }}
        run: |
          gh release create "v${{ steps.version.outputs.version }}" \
            --title "v${{ steps.version.outputs.version }}" \
            --notes "${{ steps.changelog.outputs.CHANGELOG }}"
```

- [ ] **Step 2: Commit**

```bash
git add .github/workflows/release.yml
git commit -m "ci: add release workflow with semver auto-bump

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

---

## Task 3: Verify Locally

- [ ] **Step 1: Validate workflow YAML syntax**

```bash
python -c "
import yaml, pathlib
for f in ['.github/workflows/ci.yml', '.github/workflows/release.yml']:
    yaml.safe_load(pathlib.Path(f).read_text())
    print(f'OK: {f}')
"
```

Expected output:
```
OK: .github/workflows/ci.yml
OK: .github/workflows/release.yml
```

- [ ] **Step 2: Confirm the version regex works against the current pyproject.toml**

```bash
python -c "
import re, pathlib
p = pathlib.Path('pyproject.toml')
content = p.read_text()
match = re.search(r'^version\s*=\s*\"([^\"]+)\"', content, flags=re.MULTILINE)
print('Current version found:', match.group(1) if match else 'NOT FOUND')
new = re.sub(r'^version\s*=\s*\"[^\"]+\"', 'version = \"9.9.9\"', content, flags=re.MULTILINE)
assert '9.9.9' in new, 'Substitution failed'
print('Substitution test: OK')
"
```

Expected output:
```
Current version found: 0.1.0
Substitution test: OK
```

- [ ] **Step 3: Push to a non-main branch and confirm CI workflow appears in GitHub Actions**

```bash
git checkout -b test/ci-smoke
git commit --allow-empty -m "test: trigger CI smoke test"
git push -u origin test/ci-smoke
```

Then navigate to `https://github.com/jwill824/scrooge/actions` and confirm the **CI** workflow appears and runs.

- [ ] **Step 4: Clean up smoke-test branch**

```bash
git checkout main
git branch -d test/ci-smoke
git push origin --delete test/ci-smoke
```
