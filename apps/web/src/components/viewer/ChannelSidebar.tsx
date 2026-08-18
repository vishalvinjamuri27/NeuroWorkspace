"use client";

import type { Channel } from "@neuroworkspace/shared-types";
import { useMemo, useState } from "react";

interface Props {
  channels: Channel[];
  visible: Set<number>;
  flaggedChannelIds: Set<string>;
  badChannelIds: Set<string>;
  editable: boolean;
  onVisible: (indices: Set<number>) => void;
  onBad: (channel: Channel, bad: boolean) => void;
}

const ROW_HEIGHT = 38;
const VIEWPORT_HEIGHT = 470;

export function ChannelSidebar({
  channels,
  visible,
  flaggedChannelIds,
  badChannelIds,
  editable,
  onVisible,
  onBad,
}: Props) {
  const [query, setQuery] = useState("");
  const [scrollTop, setScrollTop] = useState(0);
  const filtered = useMemo(
    () =>
      channels.filter((channel) =>
        `${channel.name} ${channel.channel_type}`
          .toLowerCase()
          .includes(query.toLowerCase()),
      ),
    [channels, query],
  );
  const start = Math.max(0, Math.floor(scrollTop / ROW_HEIGHT) - 3);
  const stop = Math.min(
    filtered.length,
    start + Math.ceil(VIEWPORT_HEIGHT / ROW_HEIGHT) + 6,
  );
  const windowed = filtered.slice(start, stop);

  function toggle(index: number) {
    const next = new Set(visible);
    if (next.has(index)) next.delete(index);
    else next.add(index);
    if (next.size) onVisible(next);
  }

  return (
    <aside className="channel-panel">
      <div className="panel-heading">
        <div>
          <p className="section-kicker">Signal stack</p>
          <h2>
            Channels{" "}
            <span>
              {visible.size}/{channels.length}
            </span>
          </h2>
        </div>
        <button
          className="icon-button"
          title="Channel options"
          aria-label="Channel options"
        >
          •••
        </button>
      </div>
      <input
        className="channel-search"
        type="search"
        placeholder="Filter channels"
        aria-label="Filter channels"
        value={query}
        onChange={(event) => setQuery(event.target.value)}
      />
      <div className="channel-actions">
        <button
          onClick={() =>
            onVisible(new Set(channels.map((channel) => channel.channel_index)))
          }
        >
          Show all
        </button>
        <button
          onClick={() =>
            onVisible(
              new Set(
                channels
                  .slice(0, Math.min(16, channels.length))
                  .map((channel) => channel.channel_index),
              ),
            )
          }
        >
          First 16
        </button>
      </div>
      <div
        className="channel-list"
        style={{ height: VIEWPORT_HEIGHT }}
        onScroll={(event) => setScrollTop(event.currentTarget.scrollTop)}
      >
        <div
          style={{ height: filtered.length * ROW_HEIGHT, position: "relative" }}
        >
          {windowed.map((channel, offset) => {
            const bad = badChannelIds.has(channel.id);
            return (
              <div
                className={`channel-row ${bad ? "bad" : ""}`}
                style={{
                  transform: `translateY(${(start + offset) * ROW_HEIGHT}px)`,
                }}
                key={channel.id}
              >
                <label>
                  <input
                    type="checkbox"
                    checked={visible.has(channel.channel_index)}
                    onChange={() => toggle(channel.channel_index)}
                  />
                  <span className="channel-swatch" />
                  <span className="channel-name">{channel.name}</span>
                </label>
                <span className="channel-type">{channel.channel_type}</span>
                {flaggedChannelIds.has(channel.id) && (
                  <span
                    className="qc-dot"
                    title="Automated QC review flag"
                    aria-label="QC review flag"
                  />
                )}
                <button
                  disabled={!editable}
                  className="bad-toggle"
                  title={
                    editable
                      ? "Toggle manual bad-channel state"
                      : "Sign in with workspace access to review channels"
                  }
                  onClick={() => onBad(channel, !bad)}
                  aria-pressed={bad}
                >
                  BAD
                </button>
              </div>
            );
          })}
        </div>
      </div>
      <div className="channel-legend">
        <span>
          <i className="legend-trace" /> Visible
        </span>
        <span>
          <i className="legend-qc" /> QC review
        </span>
        <span>
          <i className="legend-bad" /> Marked bad
        </span>
      </div>
    </aside>
  );
}
