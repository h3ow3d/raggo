# Helm chart

The `raggo` Helm chart lives at [`helm/raggo/`](./raggo) and brings up
the full raggo stack on Kubernetes with the same security posture as
the Compose deployment (NetworkPolicies, non-root pods, read-only root
filesystems where feasible, dropped capabilities, internal-only model
services).

For installation steps see
[`../docs/install-kubernetes.md`](../docs/install-kubernetes.md). For
the full design see [`../PHASE_4_HELM_CHART.md`](../PHASE_4_HELM_CHART.md)
and `PROJECT_SPEC.md` Section 21.

The chart is published as an OCI artifact at
`oci://ghcr.io/h3ow3d/raggo/charts/raggo` by the Phase 6 release
pipeline. Until that ships, install directly from the local checkout:

```bash
helm install raggo ./helm/raggo \
  --namespace raggo --create-namespace
```
