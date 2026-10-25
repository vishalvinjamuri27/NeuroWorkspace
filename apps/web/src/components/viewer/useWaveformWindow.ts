"use client";

import type { Recording, WaveformWindow } from "@neuroworkspace/shared-types";
import { useEffect, useState } from "react";

import { api } from "../../lib/api";

/** Fetches the visible waveform window, cancelling superseded requests and
 * prefetching the neighboring windows on either side. */
export function useWaveformWindow({
  recording,
  selectedIndices,
  start,
  duration,
  viewerWidth,
  totalDuration,
}: {
  recording: Recording | null;
  selectedIndices: number[];
  start: number;
  duration: number;
  viewerWidth: number;
  totalDuration: number;
}) {
  const [waveform, setWaveform] = useState<WaveformWindow | null>(null);
  const [waveformFetching, setWaveformFetching] = useState(false);
  const [waveformError, setWaveformError] = useState("");

  function fetchPrimary(signal: AbortSignal) {
    if (!recording) return;
    setWaveformFetching(true);
    setWaveformError("");
    api
      .waveform(
        recording.id,
        recording.active_version_id,
        start,
        duration,
        selectedIndices,
        viewerWidth,
        signal,
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

  useEffect(() => {
    if (!recording || !selectedIndices.length) return;
    // Rapid panning/zooming/channel changes can fire this effect several
    // times before the first request resolves. Aborting the previous
    // controller means a slow, now-superseded response can never land and
    // overwrite the viewport the user is actually looking at.
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      fetchPrimary(controller.signal);
      const previous = Math.max(0, start - duration);
      const next = Math.min(
        Math.max(0, totalDuration - duration),
        start + duration,
      );
      // Best-effort neighbor prefetch: not on the critical path, so it
      // keeps using the shared de-dupe cache and is never aborted.
      for (const neighbor of [previous, next]) {
        void api
          .waveform(
            recording.id,
            recording.active_version_id,
            neighbor,
            duration,
            selectedIndices,
            viewerWidth,
          )
          .catch(() => undefined);
      }
    }, 0);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
    // fetchPrimary closes over exactly these values.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [recording, selectedIndices, start, duration, viewerWidth, totalDuration]);

  function retryWaveform() {
    if (!recording || !selectedIndices.length) return;
    fetchPrimary(new AbortController().signal);
  }

  return {
    waveform,
    waveformError,
    // Explicit request-in-flight state rather than diffing the last-rendered
    // waveform against the current viewport: an AbortController cancels
    // superseded requests outright, so there is no stale response that
    // could land and be mistaken for "still loading."
    waveformLoading: waveformFetching || waveform === null,
    retryWaveform,
  };
}
