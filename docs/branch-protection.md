# Branch protection — `main`

Phase 2 locks down the default branch so every change ships behind the
quality gates defined in `.github/workflows/ci.yml`. This document is
the **source of truth** for those settings: if branch protection is
ever lost (e.g. a repository reset), restore it from this file.

The settings below are configured under
**Settings → Rules → Rulesets** (or **Settings → Branches → Branch
protection rules** for the older UI) on the `main` branch.

## Targets

- Branch: `main` (the default branch).
- Applies to: everyone, including repository administrators
  (`Do not allow bypassing the above settings`).

## Required pull request before merging

- ✅ **Require a pull request before merging.**
- ✅ **Require approvals** — at least **1** approving review.
- ✅ **Dismiss stale pull request approvals when new commits are
  pushed.**
- ✅ **Require review from Code Owners.**
  - Routes are defined in `.github/CODEOWNERS`. Phase 2 requires
    Code-Owner review for changes under:
    - `helm/`
    - `bundle/` and `airgap-bundle/`
    - `.github/workflows/`
    - `backend/app/domains/` (preps for Phase 3 / Phase 7)
- ✅ **Require approval of the most recent reviewable push.**

## Required status checks

- ✅ **Require status checks to pass before merging.**
- ✅ **Require branches to be up to date before merging.**
- Required checks (all jobs from `ci.yml`):
  - `backend (lint, types, tests)`
  - `frontend (lint, types, tests)`
  - `compose validate`
  - `shellcheck`
  - `gitleaks`
  - `commitlint (Conventional Commits)`

> CodeQL (`.github/workflows/codeql.yml`) is **not** required in Phase
> 2. It is promoted to a required check in Phase 6 once the release
> pipeline lands.

## Merge style

- ✅ **Require linear history.**
- Merge button configuration (under **Settings → General → Pull
  Requests**):
  - ✅ Allow squash merging.
  - ❌ Disallow merge commits.
  - ❌ Disallow rebase merging.
  - Default commit message: **Pull request title and description**, so
    the commitlint-validated PR title becomes the squash subject.

## Commit signing

- ✅ **Require signed commits.**
  - All commits on `main` must be signed (GPG, SSH, or web-flow signed
    via the GitHub UI).

## Additional protections

- ✅ **Require conversation resolution before merging.**
- ✅ **Block force pushes.**
- ✅ **Restrict deletions** of the default branch.
- ❌ **Allow bypass for repository administrators** — disabled.

## Native secret scanning

GitHub-native secret scanning and push protection are enabled in
**Settings → Code security and analysis** in addition to the
`gitleaks` CI job:

- ✅ **Secret scanning.**
- ✅ **Push protection.**

## CodeQL triage

The scheduled CodeQL workflow (`.github/workflows/codeql.yml`) writes
findings to **Security → Code scanning alerts**. New alerts must be
triaged within one business week:

1. Confirm or dismiss with a documented reason.
2. Open a tracking issue for any alert that requires code changes.
3. Phase 6 raises the bar by promoting CodeQL to a required PR check.

## Restoring this configuration

If branch protection is missing or out of sync with this document:

1. Apply each section above via the GitHub UI on the `main` branch.
2. Cross-check the required-checks list against the current set of
   jobs in `ci.yml`; if jobs were renamed, the protection rule must
   be updated to match.
3. Open a PR titled `chore(ci): restore branch protection on main` so
   the change is auditable.
