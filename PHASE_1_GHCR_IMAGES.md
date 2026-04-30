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
  - `release-images.yml` triggered on `v*` tags: build, push semver + digest, generate SBOM (syft), generate SLSA provenance, sign with cosign.
  - `pr-images.yml` triggered on PRs that touch image source: amd64-only build, push as `pr-<num>-sha-<short>`, no signing.
  - `image-retention.yml` scheduled weekly: prune `sha-*` and `pr-*` images older than 30 days; never prune semver-tagged images.
- **Auth:** GHCR push uses the workflow `GITHUB_TOKEN` with `packages: write`. No personal PAT.
- **Signing and SBOMs:**
  - Cosign signing uses the long-lived key pair stored in `COSIGN_PRIVATE_KEY` / `COSIGN_PASSWORD` repository secrets. Public key committed at `.github/cosign.pub`.
  - SBOMs attached to each release image as an OCI artifact.
  - SLSA provenance attestation attached to each release image.
- Update `README.md` with the public pull commands and the cosign verification command using `.github/cosign.pub`.

## Deliverables

- `Dockerfile` per service in its respective directory, hardened (non-root user, minimal base, no unnecessary tooling, `HEALTHCHECK` where applicable).
- `.dockerignore` per service.
- `.github/workflows/release-images.yml`, `.github/workflows/pr-images.yml`, `.github/workflows/image-retention.yml`.
- `.github/cosign.pub` committed.
- Documentation of the tagging scheme and verification procedure in `docs/images.md`.

## Out of scope

- Helm chart consumption of these images — Phase 4.
- Air-gap bundle that packages these images — Phase 5.
- Vulnerability scanning gates — Phase 6 (basic scan can be advisory here).

## Exit criteria

- Tagging `v0.0.1` (a dry-run release) successfully publishes signed, multi-arch images to public GHCR packages with attached SBOMs and provenance.
- `cosign verify --key .github/cosign.pub ghcr.io/h3ow3d/raggo/backend@<digest>` succeeds from an unauthenticated client.
- `docker pull ghcr.io/h3ow3d/raggo/backend@<digest>` succeeds without `docker login`.
- A PR run produces a `pr-<num>-sha-<short>` image that can be pulled.
- No reference to `:latest` exists in `docker-compose.yml`, `helm/` (when introduced), or release artifacts.
- Retention workflow runs successfully on schedule and prunes only ephemeral tags.
