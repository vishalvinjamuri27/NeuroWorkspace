import { describe, expect, it } from "vitest";

import { formatDuration } from "./api";

describe("formatDuration", () => {
  it("formats sub-hour and overnight offsets", () => {
    expect(formatDuration(65.25)).toBe("1:05.3");
    expect(formatDuration(3661.2)).toBe("1:01:01.2");
  });

  it("does not expose invalid numeric text", () => {
    expect(formatDuration(Number.NaN)).toBe("—");
    expect(formatDuration(-1)).toBe("—");
  });
});
