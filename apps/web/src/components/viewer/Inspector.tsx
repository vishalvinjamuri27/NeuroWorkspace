"use client";

import type {
  Annotation,
  Channel,
  Event,
  Job,
  ProcessingRun,
  QCResult,
  Recording,
} from "@neuroworkspace/shared-types";
import { useState } from "react";

import { formatDuration } from "../../lib/api";
import { JobStatusView } from "../JobStatus";

type Tab = "events" | "annotations" | "qc" | "processing" | "metadata";

export function Inspector({
  recording,
  events,
  annotations,
  hasMoreAnnotations,
  loadingMoreAnnotations,
  onLoadMoreAnnotations,
  qc,
  channels,
  selectedChannelIndices,
  processingRuns,
  processingJob,
  processingSubmitting,
  processingCancelling,
  processingError,
  onSubmitProcessing,
  onCancelProcessing,
  onJump,
  editable,
  onEditAnnotation,
  onDeleteAnnotation,
  onQcOpen,
}: {
  recording: Recording;
  events: Event[];
  annotations: Annotation[];
  hasMoreAnnotations?: boolean;
  loadingMoreAnnotations?: boolean;
  onLoadMoreAnnotations?: () => void;
  qc: QCResult | null;
  channels: Channel[];
  selectedChannelIndices: number[];
  processingRuns: ProcessingRun[];
  processingJob: Job | null;
  processingSubmitting: boolean;
  processingCancelling: boolean;
  processingError: string;
  onSubmitProcessing: () => void;
  onCancelProcessing: () => void;
  onJump: (seconds: number) => void;
  editable: boolean;
  onEditAnnotation: (annotation: Annotation) => void;
  onDeleteAnnotation: (annotation: Annotation) => void;
  onQcOpen?: () => void;
}) {
  const [tab, setTab] = useState<Tab>("events");
  const metadata = recording.safe_metadata as Record<string, unknown>;
  const tabs: Array<[Tab, string, number | null]> = [
    ["events", "Events", events.length],
    ["annotations", "Annotations", annotations.length],
    ["qc", "QC review", qc?.flags.length ?? 0],
    ["processing", "Processing", processingRuns.length],
    ["metadata", "Metadata", null],
  ];
  const channelNameByIndex = new Map(
    channels.map((channel) => [channel.channel_index, channel.name]),
  );
  return (
    <section className="inspector-panel">
      <div
        className="inspector-tabs"
        role="tablist"
        aria-label="Recording details"
      >
        {tabs.map(([id, label, count]) => (
          <button
            key={id}
            role="tab"
            aria-selected={tab === id}
            className={tab === id ? "active" : ""}
            onClick={() => {
              setTab(id);
              if (id === "qc") onQcOpen?.();
            }}
          >
            {label}
            {count !== null && <span>{count}</span>}
          </button>
        ))}
      </div>
      <div className="inspector-content" role="tabpanel">
        {tab === "events" && (
          <div className="data-list">
            {events.length ? (
              events.map((event) => (
                <button
                  key={event.id}
                  onClick={() => onJump(event.onset_seconds)}
                >
                  <time>{formatDuration(event.onset_seconds)}</time>
                  <strong>{event.label}</strong>
                  <span>
                    {event.duration_seconds
                      ? `${event.duration_seconds.toFixed(2)} s`
                      : "marker"}
                  </span>
                </button>
              ))
            ) : (
              <Empty text="No source events were found in this recording." />
            )}
          </div>
        )}
        {tab === "annotations" && (
          <div className="data-list">
            {annotations.length ? (
              annotations.map((annotation) => (
                <div className="annotation-row" key={annotation.id}>
                  <button onClick={() => onJump(annotation.onset_seconds)}>
                    <time>{formatDuration(annotation.onset_seconds)}</time>
                    <strong>{annotation.label}</strong>
                    <span>
                      rev {annotation.revision} · {annotation.provenance_kind}
                    </span>
                  </button>
                  {editable && (
                    <div className="annotation-actions">
                      <button onClick={() => onEditAnnotation(annotation)}>
                        Edit
                      </button>
                      <button onClick={() => onDeleteAnnotation(annotation)}>
                        Delete
                      </button>
                    </div>
                  )}
                </div>
              ))
            ) : (
              <Empty text="Select a time region to create the first annotation." />
            )}
            {hasMoreAnnotations && (
              <button
                type="button"
                className="button secondary small load-more"
                disabled={loadingMoreAnnotations}
                onClick={onLoadMoreAnnotations}
              >
                {loadingMoreAnnotations ? "Loading…" : "Load more annotations"}
              </button>
            )}
          </div>
        )}
        {tab === "qc" && (
          <div>
            <p className="qc-disclaimer">
              {qc?.disclaimer ?? "QC is loading."}
              {qc && !qc.flags.length && !qc.metrics.length && (
                <span>
                  {" "}
                  No QC data has been computed for the active version yet.
                </span>
              )}
            </p>
            {qc?.metrics.length ? (
              <div className="qc-metrics-table" role="table" aria-label="QC metrics">
                <div className="qc-metrics-row qc-metrics-head" role="row">
                  <span role="columnheader">Channel</span>
                  <span role="columnheader">Rule</span>
                  <span role="columnheader">Value</span>
                </div>
                {qc.metrics.map((metric) => (
                  <div className="qc-metrics-row" role="row" key={metric.id}>
                    <span role="cell">{metric.channel_name ?? "—"}</span>
                    <span role="cell">{metric.rule_id}</span>
                    <span role="cell">
                      {metric.value === null
                        ? "—"
                        : `${metric.value.toFixed(3)} ${metric.unit}`}
                    </span>
                  </div>
                ))}
              </div>
            ) : null}
            <div className="data-list">
              {qc?.flags.length ? (
                qc.flags.map((flag) => (
                  <div className="qc-row" key={flag.id}>
                    <span className="review-pill">REVIEW</span>
                    <strong>{flag.message}</strong>
                    <span>{flag.state}</span>
                  </div>
                ))
              ) : (
                <Empty text="No automated review flags in the bounded QC pass." />
              )}
            </div>
          </div>
        )}
        {tab === "processing" && (
          <div className="processing-panel">
            <p className="muted">
              Derived recording versions are created by processing
              operations. The source version is never modified -- each run
              produces a new, immutable version alongside it.
            </p>
            {editable ? (
              <div className="processing-launch">
                <p>
                  <strong>Average reference</strong> using the{" "}
                  {selectedChannelIndices.length} channel
                  {selectedChannelIndices.length === 1 ? "" : "s"} currently
                  visible in the viewer as the reference set.
                </p>
                <button
                  type="button"
                  className="button primary small"
                  disabled={
                    processingSubmitting ||
                    !selectedChannelIndices.length ||
                    Boolean(
                      processingJob &&
                        ["QUEUED", "RUNNING", "CANCEL_REQUESTED"].includes(
                          processingJob.status,
                        ),
                    )
                  }
                  onClick={onSubmitProcessing}
                >
                  {processingSubmitting ? "Starting…" : "Run average reference"}
                </button>
                {processingError && (
                  <p className="inline-error" role="alert">
                    {processingError}
                  </p>
                )}
                {processingJob && (
                  <JobStatusView
                    job={processingJob}
                    onCancel={onCancelProcessing}
                    cancelling={processingCancelling}
                  />
                )}
              </div>
            ) : (
              <p className="empty-state">
                The public sample is read-only; processing is available on
                recordings in your workspace.
              </p>
            )}
            <div className="data-list">
              {processingRuns.length ? (
                processingRuns.map((run) => (
                  <div className="processing-run-row" key={run.id}>
                    <div>
                      <strong>{run.operation_id}</strong>
                      <span className={`review-pill status-${run.status.toLowerCase()}`}>
                        {run.status}
                      </span>
                    </div>
                    <span className="mono">
                      source {run.input_version_id.slice(0, 8)} →{" "}
                      {run.output_version_id
                        ? run.output_version_id.slice(0, 8)
                        : "—"}
                    </span>
                    <span>
                      {(
                        (run.parameters.channel_indices as number[] | undefined) ?? []
                      )
                        .map((index) => channelNameByIndex.get(index) ?? index)
                        .join(", ")}
                    </span>
                    <time>{new Date(run.created_at).toLocaleString()}</time>
                  </div>
                ))
              ) : (
                <Empty text="No processing runs yet for this recording." />
              )}
            </div>
          </div>
        )}
        {tab === "metadata" && (
          <dl className="metadata-grid">
            <div>
              <dt>Format</dt>
              <dd>{recording.format_id?.toUpperCase() ?? "—"}</dd>
            </div>
            <div>
              <dt>Duration</dt>
              <dd>{formatDuration(Number(metadata.duration_seconds ?? 0))}</dd>
            </div>
            <div>
              <dt>Sampling</dt>
              <dd>{String(metadata.sampling_frequency_hz ?? "—")} Hz</dd>
            </div>
            <div>
              <dt>Channels</dt>
              <dd>{String(metadata.channel_count ?? "—")}</dd>
            </div>
            <div>
              <dt>Samples</dt>
              <dd>{Number(metadata.sample_count ?? 0).toLocaleString()}</dd>
            </div>
            <div>
              <dt>Active version</dt>
              <dd className="mono">
                {recording.active_version_id?.slice(0, 12) ?? "—"}
              </dd>
            </div>
            <div className="wide">
              <dt>Source provenance</dt>
              <dd>
                {recording.source_provenance.kind === "synthetic"
                  ? String(
                      metadata.attribution ??
                        "Deterministic synthetic sample; contains no human data.",
                    )
                  : "Source assets verified by SHA-256; sensitive source metadata is not displayed here."}
              </dd>
            </div>
            {recording.is_demo && metadata.limitations ? (
              <div className="wide">
                <dt>Sample limitation</dt>
                <dd>{String(metadata.limitations)}</dd>
              </div>
            ) : null}
          </dl>
        )}
      </div>
    </section>
  );
}

function Empty({ text }: { text: string }) {
  return <p className="empty-state">{text}</p>;
}
