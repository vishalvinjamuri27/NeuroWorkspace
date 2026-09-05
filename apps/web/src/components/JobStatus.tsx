"use client";

import type { Job } from "@neuroworkspace/shared-types";

const LABELS: Record<string, string> = {
  QUEUED: "Queued",
  RUNNING: "Running",
  CANCEL_REQUESTED: "Cancelling…",
  SUCCEEDED: "Succeeded",
  FAILED: "Failed",
  CANCELLED: "Cancelled",
};

const CANCELLABLE = new Set(["QUEUED", "RUNNING"]);

export function JobStatusView({
  job,
  onCancel,
  cancelling,
}: {
  job: Job;
  onCancel?: () => void;
  cancelling?: boolean;
}) {
  const label = LABELS[job.status] ?? job.status;
  return (
    <div className="job-status" data-status={job.status}>
      <div className="job-status-row">
        <span className={`job-status-pill status-${job.status.toLowerCase()}`}>
          {label}
        </span>
        <span className="job-stage">{job.stage}</span>
        {onCancel && CANCELLABLE.has(job.status) && (
          <button
            type="button"
            className="button ghost small"
            disabled={Boolean(cancelling)}
            onClick={onCancel}
          >
            {cancelling ? "Cancelling…" : "Cancel"}
          </button>
        )}
      </div>
      {(job.status === "RUNNING" || job.status === "QUEUED") && (
        <div
          className="job-progress-track"
          role="progressbar"
          aria-valuenow={Math.round(job.progress * 100)}
          aria-valuemin={0}
          aria-valuemax={100}
        >
          <div
            className="job-progress-fill"
            style={{ width: `${Math.max(4, Math.round(job.progress * 100))}%` }}
          />
        </div>
      )}
      {job.status === "FAILED" && (
        <p className="inline-error" role="alert">
          {job.error_message ?? "The job failed."}
          {job.diagnostic_id ? ` Reference ${job.diagnostic_id}.` : ""}
        </p>
      )}
      {job.status === "CANCELLED" && (
        <p className="muted">This job was cancelled.</p>
      )}
      {job.attempt > 1 && job.status === "RUNNING" && (
        <p className="muted">Retrying (attempt {job.attempt}).</p>
      )}
    </div>
  );
}
