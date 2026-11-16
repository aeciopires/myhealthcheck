{{/*
Fail early when the chart is rendered by Helm 3 (this chart supports Helm 4 only).
*/}}
{{- define "myhealthcheck.requireHelm4" -}}
{{- if semverCompare "<4.0.0-0" .Capabilities.HelmVersion.Version -}}
{{- fail (printf "This chart requires Helm 4 or newer (detected %s). See REQUIREMENTS.md." .Capabilities.HelmVersion.Version) -}}
{{- end -}}
{{- end -}}

{{/*
Expand the name of the chart.
*/}}
{{- define "myhealthcheck.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name (max 63 chars, DNS naming spec).
*/}}
{{- define "myhealthcheck.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Chart name and version as used by the chart label.
*/}}
{{- define "myhealthcheck.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "myhealthcheck.labels" -}}
helm.sh/chart: {{ include "myhealthcheck.chart" . }}
{{ include "myhealthcheck.selectorLabels" . }}
app.kubernetes.io/version: {{ include "myhealthcheck.imageTag" . | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: myhealthcheck
{{- end }}

{{/*
Selector labels
*/}}
{{- define "myhealthcheck.selectorLabels" -}}
app.kubernetes.io/name: {{ include "myhealthcheck.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Image tag (defaults to the chart appVersion)
*/}}
{{- define "myhealthcheck.imageTag" -}}
{{- default .Chart.AppVersion .Values.image.tag }}
{{- end }}

{{/*
Name of the ServiceAccount
*/}}
{{- define "myhealthcheck.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "myhealthcheck.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{/*
Cluster-scoped objects must be unique in the cluster: include the namespace.
*/}}
{{- define "myhealthcheck.clusterRoleName" -}}
{{- printf "%s-%s" (include "myhealthcheck.fullname" .) .Release.Namespace | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Name of the Secret holding the alert webhook URL ("" when not used)
*/}}
{{- define "myhealthcheck.alertSecretName" -}}
{{- if .Values.config.alerting.existingSecret }}
{{- .Values.config.alerting.existingSecret }}
{{- else if .Values.config.alerting.webhookUrl }}
{{- include "myhealthcheck.fullname" . }}
{{- end }}
{{- end }}

{{/*
Join a list as CSV ("" for empty lists)
*/}}
{{- define "myhealthcheck.csv" -}}
{{- $items := list }}
{{- range . }}{{ $items = append $items (toString .) }}{{ end }}
{{- join "," $items }}
{{- end }}
