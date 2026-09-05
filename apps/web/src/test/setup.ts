import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// Without this, a component left mounted across test boundaries (e.g. one
// with a pending setTimeout/effect) can fire after the next test has reset
// its mocks, producing confusing cross-test failures.
afterEach(() => {
  cleanup();
});

// jsdom does not implement ResizeObserver; the viewer components only use
// it to react to layout size changes, which do not occur in a test DOM.
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
if (typeof globalThis.ResizeObserver === "undefined") {
  globalThis.ResizeObserver = ResizeObserverStub as unknown as typeof ResizeObserver;
}
