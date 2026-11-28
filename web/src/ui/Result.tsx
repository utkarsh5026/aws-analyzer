/**
 * The line above a report (running with its progress and a Stop button, stopped, failed, or which call it was) and
 * the report: HTML drawn by the server's Views, which escape what AWS returns. It's the one HTML the page inserts;
 * its next steps that name a command the page offers get a ▶ Run. While a job runs, the reports it has shown so far
 * are drawn as they come.
 */

import { useEffect, useLayoutEffect, useRef, useState } from "preact/hooks";
import type { Progress, Report, Service } from "../api/types";
import { calledCommand } from "../lib/call";
import { duration, progressShare, progressText } from "../lib/progress";
import type { Shown, Status } from "./useRuns";

const TICK_MS = 1000;

function Seconds({ started }: { started: number }) {
  const [text, setText] = useState("");
  useEffect(() => {
    const timer = setInterval(() => setText(`${Math.round((Date.now() - started) / 1000)} s`), TICK_MS);
    return () => clearInterval(timer);
  }, [started]);
  return <span>{text}</span>;
}

type Running = Extract<Status, { kind: "running" }>;

function ProgressBar({ progress }: { progress: Progress }) {
  const share = progressShare(progress);
  const label = progressText(progress);
  return (
    <div class="aa-progress">
      <div
        class={`aa-progress-track${share === null ? " aa-unknown" : ""}`}
        role="progressbar"
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={share === null ? undefined : Math.round(share * 100)}
      >
        <span style={share === null ? undefined : { width: `${share * 100}%` }} />
      </div>
      <span class="aa-progress-text">{label}</span>
    </div>
  );
}

function RunningLine({ status, onStop }: { status: Running; onStop: () => void }) {
  return (
    <div class="aa-status aa-running" role="status">
      <div class="aa-status-row">
        <span class="aa-spin" aria-hidden="true" />
        {"Running "}
        <code>{status.call}</code> <Seconds started={status.started} />
        <button class="aa-ghost aa-stop" type="button" onClick={onStop} disabled={status.stopping}>
          {status.stopping ? "Stopping…" : "■ Stop"}
        </button>
      </div>
      {status.progress ? <ProgressBar progress={status.progress} /> : null}
      {status.note ? <p class="aa-status-note">{status.note}</p> : null}
    </div>
  );
}

interface LineProps {
  status: Status;
  onAgain: () => void;
  onStop: () => void;
}

function StatusLine({ status, onAgain, onStop }: LineProps) {
  if (status.kind === "running") return <RunningLine status={status} onStop={onStop} />;
  if (status.kind === "failed") {
    return <div class="aa-status aa-bad">{`Couldn't run ${status.call}: ${status.message}`}</div>;
  }
  const said = status.kind === "stopped" ? ` · stopped after ${duration(status.seconds)} · ` : ` · ${status.when} · `;
  return (
    <div class="aa-status">
      <code>{status.call}</code>
      {said}
      <button class="aa-ghost" type="button" onClick={onAgain}>
        ↻ Run again
      </button>
    </div>
  );
}

function stepButton(call: string, onStep: (call: string) => void): HTMLButtonElement {
  const button = document.createElement("button");
  button.className = "aa-step-run";
  button.type = "button";
  button.textContent = "▶ Run";
  button.title = `Run ${call} here`;
  button.addEventListener("click", () => onStep(call));
  return button;
}

function addStepButtons(report: HTMLElement, offered: Set<string>, onStep: (call: string) => void) {
  for (const step of report.querySelectorAll(".next .ni code")) {
    const call = step.textContent?.trim() ?? "";
    if (!offered.has(calledCommand(call))) continue;
    let why = step.parentElement?.querySelector(".nw");
    if (!why) {
      why = document.createElement("span");
      why.className = "nw";
      step.after(why);
    }
    why.prepend(stepButton(call, onStep));
  }
}

interface ReportProps {
  report: Report;
  waiting: boolean;
  service: Service;
  onStep: (call: string) => void;
}

function ReportHtml({ report, waiting, service, onStep }: ReportProps) {
  const box = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    if (!box.current) return;
    box.current.innerHTML = report.html;
    addStepButtons(box.current, new Set(service.commands.map((command) => command.name)), onStep);
  }, [report.html]);
  return <div ref={box} class={`aa-report${waiting ? " aa-waiting" : ""}`} />;
}

interface ResultProps {
  shown: Shown;
  service: Service;
  onAgain: () => void;
  onStop: () => void;
  onStep: (call: string) => void;
}

export function Result({ shown, service, onAgain, onStop, onStep }: ResultProps) {
  return (
    <div class="aa-result">
      {shown.status ? <StatusLine status={shown.status} onAgain={onAgain} onStop={onStop} /> : null}
      {shown.report ? (
        <ReportHtml
          key={shown.report.call}
          report={shown.report}
          waiting={shown.waiting}
          service={service}
          onStep={onStep}
        />
      ) : null}
    </div>
  );
}
