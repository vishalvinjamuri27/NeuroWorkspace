"use client";

import type { BadChannel, Channel } from "@neuroworkspace/shared-types";
import { useCallback, useState } from "react";

import { api, isConflict } from "../../lib/api";

/** Per-channel bad/good review, written with the backend's compare-and-swap. */
export function useBadChannels(
  recordingId: string,
  onError: (message: string) => void,
) {
  const [badChannelIds, setBadChannelIds] = useState<Set<string>>(new Set());
  // 0 means "no one has reviewed this channel yet" -- the same convention
  // the backend's compare-and-swap uses (BadChannelUpdate.revision).
  const [badChannelRevisions, setBadChannelRevisions] = useState<
    Map<string, number>
  >(new Map());
  const [channelConflict, setChannelConflict] = useState("");

  const resetBadChannels = useCallback((rows: BadChannel[]) => {
    setBadChannelIds(
      new Set(
        rows
          .filter((row) => row.state === "confirmed_bad")
          .map((row) => row.channel_id),
      ),
    );
    setBadChannelRevisions(
      new Map(rows.map((row) => [row.channel_id, row.revision])),
    );
  }, []);

  async function toggleBad(channel: Channel, bad: boolean) {
    const previousIds = new Set(badChannelIds);
    const nextIds = new Set(previousIds);
    if (bad) nextIds.add(channel.id);
    else nextIds.delete(channel.id);
    setBadChannelIds(nextIds);
    setChannelConflict("");
    const expectedRevision = badChannelRevisions.get(channel.id) ?? 0;
    try {
      const updated = await api.setBadChannel(
        recordingId,
        channel.id,
        bad,
        expectedRevision,
      );
      setBadChannelRevisions((current) => {
        const next = new Map(current);
        next.set(channel.id, updated.revision);
        return next;
      });
    } catch (caught) {
      setBadChannelIds(previousIds);
      if (isConflict(caught)) {
        // Someone else reviewed this channel between our load and our
        // write. Resync from the server rather than silently overwriting
        // -- the same principle as annotation conflict handling, just
        // without a form to reopen since this is a single toggle.
        try {
          resetBadChannels(await api.badChannels(recordingId));
        } catch {
          // Best effort: the stale-revision rollback above already keeps
          // local state consistent even if this resync fails.
        }
        setChannelConflict(
          `${channel.name} was already reviewed by someone else. The current status is now shown; try again if you still want to change it.`,
        );
      } else {
        onError(
          caught instanceof Error
            ? caught.message
            : "The channel decision could not be saved.",
        );
      }
    }
  }

  return {
    badChannelIds,
    resetBadChannels,
    toggleBad,
    channelConflict,
    dismissChannelConflict: () => setChannelConflict(""),
  };
}
