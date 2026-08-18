"use client";

import type {
  Annotation,
  Channel,
  Event,
  QCResult,
  Recording,
  WaveformWindow,
} from "@neuroworkspace/shared-types";
import Link from "next/link";
import {
  FormEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import { api, formatDuration } from "../../lib/api";
import { ChannelSidebar } from "./ChannelSidebar";
import { Inspector } from "./Inspector";
import { WaveformCanvas } from "./WaveformCanvas";

interface Selection {
  start: number;
  end: number;
}

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
  const [annotations, setAnnotations] = useState<Annotation[]>([]);
  const [qc, setQc] = useState<QCResult | null>(null);
  const [waveform, setWaveform] = useState<WaveformWindow | null>(null);
  const [visible, setVisible] = useState<Set<number>>(new Set());
  const [badChannelIds, setBadChannelIds] = useState<Set<string>>(new Set());
  const [start, setStart] = useState(0);
  const [duration, setDuration] = useState(10);
  const [amplitude, setAmplitude] = useState(0.8);
  const [perChannelScale, setPerChannelScale] = useState(true);
  const [cursor, setCursor] = useState<number | null>(null);
  const [selectMode, setSelectMode] = useState(false);
  const [selection, setSelection] = useState<Selection | null>(null);
  const [annotationLabel, setAnnotationLabel] = useState("");
  const [annotationDescription, setAnnotationDescription] = useState("");
  const [editable, setEditable] = useState(false);
  const [viewerWidth, setViewerWidth] = useState(1200);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [jumpValue, setJumpValue] = useState("0");
  const viewerRef = useRef<HTMLDivElement>(null);

  const metadata = (recording?.safe_metadata ?? {}) as Record<string, unknown>;
  const totalDuration = safeNumber(metadata, "duration_seconds", 1);
  const selectedIndices = useMemo(
    () => [...visible].sort((a, b) => a - b),
    [visible],
  );
  const waveformLoading =
    waveform === null ||
    Math.abs(waveform.start_seconds - start) > 0.01 ||
    waveform.channels.map((channel) => channel.index).join(",") !==
      selectedIndices.join(",");
  const flaggedChannelIds = useMemo(
    () =>
      new Set(
        qc?.flags
          .map((flag) => flag.channel_id)
          .filter((id): id is string => Boolean(id)) ?? [],
      ),
    [qc],
  );

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
    Promise.all([
      api.recording(recordingId),
      api.channels(recordingId),
      api.events(recordingId),
      api.annotations(recordingId),
      api.qc(recordingId),
    ])
      .then(
        ([
          nextRecording,
          nextChannels,
          nextEvents,
          nextAnnotations,
          nextQc,
        ]) => {
          if (!active) return;
          setRecording(nextRecording);
          setChannels(nextChannels);
          setEvents(nextEvents);
          setAnnotations(nextAnnotations);
          setQc(nextQc);
          setVisible(
            new Set(
              nextChannels
                .slice(0, Math.min(16, nextChannels.length))
                .map((channel) => channel.channel_index),
            ),
          );
          setEditable(!nextRecording.is_demo);
          setLoading(false);
        },
      )
      .catch((caught: unknown) => {
        if (active) {
          setError(
            caught instanceof Error
              ? caught.message
              : "The recording could not be opened.",
          );
          setLoading(false);
        }
      });
    return () => {
      active = false;
    };
  }, [recordingId]);

  useEffect(() => {
    if (!recording || !selectedIndices.length) return;
    let active = true;
    api
      .waveform(recording.id, start, duration, selectedIndices, viewerWidth)
      .then((window) => {
        if (active) {
          setWaveform(window);
        }
      })
      .catch((caught: unknown) => {
        if (active) {
          setError(
            caught instanceof Error
              ? caught.message
              : "The signal window could not be loaded.",
          );
        }
      });
    const previous = Math.max(0, start - duration);
    const next = Math.min(
      Math.max(0, totalDuration - duration),
      start + duration,
    );
    void api
      .waveform(recording.id, previous, duration, selectedIndices, viewerWidth)
      .catch(() => undefined);
    void api
      .waveform(recording.id, next, duration, selectedIndices, viewerWidth)
      .catch(() => undefined);
    return () => {
      active = false;
    };
  }, [recording, selectedIndices, start, duration, viewerWidth, totalDuration]);

  const moveTo = useCallback(
    (requested: number) => {
      const bounded = Math.max(
        0,
        Math.min(Math.max(0, totalDuration - duration), requested),
      );
      setStart(bounded);
      setJumpValue(bounded.toFixed(2));
    },
    [duration, totalDuration],
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
    },
    [duration, start, totalDuration],
  );

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
      if (event.key.toLowerCase() === "r") {
        setDuration(Math.min(10, totalDuration));
        moveTo(0);
      }
      if (event.key.toLowerCase() === "a" && editable)
        setSelectMode((value) => !value);
    }
    window.addEventListener("keydown", keydown);
    return () => window.removeEventListener("keydown", keydown);
  }, [duration, editable, moveTo, start, totalDuration, zoom]);

  function handleSelection(selectedStart: number, selectedEnd: number) {
    setSelection({ start: selectedStart, end: selectedEnd });
    setSelectMode(false);
    setAnnotationLabel("");
    setAnnotationDescription("");
  }

  async function saveAnnotation(event: FormEvent) {
    event.preventDefault();
    if (!selection) return;
    try {
      const created = await api.createAnnotation(recordingId, {
        onset_seconds: selection.start,
        duration_seconds: selection.end - selection.start,
        label: annotationLabel,
        description: annotationDescription,
      });
      setAnnotations((current) =>
        [...current, created].sort((a, b) => a.onset_seconds - b.onset_seconds),
      );
      setSelection(null);
    } catch (caught) {
      setError(
        caught instanceof Error
          ? caught.message
          : "The annotation could not be saved.",
      );
    }
  }

  async function toggleBad(channel: Channel, bad: boolean) {
    const previous = new Set(badChannelIds);
    const next = new Set(previous);
    if (bad) next.add(channel.id);
    else next.delete(channel.id);
    setBadChannelIds(next);
    try {
      await api.setBadChannel(recordingId, channel.id, bad);
    } catch (caught) {
      setBadChannelIds(previous);
      setError(
        caught instanceof Error
          ? caught.message
          : "The channel decision could not be saved.",
      );
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
        <Link className="button primary" href="/">
          Return home
        </Link>
      </main>
    );

  return (
    <main className="viewer-shell">
      <header className="app-header viewer-header">
        <Link href="/" className="brand compact">
          <span className="brand-mark">NW</span>
        </Link>
        <div className="breadcrumb">
          <span>Public sample</span>
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
            onClick={() => {
              setDuration(Math.min(10, totalDuration));
              moveTo(0);
            }}
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
          onClick={() => setSelectMode((value) => !value)}
        >
          ＋ Annotate
        </button>
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
          <span>{error}</span>
          <button onClick={() => setError("")} aria-label="Dismiss error">
            ×
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
          onVisible={setVisible}
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
        </div>
      </section>
      <Inspector
        recording={recording}
        events={events}
        annotations={annotations}
        qc={qc}
        onJump={(seconds) => moveTo(seconds - duration * 0.2)}
      />
      <footer className="viewer-footer">
        <span>Cursor {cursor === null ? "—" : formatDuration(cursor)}</span>
        <span>
          Missing values render as gaps · extrema are preserved in every
          envelope level
        </span>
        <span>Not for clinical use</span>
      </footer>

      {selection && (
        <div
          className="modal-backdrop"
          role="presentation"
          onMouseDown={() => setSelection(null)}
        >
          <form
            className="annotation-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="annotation-title"
            onSubmit={saveAnnotation}
            onMouseDown={(event) => event.stopPropagation()}
          >
            <p className="section-kicker">
              Selected region · {formatDuration(selection.start)}–
              {formatDuration(selection.end)}
            </p>
            <h2 id="annotation-title">Create annotation</h2>
            <label>
              Label
              <input
                autoFocus
                maxLength={160}
                required
                value={annotationLabel}
                onChange={(event) => setAnnotationLabel(event.target.value)}
              />
            </label>
            <label>
              Description
              <textarea
                rows={3}
                maxLength={4000}
                value={annotationDescription}
                onChange={(event) =>
                  setAnnotationDescription(event.target.value)
                }
              />
            </label>
            <div className="button-row">
              <button
                type="button"
                className="button ghost"
                onClick={() => setSelection(null)}
              >
                Cancel
              </button>
              <button className="button primary" type="submit">
                Save annotation
              </button>
            </div>
          </form>
        </div>
      )}
    </main>
  );
}
