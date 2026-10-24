// k6 mixed-workload load test for docs/PERFORMANCE.md.
//
// Each VU signs in once (a unique dev-auth user) and provisions its own
// tiny owned recording, then repeats a weighted-random action mix every
// iteration:
//
//   35% waveform viewport read   (shared public perf-bench 30s/5min/30min
//                                  fixtures -- readable by anyone, real
//                                  S3-backed signal data)
//   20% recording metadata read  (the VU's own recording)
//   15% annotation read          (the VU's own recording)
//   10% recording-list pagination (the VU's own project)
//   10% job status polling       (the VU's own ingestion job)
//    5% annotation write          (the VU's own recording)
//    5% export creation          (the VU's own recording -- stands in for
//                                  "processing/export-type mutation"; see
//                                  docs/PERFORMANCE.md for why
//                                  average_reference itself was not used
//                                  here -- every run would mint a new
//                                  RecordingVersion per VU per iteration,
//                                  which is not a realistic mutation rate)
//
// Deep, cross-tenant-scale reads (a recording with tens of thousands of
// annotations, a project with thousands of recordings) are deliberately
// NOT exercised here: a k6 VU is a real signed-in user and workspace
// authorization correctly refuses it access to another tenant's data
// (verified as a 404, matching API.md's "missing and unauthorized private
// resources return the same response"). That query-plan/index behavior at
// scale is covered directly against the database by scripts/bench_queries.py
// and scripts/bench_pagination.py instead; this script measures real
// end-to-end HTTP-level behavior under concurrency for the queries a
// single tenant's session actually reaches.
//
// Usage:
//   k6 run -e API_BASE=http://localhost:8000/api \
//          -e PERF_BENCH_RECORDINGS=<id1,id2,id3 from seed_signal_recordings.py> \
//          -e VUS=10 -e DURATION=30s \
//          scripts/loadtest.js
//
// Against Kubernetes with N replicas port-forwarded individually, pass a
// comma-separated API_BASE (see the API_BASE_CANDIDATES comment below):
//   k6 run -e API_BASE=http://localhost:18001,http://localhost:18002,http://localhost:18003 ...

import http from "k6/http";
import { check, sleep } from "k6";
import { Counter, Trend } from "k6/metrics";

// API_BASE may be a single URL, or a comma-separated list of URLs -- the
// latter is how the Kubernetes scaling test drives multiple replicas
// directly: `kubectl port-forward` to a Service picks a
// single backend pod for the whole forwarded connection (it does not
// round-robin the way the in-cluster Service/kube-proxy path does), so
// hitting only one forwarded URL cannot show a throughput difference as
// replicas are added. Each VU picks one base from the list at startup
// (a real client is not expected to round-robin every single request
// either) so load still spreads across all forwarded pods across the
// whole run.
const API_BASE_CANDIDATES = (__ENV.API_BASE || "http://localhost:8000/api")
  .split(",")
  .map((s) => s.trim())
  .filter(Boolean);
const API_BASE = API_BASE_CANDIDATES[__VU % API_BASE_CANDIDATES.length];
const PERF_BENCH_RECORDINGS = (__ENV.PERF_BENCH_RECORDINGS || "").split(",").filter(Boolean);

const errors = new Counter("app_errors");
const waveformDuration = new Trend("waveform_ms");
const annotationReadDuration = new Trend("annotation_read_ms");
const paginationDuration = new Trend("pagination_ms");
const jobPollDuration = new Trend("job_poll_ms");
const writeDuration = new Trend("write_ms");

export const options = {
  scenarios: {
    mixed: {
      executor: "constant-vus",
      vus: Number(__ENV.VUS || 10),
      duration: __ENV.DURATION || "30s",
    },
  },
  thresholds: {
    app_errors: ["count==0"],
  },
};

// Cookies set via one VU's http.post do not reliably carry over to a later
// iteration's http.get in this k6 version -- observed directly with
// --http-debug=full (the session cookie was simply absent from later
// requests). Rather than depend on k6's automatic per-VU jar behavior
// across iterations, the session cookie is captured explicitly from the
// sign-in response and attached by hand on every subsequent request.
function headers(cookie) {
  const base = { "Content-Type": "application/json" };
  if (cookie) base["Cookie"] = cookie;
  return { headers: base };
}

function extractSessionCookie(response) {
  const setCookie = response.headers["Set-Cookie"];
  if (!setCookie) return null;
  return setCookie.split(";")[0];
}

let vuState = null;

