/**
 * What the live panels read from a stream's samples: a bar a minute, how long ago something happened, and what
 * watching costs. Pure, so Vitest checks it.
 */

const SECONDS_PER_HOUR = 3600;
const METRIC_PRICE = 0.01 / 1000;
const LEAST_COST = 0.01;
const JUST_NOW = 5;
const MINUTE = 60;

/** A bar a minute: each value as a share (0 to 1) of the biggest, all 0 when nothing happened. */
export function minuteBars(values: number[]): number[] {
  const most = Math.max(0, ...values);
  return values.map((value) => (most ? value / most : 0));
}

/** 'just now', '45 s ago', '3 min ago'. */
export function agoText(seconds: number): string {
  if (seconds < JUST_NOW) return "just now";
  if (seconds < MINUTE) return `${Math.round(seconds)} s ago`;
  return `${Math.round(seconds / MINUTE)} min ago`;
}

/** 'last called 3 min ago', 'called this minute', or '' when CloudWatch has no call in the window. */
export function lastCalledText(minutes: number | null): string {
  if (minutes === null) return "";
  return minutes === 0 ? "called this minute" : `last called ${minutes} min ago`;
}

/**
 * What reading CloudWatch costs while watching: `metrics` read every `everySeconds` seconds, at GetMetricData's
 * $0.01 per 1,000 metrics, a dollar amount an hour ('under $0.01 an hour' when it's less).
 */
export function watchCost(metrics: number, everySeconds: number): string {
  const perHour = (metrics * SECONDS_PER_HOUR * METRIC_PRICE) / Math.max(everySeconds, 1);
  return perHour < LEAST_COST ? "under $0.01 an hour" : `about $${perHour.toFixed(2)} an hour`;
}
