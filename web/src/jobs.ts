/**
 * The jobs this page started (or found running when it loaded): each followed as its events come (progress, reports,
 * notes, done), so a command page shows its job as it goes and the bar lists the ones still running wherever the
 * page is. A job that finishes keeps its report for its address (state.ts's reports), so going back shows it again.
 */

import { signal } from "@preact/signals";
import { api, apiDelete } from "./api/client";
import { follow } from "./api/events";
import type { DoneEvent, JobState, JobSummary, NoteEvent, Progress, Report, ReportEvent } from "./api/types";
import { isSameRoute, routeFor } from "./lib/route";
import { reports } from "./state";

const JOBS_KEPT = 20;
const LOST = "Lost the connection to the server: it may have stopped. Start aws-analyzer serve again, then run it.";

/** A job as the page follows it. */
export interface TrackedJob {
  id: string;
  service: string;
  command: string;
  call: string;
  arguments: Record<string, string>;
  /** The address that shows it. */
  target: string;
  state: JobState;
  progress: Progress | null;
  notes: NoteEvent[];
  shown: ReportEvent[];
  seconds: number;
  /** When the page started following it (Date.now()), for the seconds while it runs. */
  started: number;
  stopping: boolean;
}

/** Every job followed, newest first. */
export const trackedJobs = signal<TrackedJob[]>([]);

/** The address that shows a job's command with its arguments. */
export function jobTarget(summary: JobSummary): string {
  return routeFor(summary.service, summary.command, { ...summary.arguments, run: "1" });
}

function trackedFrom(summary: JobSummary, target: string): TrackedJob {
  const started = Date.now() - summary.seconds * 1000;
  return { ...summary, target, notes: [], shown: [], started, progress: summary.progress };
}

function change(id: string, update: (job: TrackedJob) => TrackedJob) {
  trackedJobs.value = trackedJobs.value.map((job) => (job.id === id ? update(job) : job));
}

/** A job's reports as one Report, the way /api/run answers (its HTML, each report as blocks). */
export function reportOf(job: TrackedJob): Report {
  return {
    service: job.service,
    command: job.command,
    call: job.call,
    arguments: job.arguments,
    html: job.shown.map((shown) => shown.html).join(""),
    seconds: job.seconds,
    reports: job.shown.map((shown) => shown.report),
  };
}

function finished(id: string, done: DoneEvent) {
  change(id, (job) => ({ ...job, state: done.state, seconds: done.seconds, stopping: false }));
  const job = trackedJobs.value.find((each) => each.id === id);
  if (job && done.state === "done") reports.set(job.target, reportOf(job));
}

function followJob(id: string) {
  follow(
    `api/jobs/${encodeURIComponent(id)}/events`,
    {
      progress: (data) => change(id, (job) => ({ ...job, progress: data as Progress })),
      report: (data) => change(id, (job) => ({ ...job, shown: [...job.shown, data as ReportEvent] })),
      note: (data) => change(id, (job) => ({ ...job, notes: [...job.notes, data as NoteEvent] })),
      done: (data) => finished(id, data as DoneEvent),
    },
    () => change(id, (job) => ({ ...job, state: "failed", notes: [...job.notes, { text: LOST, level: "warn" }] })),
  );
}

function track(job: TrackedJob) {
  trackedJobs.value = [job, ...trackedJobs.value.filter((each) => each.id !== job.id)].slice(0, JOBS_KEPT);
  followJob(job.id);
}

/**
 * Starts a command as a job (`body` as /api/run takes it: a command and its boxes, or a call) and follows it; the
 * job's address is the one given, else the one its command and arguments make. Throws the server's words when it
 * can't run.
 */
export async function startJob(body: object, target?: string): Promise<TrackedJob> {
  const summary = await api<JobSummary>("api/jobs", body);
  const job = trackedFrom(summary, target ?? jobTarget(summary));
  track(job);
  return job;
}

/** Asks the server to stop a job at its next step. */
export async function stopJob(id: string) {
  change(id, (job) => ({ ...job, stopping: true }));
  await apiDelete<JobSummary>(`api/jobs/${encodeURIComponent(id)}`);
}

/** The newest job still running for an address, if any. */
export function jobFor(target: string): TrackedJob | undefined {
  return trackedJobs.value.find((job) => job.state === "running" && isSameRoute(job.target, target));
}

/** The jobs still running, newest first. */
export function runningJobs(): TrackedJob[] {
  return trackedJobs.value.filter((job) => job.state === "running");
}

/** Follows the jobs the server is running that this page doesn't know (started before a reload, or in another tab). */
export async function followServerJobs() {
  const { jobs } = await api<{ jobs: JobSummary[] }>("api/jobs");
  const known = new Set(trackedJobs.value.map((job) => job.id));
  for (const summary of jobs.reverse()) {
    if (summary.state === "running" && !known.has(summary.id)) track(trackedFrom(summary, jobTarget(summary)));
  }
}
