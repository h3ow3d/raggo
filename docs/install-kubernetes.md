# Installing raggo on Kubernetes

raggo ships a first-class Helm chart at `helm/raggo/`. The chart
brings up the full stack — backend API, frontend SPA, PostgreSQL with
pgvector, embedding model service, generation model service — with
the same security posture as the Compose stack:

- PostgreSQL is reachable only from the backend (NetworkPolicy).
- The model services are reachable only from the backend; their pods
  have no egress at all (no Internet, no other workloads).
- Every pod runs as non-root with a `RuntimeDefault` seccomp profile,
  `readOnlyRootFilesystem` (where feasible), and dropped capabilities.

Once Phase 6 publishes the chart, the same install can be performed
directly from the OCI registry:

```bash
helm install raggo oci://ghcr.io/h3ow3d/raggo/charts/raggo \
  --namespace raggo --create-namespace
```

Until then, install from a local checkout as shown below.

## Prerequisites

- Kubernetes ≥ 1.27 with a CNI that enforces NetworkPolicy
  (Calico, Cilium, etc.). Plain kind works for smoke tests but does
  not enforce policies unless you install a CNI that does.
- Helm ≥ 3.14.
- A storage class that supports `ReadWriteOnce` PVCs (the chart's
  default Postgres StatefulSet uses one).
- Cluster pull access to `ghcr.io/h3ow3d/raggo/*` (public — no
  pull secret required).

## Quick start on kind

```bash
# 1. Create a local cluster.
kind create cluster --name raggo

# 2. Install the chart with chart-managed secrets.
helm install raggo ./helm/raggo \
  --namespace raggo --create-namespace \
  --set secret.values.postgresPassword=$(openssl rand -hex 16) \
  --wait --timeout 20m

# 3. Verify.
helm test raggo --namespace raggo

# 4. Reach the frontend via port-forward.
kubectl -n raggo port-forward svc/raggo-raggo-frontend 8080:80
# Open http://localhost:8080
```

If you want kind to enforce NetworkPolicies, install Calico or
Cilium into the kind cluster before `helm install`. Without a
policy-aware CNI the manifests are still rendered and applied, just
not enforced.

## Quick start on a real cluster

Production installs typically:

- pin every image by digest,
- reference an externally managed Secret rather than letting the
  chart create one,
- enable the Ingress.

```yaml
# values-prod.yaml
backend:
  image:
    digest: "sha256:…"
frontend:
  image:
    digest: "sha256:…"
  ingress:
    enabled: true
    className: nginx
    host: raggo.example.com
    annotations:
      cert-manager.io/cluster-issuer: letsencrypt-prod
    tls:
      - secretName: raggo-tls
        hosts:
          - raggo.example.com
embeddingModel:
  image:
    digest: "sha256:…"
generationModel:
  image:
    digest: "sha256:…"
postgres:
  internal:
    image:
      digest: "sha256:…"
    storage:
      size: 50Gi
      storageClassName: gp3
secret:
  create: false
  existingName: raggo-app-secrets
```

```bash
kubectl create namespace raggo
kubectl -n raggo create secret generic raggo-app-secrets \
  --from-literal=postgresPassword="$(openssl rand -hex 32)"

helm install raggo ./helm/raggo \
  --namespace raggo \
  -f values-prod.yaml \
  --wait --timeout 30m
```

## Migrate and seed jobs

The chart ships two Helm hooks:

- **migrate** (`pre-install`, `pre-upgrade`) runs `python -m app.cli
  migrate` against Postgres. It enables `pgvector` and applies the
  active domain pack's `init.sql` if the schema is missing. The
  operation is idempotent.
- **seed** (`post-install`, optionally `post-upgrade`) runs `python -m
  app.cli seed`. The backend code seeds only when the domain has no
  existing rows, so re-running the hook is also idempotent.

Disable seeding for production installs that bring their own data:

```yaml
seed:
  enabled: false
```

## External Postgres

To point the chart at an existing PostgreSQL with `pgvector`
installed:

```yaml
postgres:
  external:
    enabled: true
    existingSecret: raggo-postgres-url
    secretKey: url
```

The referenced Secret must contain a SQLAlchemy-compatible
`postgresql+psycopg://user:pass@host:port/db` URL under
`url`. The migrate, seed, and backend pods read `DATABASE_URL` from
this Secret.

## GPU mode

```bash
helm install raggo ./helm/raggo \
  --namespace raggo --create-namespace \
  -f ./helm/raggo/values-gpu.yaml
```

Only the generation-model service requests a GPU, mirroring
`docker-compose.gpu.yml`. Override `generationModel.image.repository`
or `digest` if your release pipeline ships the GPU variant under a
different name.

## Air-gapped installs

```bash
helm install raggo ./helm/raggo \
  -f ./helm/raggo/ci/values-airgapped.yaml \
  --set global.image.registry=registry.local/raggo
```

The full air-gap procedure lands in Phase 5 — see
[`helm/raggo/AIRGAP.md`](../helm/raggo/AIRGAP.md).

## Uninstalling

```bash
helm uninstall raggo --namespace raggo
# PVCs are kept by default; remove them explicitly if desired.
kubectl -n raggo delete pvc -l app.kubernetes.io/instance=raggo
kubectl delete namespace raggo
```

## Troubleshooting

### `helm test` times out

Run `kubectl -n raggo describe pods` and look at the embedding/
generation pods. Cold-loading the default models on CPU can take a
few minutes; the backend's startup probe gives them up to ~5 minutes.

### `:latest` rejected at install time

The chart's `values.schema.json` rejects `latest` tags and
`imagePullPolicy: Always` to keep installs reproducible. Pin by
digest or use any other tag.

### `helm install` fails with `secret.existingName is required`

You set `secret.create: false` without providing `secret.existingName`.
Either flip `create` back to `true` (and set
`secret.values.postgresPassword`) or reference a pre-provisioned
Secret with `secret.existingName`.

### Network policies block traffic

If you are running on a cluster without a policy-aware CNI, set
`networkPolicies.enabled=false`. The chart will still render and the
stack will run, but the model and database services will not be
isolated by the cluster.
