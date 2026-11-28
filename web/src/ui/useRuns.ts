/**
 * Running a command and keeping what the page says about it: the status line above the report (running, failed or
 * done) and the report itself. Only the newest run anywhere on the page is shown; a report already run for this
 * address is shown again without running, unless the page asks for it afresh.
 */

import { useState } from "preact/hooks";
import { api } from "../api/client";
import type { Command, Report, Service } from "../api/types";
import { callText } from "../lib/call";
import { parseRoute, routeFor } from "../lib/route";
import { reports, runs } from "../state";

export type Status =
  | { kind: "running"; call: string; started: number }
  | { kind: "failed"; call: string; message: string }
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
}

const NOTHING: Shown = { status: null, report: null, waiting: false };

function formArguments(args: Record<string, string>): Record<string, string> {
  return Object.fromEntries(Object.entries(args).filter(([name]) => name !== "run"));
}

export function useRuns(service: Service, command: Command): Runs {
  const [shown, setShown] = useState<Shown>(NOTHING);

  const showReport = (report: Report, fromEarlier: boolean) => {
    const when = fromEarlier ? "shown from earlier" : `${report.seconds} s`;
    setShown({ status: { kind: "done", call: report.call, when }, report, waiting: false });
  };

  const fetchReport = async (call: string, body: object): Promise<Report | null> => {
    const id = ++runs.latest;
    setShown((before) => ({ ...before, status: { kind: "running", call, started: Date.now() }, waiting: true }));
    try {
      const report = await api<Report>("api/run", body);
      return id === runs.latest ? report : null;
    } catch (error) {
      if (id === runs.latest) {
        const message = error instanceof Error ? error.message : String(error);
        setShown((before) => ({ ...before, status: { kind: "failed", call, message }, waiting: false }));
      }
      return null;
    }
  };

  const runForm = async (again: boolean) => {
    const key = location.hash;
    const earlier = reports.get(key);
    if (!again && earlier) {
      showReport(earlier, runs.justRun !== key);
      runs.justRun = "";
      return;
    }
    const values = formArguments(parseRoute(key).args);
    const body = { service: service.key, command: command.name, arguments: values };
    const report = await fetchReport(callText(command, values), body);
    if (!report) return;
    reports.set(key, report);
    showReport(report, false);
  };

  const runCall = async (call: string) => {
    const report = await fetchReport(call, { service: service.key, call });
    if (!report) return;
    const target = routeFor(service.key, report.command, { ...report.arguments, run: "1" });
    reports.set(target, report);
    if (target === location.hash) {
      showReport(report, false);
      return;
    }
    runs.justRun = target;
    location.hash = target;
  };

  return { shown, runForm, runCall };
}
