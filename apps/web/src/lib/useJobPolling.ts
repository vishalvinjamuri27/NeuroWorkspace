"use client";

import type { Job } from "@neuroworkspace/shared-types";
import { useEffect, useState } from "react";

import { api } from "./api";

const TERMINAL_STATUSES = new Set(["SUCCEEDED", "FAILED", "CANCELLED"]);

export interface JobPollingState {
  job: Job | null;
  error: string | null;
  timedOut: boolean;
}

/** Polls GET /jobs/{id} on a bounded interval until the job reaches a
 * terminal state (SUCCEEDED / FAILED / CANCELLED), a transient-failure
 * budget is exhausted, or the caller unmounts / changes jobId.
 *
 * Requests are chained with setTimeout after each response rather than a
 * setInterval, so a slow response can never cause two polls to be in
 * flight at once. A failed poll (e.g. a momentary network blip) retries
 * with backoff instead of giving up immediately -- the job itself is not
 * assumed to have failed just because one status check did.
 */
export function useJobPolling(
  jobId: string | null,
  intervalMs = 1000,
  maxAttempts = 120,
): JobPollingState {
  const [job, setJob] = useState<Job | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [timedOut, setTimedOut] = useState(false);

  useEffect(() => {
    let active = true;
    let attempts = 0;
    let timer: number | undefined;

    async function poll() {
      if (!active || !jobId) return;
      attempts += 1;
      try {
        const next = await api.job(jobId);
        if (!active) return;
        setJob(next);
        if (TERMINAL_STATUSES.has(next.status)) return;
        if (attempts >= maxAttempts) {
          setTimedOut(true);
          return;
        }
        timer = window.setTimeout(() => void poll(), intervalMs);
      } catch (caught) {
        if (!active) return;
        if (attempts >= maxAttempts) {
          setError(
            caught instanceof Error
              ? caught.message
              : "Could not check job status.",
          );
          return;
        }
        timer = window.setTimeout(() => void poll(), intervalMs * 2);
      }
    }

    // Deferred through a task boundary (rather than calling setJob/setError
    // synchronously here) so resetting state for a new jobId does not
    // trigger a synchronous cascading render from inside the effect body.
    const resetTimer = window.setTimeout(() => {
      if (!active) return;
      setJob(null);
      setError(null);
      setTimedOut(false);
      if (jobId) void poll();
    }, 0);

    return () => {
      active = false;
      window.clearTimeout(resetTimer);
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [jobId, intervalMs, maxAttempts]);

  return { job, error, timedOut };
}
