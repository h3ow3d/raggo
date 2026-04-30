# Phase 8 — Versioning, deployment registry, and operational maturity

The work that matters once raggo is actually deployed to real environments. After this phase, every deployed install is traceable, supportable, and recoverable.

## Scope

### Deployment registry

- A **separate, private** repository (`h3ow3d/raggo-deployments` or equivalent) tracks every deployed install.
- Each entry is a YAML file under `deployments/<client>/<env>.yaml` with at least:
  - `client`, `environment` (`dev`/`stage`/`prod`)
  - `install_target` (`compose` / `kubernetes`)
  - `raggo_version`, `chart_version`
  - `image_digests` (map of service → digest)
  - `domain_pack`
  - `installed_at`, `last_upgraded_at`, `last_tested_upgrade_path`
  - `contact` and `escalation`
- A CI check in the deployments repo validates schema and rejects unknown raggo versions.
- The release notes for raggo include a templated entry that operators can paste into the deployments repo.

### Support policy

- Document in `SUPPORT.md` in this repo:
  - Supported versions (e.g. latest two minor releases receive security patches).
  - End-of-support dates announced at minor-release time.
  - Backport criteria (security only by default).
- Encode the supported-version set in `nightly.yml` (Phase 6) so out-of-support versions stop generating CVE noise automatically.

### Runbooks (`docs/ops/`)

At minimum:

- `backup-restore-postgres.md` — for both the chart-managed StatefulSet and external Postgres, covering pgvector specifics.
- `rotate-db-password.md` — without downtime, for both Compose and Helm installs.
- `rotate-model-weights.md` — replace embedding or generation model image and re-ingest where required.
- `recover-failed-upgrade.md` — including rolling back Alembic migrations and re-pointing at the previous chart version.
- `gpu-node-failure.md` — failover, scheduling, and degraded-mode behaviour.
- `disk-full-embedding-cache.md` — diagnosis and remediation.
- `incident-response.md` — pager workflow, log collection, evidence preservation.

### Observability (opt-in)

- Helm values: `observability.metrics.enabled`, `observability.tracing.enabled`, `observability.logs.json` (default true).
- Prometheus scrape annotations on backend and model services when `metrics.enabled`.
- Structured JSON logs by default.
- OpenTelemetry tracing when `tracing.enabled`, with the agent trace exported as spans (one span per agent step: classification, retrieval, generation).
- Off by default to keep air-gapped installs trivial; enabling is one values flag.
- A reference Grafana dashboard committed under `docs/ops/dashboards/` for use when operators have a stack to import it into.

### Final audit

- Walk the security and acceptance checklists in `AGENTS.md` and `PROJECT_SPEC.md` against the running system in staging. File issues for any drift; close this phase only after they are fixed or explicitly accepted with rationale.
- Verify all phase docs (`PHASE_0_*` through `PHASE_8_*`) accurately describe the shipped system; update them where reality diverged.

## Deliverables

- `SUPPORT.md` in this repo.
- `docs/ops/` runbook set above.
- Observability values, templates, and reference dashboard in the chart.
- The separate deployments repository created and seeded with at least one real entry.
- An audit report committed at `docs/audits/<date>-final.md`.

## Out of scope

- Building a hosted control plane / fleet manager. The deployment registry is a YAML-in-git artifact, not a service.
- A custom logging / metrics backend. raggo emits standard signals; operators bring their own stack.

## Exit criteria

- Every shipped raggo install is represented in the deployments repo with version + image digests + domain pack.
- The team can answer, for any given customer, "what version are they on, what's their upgrade path, and who do we call?" from the deployments repo alone.
- All listed runbooks exist and have been dry-run at least once against staging.
- Observability flags can be flipped on a kind install and produce metrics, traces, and structured logs.
- `SUPPORT.md` is published and `nightly.yml` reads from it.
- Final audit signed off; any deferred items are tracked as issues with owners and target releases.
