/**
 * Lambda's "Running now" panel: the functions that ran in the last 10 minutes, a bar a minute (the failed part red),
 * pushed by the server's live stream (GET /api/live/lambda_functions/activity) every minute while the panel is on
 * screen. It says when it last read, what reading costs, and stops after the stream's time, with Go on to watch more.
 * Every name and number is text (JSX escapes it), never HTML.
 */

import { useEffect, useState } from "preact/hooks";
import { follow } from "../api/events";
import type { ActivityRow, ActivitySample, LiveStart, NoteEvent } from "../api/types";
import { agoText, lastCalledText, minuteBars, watchCost } from "../lib/live";
import { routeFor } from "../lib/route";

const STREAM = "api/live/lambda_functions/activity";
const TICK_MS = 5000;
const SHOWN_MOST = 12;

interface Watch {
  start: LiveStart | null;
  sample: ActivitySample | null;
  note: string;
  ended: string;
  readAt: number;
}

const NOT_YET: Watch = { start: null, sample: null, note: "", ended: "", readAt: 0 };

function useNow(): number {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), TICK_MS);
    return () => clearInterval(timer);
  }, []);
  return now;
}

/** The stream's samples while `round` isn't 0: a new round follows it afresh. */
function useActivity(round: number): Watch {
  const [watch, setWatch] = useState<Watch>(NOT_YET);
  useEffect(() => {
    if (!round) return;
    setWatch(NOT_YET);
    const stream = follow(
      STREAM,
      {
        start: (data) => setWatch((now) => ({ ...now, start: data as LiveStart })),
        sample: (data) => setWatch((now) => ({ ...now, sample: data as ActivitySample, note: "", readAt: Date.now() })),
        note: (data) => setWatch((now) => ({ ...now, note: (data as NoteEvent).text })),
        done: (data) => setWatch((now) => ({ ...now, ended: (data as { reason: string }).reason })),
      },
      () => setWatch((now) => ({ ...now, ended: "Lost the connection to the server." })),
    );
    return () => stream.close();
  }, [round]);
  return watch;
}

function MinuteBars({ row }: { row: ActivityRow }) {
  const heights = minuteBars(row.calls);
  return (
    <span class="aa-minutes" aria-hidden="true">
      {heights.map((height, minute) => {
        const failedShare = row.calls[minute] ? (row.errors[minute] ?? 0) / row.calls[minute] : 0;
        return (
          <span key={minute} class="aa-minute" style={{ height: `${Math.max(height * 100, 4)}%` }}>
            <span class="aa-minute-bad" style={{ height: `${Math.min(failedShare, 1) * 100}%` }} />
          </span>
        );
      })}
    </span>
  );
}

function count(amount: number, noun: string): string {
  return `${amount.toLocaleString("en-US")} ${noun}${amount === 1 ? "" : "s"}`;
}

function RowWords({ row }: { row: ActivityRow }) {
  const most = Math.max(0, ...row.concurrency);
  const called = lastCalledText(row.minutes_since_called);
  return (
    <span class="aa-live-words">
      {count(row.total_calls, "call")}
      {row.total_errors ? (
        <>
          {" · "}
          <span class="aa-live-failed">{`${row.total_errors.toLocaleString("en-US")} failed`}</span>
        </>
      ) : null}
      {most > 1 ? ` · up to ${Math.round(most)} at once` : ""}
      {called ? ` · ${called}` : ""}
    </span>
  );
}

function ActivityLine({ row }: { row: ActivityRow }) {
  return (
    <li class="aa-live-row">
      <a href={routeFor("lambda_functions", "function_info", { name: row.name, run: "1" })}>
        <code>{row.name}</code>
      </a>
      <MinuteBars row={row} />
      <RowWords row={row} />
    </li>
  );
}

function headline(sample: ActivitySample): string {
  if (!sample.functions.length) return `Nothing ran in the last ${sample.minutes} minutes.`;
  return `${count(sample.functions.length, "function")} ran in the last ${sample.minutes} minutes.`;
}

function Footer({ watch, now }: { watch: Watch; now: number }) {
  const { sample, start } = watch;
  if (!sample || !start) return <p class="aa-live-foot">Reading CloudWatch…</p>;
  const read = agoText((now - watch.readAt) / 1000);
  const cost = watchCost(sample.metrics_read, start.every);
  const quiet = sample.quiet && sample.functions.length ? ` The other ${sample.quiet} didn't run.` : "";
  return (
    <p class="aa-live-foot">
      {`Read ${read}, every ${Math.round(start.every)} s, for up to ${start.minutes} minutes: ${sample.metrics_read} `}
      {`CloudWatch metrics a read, ${cost}. CloudWatch's numbers come a minute or two late.${quiet}`}
    </p>
  );
}

export function LiveActivity() {
  const [round, setRound] = useState(1);
  const watch = useActivity(round);
  const now = useNow();
  const rows = watch.sample?.functions.slice(0, SHOWN_MOST) ?? [];
  const isLive = round !== 0 && !watch.ended;
  return (
    <section class="aa-live" aria-labelledby="aa-live-title">
      <div class="aa-live-head">
        <h2 id="aa-live-title" class="aa-group">
          <span class={`aa-live-dot${isLive ? " aa-on" : ""}`} aria-hidden="true" />
          Running now
        </h2>
        {watch.sample ? <span class="aa-live-headline">{headline(watch.sample)}</span> : null}
        <button type="button" class="aa-ghost" onClick={() => setRound(isLive ? 0 : Date.now())}>
          {isLive ? "Pause" : "Watch again"}
        </button>
      </div>
      {rows.length ? (
        <ul class="aa-live-rows">
          {rows.map((row) => (
            <ActivityLine key={row.arn} row={row} />
          ))}
        </ul>
      ) : null}
      {watch.note ? <p class="aa-live-note">{watch.note}</p> : null}
      {watch.ended ? <p class="aa-live-note">{watch.ended}</p> : null}
      {isLive ? <Footer watch={watch} now={now} /> : null}
    </section>
  );
}
