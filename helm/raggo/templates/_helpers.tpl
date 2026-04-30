{{/*
Common helpers for the raggo chart.

Conventions:
  - Names are derived from `Release.Name` and `Chart.Name`, capped at
    63 characters to satisfy DNS-1123 label limits.
  - Component-scoped names use `{{ include "raggo.componentName" (dict "ctx" $ "component" "backend") }}`.
  - Image references prefer digest over tag when both are set.
  - Selector labels are intentionally minimal (name + component +
    instance) so a re-render with a new chart version does not break
    rolling updates.
*/}}

{{/* Resolve the chart-wide name, honouring `global.nameOverride`. */}}
{{- define "raggo.name" -}}
{{- $override := default "" .Values.global.nameOverride -}}
{{- default .Chart.Name $override | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/*
Release-scoped fullname. Honours `global.fullnameOverride`. If the
release name already contains the chart name, avoid `release-chart`
duplication and just use `Release.Name`.
*/}}
{{- define "raggo.fullname" -}}
{{- $override := default "" .Values.global.fullnameOverride -}}
{{- if $override -}}
{{- $override | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- $name := include "raggo.name" . -}}
{{- if contains $name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{/* Chart label of the form `<chart>-<version>`. */}}
{{- define "raggo.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/* Component name: `<fullname>-<component>`. */}}
{{- define "raggo.componentName" -}}
{{- $fullname := include "raggo.fullname" .ctx -}}
{{- printf "%s-%s" $fullname .component | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/*
Common labels applied to every rendered object. Includes optional
`global.commonLabels` so operators can layer in their own taxonomy.
*/}}
{{- define "raggo.labels" -}}
helm.sh/chart: {{ include "raggo.chart" . }}
app.kubernetes.io/name: {{ include "raggo.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: raggo
{{- with .Values.global.commonLabels }}
{{ toYaml . }}
{{- end -}}
{{- end -}}

{{/*
Component labels: common labels + a `component` selector.
Usage: `{{ include "raggo.componentLabels" (dict "ctx" $ "component" "backend") }}`
*/}}
{{- define "raggo.componentLabels" -}}
{{ include "raggo.labels" .ctx }}
app.kubernetes.io/component: {{ .component }}
{{- end -}}

{{/* Selector labels (stable across upgrades). */}}
{{- define "raggo.selectorLabels" -}}
app.kubernetes.io/name: {{ include "raggo.name" .ctx }}
app.kubernetes.io/instance: {{ .ctx.Release.Name }}
app.kubernetes.io/component: {{ .component }}
{{- end -}}

{{/* Common annotations from `global.commonAnnotations`. */}}
{{- define "raggo.annotations" -}}
{{- with .Values.global.commonAnnotations }}
{{ toYaml . }}
{{- end -}}
{{- end -}}

{{/* ServiceAccount name. */}}
{{- define "raggo.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{- default (include "raggo.fullname" .) .Values.serviceAccount.name -}}
{{- else -}}
{{- default "default" .Values.serviceAccount.name -}}
{{- end -}}
{{- end -}}

{{/*
Resolve a component's image reference. Digest wins over tag.

Usage:
  {{ include "raggo.image" (dict "ctx" $ "image" .Values.backend.image) }}
*/}}
{{- define "raggo.image" -}}
{{- $registry := .ctx.Values.global.image.registry -}}
{{- $repo := .image.repository -}}
{{- $digest := default "" .image.digest -}}
{{- $tag := default "" .image.tag -}}
{{- if $digest -}}
{{ $registry }}/{{ $repo }}@{{ $digest }}
{{- else if $tag -}}
{{ $registry }}/{{ $repo }}:{{ $tag }}
{{- else -}}
{{ $registry }}/{{ $repo }}
{{- end -}}
{{- end -}}

{{/* Resolve a component's `imagePullPolicy`, falling back to the global default. */}}
{{- define "raggo.imagePullPolicy" -}}
{{- $component := default "" .image.pullPolicy -}}
{{- $policy := default "IfNotPresent" .ctx.Values.global.image.pullPolicy -}}
{{- if $component -}}
{{- $component -}}
{{- else -}}
{{- $policy -}}
{{- end -}}
{{- end -}}

{{/* `imagePullSecrets` block (rendered conditionally). */}}
{{- define "raggo.imagePullSecrets" -}}
{{- with .Values.global.image.pullSecrets -}}
imagePullSecrets:
{{ toYaml . }}
{{- end -}}
{{- end -}}

{{/* Container security context with chart-wide defaults. */}}
{{- define "raggo.containerSecurityContext" -}}
{{- toYaml .Values.containerSecurityContext -}}
{{- end -}}

{{/* Pod security context. */}}
{{- define "raggo.podSecurityContext" -}}
{{- toYaml .Values.podSecurityContext -}}
{{- end -}}

{{/*
Name of the secret holding `postgresPassword` (and, in external mode,
the connection URL). When `secret.create=true` we materialise our own
secret named `<fullname>-secret`. When false, we reference whatever the
operator passed in `secret.existingName`.
*/}}
{{- define "raggo.secretName" -}}
{{- if .Values.secret.create -}}
{{- printf "%s-secret" (include "raggo.fullname" .) | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- required "secret.existingName is required when secret.create=false" .Values.secret.existingName -}}
{{- end -}}
{{- end -}}

{{/* PostgreSQL service host (only used in chart-managed mode). */}}
{{- define "raggo.postgresServiceName" -}}
{{- include "raggo.componentName" (dict "ctx" . "component" "postgres") -}}
{{- end -}}

{{/*
Database environment variables shared by the backend Deployment and
the migrate/seed Jobs. Reads credentials from the resolved Secret. In
external mode the chart hands the backend `DATABASE_URL` directly; in
internal mode we set discrete `POSTGRES_*` variables that the backend
already knows how to assemble into a DSN.
*/}}
{{- define "raggo.databaseEnv" -}}
- name: RAGGO_DOMAIN
  value: {{ .Values.domain | quote }}
{{- if .Values.postgres.external.enabled }}
- name: DATABASE_URL
  valueFrom:
    secretKeyRef:
      name: {{ required "postgres.external.existingSecret is required when postgres.external.enabled=true" .Values.postgres.external.existingSecret }}
      key: {{ .Values.postgres.external.secretKey | quote }}
{{- else }}
- name: POSTGRES_HOST
  value: {{ include "raggo.postgresServiceName" . | quote }}
- name: POSTGRES_PORT
  value: {{ .Values.postgres.internal.service.port | quote }}
- name: POSTGRES_DB
  value: {{ .Values.postgres.internal.database | quote }}
- name: POSTGRES_USER
  value: {{ .Values.postgres.internal.user | quote }}
- name: POSTGRES_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ include "raggo.secretName" . }}
      key: postgresPassword
{{- end }}
{{- end -}}
