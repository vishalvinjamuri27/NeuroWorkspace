import type {
  Annotation,
  BadChannel,
  Channel,
  Event,
  ExportRecord,
  Job,
  Page,
  Project,
  ProcessingRun,
  QCResult,
  Recording,
  WaveformWindow,
  Workspace,
} from "@neuroworkspace/shared-types";

const API_BASE = (
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000/api"
).replace(/\/$/, "");

export class APIError extends Error {
  constructor(
    message: string,
    readonly code: string,
    readonly status: number,
    readonly reference: string | null = null,
  ) {
    super(message);
  }
}

/** True for the conflict this app treats as a real, user-facing case: a
 * stale optimistic-concurrency revision (annotations, bad channels). */
export function isConflict(error: unknown): error is APIError {
  return error instanceof APIError && error.status === 409;
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    credentials: "include",
    ...init,
    headers: {
      ...(init.body instanceof FormData
        ? {}
        : { "Content-Type": "application/json" }),
      ...init.headers,
    },
  });
  if (!response.ok) {
    const payload = (await response.json().catch(() => null)) as {
      error?: { code?: string; message?: string; correlation_id?: string };
    } | null;
    throw new APIError(
      payload?.error?.message ?? `Request failed (${response.status})`,
      payload?.error?.code ?? "request_failed",
      response.status,
      payload?.error?.correlation_id ??
        response.headers.get("X-Correlation-ID"),
    );
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

function cursorQuery(
  params: Record<string, string | number | undefined>,
): string {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined) query.set(key, String(value));
  }
  const serialized = query.toString();
  return serialized ? `?${serialized}` : "";
}

const waveformCache = new Map<string, Promise<WaveformWindow>>();

