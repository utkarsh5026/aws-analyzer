/**
 * Running a command and keeping what the page says about it: the status line above the report (running with its
 * progress, stopped, failed or done) and the report itself. A command runs as a job (jobs.ts): its reports show as
 * they come, Stop stops it, and leaving the page doesn't, so coming back to its address picks it up again. A report
 * already run for this address is shown again without running, unless the page asks for it afresh.
 */

import { useState } from "preact/hooks";
import type { Command, Progress, Report, Service } from "../api/types";
import { jobFor, reportOf, startJob, stopJob, type TrackedJob, trackedJobs } from "../jobs";
import { callText } from "../lib/call";
import { parseRoute } from "../lib/route";
import { reports, runs } from "../state";

const FAILED_JOB = "it stopped with an error. The terminal running aws-analyzer serve has the details.";

export type Status =
  | { kind: "running"; call: string; started: number; progress: Progress | null; stopping: boolean; note: string }
  | { kind: "failed"; call: string; message: string }
  | { kind: "stopped"; call: string; seconds: number }
  | { kind: "done"; call: string; when: string };

export interface Shown {
  status: Status | null;
  report: Report | null;
  waiting: boolean;
}

export interface Runs {
  shown: Shown;
  /** Runs the command with what the address holds (`again`: even when it was run before). */
  runForm: (again: boolean) => void;
  /** Runs a call as written in a notebook, then moves to its command's address. */
  runCall: (call: string) => void;
  /** Stops the job running, at its next step. */
  stop: () => void;
}

interface Earlier {
  report: Report;
  fromEarlier: boolean;
}

function formArguments(args: Record<string, string>): Record<string, string> {
  return Object.fromEntries(Object.entries(args).filter(([name]) => name !== "run"));
}

function lastNote(job: TrackedJob): string {
  return job.notes.at(-1)?.text ?? "";
}

/** What the page shows for a job: running with its progress and the reports so far, or how it ended. */
function shownForJob(job: TrackedJob, earlier: Earlier | null): Shown {
  const report = job.shown.length ? reportOf(job) : null;
  if (job.state === "running") {
    const status: Status = {
      kind: "running",
      call: job.call,
      started: job.started,
      progress: job.progress,
      stopping: job.stopping,
      note: lastNote(job),
    };
    return { status, report: report ?? earlier?.report ?? null, waiting: !report };
  }
  if (job.state === "stopped")
    return { status: { kind: "stopped", call: job.call, seconds: job.seconds }, report, waiting: false };
  if (job.state === "failed") {
    const message = lastNote(job) || FAILED_JOB;
    return {
      status: { kind: "failed", call: job.call, message },
      report: report ?? earlier?.report ?? null,
      waiting: false,
    };
  }
  return { status: { kind: "done", call: job.call, when: `${job.seconds} s` }, report, waiting: false };
}

function shownForEarlier(earlier: Earlier | null): Shown {
  if (!earlier) return { status: null, report: null, waiting: false };
  const when = earlier.fromEarlier ? "shown from earlier" : `${earlier.report.seconds} s`;
  return { status: { kind: "done", call: earlier.report.call, when }, report: earlier.report, waiting: false };
}

export function useRuns(service: Service, command: Command): Runs {
  const [jobId, setJobId] = useState("");
  const [earlier, setEarlier] = useState<Earlier | null>(null);
  const [failure, setFailure] = useState<Status | null>(null);
  const job = trackedJobs.value.find((each) => each.id === jobId);

  const start = async (body: object, call: string, target?: string): Promise<TrackedJob | null> => {
    setFailure(null);
    try {
      return await startJob(body, target);
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      setFailure({ kind: "failed", call, message });
      return null;
    }
  };

  const runForm = async (again: boolean) => {
    const key = location.hash;
    const handedOver = runs.handedOver.target === key ? runs.handedOver.id : "";
    runs.handedOver = { target: "", id: "" };
    const running = handedOver || jobFor(key)?.id;
    if (running && !again) {
      setJobId(running);
      return;
    }
    const kept = reports.get(key);
    if (!again && kept) {
      setEarlier({ report: kept, fromEarlier: true });
      return;
    }
    const values = formArguments(parseRoute(key).args);
    const body = { service: service.key, command: command.name, arguments: values };
    const started = await start(body, callText(command, values), key);
    if (started) setJobId(started.id);
  };

  const runCall = async (call: string) => {
    const started = await start({ service: service.key, call }, call);
    if (!started) return;
    if (started.target === location.hash) {
      setJobId(started.id);
      return;
    }
    runs.handedOver = { target: started.target, id: started.id };
    location.hash = started.target;
  };

  const stop = () => {
    if (jobId) stopJob(jobId);
  };

  const shown = job ? shownForJob(job, earlier) : shownForEarlier(earlier);
  const withFailure = failure ? { ...shown, status: failure, waiting: false } : shown;
  return { shown: withFailure, runForm, runCall, stop };
}
