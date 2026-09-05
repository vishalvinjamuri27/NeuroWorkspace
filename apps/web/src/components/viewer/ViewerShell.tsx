"use client";

import type {
  Annotation,
  Channel,
  Event,
  ProcessingRun,
  QCResult,
  Recording,
  WaveformWindow,
} from "@neuroworkspace/shared-types";
import Link from "next/link";
import {
  FormEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import { FeedbackButton } from "../FeedbackButton";
import { APIError, api, formatDuration, isConflict } from "../../lib/api";
import { useJobPolling } from "../../lib/useJobPolling";
import { ChannelSidebar } from "./ChannelSidebar";
import { Inspector } from "./Inspector";
import { WaveformCanvas } from "./WaveformCanvas";

interface AnnotationConflict {
  /** The edit the user was trying to save when the server rejected it. */
  attempted: {
    onset_seconds: number;
    duration_seconds: number;
    label: string;
    description: string;
  };
  /** What is actually on the server right now. */
  server: Annotation | null;
  /** "update" vs "delete" changes the recovery copy/actions shown. */
  action: "update" | "delete";
}

interface Selection {
  start: number;
  end: number;
}

function safeNumber(
  metadata: Record<string, unknown>,
  key: string,
  fallback: number,
): number {
  const value = Number(metadata[key]);
  return Number.isFinite(value) ? value : fallback;
}

export function ViewerShell({ recordingId }: { recordingId: string }) {
  const [recording, setRecording] = useState<Recording | null>(null);
  const [channels, setChannels] = useState<Channel[]>([]);
  const [events, setEvents] = useState<Event[]>([]);
  const [annotations, setAnnotations] = useState<Annotation[]>([]);
  const [qc, setQc] = useState<QCResult | null>(null);
  const [waveform, setWaveform] = useState<WaveformWindow | null>(null);
  const [waveformFetching, setWaveformFetching] = useState(false);
  const [waveformError, setWaveformError] = useState("");
  const [visible, setVisible] = useState<Set<number>>(new Set());
  const [badChannelIds, setBadChannelIds] = useState<Set<string>>(new Set());
  // 0 means "no one has reviewed this channel yet" -- the same convention
  // the backend's compare-and-swap uses (BadChannelUpdate.revision).
  const [badChannelRevisions, setBadChannelRevisions] = useState<
    Map<string, number>
  >(new Map());
  const [channelConflict, setChannelConflict] = useState("");
  const [annotationsCursor, setAnnotationsCursor] = useState<string | null>(
    null,
  );
  const [loadingMoreAnnotations, setLoadingMoreAnnotations] = useState(false);
  const [annotationConflict, setAnnotationConflict] =
    useState<AnnotationConflict | null>(null);
  const [savingAnnotation, setSavingAnnotation] = useState(false);
  const [processingRuns, setProcessingRuns] = useState<ProcessingRun[]>([]);
  const [activeProcessingJobId, setActiveProcessingJobId] = useState<
    string | null
  >(null);
  const [processingSubmitting, setProcessingSubmitting] = useState(false);
  const [processingCancelling, setProcessingCancelling] = useState(false);
  const [processingError, setProcessingError] = useState("");
  const [start, setStart] = useState(0);
  const [duration, setDuration] = useState(10);
  const [amplitude, setAmplitude] = useState(0.8);
  const [perChannelScale, setPerChannelScale] = useState(true);
  const [cursor, setCursor] = useState<number | null>(null);
  const [selectMode, setSelectMode] = useState(false);
  const [selection, setSelection] = useState<Selection | null>(null);
  const [annotationLabel, setAnnotationLabel] = useState("");
  const [annotationDescription, setAnnotationDescription] = useState("");
  const [editingAnnotation, setEditingAnnotation] = useState<Annotation | null>(
    null,
  );
  const [exportFormat, setExportFormat] = useState<
    "annotations_csv" | "provenance_json" | "qc_json"
  >("annotations_csv");
  const [exporting, setExporting] = useState(false);
  const [editable, setEditable] = useState(false);
  const [viewerWidth, setViewerWidth] = useState(1200);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [errorReference, setErrorReference] = useState<string | null>(null);
  const [retryKey, setRetryKey] = useState(0);
  const [showSampleGuide, setShowSampleGuide] = useState(true);
  const [jumpValue, setJumpValue] = useState("0");
  const viewerRef = useRef<HTMLDivElement>(null);

  const metadata = (recording?.safe_metadata ?? {}) as Record<string, unknown>;
  const totalDuration = safeNumber(metadata, "duration_seconds", 1);
  const selectedIndices = useMemo(
    () => [...visible].sort((a, b) => a - b),
    [visible],
  );
  // Explicit request-in-flight state rather than diffing the last-rendered
  // waveform against the current viewport: an AbortController now cancels
  // superseded requests outright, so there is no longer a stale response
  // that could land and be mistaken for "still loading."
  const waveformLoading = waveformFetching || waveform === null;
  const { job: processingJob } = useJobPolling(activeProcessingJobId);
  const flaggedChannelIds = useMemo(
    () =>
      new Set(
        qc?.flags
          .map((flag) => flag.channel_id)
          .filter((id): id is string => Boolean(id)) ?? [],
      ),
    [qc],
  );

  useEffect(() => {
    const element = viewerRef.current;
    if (!element) return;
    const observer = new ResizeObserver(([entry]) =>
      setViewerWidth(Math.max(320, entry.contentRect.width)),
    );
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    let active = true;
    async function load() {
      setLoading(true);
      setError("");
      setErrorReference(null);
      for (let attempt = 0; attempt < 2; attempt += 1) {
        try {
          const [
            nextRecording,
            nextChannels,
            nextEvents,
            annotationPage,
            nextQc,
            nextBadChannels,
            nextProcessingRuns,
          ] = await Promise.all([
            api.recording(recordingId),
            api.channels(recordingId),
            api.events(recordingId),
            api.annotations(recordingId),
            api.qc(recordingId),
            api.badChannels(recordingId).catch(() => []),
            api.processingRuns(recordingId).catch(() => []),
          ]);
          if (!active) return;
          setRecording(nextRecording);
          setChannels(nextChannels);
          setEvents(nextEvents);
          setAnnotations(annotationPage.items);
          setAnnotationsCursor(annotationPage.next_cursor);
          setQc(nextQc);
          setBadChannelIds(
            new Set(
              nextBadChannels
                .filter((row) => row.state === "confirmed_bad")
                .map((row) => row.channel_id),
            ),
          );
          setBadChannelRevisions(
            new Map(nextBadChannels.map((row) => [row.channel_id, row.revision])),
          );
          setProcessingRuns(nextProcessingRuns);
          setVisible(
            new Set(
              nextChannels
                .slice(0, Math.min(16, nextChannels.length))
                .map((channel) => channel.channel_index),
            ),
          );
          setEditable(!nextRecording.is_demo);
          setLoading(false);
          void api
            .track(
              nextRecording.is_demo
                ? "sample_recording_opened"
                : "recording_opened",
              nextRecording.id,
            )
            .catch(() => undefined);
          return;
        } catch (caught) {
          if (attempt === 0) {
            await new Promise((resolve) => window.setTimeout(resolve, 250));
            continue;
          }
          if (!active) return;
          setError(
            caught instanceof Error
              ? caught.message
              : "The recording could not be opened.",
          );
          setErrorReference(
            caught instanceof APIError ? caught.reference : null,
          );
          setLoading(false);
        }
      }
    }
    const timer = window.setTimeout(() => void load(), 0);
    return () => {
      active = false;
      window.clearTimeout(timer);
    };
  }, [recordingId, retryKey]);

  // Refresh the processing-runs list (status, output_version_id) whenever
  // the polled job reaches a terminal state -- but do not auto-reload the
  // whole viewer out from under the user; that is an explicit action via
  // the "View updated recording" banner below, once SUCCEEDED.
  useEffect(() => {
    if (!processingJob) return;
    if (["SUCCEEDED", "FAILED", "CANCELLED"].includes(processingJob.status)) {
      void api
        .processingRuns(recordingId)
        .then(setProcessingRuns)
        .catch(() => undefined);
    }
    // Only the id/status pair actually determines whether this effect
    // should re-run; depending on the whole `processingJob` object would
    // re-fire on every poll tick (progress/stage change but not status).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [processingJob?.status, processingJob?.id, recordingId]);

  useEffect(() => {
    if (!recording || !selectedIndices.length) return;
    // Rapid panning/zooming/channel changes can fire this effect several
    // times before the first request resolves. Aborting the previous
    // controller means a slow, now-superseded response can never land and
    // overwrite the viewport the user is actually looking at.
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      setWaveformFetching(true);
      setWaveformError("");
      api
        .waveform(
          recording.id,
          start,
          duration,
          selectedIndices,
          viewerWidth,
          controller.signal,
        )
        .then((window) => {
          setWaveform(window);
          setWaveformFetching(false);
        })
        .catch((caught: unknown) => {
          if (caught instanceof DOMException && caught.name === "AbortError")
            return;
          setWaveformFetching(false);
          setWaveformError(
            caught instanceof Error
              ? caught.message
              : "The signal window could not be loaded.",
          );
        });
      const previous = Math.max(0, start - duration);
      const next = Math.min(
        Math.max(0, totalDuration - duration),
        start + duration,
      );
      // Best-effort neighbor prefetch: not on the critical path, so it
      // keeps using the shared de-dupe cache and is never aborted.
      void api
        .waveform(recording.id, previous, duration, selectedIndices, viewerWidth)
        .catch(() => undefined);
      void api
        .waveform(recording.id, next, duration, selectedIndices, viewerWidth)
        .catch(() => undefined);
    }, 0);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [recording, selectedIndices, start, duration, viewerWidth, totalDuration]);

  function retryWaveform() {
    if (!recording || !selectedIndices.length) return;
    setWaveformError("");
    setWaveformFetching(true);
    const controller = new AbortController();
    api
      .waveform(
        recording.id,
        start,
        duration,
        selectedIndices,
        viewerWidth,
        controller.signal,
      )
      .then((window) => {
        setWaveform(window);
        setWaveformFetching(false);
      })
      .catch((caught: unknown) => {
        if (caught instanceof DOMException && caught.name === "AbortError")
          return;
        setWaveformFetching(false);
        setWaveformError(
          caught instanceof Error
            ? caught.message
            : "The signal window could not be loaded.",
        );
      });
  }

  const moveTo = useCallback(
    (requested: number) => {
      const bounded = Math.max(
        0,
        Math.min(Math.max(0, totalDuration - duration), requested),
      );
      setStart(bounded);
      setJumpValue(bounded.toFixed(2));
      void api.track("viewer_panned", recordingId).catch(() => undefined);
    },
    [duration, recordingId, totalDuration],
  );

  const zoom = useCallback(
    (factor: number, anchor = 0.5) => {
      const nextDuration = Math.max(
        0.25,
        Math.min(totalDuration, duration * factor),
      );
      const anchorTime = start + duration * anchor;
      const nextStart = Math.max(
        0,
        Math.min(
          totalDuration - nextDuration,
          anchorTime - nextDuration * anchor,
        ),
      );
      setDuration(nextDuration);
      setStart(nextStart);
      setJumpValue(nextStart.toFixed(2));
      void api.track("viewer_zoomed", recordingId).catch(() => undefined);
    },
    [duration, recordingId, start, totalDuration],
  );

  useEffect(() => {
    function keydown(event: KeyboardEvent) {
      if (
        event.target instanceof HTMLInputElement ||
        event.target instanceof HTMLTextAreaElement
      )
        return;
      if (event.key === "+" || event.key === "=") {
        event.preventDefault();
        zoom(0.7);
      }
      if (event.key === "-") {
        event.preventDefault();
        zoom(1.4);
      }
      if (event.key === "ArrowLeft") {
        event.preventDefault();
        moveTo(start - duration * 0.25);
      }
      if (event.key === "ArrowRight") {
        event.preventDefault();
        moveTo(start + duration * 0.25);
      }
      if (event.key.toLowerCase() === "r") {
        setDuration(Math.min(10, totalDuration));
        moveTo(0);
      }
      if (event.key.toLowerCase() === "a" && editable)
        setSelectMode((value) => !value);
    }
    window.addEventListener("keydown", keydown);
    return () => window.removeEventListener("keydown", keydown);
  }, [duration, editable, moveTo, start, totalDuration, zoom]);

  function handleSelection(selectedStart: number, selectedEnd: number) {
    setSelection({ start: selectedStart, end: selectedEnd });
    setSelectMode(false);
    setAnnotationLabel("");
    setAnnotationDescription("");
  }

  async function saveAnnotation(event: FormEvent) {
    event.preventDefault();
    if (!selection || savingAnnotation) return;
    setSavingAnnotation(true);
    try {
      const created = await api.createAnnotation(recordingId, {
        onset_seconds: selection.start,
        duration_seconds: selection.end - selection.start,
        label: annotationLabel,
        description: annotationDescription,
      });
      setAnnotations((current) =>
        [...current, created].sort((a, b) => a.onset_seconds - b.onset_seconds),
      );
      setSelection(null);
    } catch (caught) {
      setError(
        caught instanceof Error
          ? caught.message
          : "The annotation could not be saved.",
      );
    } finally {
      setSavingAnnotation(false);
    }
  }

  async function toggleBad(channel: Channel, bad: boolean) {
    const previousIds = new Set(badChannelIds);
    const nextIds = new Set(previousIds);
    if (bad) nextIds.add(channel.id);
    else nextIds.delete(channel.id);
    setBadChannelIds(nextIds);
    setChannelConflict("");
    const expectedRevision = badChannelRevisions.get(channel.id) ?? 0;
    try {
      const updated = await api.setBadChannel(
        recordingId,
        channel.id,
        bad,
        expectedRevision,
      );
      setBadChannelRevisions((current) => {
        const next = new Map(current);
        next.set(channel.id, updated.revision);
        return next;
      });
    } catch (caught) {
      setBadChannelIds(previousIds);
      if (isConflict(caught)) {
        // Someone else reviewed this channel between our load and our
        // write. Resync from the server rather than silently overwriting
        // -- the same principle as the annotation conflict handling below,
        // just without a form to reopen since this is a single toggle.
        try {
          const fresh = await api.badChannels(recordingId);
          setBadChannelIds(
            new Set(
              fresh
                .filter((row) => row.state === "confirmed_bad")
                .map((row) => row.channel_id),
            ),
          );
          setBadChannelRevisions(
            new Map(fresh.map((row) => [row.channel_id, row.revision])),
          );
        } catch {
          // Best effort: the stale-revision rollback above already keeps
          // local state consistent even if this resync fails.
        }
        setChannelConflict(
          `${channel.name} was already reviewed by someone else. The current status is now shown; try again if you still want to change it.`,
        );
      } else {
        setError(
          caught instanceof Error
            ? caught.message
            : "The channel decision could not be saved.",
        );
      }
    }
  }

  function beginEditAnnotation(annotation: Annotation) {
    setEditingAnnotation(annotation);
    setAnnotationLabel(annotation.label);
    setAnnotationDescription(annotation.description);
  }

  async function saveEditedAnnotation(event: FormEvent) {
    event.preventDefault();
    if (!editingAnnotation || savingAnnotation) return;
    const attempted = {
      onset_seconds: editingAnnotation.onset_seconds,
      duration_seconds: editingAnnotation.duration_seconds,
      label: annotationLabel,
      description: annotationDescription,
    };
    setSavingAnnotation(true);
    try {
      const updated = await api.updateAnnotation(editingAnnotation.id, {
        revision: editingAnnotation.revision,
        ...attempted,
      });
      setAnnotations((current) =>
        current.map((annotation) =>
          annotation.id === updated.id ? updated : annotation,
        ),
      );
      setEditingAnnotation(null);
    } catch (caught) {
      if (isConflict(caught)) {
        setEditingAnnotation(null);
        const server = await api
          .annotationById(recordingId, editingAnnotation.id)
          .catch(() => null);
        if (server) {
          setAnnotations((current) =>
            current.map((item) => (item.id === server.id ? server : item)),
          );
        }
        setAnnotationConflict({ attempted, server, action: "update" });
      } else {
        setError(
          caught instanceof Error
            ? caught.message
            : "The annotation could not be updated.",
        );
      }
    } finally {
      setSavingAnnotation(false);
    }
  }

  async function deleteAnnotation(annotation: Annotation) {
    if (!window.confirm(`Delete annotation “${annotation.label}”?`)) return;
    try {
      await api.deleteAnnotation(annotation.id, annotation.revision);
      setAnnotations((current) =>
        current.filter((item) => item.id !== annotation.id),
      );
    } catch (caught) {
      if (isConflict(caught)) {
        const server = await api
          .annotationById(recordingId, annotation.id)
          .catch(() => null);
        if (server) {
          setAnnotations((current) =>
            current.map((item) => (item.id === server.id ? server : item)),
          );
        } else {
          // The other change was itself a delete: nothing to resync to.
          setAnnotations((current) =>
            current.filter((item) => item.id !== annotation.id),
          );
        }
        setAnnotationConflict({
          attempted: {
            onset_seconds: annotation.onset_seconds,
            duration_seconds: annotation.duration_seconds,
            label: annotation.label,
            description: annotation.description,
          },
          server,
          action: "delete",
        });
      } else {
        setError(
          caught instanceof Error
            ? caught.message
            : "The annotation could not be deleted.",
        );
      }
    }
  }

  function reapplyConflictedEdit() {
    if (!annotationConflict?.server) return;
    setAnnotationLabel(annotationConflict.attempted.label);
    setAnnotationDescription(annotationConflict.attempted.description);
    setEditingAnnotation(annotationConflict.server);
    setAnnotationConflict(null);
  }

  async function loadMoreAnnotations() {
    if (!annotationsCursor || loadingMoreAnnotations) return;
    setLoadingMoreAnnotations(true);
    try {
      const page = await api.annotationsAfter(recordingId, annotationsCursor);
      setAnnotations((current) => {
        const seen = new Set(current.map((item) => item.id));
        return [...current, ...page.items.filter((item) => !seen.has(item.id))];
      });
      setAnnotationsCursor(page.next_cursor);
    } catch (caught) {
      setError(
        caught instanceof Error
          ? caught.message
          : "More annotations could not be loaded.",
      );
    } finally {
      setLoadingMoreAnnotations(false);
    }
  }

  async function submitProcessing() {
    if (!selectedIndices.length) {
      setProcessingError("Select at least one visible channel to reference.");
      return;
    }
    setProcessingSubmitting(true);
    setProcessingError("");
    try {
      const run = await api.createProcessingRun(
        recordingId,
        "average_reference",
        { channel_indices: selectedIndices },
        window.crypto.randomUUID(),
      );
      setProcessingRuns((current) => [run, ...current]);
      setActiveProcessingJobId(run.job_id);
    } catch (caught) {
      setProcessingError(
        caught instanceof Error
          ? caught.message
          : "The processing run could not be started.",
      );
    } finally {
      setProcessingSubmitting(false);
    }
  }

  async function cancelProcessing() {
    if (!activeProcessingJobId) return;
    setProcessingCancelling(true);
    try {
      await api.cancelJob(activeProcessingJobId);
    } catch (caught) {
      setProcessingError(
        caught instanceof Error
          ? caught.message
          : "The cancellation request could not be sent.",
      );
    } finally {
      setProcessingCancelling(false);
    }
  }

  async function downloadExport() {
    setExporting(true);
    try {
      const created = await api.createExport(recordingId, exportFormat);
      window.location.assign(api.exportUrl(created.id));
    } catch (caught) {
      setError(
        caught instanceof Error
          ? caught.message
          : "The export could not be created.",
      );
    } finally {
      setExporting(false);
    }
  }

  if (loading)
    return (
      <main className="viewer-loading">
        <div className="processing-glyph">
          <span />
          <span />
          <span />
        </div>
        <p>Opening bounded signal view…</p>
      </main>
    );
  if (!recording)
    return (
      <main className="viewer-loading">
        <h1>Recording unavailable</h1>
        <p>{error}</p>
        {errorReference && (
          <p className="error-reference">Error reference: {errorReference}</p>
        )}
        <div className="button-row">
          <button
            className="button primary"
            onClick={() => setRetryKey((value) => value + 1)}
          >
            Try again
          </button>
          <Link className="button ghost" href="/">
            Return home
          </Link>
        </div>
        <FeedbackButton recordingId={recordingId} />
      </main>
    );

  return (
    <main className="viewer-shell">
      <header className="app-header viewer-header">
        <Link
          href={recording.is_demo ? "/" : "/workspace"}
          className="brand compact"
        >
          <span className="brand-mark">NW</span>
        </Link>
        <div className="breadcrumb">
          <span>{recording.is_demo ? "Demo sample" : "My workspace"}</span>
          <i>/</i>
          <strong>{recording.name}</strong>
        </div>
        <div className="header-status">
          <span className="ready-pill">● {recording.state}</span>
          <span className="scope-badge">
            {recording.is_demo ? "READ-ONLY SAMPLE" : "WORKSPACE RECORDING"}
          </span>
        </div>
      </header>

      <section className="recording-strip">
        <div>
          <p className="section-kicker">
            {recording.format_id?.toUpperCase()} recording
          </p>
          <h1>{recording.name}</h1>
        </div>
        <dl>
          <div>
            <dt>Duration</dt>
            <dd>{formatDuration(totalDuration)}</dd>
          </div>
          <div>
            <dt>Sampling</dt>
            <dd>
              {safeNumber(
                metadata,
                "sampling_frequency_hz",
                0,
              ).toLocaleString()}{" "}
              Hz
            </dd>
          </div>
          <div>
            <dt>Channels</dt>
            <dd>{channels.length}</dd>
          </div>
          <div>
            <dt>Window</dt>
            <dd>{duration.toFixed(duration < 10 ? 2 : 1)} s</dd>
          </div>
        </dl>
      </section>

      {recording.is_demo && showSampleGuide && (
        <aside className="sample-guide" aria-label="Sample recording guide">
          <div>
            <p className="section-kicker">One-minute sample</p>
            <strong>
              {recording.source_provenance.kind === "synthetic"
                ? "Explore a generated EEG-like recording with no human data."
                : "Explore attributed public EEG research data without adding it to your workspace."}
            </strong>
            <p>
              Use + / − to zoom, arrows to pan, uncheck a channel, then open QC
              review and Metadata below. The shared demo is read-only; its
              example events show where source markers and annotations appear.
            </p>
            <small>
              {String(
                metadata.attribution ??
                  "Deterministically generated by NeuroWorkspace.",
              )}
            </small>
          </div>
          <button
            onClick={() => setShowSampleGuide(false)}
            aria-label="Dismiss sample guide"
          >
            ×
          </button>
        </aside>
      )}

      <section className="viewer-toolbar" aria-label="Waveform controls">
        <div className="control-group">
          <button
            title="Pan left (Left arrow)"
            onClick={() => moveTo(start - duration * 0.5)}
          >
            ←
          </button>
          <button
            title="Pan right (Right arrow)"
            onClick={() => moveTo(start + duration * 0.5)}
          >
            →
          </button>
          <button title="Zoom in (+)" onClick={() => zoom(0.65)}>
            ＋
          </button>
          <button title="Zoom out (-)" onClick={() => zoom(1.5)}>
            −
          </button>
          <button
            title="Reset view (R)"
            onClick={() => {
              setDuration(Math.min(10, totalDuration));
              moveTo(0);
            }}
          >
            Reset
          </button>
        </div>
        <form
          className="jump-control"
          onSubmit={(event) => {
            event.preventDefault();
            moveTo(Number(jumpValue));
          }}
        >
          <label>
            Jump to{" "}
            <input
              aria-label="Jump to time in seconds"
              inputMode="decimal"
              value={jumpValue}
              onChange={(event) => setJumpValue(event.target.value)}
            />
            <span>s</span>
          </label>
        </form>
        <div className="scale-control">
          <label>
            Amplitude{" "}
            <input
              type="range"
              min="0.2"
              max="3"
              step="0.1"
              value={amplitude}
              onChange={(event) => setAmplitude(Number(event.target.value))}
            />
          </label>
          <button
            className={perChannelScale ? "active" : ""}
            onClick={() => setPerChannelScale((value) => !value)}
          >
            {perChannelScale ? "Per-channel" : "Global"}
          </button>
        </div>
        <button
          className={`annotate-button ${selectMode ? "active" : ""}`}
          disabled={!editable}
          title={
            editable
              ? "Select a region to annotate (A)"
              : "The public sample is read-only"
          }
          onClick={() => setSelectMode((value) => !value)}
        >
          ＋ Annotate
        </button>
        <div className="export-control">
          <select
            aria-label="Export format"
            value={exportFormat}
            disabled={!editable || exporting}
            onChange={(event) =>
              setExportFormat(
                event.target.value as
                  | "annotations_csv"
                  | "provenance_json"
                  | "qc_json",
              )
            }
          >
            <option value="annotations_csv">Annotations CSV</option>
            <option value="provenance_json">Provenance JSON</option>
            <option value="qc_json">QC JSON</option>
          </select>
          <button
            disabled={!editable || exporting}
            onClick={() => void downloadExport()}
          >
            {exporting ? "Preparing…" : "Export"}
          </button>
        </div>
        <div className="time-readout">
          <span>{formatDuration(start)}</span>
          <i>—</i>
          <span>
            {formatDuration(Math.min(totalDuration, start + duration))}
          </span>
        </div>
      </section>

      {error && (
        <div className="viewer-error" role="alert">
          <span>
            {error}
            {errorReference ? ` Reference ${errorReference}.` : ""}
          </span>
          <button onClick={() => setError("")} aria-label="Dismiss error">
            ×
          </button>
        </div>
      )}
      {channelConflict && (
        <div className="viewer-warning" role="alert">
          <span>{channelConflict}</span>
          <button
            onClick={() => setChannelConflict("")}
            aria-label="Dismiss notice"
          >
            ×
          </button>
        </div>
      )}
      {processingJob?.status === "SUCCEEDED" && (
        <div className="viewer-notice" role="status">
          <span>
            Processing finished. A new recording version is ready.
          </span>
          <button
            className="button primary small"
            onClick={() => {
              setActiveProcessingJobId(null);
              setRetryKey((value) => value + 1);
            }}
          >
            View updated recording
          </button>
        </div>
      )}
      <section className="viewer-grid">
        <ChannelSidebar
          channels={channels}
          visible={visible}
          flaggedChannelIds={flaggedChannelIds}
          badChannelIds={badChannelIds}
          editable={editable}
          onVisible={(next) => {
            setVisible(next);
            void api
              .track("channel_visibility_changed", recordingId)
              .catch(() => undefined);
          }}
          onBad={toggleBad}
        />
        <div className="waveform-panel" ref={viewerRef}>
          <div className="waveform-status">
            <span>
              <i className={`live-dot ${waveformLoading ? "loading" : ""}`} />
              {waveformLoading
                ? "Loading window"
                : `Level ${waveform?.level ?? 0} · ${waveform?.samples_per_bin ?? 1} samples/bin`}
            </span>
            <span>Wheel to zoom · click to place cursor</span>
          </div>
          {waveformError ? (
            <div className="waveform-error-panel" role="alert">
              <p>{waveformError}</p>
              <button
                className="button secondary small"
                onClick={retryWaveform}
              >
                Retry
              </button>
            </div>
          ) : (
            <WaveformCanvas
              data={waveform}
              events={events}
              amplitude={amplitude}
              perChannelScale={perChannelScale}
              cursorSeconds={cursor}
              selectionMode={selectMode}
              badChannelIndices={
                new Set(
                  channels
                    .filter((channel) => badChannelIds.has(channel.id))
                    .map((channel) => channel.channel_index),
                )
              }
              onCursor={setCursor}
              onSelect={handleSelection}
              onZoom={zoom}
            />
          )}
        </div>
      </section>
      <Inspector
        recording={recording}
        events={events}
        annotations={annotations}
        hasMoreAnnotations={Boolean(annotationsCursor)}
        loadingMoreAnnotations={loadingMoreAnnotations}
        onLoadMoreAnnotations={() => void loadMoreAnnotations()}
        qc={qc}
        channels={channels}
        selectedChannelIndices={selectedIndices}
        processingRuns={processingRuns}
        processingJob={processingJob}
        processingSubmitting={processingSubmitting}
        processingCancelling={processingCancelling}
        processingError={processingError}
        onSubmitProcessing={() => void submitProcessing()}
        onCancelProcessing={() => void cancelProcessing()}
        onJump={(seconds) => moveTo(seconds - duration * 0.2)}
        editable={editable}
        onEditAnnotation={beginEditAnnotation}
        onDeleteAnnotation={(annotation) => void deleteAnnotation(annotation)}
        onQcOpen={() => {
          void api.track("qc_opened", recordingId).catch(() => undefined);
        }}
      />
      <footer className="viewer-footer">
        <span>Cursor {cursor === null ? "—" : formatDuration(cursor)}</span>
        <span>
          Missing values render as gaps · extrema are preserved in every
          envelope level
        </span>
        <span>Not for clinical use</span>
      </footer>

      {selection && (
        <div
          className="modal-backdrop"
          role="presentation"
          onMouseDown={() => setSelection(null)}
        >
          <form
            className="annotation-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="annotation-title"
            onSubmit={saveAnnotation}
            onMouseDown={(event) => event.stopPropagation()}
          >
            <p className="section-kicker">
              Selected region · {formatDuration(selection.start)}–
              {formatDuration(selection.end)}
            </p>
            <h2 id="annotation-title">Create annotation</h2>
            <label>
              Label
              <input
                autoFocus
                maxLength={160}
                required
                value={annotationLabel}
                onChange={(event) => setAnnotationLabel(event.target.value)}
              />
            </label>
            <label>
              Description
              <textarea
                rows={3}
                maxLength={4000}
                value={annotationDescription}
                onChange={(event) =>
                  setAnnotationDescription(event.target.value)
                }
              />
            </label>
            <div className="button-row">
              <button
                type="button"
                className="button ghost"
                onClick={() => setSelection(null)}
              >
                Cancel
              </button>
              <button className="button primary" type="submit" disabled={savingAnnotation}>
                {savingAnnotation ? "Saving…" : "Save annotation"}
              </button>
            </div>
          </form>
        </div>
      )}
      {editingAnnotation && (
        <div
          className="modal-backdrop"
          role="presentation"
          onMouseDown={() => setEditingAnnotation(null)}
        >
          <form
            className="annotation-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="edit-annotation-title"
            onSubmit={saveEditedAnnotation}
            onMouseDown={(event) => event.stopPropagation()}
          >
            <p className="section-kicker">
              Revision {editingAnnotation.revision}
            </p>
            <h2 id="edit-annotation-title">Edit annotation</h2>
            <label>
              Label
              <input
                autoFocus
                maxLength={160}
                required
                value={annotationLabel}
                onChange={(event) => setAnnotationLabel(event.target.value)}
              />
            </label>
            <label>
              Description
              <textarea
                rows={3}
                maxLength={4000}
                value={annotationDescription}
                onChange={(event) =>
                  setAnnotationDescription(event.target.value)
                }
              />
            </label>
            <div className="button-row">
              <button
                type="button"
                className="button ghost"
                onClick={() => setEditingAnnotation(null)}
              >
                Cancel
              </button>
              <button className="button primary" type="submit" disabled={savingAnnotation}>
                {savingAnnotation ? "Saving…" : "Save changes"}
              </button>
            </div>
          </form>
        </div>
      )}
      {annotationConflict && (
        <div
          className="modal-backdrop"
          role="presentation"
          onMouseDown={() => setAnnotationConflict(null)}
        >
          <div
            className="annotation-modal conflict-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="conflict-title"
            onMouseDown={(event) => event.stopPropagation()}
          >
            <p className="section-kicker">Someone else changed this first</p>
            <h2 id="conflict-title">
              This annotation changed{" "}
              {annotationConflict.action === "delete" ? "before your delete" : "elsewhere"}
            </h2>
            <p className="muted">
              {annotationConflict.server
                ? "Another edit was saved after you loaded this annotation. Your change was not applied, so nothing was silently overwritten. Review the current version below."
                : "This annotation was deleted by someone else. Your change was not applied."}
            </p>
            {annotationConflict.server && (
              <div className="conflict-columns">
                <div>
                  <p className="section-kicker">Current on server</p>
                  <strong>{annotationConflict.server.label}</strong>
                  <p>{annotationConflict.server.description || "—"}</p>
                  <span className="mono">
                    rev {annotationConflict.server.revision} ·{" "}
                    {formatDuration(annotationConflict.server.onset_seconds)}
                  </span>
                </div>
                <div>
                  <p className="section-kicker">Your attempted change</p>
                  <strong>{annotationConflict.attempted.label}</strong>
                  <p>{annotationConflict.attempted.description || "—"}</p>
                  <span className="mono">
                    {formatDuration(annotationConflict.attempted.onset_seconds)}
                  </span>
                </div>
              </div>
            )}
            <div className="button-row">
              <button
                type="button"
                className="button ghost"
                onClick={() => setAnnotationConflict(null)}
              >
                Discard my change
              </button>
              {annotationConflict.action === "update" &&
                annotationConflict.server && (
                  <button
                    type="button"
                    className="button primary"
                    onClick={reapplyConflictedEdit}
                  >
                    Review &amp; reapply my edit
                  </button>
                )}
            </div>
          </div>
        </div>
      )}
      <FeedbackButton
        workspaceId={recording.is_demo ? null : recording.workspace_id}
        recordingId={recording.id}
      />
    </main>
  );
}
