# raggo container images

raggo publishes its container images to GitHub Container Registry under
[`ghcr.io/h3ow3d/raggo`](https://github.com/h3ow3d/raggo/pkgs/container).
This document describes the image inventory, the tagging scheme, and
the verification procedure that consumers run before installing a
release.

The supply chain is defined by [`PHASE_1_GHCR_IMAGES.md`](../PHASE_1_GHCR_IMAGES.md)
and `PROJECT_SPEC.md` §21.2 / §21.3.

## Image inventory

| Image                                           | Built from           | Default architectures                                                                       |
| ----------------------------------------------- | -------------------- | ------------------------------------------------------------------------------------------- |
| `ghcr.io/h3ow3d/raggo/backend`                  | `backend/Dockerfile` | `linux/amd64`, `linux/arm64`                                                                |
| `ghcr.io/h3ow3d/raggo/frontend`                 | `frontend/Dockerfile`| `linux/amd64`, `linux/arm64`                                                                |
| `ghcr.io/h3ow3d/raggo/postgres-pgvector`        | `db/Dockerfile`      | `linux/amd64`, `linux/arm64`                                                                |
| `ghcr.io/h3ow3d/raggo/embedding-model`          | `models/embedding/Dockerfile` | `linux/amd64` by default; optional `linux/arm64` best-effort when `model_archs` includes it |
| `ghcr.io/h3ow3d/raggo/generation-model`         | `models/generation/Dockerfile`| `linux/amd64` by default; optional `linux/arm64` best-effort when `model_archs` includes it |

All packages are **public**: pulling does not require `docker login`
and installs do not require an image pull secret.

Model images are published amd64-only by default (the workflow's
`model_archs` input defaults to `linux/amd64`). arm64 builds for
those services are best-effort because the upstream model assets and
torch wheels are not always arm64-clean; if you need arm64 model
images, invoke `build-images.yml` with `model_archs:
linux/amd64,linux/arm64` and supply arm64-compatible wheels, or run
the model services on amd64 nodes.

## Tagging scheme

Every push to GHCR uses one or more of the following tags. Release
deployment artifacts (the published Helm chart's default values, the
air-gap bundle's pinned Compose overlay, installers) reference images
**by digest** only — never by `:latest` — per `PROJECT_SPEC.md` §21.2.

| Tag pattern              | When                                             | Pruned? |
| ------------------------ | ------------------------------------------------ | ------- |
| `vMAJOR.MINOR.PATCH`     | Tag-triggered release pipeline (Phase 6).        | Never.  |
| `sha-<short>`            | Every default-branch build.                      | After 30 days. |
| `pr-<num>-sha-<short>`   | Every pull request build (`pr-images.yml`).      | After 30 days. |
| `latest`                 | Default-branch builds when `push_latest=true`.   | Never.  |
| `<model-slug>-r<rev>`    | Additional tag on `embedding-model` / `generation-model` images, derived from the embedded model name + revision (e.g. `all-minilm-l6-v2-r1`). | Never. |
| `vX.Y.Z-<model-slug>-r<rev>` | Additional release-pinned tag on model images. | Never. |

Retention is enforced by `image-retention.yml`, which runs every
Sunday at 03:00 UTC and prunes only `sha-*` and `pr-*` versions older
than 30 days. Multi-tagged versions are preserved if any tag is
non-ephemeral.

## Workflows

The image supply chain is implemented as three workflows under
[`.github/workflows/`](../.github/workflows/):

- **`build-images.yml`** — the **single source of truth** for image
  builds. Reusable (`workflow_call`) and also exposed as
  `workflow_dispatch` for the Phase 1 manual exit-criterion run. It
  builds and pushes every image, optionally signs with cosign and
  attaches an SBOM (SPDX JSON via syft) and a SLSA build provenance
  attestation as OCI artifacts.
- **`pr-images.yml`** — calls `build-images.yml` on PRs that touch
  image source with `sign: false` and amd64-only, pushing
  `pr-<num>-sha-<short>` previews.
- **`image-retention.yml`** — scheduled weekly job that prunes stale
  ephemeral tags only. Semver and `latest` are never pruned.

The tag-triggered release pipeline (`release.yml`) is intentionally
introduced in Phase 6, not Phase 1; when it lands it will **call
`build-images.yml`** rather than duplicate its build/sign logic.

## Signing and SBOM/provenance

raggo uses `cosign` with a long-lived key pair (see
[`.github/cosign.README.md`](../.github/cosign.README.md)). The public
key is committed at [`.github/cosign.pub`](../.github/cosign.pub) so
verification works without trusting any third-party identity.

Each release image carries:

- A **cosign signature** (key-based, by digest).
- An **SBOM** (SPDX JSON, generated with syft) attached as an OCI
  attestation via [`actions/attest-sbom`](https://github.com/actions/attest-sbom).
- A **SLSA build provenance** attestation via
  [`actions/attest-build-provenance`](https://github.com/actions/attest-build-provenance).

PR-preview images are unsigned and have no attestations.

## Pulling images

Public images can be pulled with no credentials. Always pin by digest
in production:

```bash
docker pull ghcr.io/h3ow3d/raggo/backend@sha256:<digest>
docker pull ghcr.io/h3ow3d/raggo/frontend@sha256:<digest>
docker pull ghcr.io/h3ow3d/raggo/postgres-pgvector@sha256:<digest>
docker pull ghcr.io/h3ow3d/raggo/embedding-model@sha256:<digest>
docker pull ghcr.io/h3ow3d/raggo/generation-model@sha256:<digest>
```

Replace `<digest>` with the value advertised in the corresponding
GitHub Release notes (Phase 6).

## Verifying signatures

Once the maintainer has replaced the placeholder
[`.github/cosign.pub`](../.github/cosign.pub) with the real public key
(see [`.github/cosign.README.md`](../.github/cosign.README.md)),
consumers can verify a signed image directly against the committed
key:

```bash
cosign verify \
  --key https://raw.githubusercontent.com/h3ow3d/raggo/main/.github/cosign.pub \
  ghcr.io/h3ow3d/raggo/backend@sha256:<digest>
```

Or against a local checkout of this repository:

```bash
cosign verify \
  --key .github/cosign.pub \
  ghcr.io/h3ow3d/raggo/backend@sha256:<digest>
```

To inspect the SBOM and SLSA provenance attestations attached to a
release image:

```bash
cosign download attestation \
  --predicate-type=https://spdx.dev/Document \
  ghcr.io/h3ow3d/raggo/backend@sha256:<digest>

cosign download attestation \
  --predicate-type=https://slsa.dev/provenance/v1 \
  ghcr.io/h3ow3d/raggo/backend@sha256:<digest>
```

## Manually triggering a signed multi-arch build

This is the Phase 1 exit-criterion path. From the Actions tab, run
`build-images.yml` via "Run workflow" against the default branch with:

| Input         | Value                       |
| ------------- | --------------------------- |
| `semver`      | empty (or a release tag)    |
| `push_latest` | `false`                     |
| `sign`        | `true`                      |

The job will publish multi-arch images for the application services
and amd64 (best-effort arm64) for the model images, sign each by
digest with cosign, and attach SBOMs and SLSA provenance.
