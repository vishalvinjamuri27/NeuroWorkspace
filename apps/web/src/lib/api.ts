import type {
  Annotation,
  Channel,
  Event,
  Project,
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
  ) {
    super(message);
  }
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
      error?: { code?: string; message?: string };
    } | null;
    throw new APIError(
      payload?.error?.message ?? `Request failed (${response.status})`,
      payload?.error?.code ?? "request_failed",
      response.status,
    );
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

const waveformCache = new Map<string, Promise<WaveformWindow>>();

export const api = {
  demo: () => request<Recording>("/demo/recording"),
  session: () =>
    request<{ user: { display_name: string; email: string } }>("/session"),
  signIn: (email: string, displayName: string) =>
    request<{ user: { display_name: string; email: string } }>("/session/dev", {
      method: "POST",
      body: JSON.stringify({ email, display_name: displayName }),
    }),
  recording: (id: string) => request<Recording>(`/recordings/${id}`),
  channels: (id: string) => request<Channel[]>(`/recordings/${id}/channels`),
  events: (id: string) => request<Event[]>(`/recordings/${id}/events`),
  annotations: (id: string) =>
    request<Annotation[]>(`/recordings/${id}/annotations`),
  qc: (id: string) => request<QCResult>(`/recordings/${id}/qc`),
  waveform: (
    id: string,
    start: number,
    duration: number,
    channelIndices: number[],
    pixelWidth: number,
  ) => {
    const query = new URLSearchParams({
      start_seconds: start.toString(),
      duration_seconds: duration.toString(),
      pixel_width: Math.max(64, Math.round(pixelWidth)).toString(),
    });
    channelIndices.forEach((index) =>
      query.append("channel_index", index.toString()),
    );
    const key = `${id}?${query.toString()}`;
    const cached = waveformCache.get(key);
    if (cached) return cached;
    const pending = request<WaveformWindow>(
      `/recordings/${id}/waveform?${query}`,
    ).catch((error: unknown) => {
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
  setBadChannel: (recordingId: string, channelId: string, bad: boolean) =>
    request(`/recordings/${recordingId}/bad-channels/${channelId}`, {
      method: "PUT",
      body: JSON.stringify({
        state: bad ? "confirmed_bad" : "confirmed_good",
        reason: "Manual viewer review",
      }),
    }),
  createWorkspace: (name: string) =>
    request<Workspace>("/workspaces", {
      method: "POST",
      body: JSON.stringify({ name }),
    }),
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
  job: (id: string) =>
    request<{
      status: string;
      stage: string;
      progress: number;
      error_message: string | null;
    }>(`/jobs/${id}`),
  createExport: (
    recordingId: string,
    formatId: "annotations_csv" | "provenance_json" | "qc_json",
  ) =>
    request<{ id: string }>(`/recordings/${recordingId}/exports`, {
      method: "POST",
      body: JSON.stringify({ format_id: formatId }),
    }),
  exportUrl: (id: string) => `${API_BASE}/exports/${id}/content`,
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
