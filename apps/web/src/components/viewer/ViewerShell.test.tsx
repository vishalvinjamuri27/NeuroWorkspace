import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ViewerShell } from "./ViewerShell";

vi.mock("../../lib/api", async () => {
  const actual =
    await vi.importActual<typeof import("../../lib/api")>("../../lib/api");
  return {
    ...actual,
    api: {
      ...actual.api,
      recording: vi.fn(),
      channels: vi.fn(),
      events: vi.fn(),
      annotations: vi.fn(),
      annotationsAfter: vi.fn(),
      annotationById: vi.fn(),
      qc: vi.fn(),
      badChannels: vi.fn(),
      processingRuns: vi.fn(),
      waveform: vi.fn(),
      updateAnnotation: vi.fn(),
      createProcessingRun: vi.fn(),
      track: vi.fn(),
    },
  };
});

import { api } from "../../lib/api";

const RECORDING = {
  id: "rec-1",
  workspace_id: "ws-1",
  project_id: "proj-1",
  name: "Sample recording",
  format_id: "edf",
  state: "READY",
  active_version_id: "ver-1",
  safe_metadata: {
    duration_seconds: 120,
    sampling_frequency_hz: 256,
    channel_count: 2,
    sample_count: 30720,
  },
  source_provenance: { kind: "upload" },
  is_demo: false,
  error_code: null,
  error_message: null,
  created_at: "2026-01-01T00:00:00Z",
};

const CHANNELS = [
  {
    id: "chan-1",
    channel_index: 0,
    name: "C3",
    source_name: "C3",
    channel_type: "eeg",
    unit: "V",
    source_unit: "uV",
    source_sampling_frequency_hz: 256,
  },
  {
    id: "chan-2",
    channel_index: 1,
    name: "C4",
    source_name: "C4",
    channel_type: "eeg",
    unit: "V",
    source_unit: "uV",
    source_sampling_frequency_hz: 256,
  },
];

function annotation(overrides: Partial<Record<string, unknown>> = {}) {
  return {
    id: "ann-1",
    recording_id: "rec-1",
    version_id: "ver-1",
    creator_id: "user-1",
    updater_id: "user-1",
    onset_seconds: 1,
    duration_seconds: 2,
    label: "Original label",
    description: "",
    provenance_kind: "manual",
    revision: 1,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

const QC_EMPTY = { disclaimer: "Automated review heuristics.", metrics: [], flags: [] };
const WAVEFORM = {
  start_seconds: 0,
  end_seconds: 10,
  level: 0,
  samples_per_bin: 1,
  bin_start_seconds: [0],
  channels: [
    { index: 0, name: "C3", unit: "V", minimum: [0], maximum: [1], valid_count: [1] },
    { index: 1, name: "C4", unit: "V", minimum: [0], maximum: [1], valid_count: [1] },
  ],
};

function mockBaseLoad(annotationsList = [annotation()]) {
  vi.mocked(api.recording).mockResolvedValue(RECORDING as never);
  vi.mocked(api.channels).mockResolvedValue(CHANNELS as never);
  vi.mocked(api.events).mockResolvedValue([]);
  vi.mocked(api.annotations).mockResolvedValue({
    items: annotationsList,
    next_cursor: null,
  } as never);
  vi.mocked(api.qc).mockResolvedValue(QC_EMPTY as never);
  vi.mocked(api.badChannels).mockResolvedValue([]);
  vi.mocked(api.processingRuns).mockResolvedValue([]);
  vi.mocked(api.waveform).mockResolvedValue(WAVEFORM as never);
  vi.mocked(api.track).mockResolvedValue(undefined as never);
}

describe("ViewerShell annotation conflict recovery", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("shows what changed on the server and never silently overwrites it on a 409", async () => {
    const existing = annotation({ revision: 1, label: "Original label" });
    mockBaseLoad([existing]);

    const { APIError } = await vi.importActual<typeof import("../../lib/api")>(
      "../../lib/api",
    );
    vi.mocked(api.updateAnnotation).mockRejectedValue(
      new APIError(
        "This annotation changed. Reload before editing it.",
        "annotation_revision_conflict",
        409,
      ),
    );
    const serverVersion = annotation({
      revision: 2,
      label: "Changed by someone else",
    });
    vi.mocked(api.annotationById).mockResolvedValue(serverVersion as never);

    render(<ViewerShell recordingId="rec-1" />);

    await waitFor(() =>
      expect(screen.getByRole("heading", { name: "Sample recording" })).toBeInTheDocument(),
    );

    fireEvent.click(screen.getByRole("tab", { name: /annotations/i }));
    await waitFor(() => expect(screen.getByText("Original label")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Edit" }));

    const labelInput = screen.getByLabelText("Label");
    fireEvent.change(labelInput, { target: { value: "My conflicting edit" } });
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));

    // The conflict must be surfaced explicitly, with the real server state
    // visible -- never a silent overwrite and never a bare "Request failed".
    await waitFor(() =>
      expect(
        screen.getByRole("heading", { name: /this annotation changed/i }),
      ).toBeInTheDocument(),
    );
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText("Changed by someone else")).toBeInTheDocument();
    expect(within(dialog).getByText("My conflicting edit")).toBeInTheDocument();
    // The locally-typed edit must never have been applied as if it succeeded.
    expect(screen.queryByText("Original label")).not.toBeInTheDocument();

    fireEvent.click(
      within(dialog).getByRole("button", { name: /review & reapply my edit/i }),
    );

    // Reapplying reopens the edit form against the fresh server revision,
    // keeping the user's attempted text so they don't have to retype it.
    expect(screen.getByText("Revision 2")).toBeInTheDocument();
    expect(screen.getByDisplayValue("My conflicting edit")).toBeInTheDocument();
  });
});

