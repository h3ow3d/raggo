# Phase 4 — Helm chart (`raggo`)

Deliver the Kubernetes install path as a first-class artifact, equivalent in capability and security posture to the Compose stack.

## Scope

### Chart layout

```
helm/raggo/
  Chart.yaml
  values.yaml
  values.schema.json
  values-gpu.yaml
  templates/
    _helpers.tpl
    backend-deployment.yaml
    backend-service.yaml
    frontend-deployment.yaml
    frontend-service.yaml
    frontend-ingress.yaml
    embedding-model-deployment.yaml
    embedding-model-service.yaml
    generation-model-deployment.yaml
    generation-model-service.yaml
    postgres-statefulset.yaml
    postgres-service.yaml
    networkpolicies.yaml
    serviceaccount.yaml
    configmap.yaml
    secret.yaml                  # only when secret.create=true
    seed-job.yaml                # Helm hook: post-install,post-upgrade
    migrate-job.yaml             # Helm hook: pre-upgrade
    tests/
      test-connection.yaml       # `helm test`
  ci/
    values-minimal.yaml
    values-prod.yaml
    values-airgapped.yaml
  AIRGAP.md                      # populated in Phase 5
```

- **Chart name:** `raggo`.
- **Published to:** `oci://ghcr.io/h3ow3d/raggo/charts/raggo`.

### Image references

- All images default to GHCR pinned by **digest** (the digests produced in Phase 1).
- `global.image.registry` overrides the registry prefix for air-gap mirrors.
- `values.schema.json` rejects `latest` tags and `imagePullPolicy: Always`.
- Public GHCR means no `imagePullSecrets` are required by default.

### Security defaults

- NetworkPolicies enabled by default: default-deny in the namespace plus explicit allows:
  - frontend → backend
  - backend → postgres
  - backend → embedding-model
  - backend → generation-model
  - model pods: deny all egress, allow only ingress from backend
- Pod security:
  - `runAsNonRoot: true`
  - `readOnlyRootFilesystem: true` where feasible (with `emptyDir` for `/tmp` and HF caches)
  - `allowPrivilegeEscalation: false`
  - `capabilities.drop: [ALL]`
  - `seccompProfile.type: RuntimeDefault`
- `automountServiceAccountToken: false` for all pods that don't need the API.
- Resource requests/limits set on every container.

### Postgres

- Default: chart-managed StatefulSet running the `postgres-pgvector` image from Phase 1, with a `PersistentVolumeClaim` and configurable `storageClassName`.
- Optional: `postgres.external.enabled=true` accepts a connection string from a referenced Secret.
- No Bitnami / community chart dependencies.

### Secrets

- Two modes:
  - `secret.create=true` (dev convenience): chart creates a Secret from values.
  - Default for prod: chart references an existing Secret name.
- `values.schema.json` enforces that exactly one mode is selected.

### Hooks

- `migrate-job.yaml` runs Alembic migrations as a `pre-upgrade` and `pre-install` hook.
- `seed-job.yaml` runs the seed generator as a `post-install` (and optionally `post-upgrade`) hook, gated by `seed.enabled`.

### GPU overlay

- `values-gpu.yaml` mirrors `docker-compose.gpu.yml`: requests `nvidia.com/gpu`, sets `runtimeClassName` if configured, and switches model containers to the GPU image variants.

### Chart CI

`.github/workflows/helm-ci.yml` runs on PRs that touch `helm/`:

- `helm lint helm/raggo`.
- `helm template helm/raggo -f helm/raggo/ci/values-minimal.yaml | kubeconform`.
- Same for `values-prod.yaml` and `values-airgapped.yaml`.
- `kube-linter` over rendered manifests.
- Spin up a `kind` cluster, `helm install`, run `helm test`, tear down.
- Schema check: confirm `values.schema.json` rejects a known-bad values file.

### Publishing

- Release workflow (Phase 6) packages the chart and pushes it to `oci://ghcr.io/h3ow3d/raggo/charts/raggo`.
- The pushed chart artifact is signed with the cosign long-lived key.

## Deliverables

- Complete chart under `helm/raggo/`.
- `helm-ci.yml` workflow.
- `docs/install-kubernetes.md` walking through a fresh install on kind and on a real cluster.
- README updated with a Kubernetes install section.

## Out of scope

- Air-gap install procedure document — Phase 5 fills in `helm/raggo/AIRGAP.md`.
- Release publishing pipeline — Phase 6.
- Observability stack — Phase 8.

## Exit criteria

- `helm install raggo ./helm/raggo` on kind, with a generated Secret, brings the full stack up; `helm test` passes.
- `helm install raggo ./helm/raggo -f helm/raggo/ci/values-airgapped.yaml --set global.image.registry=registry.local/raggo` renders without errors.
- A values file containing a `:latest` image tag fails `helm install` due to the schema.
- Pulling images during install requires no pull secret (public GHCR).
- `kubeconform` and `kube-linter` are clean.
- All security defaults listed above are present in rendered manifests for every pod.
