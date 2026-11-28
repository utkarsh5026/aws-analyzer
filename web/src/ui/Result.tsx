/**
 * The line above a report (running, failed, or which call it was) and the report: HTML drawn by the server's Views,
 * which escape what AWS returns. It's the one HTML the page inserts; its next steps that name a command the page
 * offers get a ▶ Run.
 */

import { useEffect, useLayoutEffect, useRef, useState } from "preact/hooks";
import type { Report, Service } from "../api/types";
import { calledCommand } from "../lib/call";
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

function StatusLine({ status, onAgain }: { status: Status; onAgain: () => void }) {
  if (status.kind === "running") {
    return (
      <div class="aa-status">
        <span class="aa-spin" aria-hidden="true" />
        {"Running "}
        <code>{status.call}</code> <Seconds started={status.started} />
      </div>
    );
  }
  if (status.kind === "failed") {
    return <div class="aa-status aa-bad">{`Couldn't run ${status.call}: ${status.message}`}</div>;
  }
  return (
    <div class="aa-status">
      <code>{status.call}</code>
      {` · ${status.when} · `}
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
  }, [report]);
  return <div ref={box} class={`aa-report${waiting ? " aa-waiting" : ""}`} />;
}

interface ResultProps {
  shown: Shown;
  service: Service;
  onAgain: () => void;
  onStep: (call: string) => void;
}

export function Result({ shown, service, onAgain, onStep }: ResultProps) {
  return (
    <div class="aa-result">
      {shown.status ? <StatusLine status={shown.status} onAgain={onAgain} /> : null}
      {shown.report ? (
        <ReportHtml
          key={shown.report}
          report={shown.report}
          waiting={shown.waiting}
          service={service}
          onStep={onStep}
        />
      ) : null}
    </div>
  );
}
