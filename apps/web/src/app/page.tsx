"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { api } from "../lib/api";
import { FeedbackButton } from "../components/FeedbackButton";

const repositoryUrl = process.env.NEXT_PUBLIC_REPOSITORY_URL;

export default function HomePage() {
  const router = useRouter();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  async function openDemo() {
    setLoading(true);
    setError("");
    try {
      void api.track("sample_demo_started").catch(() => undefined);
      const recording = await api.demo();
      router.push(`/recordings/${recording.id}`);
    } catch (caught) {
      setError(
        caught instanceof Error
          ? caught.message
          : "The sample is not available.",
      );
      setLoading(false);
    }
  }

  return (
    <main className="landing">
      <header className="landing-nav">
        <Link href="/" className="brand" aria-label="NeuroWorkspace home">
          <span className="brand-mark" aria-hidden="true">
            NW
          </span>
          <span>NeuroWorkspace</span>
        </Link>
        <nav aria-label="Primary navigation">
          <a href="#capabilities">Capabilities</a>
          <a href="#capabilities">How it works</a>
          {repositoryUrl && <a href={repositoryUrl}>Source</a>}
          <Link className="button secondary small" href="/workspace">
            Open workspace
          </Link>
        </nav>
      </header>

      <section className="hero">
        <div className="eyebrow">
          <span className="status-dot" /> Open source · research use
        </div>
        <h1>
          Inspect neural signals
          <br />
          without losing the science.
        </h1>
        <p className="hero-copy">
          Upload EEG recordings, navigate long signals smoothly, review QC,
          annotate evidence, and preserve every transformation in a reproducible
          workspace for researchers, BCI developers, and neurotechnology teams.
        </p>
        <div className="hero-actions">
          <button
            className="button primary"
            onClick={openDemo}
            disabled={loading}
          >
            {loading ? "Opening sample…" : "Try a sample EEG"}
            <span aria-hidden="true">→</span>
          </button>
          <Link className="button secondary" href="/workspace">
            Import a recording
          </Link>
        </div>
        {error && (
          <p className="inline-error" role="alert">
            {error} Please try once more. If it persists, send feedback with
            this page open.
          </p>
        )}
        <div className="trust-row" aria-label="Product properties">
          <span>EDF · BDF · BrainVision · EEGLAB · FIF · CSV</span>
          <span>Non-clinical research tooling</span>
          <span>Apache-2.0</span>
        </div>
      </section>

      <section className="signal-preview" aria-label="Waveform viewer preview">
        <div className="preview-bar">
          <span>Illustrative viewer preview · not shipped subject data</span>
          <span className="ready-pill">● READY</span>
        </div>
        <div className="preview-body">
          <div className="preview-channels">
            {[
              "EEG Fpz-Cz",
              "EEG Pz-Oz",
              "EOG horizontal",
              "Resp oro-nasal",
              "EMG submental",
            ].map((name, index) => (
              <div key={name}>
                <span className={index === 3 ? "bad-dot" : "channel-dot"} />
                {name}
              </div>
            ))}
          </div>
          <svg
            className="preview-wave"
            viewBox="0 0 960 300"
            role="img"
            aria-label="Illustrative multi-channel signal traces"
          >
            <defs>
              <pattern
                id="grid"
                width="48"
                height="30"
                patternUnits="userSpaceOnUse"
              >
                <path
                  d="M 48 0 L 0 0 0 30"
                  fill="none"
                  stroke="currentColor"
                  strokeOpacity=".09"
                />
              </pattern>
            </defs>
            <rect width="960" height="300" fill="url(#grid)" />
            {[45, 100, 155, 210, 265].map((base, row) => (
              <path
                key={base}
                d={Array.from({ length: 97 }, (_, index) => {
                  const x = index * 10;
                  const impulse = index === 55 && row < 2 ? -26 : 0;
                  const y =
                    base +
                    Math.sin(index * (0.8 + row * 0.08)) * (6 + row) +
                    Math.sin(index * 2.7) * 2 +
                    impulse;
                  // Round values so Node prerendering and browser hydration emit
                  // byte-identical SVG path data across math implementations.
                  return `${index ? "L" : "M"}${x},${y.toFixed(3)}`;
                }).join(" ")}
                fill="none"
                stroke={row === 3 ? "#de7d66" : "#3dd6bd"}
                strokeWidth="1.35"
                vectorEffect="non-scaling-stroke"
              />
            ))}
            <line
              x1="550"
              y1="0"
              x2="550"
              y2="300"
              stroke="#f2be5c"
              strokeWidth="1"
            />
          </svg>
        </div>
        <div className="preview-footer">
          <span>00:18:20</span>
          <span>10 s window · 100 µV</span>
          <span>4 events · 1 QC review</span>
        </div>
      </section>

      <section className="capabilities" id="capabilities">
        <article>
          <span>01</span>
          <h2>Large data, bounded views</h2>
          <p>
            Windowed requests and extrema-preserving pyramids keep overnight
            recordings responsive.
          </p>
        </article>
        <article>
          <span>02</span>
          <h2>Review with provenance</h2>
          <p>
            Events, annotations, QC, bad-channel decisions, and derived runs
            remain attributable.
          </p>
        </article>
        <article>
          <span>03</span>
          <h2>Open formats first</h2>
          <p>
            Mature MNE readers normalize common EEG formats while retaining
            source hashes and units.
          </p>
        </article>
      </section>
      <footer className="landing-footer">
        <span>
          NeuroWorkspace is not a medical device and is not intended for
          diagnosis or treatment.
        </span>
        {repositoryUrl && (
          <a href={repositoryUrl}>View the open-source repository</a>
        )}
      </footer>
      <FeedbackButton />
    </main>
  );
}
