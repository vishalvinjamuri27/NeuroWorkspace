"use client";

import type {
  Channel,
  Event,
  QCResult,
  Recording,
} from "@neuroworkspace/shared-types";
import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";

import { FeedbackButton } from "../FeedbackButton";
import { APIError, api, formatDuration } from "../../lib/api";
import { AnnotationDialogs } from "./AnnotationDialogs";
import { ChannelSidebar } from "./ChannelSidebar";
import { Inspector } from "./Inspector";
import { useAnnotations } from "./useAnnotations";
import { useBadChannels } from "./useBadChannels";
import { useProcessing } from "./useProcessing";
import { useViewport } from "./useViewport";
import { useWaveformWindow } from "./useWaveformWindow";
import { WaveformCanvas } from "./WaveformCanvas";

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
  const [qc, setQc] = useState<QCResult | null>(null);
  const [visible, setVisible] = useState<Set<number>>(new Set());
  const [amplitude, setAmplitude] = useState(0.8);
  const [perChannelScale, setPerChannelScale] = useState(true);
  const [cursor, setCursor] = useState<number | null>(null);
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
  const viewerRef = useRef<HTMLDivElement>(null);

  const metadata = (recording?.safe_metadata ?? {}) as Record<string, unknown>;
  const totalDuration = safeNumber(metadata, "duration_seconds", 1);
  const selectedIndices = useMemo(
    () => [...visible].sort((a, b) => a - b),
    [visible],
  );
  const flaggedChannelIds = useMemo(
    () =>
      new Set(
        qc?.flags
          .map((flag) => flag.channel_id)
          .filter((id): id is string => Boolean(id)) ?? [],
      ),
    [qc],
  );

  const annotationState = useAnnotations(recordingId, setError);
  const {
    annotations,
    hasMoreAnnotations,
    loadingMoreAnnotations,
    loadMoreAnnotations,
    resetAnnotations,
    selectMode,
    toggleSelectMode,
    handleSelection,
    beginEditAnnotation,
    deleteAnnotation,
  } = annotationState;
  const {
    badChannelIds,
    resetBadChannels,
    toggleBad,
    channelConflict,
    dismissChannelConflict,
  } = useBadChannels(recordingId, setError);
  const {
    processingRuns,
    setProcessingRuns,
    processingJob,
    processingSubmitting,
    processingCancelling,
    processingError,
    submitProcessing,
    cancelProcessing,
    clearActiveProcessingJob,
  } = useProcessing(recordingId);
  const { start, duration, jumpValue, setJumpValue, moveTo, zoom, resetView } =
    useViewport({
      recordingId,
      totalDuration,
      editable,
      onToggleSelectMode: toggleSelectMode,
    });
  const { waveform, waveformError, waveformLoading, retryWaveform } =
    useWaveformWindow({
      recording,
      selectedIndices,
      start,
      duration,
      viewerWidth,
      totalDuration,
    });

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
          resetAnnotations(annotationPage);
          setQc(nextQc);
          resetBadChannels(nextBadChannels);
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
  }, [recordingId, retryKey, resetAnnotations, resetBadChannels, setProcessingRuns]);

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
            onClick={resetView}
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
          onClick={toggleSelectMode}
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
            onClick={dismissChannelConflict}
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
              clearActiveProcessingJob();
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
        hasMoreAnnotations={hasMoreAnnotations}
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
        onSubmitProcessing={() => void submitProcessing(selectedIndices)}
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

      <AnnotationDialogs annotations={annotationState} />
      <FeedbackButton
        workspaceId={recording.is_demo ? null : recording.workspace_id}
        recordingId={recording.id}
      />
    </main>
  );
}
