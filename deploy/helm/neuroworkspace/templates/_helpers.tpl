{{- define "neuroworkspace.name" -}}
{{- .Chart.Name -}}
{{- end -}}

{{- define "neuroworkspace.fullname" -}}
{{- .Release.Name -}}
{{- end -}}

{{- define "neuroworkspace.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" -}}
{{- end -}}

{{- define "neuroworkspace.labels" -}}
helm.sh/chart: {{ include "neuroworkspace.chart" . }}
app.kubernetes.io/part-of: neuroworkspace
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{- define "neuroworkspace.selectorLabels" -}}
app.kubernetes.io/name: {{ include "neuroworkspace.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/component: {{ .component }}
{{- end -}}

{{- define "neuroworkspace.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{- .Values.serviceAccount.name | default (include "neuroworkspace.fullname" .) -}}
{{- else -}}
{{- .Values.serviceAccount.name | default "default" -}}
{{- end -}}
{{- end -}}

{{- define "neuroworkspace.image" -}}
{{- $tag := .Values.image.tag | default .Chart.AppVersion -}}
{{- printf "%s/%s:%s" .Values.image.registry .imageName $tag -}}
{{- end -}}

{{/*
Non-secret env vars every Python workload (api, worker, worker-beat) needs.
Shared here so the three deployments cannot drift out of sync on a config
key -- see docs/KUBERNETES.md for why api/worker/worker-beat all read the
exact same Settings model (packages/signal-core/src/neuro_workspace/config.py).
*/}}
{{- define "neuroworkspace.commonEnv" -}}
- name: ENVIRONMENT
  value: {{ .Values.config.environment | quote }}
- name: OBJECT_STORE_BACKEND
  value: {{ .Values.config.objectStoreBackend | quote }}
- name: CORS_ORIGINS
  value: {{ .Values.config.corsOrigins | quote }}
- name: API_PUBLIC_URL
  value: {{ .Values.config.apiPublicUrl | quote }}
- name: WEB_PUBLIC_URL
  value: {{ .Values.config.webPublicUrl | quote }}
- name: SESSION_COOKIE_NAME
  value: {{ .Values.config.sessionCookieName | quote }}
- name: SESSION_TTL_SECONDS
  value: {{ .Values.config.sessionTtlSeconds | quote }}
- name: DEV_AUTH_ENABLED
  value: {{ .Values.config.devAuthEnabled | quote }}
- name: REQUIRE_DEMO_READY
  value: {{ .Values.config.requireDemoReady | quote }}
- name: WORKER_EAGER
  value: {{ .Values.config.workerEager | quote }}
- name: MAX_UPLOAD_BYTES
  value: {{ .Values.config.maxUploadBytes | quote }}
- name: MAX_UPLOAD_FILES
  value: {{ .Values.config.maxUploadFiles | quote }}
- name: MAX_WAVEFORM_CHANNELS
  value: {{ .Values.config.maxWaveformChannels | quote }}
- name: MAX_WAVEFORM_PIXELS
  value: {{ .Values.config.maxWaveformPixels | quote }}
- name: MAX_WAVEFORM_POINTS
  value: {{ .Values.config.maxWaveformPoints | quote }}
- name: WAVEFORM_CACHE_SECONDS
  value: {{ .Values.config.waveformCacheSeconds | quote }}
- name: LOG_LEVEL
  value: {{ .Values.config.logLevel | quote }}
- name: BUILD_VERSION
  value: {{ .Values.config.buildVersion | default .Chart.AppVersion | quote }}
- name: FEATURE_FLAGS
  value: {{ .Values.config.featureFlags | quote }}
- name: ALPHA_ADMIN_EMAILS
  value: {{ .Values.config.alphaAdminEmails | quote }}
- name: S3_BUCKET
  value: {{ .Values.config.s3Bucket | quote }}
- name: S3_ENDPOINT_URL
  {{- /* Same hardcoded-hostname bug as secret.yaml's DATABASE_URL/REDIS_URL
  -- computed from the real release fullname when the bundled dev MinIO is
  enabled, instead of trusting values-dev.yaml's static "neuroworkspace-minio". */}}
  {{- if .Values.devDependencies.enabled }}
  value: {{ printf "http://%s-minio:9000" (include "neuroworkspace.fullname" .) | quote }}
  {{- else }}
  value: {{ .Values.config.s3EndpointUrl | quote }}
  {{- end }}
- name: S3_PUBLIC_ENDPOINT_URL
  value: {{ .Values.config.s3PublicEndpointUrl | quote }}
- name: OIDC_ISSUER
  value: {{ .Values.config.oidcIssuer | quote }}
- name: OIDC_AUDIENCE
  value: {{ .Values.config.oidcAudience | quote }}
- name: OIDC_CLIENT_ID
  value: {{ .Values.config.oidcClientId | quote }}
- name: DATABASE_URL
  valueFrom:
    secretKeyRef:
      name: {{ .Values.secrets.existingSecret }}
      key: DATABASE_URL
- name: REDIS_URL
  valueFrom:
    secretKeyRef:
      name: {{ .Values.secrets.existingSecret }}
      key: REDIS_URL
- name: SESSION_SECRET
  valueFrom:
    secretKeyRef:
      name: {{ .Values.secrets.existingSecret }}
      key: SESSION_SECRET
- name: S3_ACCESS_KEY
  valueFrom:
    secretKeyRef:
      name: {{ .Values.secrets.existingSecret }}
      key: S3_ACCESS_KEY
- name: S3_SECRET_KEY
  valueFrom:
    secretKeyRef:
      name: {{ .Values.secrets.existingSecret }}
      key: S3_SECRET_KEY
- name: OIDC_CLIENT_SECRET
  valueFrom:
    secretKeyRef:
      name: {{ .Values.secrets.existingSecret }}
      key: OIDC_CLIENT_SECRET
{{- end -}}
