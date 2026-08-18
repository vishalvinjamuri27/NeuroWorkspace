# ADR 0003: Canvas waveform renderer

## Context

The browser must show dozens to hundreds of channels interactively without SVG/DOM
point explosion. Server envelopes bound points to viewport pixels.

## Decision

Implement a dependency-light Canvas 2D renderer over typed envelope arrays, with
HTML controls/labels, row virtualization, requestAnimationFrame scheduling, and an
interface that can accept a WebGL backend. Benchmark 64/128/256-channel cases.

## Alternatives

SVG/DOM is accessible but scales poorly with marks. A general chart library hides
critical rendering/request behavior. WebGL offers higher ceilings but adds shader,
buffer, text, accessibility, and fallback complexity before measurements require
it.

## Consequences

V1 is easier to inspect and broadly supported. Accessibility requires equivalent
text/table controls outside Canvas. If draw p95 exceeds the defined ceiling after
point capping and virtualization, WebGL is the next implementation, not a redesign
of the API or view state.

