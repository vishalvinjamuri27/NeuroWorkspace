"use client";

import type { ProcessingRun } from "@neuroworkspace/shared-types";
import { useEffect, useRef, useState } from "react";

import { api } from "../../lib/api";
import { useJobPolling } from "../../lib/useJobPolling";

/** Starting, cancelling, and tracking processing runs for one recording. */
export function useProcessing(recordingId: string) {
  const [processingRuns, setProcessingRuns] = useState<ProcessingRun[]>([]);
  const [activeProcessingJobId, setActiveProcessingJobId] = useState<
    string | null
  >(null);
  const [processingSubmitting, setProcessingSubmitting] = useState(false);
  // A ref guard closes the race the `disabled` attribute alone cannot: two
  // clicks arriving before React flushes `processingSubmitting` to the DOM
  // (a fast double-click, or a scripted double-submit) would otherwise both
  // pass the disabled check and each mint their own idempotency key --
  // idempotency-key dedup only helps when the key is the same, and this
  // button generates a fresh one per call, so it cannot catch this race on
  // its own.
  const processingSubmitInFlight = useRef(false);
  const [processingCancelling, setProcessingCancelling] = useState(false);
  const [processingError, setProcessingError] = useState("");
  const { job: processingJob } = useJobPolling(activeProcessingJobId);

  // Refresh the processing-runs list (status, output_version_id) whenever
  // the polled job reaches a terminal state -- but do not auto-reload the
  // whole viewer out from under the user; that is an explicit action via
  // the "View updated recording" banner, once SUCCEEDED.
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

  async function submitProcessing(channelIndices: number[]) {
    if (processingSubmitInFlight.current) return;
    if (!channelIndices.length) {
      setProcessingError("Select at least one visible channel to reference.");
      return;
    }
    processingSubmitInFlight.current = true;
    setProcessingSubmitting(true);
    setProcessingError("");
    try {
      const run = await api.createProcessingRun(
        recordingId,
        "average_reference",
        { channel_indices: channelIndices },
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
      processingSubmitInFlight.current = false;
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

  return {
    processingRuns,
    setProcessingRuns,
    processingJob,
    processingSubmitting,
    processingCancelling,
    processingError,
    submitProcessing,
    cancelProcessing,
    clearActiveProcessingJob: () => setActiveProcessingJobId(null),
  };
}
