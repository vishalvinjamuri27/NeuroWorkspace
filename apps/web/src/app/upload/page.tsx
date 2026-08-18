"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { FormEvent, useMemo, useState } from "react";

import { APIError, api } from "../../lib/api";

const FORMATS = [
  ["edf", "EDF / EDF+", ".edf"],
  ["bdf", "BioSemi BDF", ".bdf"],
  ["brainvision", "BrainVision", ".vhdr + .vmrk + .eeg"],
  ["eeglab", "EEGLAB", ".set + optional .fdt"],
  ["fif", "MNE FIF", ".fif"],
  ["csv", "Delimited text", ".csv / .tsv"],
] as const;

type Phase =
  | "account"
  | "files"
  | "mapping"
  | "uploading"
  | "processing"
  | "error";

function roleForFile(file: File): string {
  const suffix = file.name.toLowerCase().split(".").pop();
  if (suffix === "vhdr") return "header";
  if (suffix === "vmrk") return "marker";
  if (suffix === "eeg" || suffix === "fdt") return "data";
  return "primary";
}

export default function UploadPage() {
  const router = useRouter();
  const [phase, setPhase] = useState<Phase>("account");
  const [email, setEmail] = useState("researcher@example.test");
  const [displayName, setDisplayName] = useState("Researcher");
  const [workspaceName, setWorkspaceName] = useState("My lab");
  const [projectName, setProjectName] = useState("EEG review");
  const [format, setFormat] = useState<(typeof FORMATS)[number][0]>("edf");
  const [files, setFiles] = useState<File[]>([]);
  const [recordingName, setRecordingName] = useState("");
  const [samplingRate, setSamplingRate] = useState("256");
  const [timeColumn, setTimeColumn] = useState("");
  const [channelColumns, setChannelColumns] = useState("C3,C4");
  const [channelNames, setChannelNames] = useState("C3,C4");
  const [units, setUnits] = useState("uV,uV");
  const [progress, setProgress] = useState(0);
  const [stage, setStage] = useState("");
  const [error, setError] = useState("");

  const totalSize = useMemo(
    () => files.reduce((sum, file) => sum + file.size, 0),
    [files],
  );

  async function establishWorkspace(event: FormEvent) {
    event.preventDefault();
    setError("");
    try {
      await api.signIn(email, displayName);
      setPhase("files");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Sign-in failed.");
    }
  }

  function chooseFiles(selected: FileList | null) {
    const next = Array.from(selected ?? []);
    setFiles(next);
    if (next.length && !recordingName)
      setRecordingName(next[0].name.replace(/\.[^.]+$/, ""));
  }

  function csvList(value: string): string[] {
    return value
      .split(",")
      .map((item) => item.trim())
      .filter(Boolean);
  }

  async function startUpload() {
    setError("");
    setPhase("uploading");
    try {
      const workspace = await api.createWorkspace(workspaceName);
      const project = await api.createProject(workspace.id, projectName);
      const columns = csvList(channelColumns);
      const payload = {
        recording_name: recordingName,
        format_hint: format,
        assets: files.map((file) => ({
          name: file.name,
          size_bytes: file.size,
          role: roleForFile(file),
          media_type: file.type || null,
        })),
        csv_config:
          format === "csv"
            ? {
                sampling_frequency_hz: samplingRate
                  ? Number(samplingRate)
                  : null,
                time_column: timeColumn || null,
                channel_columns: columns,
                channel_names: csvList(channelNames),
                units: csvList(units),
                channel_types: columns.map(() => "eeg"),
                delimiter: files[0]?.name.toLowerCase().endsWith(".tsv")
                  ? "\t"
                  : ",",
              }
            : null,
      };
      const upload = await api.initiateUpload(project.id, payload);
      for (let index = 0; index < files.length; index += 1) {
        setStage(`Uploading ${files[index].name}`);
        await api.uploadAsset(
          upload.assets[index].upload_url,
          files[index],
          upload.assets[index].direct,
        );
        setProgress(Math.round(((index + 1) / files.length) * 70));
      }
      setPhase("processing");
      setStage("Validating recording");
      const completion = await api.completeUpload(upload.id);
      for (let attempt = 0; attempt < 120; attempt += 1) {
        const job = await api.job(completion.job_id);
        setStage(job.stage);
        setProgress(70 + Math.round(job.progress * 30));
        if (job.status === "SUCCEEDED") {
          router.push(`/recordings/${completion.recording_id}`);
          return;
        }
        if (job.status === "FAILED")
          throw new Error(job.error_message ?? "Ingestion failed.");
        await new Promise((resolve) => window.setTimeout(resolve, 1000));
      }
      throw new Error(
        "Processing is taking longer than expected. The job remains safe to revisit.",
      );
    } catch (caught) {
      setPhase("error");
      setError(
        caught instanceof APIError || caught instanceof Error
          ? caught.message
          : "Import failed.",
      );
    }
  }

  return (
    <main className="import-shell">
      <header className="app-header simple-header">
        <Link href="/" className="brand">
          <span className="brand-mark">NW</span>
          <span>NeuroWorkspace</span>
        </Link>
        <span className="scope-badge">Research use · non-clinical</span>
      </header>
      <div className="import-layout">
        <aside className="import-steps" aria-label="Import progress">
          <p className="eyebrow-text">New recording</p>
          <h1>Bring signal data into a reproducible workspace.</h1>
          <ol>
            {[
              "Account",
              "Files and format",
              "Schema review",
              "Upload",
              "Processing",
            ].map((label, index) => {
              const phaseIndex = {
                account: 0,
                files: 1,
                mapping: 2,
                uploading: 3,
                processing: 4,
                error: 3,
              }[phase];
              return (
                <li
                  key={label}
                  className={
                    index === phaseIndex
                      ? "active"
                      : index < phaseIndex
                        ? "done"
                        : ""
                  }
                >
                  <span>{index < phaseIndex ? "✓" : index + 1}</span>
                  {label}
                </li>
              );
            })}
          </ol>
          <p className="privacy-note">
            Source files stay private. Filenames and subject metadata are not
            emitted to analytics or application logs.
          </p>
        </aside>

        <section className="import-card">
          {phase === "account" && (
            <form onSubmit={establishWorkspace}>
              <p className="section-kicker">Step 1 of 5</p>
              <h2>Development sign-in</h2>
              <p className="muted">
                The local build uses a signed development session. Production
                deployments require OIDC.
              </p>
              <div className="form-grid two">
                <label>
                  Display name
                  <input
                    value={displayName}
                    onChange={(event) => setDisplayName(event.target.value)}
                    required
                  />
                </label>
                <label>
                  Email
                  <input
                    type="email"
                    value={email}
                    onChange={(event) => setEmail(event.target.value)}
                    required
                  />
                </label>
              </div>
              <div className="form-grid two">
                <label>
                  Workspace
                  <input
                    value={workspaceName}
                    onChange={(event) => setWorkspaceName(event.target.value)}
                    required
                  />
                </label>
                <label>
                  Project
                  <input
                    value={projectName}
                    onChange={(event) => setProjectName(event.target.value)}
                    required
                  />
                </label>
              </div>
              <button className="button primary" type="submit">
                Continue
              </button>
            </form>
          )}

          {phase === "files" && (
            <div>
              <p className="section-kicker">Step 2 of 5</p>
              <h2>Select a recording format</h2>
              <div className="format-grid">
                {FORMATS.map(([id, label, hint]) => (
                  <button
                    key={id}
                    className={`format-option ${format === id ? "selected" : ""}`}
                    onClick={() => setFormat(id)}
                  >
                    <strong>{label}</strong>
                    <span>{hint}</span>
                  </button>
                ))}
              </div>
              <label className="drop-zone">
                <input
                  type="file"
                  multiple={format === "brainvision" || format === "eeglab"}
                  onChange={(event) => chooseFiles(event.target.files)}
                />
                <span className="upload-icon">↑</span>
                <strong>
                  {files.length
                    ? `${files.length} file${files.length > 1 ? "s" : ""} selected`
                    : "Choose files or drop them here"}
                </strong>
                <small>
                  {files.length
                    ? `${(totalSize / 1_048_576).toFixed(2)} MiB · ${files.map((file) => file.name).join(", ")}`
                    : "Files are validated together before scientific processing."}
                </small>
              </label>
              <label>
                Recording name
                <input
                  value={recordingName}
                  onChange={(event) => setRecordingName(event.target.value)}
                  maxLength={180}
                  required
                />
              </label>
              <div className="button-row">
                <Link className="button ghost" href="/">
                  Cancel
                </Link>
                <button
                  className="button primary"
                  disabled={!files.length || !recordingName.trim()}
                  onClick={() => {
                    if (format === "csv") setPhase("mapping");
                    else void startUpload();
                  }}
                >
                  Review import
                </button>
              </div>
            </div>
          )}

          {phase === "mapping" && (
            <div>
              <p className="section-kicker">Step 3 of 5</p>
              <h2>Define the delimited-text schema</h2>
              <p className="muted">
                CSV does not carry a universal EEG schema. These choices become
                import provenance.
              </p>
              <div className="form-grid two">
                <label>
                  Sampling frequency (Hz)
                  <input
                    inputMode="decimal"
                    value={samplingRate}
                    onChange={(event) => setSamplingRate(event.target.value)}
                  />
                </label>
                <label>
                  Time column (optional)
                  <input
                    placeholder="time"
                    value={timeColumn}
                    onChange={(event) => setTimeColumn(event.target.value)}
                  />
                </label>
              </div>
              <label>
                Channel columns
                <input
                  value={channelColumns}
                  onChange={(event) => setChannelColumns(event.target.value)}
                />
                <small>Comma-separated source columns</small>
              </label>
              <label>
                Display channel names
                <input
                  value={channelNames}
                  onChange={(event) => setChannelNames(event.target.value)}
                />
              </label>
              <label>
                Source units
                <input
                  value={units}
                  onChange={(event) => setUnits(event.target.value)}
                />
                <small>Use V, mV, uV, µV, nV, or 1</small>
              </label>
              <div className="button-row">
                <button
                  className="button ghost"
                  onClick={() => setPhase("files")}
                >
                  Back
                </button>
                <button className="button primary" onClick={startUpload}>
                  Validate and upload
                </button>
              </div>
            </div>
          )}

          {(phase === "uploading" || phase === "processing") && (
            <div className="progress-panel" aria-live="polite">
              <div className="processing-glyph">
                <span />
                <span />
                <span />
              </div>
              <p className="section-kicker">
                {phase === "uploading"
                  ? "Secure upload"
                  : "Scientific processing"}
              </p>
              <h2>{stage || "Preparing import"}</h2>
              <div className="progress-track">
                <span style={{ width: `${progress}%` }} />
              </div>
              <p>{progress}% · Keep this window open during local uploads.</p>
            </div>
          )}

          {phase === "error" && (
            <div className="error-panel">
              <p className="section-kicker">Import stopped safely</p>
              <h2>Review the recording and try again.</h2>
              <p role="alert">{error}</p>
              <button
                className="button primary"
                onClick={() => setPhase("files")}
              >
                Return to files
              </button>
            </div>
          )}
          {error && phase !== "error" && (
            <p className="inline-error" role="alert">
              {error}
            </p>
          )}
        </section>
      </div>
    </main>
  );
}
