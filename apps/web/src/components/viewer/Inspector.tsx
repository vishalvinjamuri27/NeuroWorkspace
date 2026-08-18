"use client";

import type {
  Annotation,
  Event,
  QCResult,
  Recording,
} from "@neuroworkspace/shared-types";
import { useState } from "react";

import { formatDuration } from "../../lib/api";

type Tab = "events" | "annotations" | "qc" | "metadata";

export function Inspector({
  recording,
  events,
  annotations,
  qc,
  onJump,
}: {
  recording: Recording;
  events: Event[];
  annotations: Annotation[];
  qc: QCResult | null;
  onJump: (seconds: number) => void;
}) {
  const [tab, setTab] = useState<Tab>("events");
  const metadata = recording.safe_metadata as Record<string, unknown>;
  const tabs: Array<[Tab, string, number | null]> = [
    ["events", "Events", events.length],
    ["annotations", "Annotations", annotations.length],
    ["qc", "QC review", qc?.flags.length ?? 0],
    ["metadata", "Metadata", null],
  ];
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
            onClick={() => setTab(id)}
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
                <button
                  key={annotation.id}
                  onClick={() => onJump(annotation.onset_seconds)}
                >
                  <time>{formatDuration(annotation.onset_seconds)}</time>
                  <strong>{annotation.label}</strong>
                  <span>
                    rev {annotation.revision} · {annotation.provenance_kind}
                  </span>
                </button>
              ))
            ) : (
              <Empty text="Select a time region to create the first annotation." />
            )}
          </div>
        )}
        {tab === "qc" && (
          <div>
            <p className="qc-disclaimer">
              {qc?.disclaimer ?? "QC is loading."}
            </p>
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
                  ? "Deterministic synthetic sample; contains no human data."
                  : "Source assets verified by SHA-256; sensitive source metadata is not displayed here."}
              </dd>
            </div>
          </dl>
        )}
      </div>
    </section>
  );
}

function Empty({ text }: { text: string }) {
  return <p className="empty-state">{text}</p>;
}
