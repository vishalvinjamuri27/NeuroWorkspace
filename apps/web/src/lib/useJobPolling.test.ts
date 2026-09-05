import type { Job } from "@neuroworkspace/shared-types";
import { renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import * as apiModule from "./api";
import { useJobPolling } from "./useJobPolling";

function job(overrides: Partial<Job> = {}): Job {
  return {
    id: "job-1",
    kind: "PROCESSING",
    subject_type: "recording",
    subject_id: "rec-1",
    status: "RUNNING",
    attempt: 1,
    progress: 0.2,
    stage: "Processing",
    error_code: null,
    error_message: null,
    diagnostic_id: null,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

describe("useJobPolling", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("does nothing when jobId is null", async () => {
    const spy = vi.spyOn(apiModule.api, "job");
    renderHook(() => useJobPolling(null));
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(spy).not.toHaveBeenCalled();
  });

  it("stops polling once the job reaches a terminal state", async () => {
    const spy = vi
      .spyOn(apiModule.api, "job")
      .mockResolvedValueOnce(job({ status: "RUNNING", progress: 0.3 }))
      .mockResolvedValueOnce(job({ status: "SUCCEEDED", progress: 1 }));

    const { result } = renderHook(() => useJobPolling("job-1", 20, 10));

    await waitFor(() => expect(result.current.job?.status).toBe("SUCCEEDED"));
    expect(spy).toHaveBeenCalledTimes(2);

    // A terminal status must stop the chain: no further calls even after
    // waiting well past another interval.
    await new Promise((resolve) => setTimeout(resolve, 80));
    expect(spy).toHaveBeenCalledTimes(2);
  });

  it("retries with backoff on a transient failure instead of giving up", async () => {
    const spy = vi
      .spyOn(apiModule.api, "job")
      .mockRejectedValueOnce(new Error("network blip"))
      .mockResolvedValueOnce(job({ status: "SUCCEEDED" }));

    const { result } = renderHook(() => useJobPolling("job-1", 20, 10));

    // The transient failure must not surface as a fatal error -- polling
    // keeps going and the job still resolves to SUCCEEDED on the retry.
    await waitFor(() => expect(result.current.job?.status).toBe("SUCCEEDED"));
    expect(spy).toHaveBeenCalledTimes(2);
    expect(result.current.error).toBeNull();
  });

  it("stops issuing requests after unmount", async () => {
    const spy = vi.spyOn(apiModule.api, "job").mockResolvedValue(job());
    const { unmount } = renderHook(() => useJobPolling("job-1", 20, 10));
    await waitFor(() => expect(spy).toHaveBeenCalled());
    unmount();
    const countAtUnmount = spy.mock.calls.length;
    await new Promise((resolve) => setTimeout(resolve, 100));
    expect(spy).toHaveBeenCalledTimes(countAtUnmount);
  });
});
