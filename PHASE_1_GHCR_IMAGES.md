# Phase 1 — GHCR image publishing

Stand up the container image supply chain on GitHub Container Registry before any installer or release work. Every later phase (CI, Helm chart, air-gap bundle, releases) consumes images from here, pinned by digest.

## Scope

- Image inventory under `ghcr.io/h3ow3d/raggo/`:
  - `backend`
  - `frontend`
  - `embedding-model`
  - `generation-model`
  - `postgres-pgvector`
- **Visibility: public.** Each package is created and explicitly set to public. No pull secret is required to install raggo.
- **Tagging scheme:**
  - Semver release tags: `vMAJOR.MINOR.PATCH` (e.g. `v0.1.0`).
  - Commit SHA tags: `sha-<short>` for every default-branch build.
  - `latest` published only on default-branch builds. Compose files and Helm values reference images by **digest**, never by `latest`.
  - PR previews tagged `pr-<number>-sha-<short>`, expired after 30 days.
  - Model images carry an additional revision suffix derived from the embedded model name + revision (e.g. `-mini-l6-r1`).
- **Architectures:**
  - Application images (`backend`, `frontend`, `postgres-pgvector`): `linux/amd64` and `linux/arm64` via `docker buildx`.
  - Model images: amd64 required, arm64 best-effort (skip if base image or model assets are not arm64-clean; document in image README).
- **Workflows:**
  - `build-images.yml` — a **reusable** workflow (`workflow_call`) containing the build/push/sign/SBOM/provenance logic. It takes inputs for tag set (semver, sha, pr), arch matrix, and whether to sign. This is the single source of truth for image builds.
  - `pr-images.yml` triggered on PRs that touch image source: calls `build-images.yml` with amd64-only and `sign: false`, pushing `pr-<num>-sha-<short>`.
  - `image-retention.yml` scheduled weekly: prune `sha-*` and `pr-*` images older than 30 days; never prune semver-tagged images.
  - **No standalone tag-triggered workflow is added in Phase 1.** The tag-triggered release pipeline (`release.yml`) is introduced in Phase 6 and **calls `build-images.yml`** to produce signed release images alongside the chart, bundle, and GitHub Release. This avoids two overlapping tag pipelines.
- **Auth:** GHCR push uses the workflow `GITHUB_TOKEN` with `packages: write`. No personal PAT.
- **Signing and SBOMs:**
  - Cosign signing uses the long-lived key pair stored in `COSIGN_PRIVATE_KEY` / `COSIGN_PASSWORD` repository secrets. Public key committed at `.github/cosign.pub`.
  - SBOMs attached to each release image as an OCI artifact.
  - SLSA provenance attestation attached to each release image.
- Update `README.md` with the public pull commands and the cosign verification command using `.github/cosign.pub`.

## Deliverables

- `Dockerfile` per service in its respective directory, hardened (non-root user, minimal base, no unnecessary tooling, `HEALTHCHECK` where applicable).
- `.dockerignore` per service.
- `.github/workflows/build-images.yml` (reusable), `.github/workflows/pr-images.yml`, `.github/workflows/image-retention.yml`.
- `.github/cosign.pub` committed.
- Documentation of the tagging scheme and verification procedure in `docs/images.md`.

## Out of scope

- Helm chart consumption of these images — Phase 4.
- Air-gap bundle that packages these images — Phase 5.
- Vulnerability scanning gates — Phase 6 (basic scan can be advisory here).

## Exit criteria

- A manual `workflow_dispatch` of `build-images.yml` with `sign: true` against the default branch successfully publishes signed, multi-arch images to public GHCR packages with attached SBOMs and provenance. (The full tag-triggered release flow is wired up in Phase 6.)
- `cosign verify --key .github/cosign.pub ghcr.io/h3ow3d/raggo/backend@<digest>` succeeds from an unauthenticated client.
- `docker pull ghcr.io/h3ow3d/raggo/backend@<digest>` succeeds without `docker login`.
- A PR run produces a `pr-<num>-sha-<short>` image that can be pulled.
- No reference to `:latest` exists in `helm/` (when introduced) or in any release/air-gap deployment artifact. The dev `docker-compose.yml` is exempt per `PROJECT_SPEC.md` §21.2.
- Retention workflow runs successfully on schedule and prunes only ephemeral tags.
