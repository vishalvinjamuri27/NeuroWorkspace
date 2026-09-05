import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { APIError, api, formatDuration, isConflict } from "./api";

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

describe("isConflict", () => {
  it("is true only for a 409 APIError, not other errors or statuses", () => {
    expect(isConflict(new APIError("stale", "annotation_revision_conflict", 409))).toBe(
      true,
    );
    expect(isConflict(new APIError("missing", "not_found", 404))).toBe(false);
    expect(isConflict(new Error("boom"))).toBe(false);
    expect(isConflict("boom")).toBe(false);
  });
});

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("request() / the standardized error envelope", () => {
  const originalFetch = global.fetch;

  beforeEach(() => {
    global.fetch = vi.fn();
  });

  afterEach(() => {
    global.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("surfaces the error envelope's code, message, and correlation id", async () => {
    vi.mocked(global.fetch).mockResolvedValueOnce(
      jsonResponse(
        {
          error: {
            code: "annotation_revision_conflict",
            message: "This annotation changed. Reload before editing it.",
            correlation_id: "corr-123",
          },
        },
        409,
      ),
    );
    await expect(api.updateAnnotation("ann-1", {
      revision: 1,
      onset_seconds: 0,
      duration_seconds: 1,
      label: "x",
      description: "",
    })).rejects.toMatchObject({
      code: "annotation_revision_conflict",
      status: 409,
      reference: "corr-123",
    });
  });

  it("falls back to a generic message when the body is not the envelope shape", async () => {
    vi.mocked(global.fetch).mockResolvedValueOnce(
      new Response("Internal Server Error", { status: 500 }),
    );
    await expect(api.recording("rec-1")).rejects.toMatchObject({
      code: "request_failed",
      status: 500,
    });
  });
});

describe("cursor pagination", () => {
  const originalFetch = global.fetch;

  beforeEach(() => {
    global.fetch = vi.fn();
  });

  afterEach(() => {
    global.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("omits the cursor param on the first page and includes it on follow-up pages", async () => {
    vi.mocked(global.fetch).mockImplementation(async () =>
      jsonResponse({ items: [], next_cursor: null }),
    );
    await api.annotations("rec-1");
    const firstUrl = vi.mocked(global.fetch).mock.calls[0][0] as string;
    expect(firstUrl).not.toContain("cursor=");

    await api.annotationsAfter("rec-1", "cursor-abc");
    const secondUrl = vi.mocked(global.fetch).mock.calls[1][0] as string;
    expect(secondUrl).toContain("cursor=cursor-abc");
  });

  it("annotationById follows next_cursor across pages to find a later item", async () => {
    vi.mocked(global.fetch)
      .mockResolvedValueOnce(
        jsonResponse({
          items: [{ id: "a1", label: "first" }],
          next_cursor: "page-2",
        }),
      )
      .mockResolvedValueOnce(
        jsonResponse({
          items: [{ id: "a2", label: "second" }],
          next_cursor: null,
        }),
      );
    const found = await api.annotationById("rec-1", "a2");
    expect(found).toMatchObject({ id: "a2", label: "second" });
    expect(global.fetch).toHaveBeenCalledTimes(2);
  });

  it("annotationById returns null once next_cursor runs out without a match", async () => {
    vi.mocked(global.fetch).mockResolvedValue(
      jsonResponse({ items: [{ id: "other" }], next_cursor: null }),
    );
    const found = await api.annotationById("rec-1", "missing");
    expect(found).toBeNull();
  });
});
