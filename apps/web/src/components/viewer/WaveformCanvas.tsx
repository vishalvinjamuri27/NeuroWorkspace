"use client";

import type { Event, WaveformWindow } from "@neuroworkspace/shared-types";
import {
  PointerEvent as ReactPointerEvent,
  useEffect,
  useRef,
  useState,
} from "react";

interface Props {
  data: WaveformWindow | null;
  events: Event[];
  amplitude: number;
  perChannelScale: boolean;
  cursorSeconds: number | null;
  selectionMode: boolean;
  badChannelIndices: Set<number>;
  onCursor: (seconds: number) => void;
  onSelect: (start: number, end: number) => void;
  onZoom: (factor: number, anchor: number) => void;
}

const COLORS = {
  grid: "rgba(130, 160, 174, .12)",
  trace: "#42d7c1",
  traceMuted: "#b87470",
  cursor: "#f2be5c",
  event: "#a68ae8",
  selection: "rgba(61, 214, 189, .14)",
};

export function WaveformCanvas({
  data,
  events,
  amplitude,
  perChannelScale,
  cursorSeconds,
  selectionMode,
  badChannelIndices,
  onCursor,
  onSelect,
  onZoom,
}: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [size, setSize] = useState({ width: 900, height: 520 });
  const [drag, setDrag] = useState<{ startX: number; currentX: number } | null>(
    null,
  );

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const observer = new ResizeObserver(([entry]) => {
      setSize({
        width: Math.max(1, entry.contentRect.width),
        height: Math.max(260, entry.contentRect.height),
      });
    });
    observer.observe(canvas);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const context = canvas.getContext("2d");
    if (!context) return;
    const ratio = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = Math.round(size.width * ratio);
    canvas.height = Math.round(size.height * ratio);
    context.setTransform(ratio, 0, 0, ratio, 0, 0);
    context.clearRect(0, 0, size.width, size.height);
    context.fillStyle = "#0a1115";
    context.fillRect(0, 0, size.width, size.height);

    const gutter = 18;
    const top = 24;
    const bottom = 28;
    const plotWidth = size.width - gutter * 2;
    const plotHeight = size.height - top - bottom;
    context.strokeStyle = COLORS.grid;
    context.lineWidth = 1;
    const verticalLines = 10;
    for (let index = 0; index <= verticalLines; index += 1) {
      const x = gutter + (index / verticalLines) * plotWidth;
      context.beginPath();
      context.moveTo(x, top);
      context.lineTo(x, top + plotHeight);
      context.stroke();
    }
    if (!data || !data.channels.length) {
      context.fillStyle = "#82949c";
      context.font = "13px var(--font-mono)";
      context.fillText("Waiting for a bounded signal window…", 32, 52);
      return;
    }

    const duration = data.end_seconds - data.start_seconds;
    const xForTime = (seconds: number) =>
      gutter + ((seconds - data.start_seconds) / duration) * plotWidth;
    context.font = "11px var(--font-mono)";
    context.fillStyle = "#7f929a";
    for (let index = 0; index <= verticalLines; index += 1) {
      const seconds = data.start_seconds + (duration * index) / verticalLines;
      context.fillText(
        `${seconds.toFixed(duration < 5 ? 2 : 1)}s`,
        xForTime(seconds) - 13,
        size.height - 8,
      );
    }

    for (const event of events) {
      if (
        event.onset_seconds < data.start_seconds ||
        event.onset_seconds > data.end_seconds
      )
        continue;
      const x = xForTime(event.onset_seconds);
      context.strokeStyle = COLORS.event;
      context.setLineDash([3, 4]);
      context.beginPath();
      context.moveTo(x, top);
      context.lineTo(x, top + plotHeight);
      context.stroke();
      context.setLineDash([]);
    }

    const laneHeight = plotHeight / data.channels.length;
    const globalMax = Math.max(
      1e-12,
      ...data.channels.flatMap((channel) =>
        [...channel.minimum, ...channel.maximum]
          .filter((value): value is number => value !== null)
          .map(Math.abs),
      ),
    );
    data.channels.forEach((channel, row) => {
      const center = top + laneHeight * (row + 0.5);
      context.strokeStyle = "rgba(130, 160, 174, .08)";
      context.beginPath();
      context.moveTo(gutter, center);
      context.lineTo(gutter + plotWidth, center);
      context.stroke();
      const values = [...channel.minimum, ...channel.maximum].filter(
        (value): value is number => value !== null,
      );
      const channelMax = Math.max(1e-12, ...values.map(Math.abs));
      const normalization = perChannelScale ? channelMax : globalMax;
      const scale = (laneHeight * 0.39 * amplitude) / normalization;
      context.strokeStyle = badChannelIndices.has(channel.index)
        ? COLORS.traceMuted
        : COLORS.trace;
      context.globalAlpha = badChannelIndices.has(channel.index) ? 0.7 : 0.96;
      context.lineWidth = 1;
      for (let index = 0; index < data.bin_start_seconds.length; index += 1) {
        const minimum = channel.minimum[index];
        const maximum = channel.maximum[index];
        if (minimum === null || maximum === null) continue;
        const x = xForTime(data.bin_start_seconds[index]);
        context.beginPath();
        context.moveTo(x, center - maximum * scale);
        context.lineTo(x, center - minimum * scale);
        context.stroke();
      }
      context.globalAlpha = 1;
    });

    if (
      cursorSeconds !== null &&
      cursorSeconds >= data.start_seconds &&
      cursorSeconds <= data.end_seconds
    ) {
      const x = xForTime(cursorSeconds);
      context.strokeStyle = COLORS.cursor;
      context.beginPath();
      context.moveTo(x, top);
      context.lineTo(x, top + plotHeight);
      context.stroke();
    }
    if (drag) {
      context.fillStyle = COLORS.selection;
      const x = Math.min(drag.startX, drag.currentX);
      context.fillRect(
        x,
        top,
        Math.abs(drag.currentX - drag.startX),
        plotHeight,
      );
    }
  }, [
    data,
    events,
    amplitude,
    perChannelScale,
    cursorSeconds,
    drag,
    size,
    badChannelIndices,
  ]);

  function secondsAt(clientX: number): number | null {
    const canvas = canvasRef.current;
    if (!canvas || !data) return null;
    const bounds = canvas.getBoundingClientRect();
    const ratio = Math.min(
      1,
      Math.max(
        0,
        (clientX - bounds.left - 18) / Math.max(1, bounds.width - 36),
      ),
    );
    return data.start_seconds + ratio * (data.end_seconds - data.start_seconds);
  }

  function pointerDown(event: ReactPointerEvent<HTMLCanvasElement>) {
    event.currentTarget.setPointerCapture(event.pointerId);
    if (selectionMode) {
      const bounds = event.currentTarget.getBoundingClientRect();
      setDrag({
        startX: event.clientX - bounds.left,
        currentX: event.clientX - bounds.left,
      });
    } else {
      const seconds = secondsAt(event.clientX);
      if (seconds !== null) onCursor(seconds);
    }
  }

  function pointerMove(event: ReactPointerEvent<HTMLCanvasElement>) {
    if (!drag) return;
    const bounds = event.currentTarget.getBoundingClientRect();
    setDrag({
      ...drag,
      currentX: Math.min(
        bounds.width - 18,
        Math.max(18, event.clientX - bounds.left),
      ),
    });
  }

  function pointerUp(event: ReactPointerEvent<HTMLCanvasElement>) {
    if (!drag) return;
    const start = secondsAt(
      drag.startX + event.currentTarget.getBoundingClientRect().left,
    );
    const end = secondsAt(
      drag.currentX + event.currentTarget.getBoundingClientRect().left,
    );
    setDrag(null);
    if (start !== null && end !== null && Math.abs(end - start) > 0.001)
      onSelect(Math.min(start, end), Math.max(start, end));
  }

  return (
    <div className={`canvas-wrap ${selectionMode ? "selecting" : ""}`}>
      <canvas
        ref={canvasRef}
        className="waveform-canvas"
        aria-label={
          data
            ? `${data.channels.length} signal channels from ${data.start_seconds.toFixed(2)} to ${data.end_seconds.toFixed(2)} seconds`
            : "Signal viewer loading"
        }
        tabIndex={0}
        onPointerDown={pointerDown}
        onPointerMove={pointerMove}
        onPointerUp={pointerUp}
        onWheel={(event) => {
          event.preventDefault();
          onZoom(
            event.deltaY > 0 ? 1.35 : 0.74,
            Math.min(
              1,
              Math.max(0, event.nativeEvent.offsetX / Math.max(1, size.width)),
            ),
          );
        }}
      />
      <p className="sr-only" aria-live="polite">
        {data
          ? `Resolution level ${data.level}; ${data.samples_per_bin} samples per displayed bin. Missing samples are rendered as gaps.`
          : "Loading waveform data."}
      </p>
    </div>
  );
}
