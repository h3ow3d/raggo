# Phase 6 — Release pipeline, security scanning, and upgrade safety

With installable artifacts (images, chart, bundle) all working in isolation, harden the release process that produces them and the upgrade story between releases.

## Scope

### Conventional Commits + automated releases

- Adopt **release-please** (default) or `semantic-release` to drive versioning and changelog generation from Conventional Commit messages on the default branch.
- A `release-please` PR is kept open against `main`, accumulating the next version bump and changelog. Merging it tags the release.
- Tagging triggers `release.yml`.

### `release.yml` (on `v*` tags)

Single workflow that performs, in order:

1. Re-run the full PR check suite for the release commit.
2. **Call** the reusable `build-images.yml` from Phase 1 with `sign: true` and the release tag set (semver + digest), pushing multi-arch images to GHCR.
3. Generate SBOMs (syft) and SLSA provenance attestations.
4. Sign images and the chart with cosign using the long-lived key.
5. `helm package helm/raggo` with the release version, push to `oci://ghcr.io/h3ow3d/raggo/charts/raggo`, sign the chart artifact.
6. Build the air-gap bundle (Phase 5) referencing the just-published digests.
7. Trivy + Grype scans on every release image; fail the release on HIGH/CRITICAL not listed in `.trivyignore`. Upload SARIF to the Security tab.
8. Create the GitHub Release: changelog from release-please, attach bundle tarball, chart `.tgz`, SBOMs, scan reports, `cosign.pub`, and verification instructions.

### Security scanning

- **Per-PR:** Trivy fs scan on changed Dockerfiles and lockfiles (advisory in this phase only if it produces too much noise; otherwise gating).
- **Per-release:** Trivy + Grype on every image, gating on HIGH/CRITICAL.
- **Promote CodeQL** from scheduled-only (Phase 2) to PR-gating for changes under `backend/` and `frontend/`.
- `.trivyignore` and `.grype.yaml` documented; allowlisted CVEs require justification in a PR comment.

### Upgrade smoke tests

`.github/workflows/upgrade-smoke.yml` runs on PRs that touch the chart, the per-domain `init.sql` files, or `backend/app/core/database.py`:

- **Matrix:** `(N = previous-release, N+1 = PR build) × (compose, helm)`.
- **Procedure:**
  1. Install N from its release artifacts.
  2. Seed and ingest a fixture dataset.
  3. Upgrade to N+1 (Compose: pull new images, `up -d`; Helm: `helm upgrade`, which triggers the pre-upgrade migration hook).
  4. Assert all rows are still present, `/health` is green, `/search` and `/query` still work against the upgraded stack.
- Failure blocks merge.

### Nightly maintenance

`.github/workflows/nightly.yml`:

- Re-scan the latest released images with Trivy + Grype against the current vuln DB.
- Open or update a single tracking issue per supported release for any new HIGH/CRITICAL findings.
- Run Renovate (or Dependabot) for backend, frontend, GitHub Actions, and Dockerfile bases. PRs auto-labelled and routed for review.

### Staging auto-deploy

On merge to the default branch:

- `staging-deploy.yml` spins up a kind cluster, installs the chart at the freshly built default-branch digest, runs Playwright + pytest E2E.
- A failing staging deploy posts to the team chat and opens an issue. It does not block the merge (it has already happened) but blocks the next release-please PR from merging until staging is green.

## Deliverables

- `release-please-config.json` and manifest, or equivalent `semantic-release` config.
- `.github/workflows/release.yml`, `.github/workflows/upgrade-smoke.yml`, `.github/workflows/nightly.yml`, `.github/workflows/staging-deploy.yml`.
- `.trivyignore`, `.grype.yaml`, and `docs/security-scanning.md` describing the scanning policy and allowlist process.
- `docs/release-process.md` documenting the cut-a-release runbook.
- README updated with verification instructions (cosign verify image + chart, SBOM download).

## Out of scope

- Domain pack expansion — Phase 7.
- Operational runbooks beyond the release runbook — Phase 8.

## Exit criteria

- A merged release-please PR produces a tag that drives a complete signed release: signed multi-arch images, signed chart at `oci://ghcr.io/h3ow3d/raggo/charts/raggo`, attached SBOMs and provenance, attached air-gap bundle, GitHub Release with changelog.
- Verification works end-to-end: `cosign verify --key .github/cosign.pub <ref>` succeeds for both image (`ghcr.io/h3ow3d/raggo/<image>@<digest>`) and chart (`ghcr.io/h3ow3d/raggo/charts/raggo:<version>`) artifacts of the release.
- Upgrade smoke test passes for both Compose and Helm from the previous release to the PR build.
- A deliberately introduced HIGH CVE in a Dockerfile fails the release scan.
- CodeQL findings on a backend PR block the merge.
- Nightly workflow has run at least once and either reported clean or opened a tracking issue.