export const api = {
  demo: () => request<Recording>("/demo/recording"),
  session: () =>
    request<{ user: { display_name: string; email: string } }>("/session"),
  sessionConfig: () =>
    request<{
      dev_auth_enabled: boolean;
      oidc_configured: boolean;
      oidc_login_url: string;
      build_version: string;
    }>("/session/config"),
  signIn: (email: string, displayName: string) =>
    request<{ user: { display_name: string; email: string } }>("/session/dev", {
      method: "POST",
      body: JSON.stringify({ email, display_name: displayName }),
    }),
  recording: (id: string) => request<Recording>(`/recordings/${id}`),
  channels: (id: string) => request<Channel[]>(`/recordings/${id}/channels`),
  events: (id: string) => request<Event[]>(`/recordings/${id}/events`),
  // Cursor-paginated (see AnnotationPage / neuro_workspace.pagination): a
  // recording with a huge annotation count cannot force an unbounded query.
  // Callers follow next_cursor explicitly with annotationsAfter() below
  // rather than this module trying to guess when to auto-page.
  annotations: (id: string, limit = 200) =>
    request<Page<Annotation>>(
      `/recordings/${id}/annotations${cursorQuery({ limit })}`,
    ),
  annotationsAfter: (id: string, cursor: string, limit = 200) =>
    request<Page<Annotation>>(
      `/recordings/${id}/annotations${cursorQuery({ limit, cursor })}`,
    ),
  qc: (id: string) => request<QCResult>(`/recordings/${id}/qc`),
  waveform: (
    id: string,
    start: number,
    duration: number,
    channelIndices: number[],
    pixelWidth: number,
    signal?: AbortSignal,
  ) => {
    const query = new URLSearchParams({
      start_seconds: start.toString(),
      duration_seconds: duration.toString(),
      pixel_width: Math.max(64, Math.round(pixelWidth)).toString(),
    });
    channelIndices.forEach((index) =>
      query.append("channel_index", index.toString()),
    );
    const path = `/recordings/${id}/waveform?${query}`;
    // A caller that passes a signal is rendering the primary, user-visible
    // viewport and needs this exact request to be cancellable when a newer
    // pan/zoom supersedes it -- so it bypasses the shared de-dupe cache
    // (aborting a cached promise would also break any other in-flight
    // caller waiting on the same window, e.g. neighbor prefetch below).
    if (signal) return request<WaveformWindow>(path, { signal });
    const key = `${id}?${query.toString()}`;
    const cached = waveformCache.get(key);
    if (cached) return cached;
    const pending = request<WaveformWindow>(path).catch((error: unknown) => {
      waveformCache.delete(key);
      throw error;
    });
    waveformCache.set(key, pending);
    if (waveformCache.size > 40)
      waveformCache.delete(waveformCache.keys().next().value ?? key);
    return pending;
  },
  createAnnotation: (
    id: string,
    input: {
      onset_seconds: number;
      duration_seconds: number;
      label: string;
      description: string;
    },
  ) =>
    request<Annotation>(`/recordings/${id}/annotations`, {
      method: "POST",
      body: JSON.stringify(input),
    }),
  updateAnnotation: (
    id: string,
    input: {
      revision: number;
      onset_seconds: number;
      duration_seconds: number;
      label: string;
      description: string;
    },
  ) =>
    request<Annotation>(`/annotations/${id}`, {
      method: "PATCH",
      body: JSON.stringify(input),
    }),
  deleteAnnotation: (id: string, revision: number) =>
    request<void>(`/annotations/${id}`, {
      method: "DELETE",
      body: JSON.stringify({ revision }),
    }),
  // Refetches a single annotation's current server state after a 409, by
  // re-listing (there is no single-annotation GET) and finding it. Small
  // lists in practice -- a recording's annotations are already capped by
  // the viewer's page size -- so this is an acceptable cost for a
  // conflict-recovery path that is not on any hot path.
  annotationById: async (
    recordingId: string,
    annotationId: string,
  ): Promise<Annotation | null> => {
    let cursor: string | undefined;
    for (let page = 0; page < 20; page += 1) {
      const result: Page<Annotation> = cursor
        ? await api.annotationsAfter(recordingId, cursor)
        : await api.annotations(recordingId);
      const found = result.items.find((item) => item.id === annotationId);
      if (found) return found;
      if (!result.next_cursor) return null;
      cursor = result.next_cursor;
    }
    return null;
  },
  badChannels: (id: string) => request<BadChannel[]>(`/recordings/${id}/bad-channels`),
  setBadChannel: (
    recordingId: string,
    channelId: string,
    bad: boolean,
    expectedRevision: number,
    reason = "Manual viewer review",
  ) =>
    request<BadChannel>(`/recordings/${recordingId}/bad-channels/${channelId}`, {
      method: "PUT",
      body: JSON.stringify({
        state: bad ? "confirmed_bad" : "confirmed_good",
        reason,
        revision: expectedRevision,
      }),
    }),
  createWorkspace: (name: string) =>
    request<Workspace>("/workspaces", {
      method: "POST",
      body: JSON.stringify({ name }),
    }),
  workspaces: () => request<Workspace[]>("/workspaces"),
  projects: (workspaceId: string) =>
    request<Project[]>(`/workspaces/${workspaceId}/projects`),
  // Same cursor-paginated shape as annotations() above.
  recordings: (projectId: string, limit = 100) =>
    request<Page<Recording>>(
      `/projects/${projectId}/recordings${cursorQuery({ limit })}`,
    ),
  recordingsAfter: (projectId: string, cursor: string, limit = 100) =>
    request<Page<Recording>>(
      `/projects/${projectId}/recordings${cursorQuery({ limit, cursor })}`,
    ),
  createProject: (workspaceId: string, name: string) =>
    request<Project>(`/workspaces/${workspaceId}/projects`, {
      method: "POST",
      body: JSON.stringify({ name, description: "Created in NeuroWorkspace" }),
    }),
  initiateUpload: (projectId: string, payload: unknown) =>
    request<{
      id: string;
      assets: Array<{ id: string; upload_url: string; direct: boolean }>;
    }>(`/projects/${projectId}/uploads`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  uploadAsset: async (url: string, file: File, direct: boolean) => {
    const body: BodyInit = direct
      ? file
      : (() => {
          const data = new FormData();
          data.append("file", file);
          return data;
        })();
    const response = await fetch(url, {
      method: "PUT",
      body,
      credentials: direct ? "omit" : "include",
    });
    if (!response.ok)
      throw new APIError(
        "The file upload failed.",
        "upload_failed",
        response.status,
      );
  },
  completeUpload: (uploadId: string) =>
    request<{ recording_id: string; job_id: string; state: string }>(
      `/uploads/${uploadId}/complete`,
      { method: "POST" },
    ),
  job: (id: string) => request<Job>(`/jobs/${id}`),
  cancelJob: (id: string) =>
    request<Job>(`/jobs/${id}/cancel`, { method: "POST" }),
  createProcessingRun: (
    recordingId: string,
    operationId: "average_reference",
    parameters: { channel_indices: number[] },
    idempotencyKey: string,
  ) =>
    request<ProcessingRun>(`/recordings/${recordingId}/processing-runs`, {
      method: "POST",
      body: JSON.stringify({ operation_id: operationId, parameters }),
      headers: { "Idempotency-Key": idempotencyKey },
    }),
  processingRuns: (recordingId: string) =>
    request<ProcessingRun[]>(`/recordings/${recordingId}/processing-runs`),
  createExport: (
    recordingId: string,
    formatId: "annotations_csv" | "provenance_json" | "qc_json",
  ) =>
    request<ExportRecord>(`/recordings/${recordingId}/exports`, {
      method: "POST",
      body: JSON.stringify({ format_id: formatId }),
    }),
  exportUrl: (id: string) => `${API_BASE}/exports/${id}/content`,
  feedback: (input: {
    category: string;
    message: string;
    route: string;
    workspace_id?: string | null;
    recording_id?: string | null;
    may_contact: boolean;
    contact_email?: string | null;
  }) =>
    request<{ id: string }>("/feedback", {
      method: "POST",
      body: JSON.stringify(input),
    }),
  track: async (
    name:
      | "sample_demo_started"
      | "sample_recording_opened"
      | "recording_opened"
      | "viewer_zoomed"
      | "viewer_panned"
      | "channel_visibility_changed"
      | "qc_opened",
    recordingId?: string,
  ) => {
    if (typeof window === "undefined") return;
    const key = `nw-event:${name}:${recordingId ?? "global"}`;
    if (window.sessionStorage.getItem(key)) return;
    let sessionId = window.sessionStorage.getItem("nw-product-session");
    if (!sessionId) {
      sessionId = window.crypto.randomUUID();
      window.sessionStorage.setItem("nw-product-session", sessionId);
    }
    await request<void>("/product-events", {
      method: "POST",
      body: JSON.stringify({
        name,
        session_id: sessionId,
        recording_id: recordingId ?? null,
      }),
    });
    window.sessionStorage.setItem(key, "1");
  },
};

export function formatDuration(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return "—";
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const remainder = seconds % 60;
  return hours > 0
    ? `${hours}:${minutes.toString().padStart(2, "0")}:${remainder.toFixed(1).padStart(4, "0")}`
    : `${minutes}:${remainder.toFixed(1).padStart(4, "0")}`;
}