function ensureVuState() {
  if (vuState) return vuState;
  const vu = __VU;
  const email = `k6-bench-vu-${vu}-${Date.now()}@example.test`;
  const signIn = http.post(
    `${API_BASE}/session/dev`,
    JSON.stringify({ email, display_name: `k6 VU ${vu}` }),
    headers(null),
  );
  check(signIn, { "sign-in 200": (r) => r.status === 200 }) || errors.add(1);
  const cookie = extractSessionCookie(signIn);

  const wsResp = http.post(
    `${API_BASE}/workspaces`,
    JSON.stringify({ name: `k6-bench-ws-${vu}-${Date.now()}` }),
    headers(cookie),
  );
  const workspace = JSON.parse(wsResp.body);
  const projResp = http.post(
    `${API_BASE}/workspaces/${workspace.id}/projects`,
    JSON.stringify({ name: `k6-bench-project-${vu}`, description: "" }),
    headers(cookie),
  );
  const project = JSON.parse(projResp.body);

  const csvBody =
    "time,C3,C4\n" +
    Array.from({ length: 512 }, (_, i) => `${(i / 256).toFixed(4)},${i},${-i}`).join("\n");

  const uploadResp = http.post(
    `${API_BASE}/projects/${project.id}/uploads`,
    JSON.stringify({
      recording_name: `k6-bench-${vu}`,
      format_hint: "csv",
      assets: [{ name: "d.csv", size_bytes: csvBody.length, role: "primary", media_type: "text/csv" }],
      csv_config: {
        sampling_frequency_hz: 256,
        time_column: "time",
        channel_columns: ["C3", "C4"],
        channel_names: ["C3", "C4"],
        units: ["uV", "uV"],
        channel_types: ["eeg", "eeg"],
        delimiter: ",",
      },
    }),
    headers(cookie),
  );
  const upload = JSON.parse(uploadResp.body);
  const asset = upload.assets[0];
  http.put(asset.upload_url, csvBody, { headers: { "Content-Type": "text/csv" } });
  const completeResp = http.post(`${API_BASE}/uploads/${upload.id}/complete`, null, headers(cookie));
  const completion = JSON.parse(completeResp.body);

  // Ingestion is asynchronous (WORKER_EAGER=false in this stack): wait for
  // it to actually finish before the main loop starts writing annotations
  // against this recording, otherwise early writes correctly 422
  // ("annotation_out_of_bounds" against a recording whose safe_metadata
  // isn't populated yet) -- a real race, not something to paper over by
  // loosening the check.
  for (let attempt = 0; attempt < 30; attempt++) {
    const jobResp = http.get(`${API_BASE}/jobs/${completion.job_id}`, headers(cookie));
    const job = JSON.parse(jobResp.body);
    if (job.status === "SUCCEEDED" || job.status === "FAILED") break;
    sleep(0.5);
  }

  vuState = {
    cookie,
    projectId: project.id,
    recordingId: completion.recording_id,
    jobId: completion.job_id,
  };
  return vuState;
}

function pick(weights) {
  const total = weights.reduce((sum, [, w]) => sum + w, 0);
  let roll = Math.random() * total;
  for (const [name, w] of weights) {
    if (roll < w) return name;
    roll -= w;
  }
  return weights[weights.length - 1][0];
}

export default function () {
  const state = ensureVuState();

  const action = pick([
    ["waveform", 35],
    ["metadata", 20],
    ["annotation_read", 15],
    ["pagination", 10],
    ["job_poll", 10],
    ["annotation_write", 5],
    ["export", 5],
  ]);

  if (action === "waveform" && PERF_BENCH_RECORDINGS.length) {
    const rid = PERF_BENCH_RECORDINGS[Math.floor(Math.random() * PERF_BENCH_RECORDINGS.length)];
    const start = Math.random() * 20;
    const duration = [10, 30][Math.floor(Math.random() * 2)];
    const res = http.get(
      `${API_BASE}/recordings/${rid}/waveform?start_seconds=${start}&duration_seconds=${duration}&pixel_width=1200`,
      headers(state.cookie),
    );
    waveformDuration.add(res.timings.duration);
    check(res, { "waveform 200": (r) => r.status === 200 }) || errors.add(1);
  } else if (action === "metadata") {
    const res = http.get(`${API_BASE}/recordings/${state.recordingId}`, headers(state.cookie));
    check(res, { "metadata 200": (r) => r.status === 200 }) || errors.add(1);
  } else if (action === "annotation_read") {
    const res = http.get(
      `${API_BASE}/recordings/${state.recordingId}/annotations?limit=200`,
      headers(state.cookie),
    );
    annotationReadDuration.add(res.timings.duration);
    check(res, { "annotations 200": (r) => r.status === 200 }) || errors.add(1);
  } else if (action === "pagination") {
    const res = http.get(
      `${API_BASE}/projects/${state.projectId}/recordings?limit=50`,
      headers(state.cookie),
    );
    paginationDuration.add(res.timings.duration);
    check(res, { "pagination 200": (r) => r.status === 200 }) || errors.add(1);
  } else if (action === "job_poll") {
    const res = http.get(`${API_BASE}/jobs/${state.jobId}`, headers(state.cookie));
    jobPollDuration.add(res.timings.duration);
    check(res, { "job 200": (r) => r.status === 200 }) || errors.add(1);
  } else if (action === "annotation_write") {
    const res = http.post(
      `${API_BASE}/recordings/${state.recordingId}/annotations`,
      JSON.stringify({
        // The VU's own uploaded recording is only 512 samples @ 256Hz (2s)
        // -- stay well inside it so this exercises the annotation-write
        // path itself, not the (correct, separately-tested) out-of-bounds
        // rejection.
        onset_seconds: Math.random() * 1.5,
        duration_seconds: 0.1,
        label: "k6-bench",
        description: "",
      }),
      headers(state.cookie),
    );
    writeDuration.add(res.timings.duration);
    check(res, { "annotation write 201": (r) => r.status === 201 }) || errors.add(1);
  } else if (action === "export") {
    const res = http.post(
      `${API_BASE}/recordings/${state.recordingId}/exports`,
      JSON.stringify({ format_id: "annotations_csv" }),
      headers(state.cookie),
    );
    writeDuration.add(res.timings.duration);
    check(res, { "export 201": (r) => r.status === 201 }) || errors.add(1);
  }
}
