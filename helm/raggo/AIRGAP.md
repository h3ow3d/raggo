# Air-gap install procedure

The air-gap install procedure for the raggo Helm chart is delivered in
**Phase 5** alongside the offline release bundle.

This file is intentionally a placeholder: the chart already supports
air-gap installs today via:

- `global.image.registry` — point every pull at your internal mirror.
- `global.image.pullSecrets` — attach a registry credential.
- `helm/raggo/ci/values-airgapped.yaml` — example values rendered by
  the chart's CI workflow.

Until Phase 5 lands, the canonical example invocation is:

```bash
helm install raggo ./helm/raggo \
  -f helm/raggo/ci/values-airgapped.yaml \
  --set global.image.registry=registry.local/raggo
```

See `PHASE_5_AIRGAP_BUNDLE.md` for the full plan.
