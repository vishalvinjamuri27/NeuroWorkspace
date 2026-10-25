"use client";

import { useCallback, useEffect, useState } from "react";

import { api } from "../../lib/api";

/** Visible time window, pan/zoom, jump-to, and the viewer's keyboard shortcuts. */
export function useViewport({
  recordingId,
  totalDuration,
  editable,
  onToggleSelectMode,
}: {
  recordingId: string;
  totalDuration: number;
  editable: boolean;
  onToggleSelectMode: () => void;
}) {
  const [start, setStart] = useState(0);
  const [duration, setDuration] = useState(10);
  const [jumpValue, setJumpValue] = useState("0");

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

  const resetView = useCallback(() => {
    setDuration(Math.min(10, totalDuration));
    moveTo(0);
  }, [moveTo, totalDuration]);

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
      if (event.key.toLowerCase() === "r") resetView();
      if (event.key.toLowerCase() === "a" && editable) onToggleSelectMode();
    }
    window.addEventListener("keydown", keydown);
    return () => window.removeEventListener("keydown", keydown);
  }, [duration, editable, moveTo, onToggleSelectMode, resetView, start, zoom]);

  return {
    start,
    duration,
    jumpValue,
    setJumpValue,
    moveTo,
    zoom,
    resetView,
  };
}
