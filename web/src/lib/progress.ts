/**
 * A job's progress in words, as the notebook's progress line says it: 'Scanning… 1,200 of 4,000 objects (30%) ·
 * 12 s · 100/s · about 28 s left'. Pure, so Vitest checks it.
 */

import type { Progress } from "../api/types";

const BYTES = "B";
const KIBI = 1024;
const SIZE_UNITS = ["B", "KB", "MB", "GB", "TB", "PB"];
const MINUTE = 60;
const HOUR = 3600;
const SHOWS_RATE_AFTER = 1;
const WHOLE_RATE = 10;

/** 1536 -> '1.5 KB' (binary units, like the reports). */
export function humanSize(bytes: number): string {
  let amount = bytes;
  let unit = 0;
  while (amount >= KIBI && unit < SIZE_UNITS.length - 1) {
    amount /= KIBI;
    unit += 1;
  }
  return unit === 0 ? `${Math.round(amount)} B` : `${amount.toFixed(1)} ${SIZE_UNITS[unit]}`;
}

/** 75 -> '1 min 15 s', 4000 -> '1 h 6 min'. */
export function duration(seconds: number): string {
  const whole = Math.max(0, Math.round(seconds));
  if (whole < MINUTE) return `${whole} s`;
  if (whole < HOUR) {
    const rest = whole % MINUTE;
    return rest ? `${Math.floor(whole / MINUTE)} min ${rest} s` : `${whole / MINUTE} min`;
  }
  const minutes = Math.floor((whole % HOUR) / MINUTE);
  return minutes ? `${Math.floor(whole / HOUR)} h ${minutes} min` : `${whole / HOUR} h`;
}

/** How much of the work is done, 0 to 1, or null when the total isn't known. */
export function progressShare(progress: Progress): number | null {
  if (!progress.total) return null;
  return Math.min(progress.count / progress.total, 1);
}

function amount(progress: Progress, value: number): string {
  return progress.unit === BYTES ? humanSize(value) : value.toLocaleString("en-US");
}

function rateText(progress: Progress, rate: number): string {
  if (progress.unit === BYTES) return `${humanSize(rate)}/s`;
  return rate >= WHOLE_RATE ? `${Math.round(rate).toLocaleString("en-US")}/s` : `${rate.toFixed(1)}/s`;
}

function countText(progress: Progress): string {
  const of = progress.total ? ` of ${amount(progress, progress.total)}` : "";
  const unit = progress.unit === BYTES ? "" : ` ${progress.unit}`;
  const share = progressShare(progress);
  const percent = share === null ? "" : ` (${Math.round(share * 100)}%)`;
  return `${progress.label}… ${amount(progress, progress.count)}${of}${unit}${percent}`;
}

/** The progress as one line: what, how many of how many, the share, the time so far, the rate and the time left. */
export function progressText(progress: Progress): string {
  const parts = [countText(progress), duration(progress.seconds)];
  const isMeasured = progress.seconds >= SHOWS_RATE_AFTER && progress.count > 0;
  if (!isMeasured) return parts.join(" · ");
  const rate = progress.count / progress.seconds;
  parts.push(rateText(progress, rate));
  if (progress.total && progress.total > progress.count) {
    parts.push(`about ${duration((progress.total - progress.count) / rate)} left`);
  }
  return parts.join(" · ");
}
