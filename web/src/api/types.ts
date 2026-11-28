/**
 * What the server's API sends: the catalog of services and commands (GET /api/catalog), a report, a job and its
 * events (POST /api/jobs, GET /api/jobs/<id>/events), one service's resources for the search
 * (GET /api/resources/<service>), and the live streams' samples (GET /api/live/...).
 */

/** One of a command's parameters as its form shows it (server/catalog.py's Parameter). */
export interface Parameter {
  name: string;
  /** The default written as Python, '' when there's none (then it's required). */
  default: string;
  required: boolean;
  /** 'flag' (a checkbox, for a True / False default) or 'text'. */
  kind: "flag" | "text";
  /** False after a bare * in the signature: it can only be given by name. */
  positional: boolean;
}

/** A command the page can run (server/catalog.py's Command). */
export interface Command {
  name: string;
  signature: string;
  about: string;
  doc: string;
  group: string;
  parameters: Parameter[];
}

/** A service and the commands the page offers of its View. */
export interface Service {
  key: string;
  title: string;
  icon: string;
  /** Whether its reports come as data too (GET /api/data). */
  data: boolean;
  /** Whether the page can search its resources (GET /api/resources). */
  searchable: boolean;
  commands: Command[];
}

export interface Catalog {
  version: string;
  region: string;
  profile: string;
  services: Service[];
}

/** One block of a report as JSON (_kit/blockjson.py): its kind ('title', 'table', ..., 'html') and its parts. */
export interface Block {
  kind: string;
  [part: string]: unknown;
}

/** One report as blocks, the box class they go in, and the CSS rules its blocks added (_report_json). */
export interface ReportBlocks {
  box_class: string;
  badge: string;
  rules: string[];
  blocks: Block[];
}

/**
 * A command's report (server/runner.py's Report): the call it ran, its arguments as form texts, its HTML, and each
 * report it showed as blocks.
 */
export interface Report {
  service: string;
  command: string;
  call: string;
  arguments: Record<string, string>;
  html: string;
  seconds: number;
  reports: ReportBlocks[];
}

/** How far a job has got (a View's progress tick): what it's reading, how many so far, of how many. */
export interface Progress {
  label: string;
  /** What it counts ('objects', 'items read'), or 'B' for bytes. */
  unit: string;
  count: number;
  total: number | null;
  /** Seconds since this count started. */
  seconds: number;
}

export type JobState = "running" | "done" | "failed" | "stopped";

/** A job as the server lists it (server/jobs.py's Job.summary). */
export interface JobSummary {
  id: string;
  service: string;
  command: string;
  call: string;
  arguments: Record<string, string>;
  started_at: string;
  state: JobState;
  seconds: number;
  progress: Progress | null;
  events: number;
  stopping: boolean;
}

/** A report a job showed: as the notebook draws it, and as blocks. */
export interface ReportEvent {
  html: string;
  report: ReportBlocks;
}

/** A sentence a job or a live stream says (stopping, a read that failed). */
export interface NoteEvent {
  text: string;
  level: string;
}

/** How a job ended. */
export interface DoneEvent {
  state: JobState;
  seconds: number;
}

/** One function in Lambda's live activity: CloudWatch's last minutes, a value a minute, oldest first. */
export interface ActivityRow {
  name: string;
  arn: string;
  region: string;
  runtime: string;
  timeout: number;
  calls: number[];
  errors: number[];
  concurrency: number[];
  total_calls: number;
  total_errors: number;
  minutes_since_called: number | null;
}

/** A sample of Lambda's live activity (server/feeds.py's lambda_functions/activity). */
export interface ActivitySample {
  read_at: string;
  minutes: number;
  functions: ActivityRow[];
  quiet: number;
  metrics_read: number;
  errors: Record<string, string>;
}

/** What a live stream says it is when it starts. */
export interface LiveStart {
  service: string;
  feed: string;
  params: Record<string, string>;
  every: number;
  minutes: number;
  about: string;
}

/** One thing the search finds (server/resources.py's Resource), with the command that opens its report. */
export interface Resource {
  kind: string;
  name: string;
  detail: string;
  command: string;
  arguments: Record<string, string>;
}

/** One service's resources, as its listing call read them (server/resources.py's ResourceIndex). */
export interface ResourceIndex {
  service: string;
  resources: Resource[];
  /** When they were read, ISO 8601. */
  read_at: string;
  seconds: number;
  /** True when the server answered from what it kept rather than asking AWS. */
  from_memory: boolean;
}
