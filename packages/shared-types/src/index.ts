import type { components } from "./openapi";

export type Recording = components["schemas"]["RecordingResponse"];
export type Channel = components["schemas"]["ChannelResponse"];
export type Event = components["schemas"]["EventResponse"];
export type Annotation = components["schemas"]["AnnotationResponse"];
export type Workspace = components["schemas"]["WorkspaceResponse"];
export type Project = components["schemas"]["ProjectResponse"];
export type Job = components["schemas"]["JobResponse"];
export type JobStatus = components["schemas"]["JobState"];
export type BadChannel = components["schemas"]["BadChannelResponse"];
export type ProcessingRun = components["schemas"]["ProcessingRunResponse"];
export type ExportRecord = components["schemas"]["ExportResponse"];

export interface Page<T> {
  items: T[];
  next_cursor: string | null;
}

export interface WaveformChannel {
  index: number;
  name: string;
  unit: string;
  minimum: Array<number | null>;
  maximum: Array<number | null>;
  valid_count: number[];
}

export interface WaveformWindow {
  start_seconds: number;
  end_seconds: number;
  level: number;
  samples_per_bin: number;
  bin_start_seconds: number[];
  channels: WaveformChannel[];
}

export interface QCResult {
  disclaimer: string;
  metrics: Array<{
    id: string;
    channel_id: string | null;
    channel_name: string | null;
    rule_id: string;
    value: number | null;
    unit: string;
  }>;
  flags: Array<{
    id: string;
    metric_id: string;
    channel_id: string | null;
    severity: string;
    message: string;
    state: string;
  }>;
}
