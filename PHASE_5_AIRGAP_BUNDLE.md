# Phase 5 — Air-gap bundle and offline install

Make raggo installable on hosts and clusters with zero internet access for both the Compose path and the Helm path.

## Scope

### Bundle contents

A single release-versioned tarball `raggo-bundle-<version>.tar.gz` containing:

- `images/` — `docker save` tarballs for every release image, pulled by **digest** from GHCR. One file per image or a single combined archive.
- `chart/raggo-<version>.tgz` — packaged Helm chart.
- `compose/` — copy of `docker-compose.yml`, `docker-compose.gpu.yml`, and `.env.example` with image references rewritten to digests.
- `sboms/` — SBOMs for every image and for the chart.
- `scans/` — Trivy / Grype reports for every image.
- `cosign.pub` — the public verification key.
- `install.sh` — Compose-path offline installer.
- `doctor.sh` — preflight checks.
- `MANIFEST.json` — list of every artifact with digest + size.
- `README-AIRGAP.md` — operator-facing instructions.

### Compose offline install

`install.sh`:

1. Verifies prerequisites (Docker, disk space, optional GPU).
2. `docker load` for every image tarball.
3. Verifies image digests against `MANIFEST.json`.
4. (Optional) `cosign verify` against `cosign.pub`.
5. Prompts for required `.env` values and writes `.env`.
6. `docker compose up -d`.
7. Runs the seed/migrate steps already wired into the backend container.

### Kubernetes offline install

Documented in `helm/raggo/AIRGAP.md`:

1. On a connected jumpbox: `helm pull oci://ghcr.io/h3ow3d/raggo/charts/raggo --version <v>` and download the bundle.
2. Transfer to the air-gapped network.
3. Mirror images to the customer's internal registry (a helper script `bundle/mirror-images.sh` is provided that wraps `crane` or `skopeo`).
4. `helm install raggo ./raggo-<v>.tgz --set global.image.registry=<internal-registry>`.

### Doctor script

`doctor.sh` validates a host or cluster before install. Modes:

- `--mode compose`: Docker version, Compose plugin, free disk, free memory, optional GPU + driver, port availability.
- `--mode kubernetes`: `kubectl` reachable, cluster version, default StorageClass present, ability to pull from the supplied internal registry, sufficient node resources, optional GPU node presence.

Exit non-zero with actionable messages on any failure.

### Air-gap smoke tests in CI

`.github/workflows/airgap-smoke.yml` runs on PRs touching the bundle, the chart, or model code:

- **Compose air-gap path:** build images, run the bundle build, on a fresh runner with model containers attached only to a network with no egress (e.g. `--network none` or an iptables rule), run `install.sh`, then exercise `/health`, ingestion, vector search, and `/query`. Fail if any container makes an outbound request.
- **Kubernetes air-gap path:** spin up kind, push images to a local registry that proxies nothing, mirror chart, install with `global.image.registry` pointing at the local registry, block egress from model pods via NetworkPolicy + a kind-level deny, run `helm test` plus a smoke E2E.

## Deliverables

- `bundle/build.sh` producing the tarball deterministically.
- `bundle/install.sh`, `bundle/doctor.sh`, `bundle/mirror-images.sh`.
- `helm/raggo/AIRGAP.md` populated.
- `airgap-smoke.yml` workflow.
- README section: "Installing offline" covering both paths.

## Out of scope

- Attaching the bundle to GitHub Releases — Phase 6.
- Customer-specific registry configurations — those are deployment-time concerns, captured by the deployment registry in Phase 8.

## Exit criteria

- `bundle/build.sh` produces a tarball whose `MANIFEST.json` digests match the GHCR digests for the same release version.
- A genuinely offline VM installs raggo from the bundle via `install.sh` and passes a smoke test (health, ingest, vector search, agent query).
- A genuinely offline kind cluster installs the chart from the bundle via the documented procedure and passes `helm test` plus the smoke E2E.
- Air-gap smoke workflow passes in CI on every relevant PR.
- `doctor.sh` correctly fails on a deliberately misconfigured host (e.g. missing Docker, missing StorageClass) and passes on a healthy one.