describe("ViewerShell annotation pagination", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("only shows a Load more affordance when the server reports another page, and appends without duplicates", async () => {
    vi.mocked(api.recording).mockResolvedValue(RECORDING as never);
    vi.mocked(api.channels).mockResolvedValue(CHANNELS as never);
    vi.mocked(api.events).mockResolvedValue([]);
    vi.mocked(api.annotations).mockResolvedValue({
      items: [annotation({ id: "ann-1", label: "First page item" })],
      next_cursor: "cursor-2",
    } as never);
    vi.mocked(api.annotationsAfter).mockResolvedValue({
      items: [annotation({ id: "ann-2", label: "Second page item" })],
      next_cursor: null,
    } as never);
    vi.mocked(api.qc).mockResolvedValue(QC_EMPTY as never);
    vi.mocked(api.badChannels).mockResolvedValue([]);
    vi.mocked(api.processingRuns).mockResolvedValue([]);
    vi.mocked(api.waveform).mockResolvedValue(WAVEFORM as never);
    vi.mocked(api.track).mockResolvedValue(undefined as never);

    render(<ViewerShell recordingId="rec-1" />);
    await waitFor(() =>
      expect(screen.getByRole("heading", { name: "Sample recording" })).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByRole("tab", { name: /annotations/i }));

    expect(screen.getByText("First page item")).toBeInTheDocument();
    const loadMore = screen.getByRole("button", { name: /load more annotations/i });
    fireEvent.click(loadMore);

    await waitFor(() =>
      expect(screen.getByText("Second page item")).toBeInTheDocument(),
    );
    expect(api.annotationsAfter).toHaveBeenCalledWith("rec-1", "cursor-2");
    // The server said there is no further page: the affordance must go away
    // rather than keep inviting more clicks that would return nothing.
    expect(
      screen.queryByRole("button", { name: /load more annotations/i }),
    ).not.toBeInTheDocument();
  });
});

describe("ViewerShell processing double-submit", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("never starts two processing runs from two clicks that race ahead of a render", async () => {
    mockBaseLoad([]);
    let resolveRun: (value: unknown) => void = () => undefined;
    vi.mocked(api.createProcessingRun).mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveRun = resolve as (value: unknown) => void;
        }),
    );

    render(<ViewerShell recordingId="rec-1" />);
    await waitFor(() =>
      expect(screen.getByRole("heading", { name: "Sample recording" })).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByRole("tab", { name: /processing/i }));

    const runButton = await screen.findByRole("button", { name: /run average reference/i });
    // Two clicks fired back to back, before the in-flight request's promise
    // resolves and before React has necessarily re-rendered the `disabled`
    // attribute -- the exact race a real fast double-click (or a scripted
    // double-submit) can win against a `disabled`-attribute-only guard.
    fireEvent.click(runButton);
    fireEvent.click(runButton);

    resolveRun({
      id: "run-1",
      job_id: "job-1",
      recording_id: "rec-1",
      operation: "average_reference",
      status: "QUEUED",
      input_version_id: "ver-1",
      parameters: { channel_indices: [0, 1] },
      created_at: "2026-01-01T00:00:00Z",
    });
    await waitFor(() => expect(api.createProcessingRun).toHaveBeenCalled());

    expect(api.createProcessingRun).toHaveBeenCalledTimes(1);
  });
});
