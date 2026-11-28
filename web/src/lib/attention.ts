/**
 * What a service's check found, as plain data: how many concerns of each level, the words that sum them up, and the
 * concerns about one resource (for the quick look).
 */

import type { Attention, Concern, Level } from "../api/types";

export interface Counts {
  bad: number;
  warn: number;
  info: number;
}

const LEVEL_WORDS: Record<Level, [string, string]> = {
  bad: ["problem", "problems"],
  warn: ["warning", "warnings"],
  info: ["note", "notes"],
};
const LEVELS: Level[] = ["bad", "warn", "info"];

/** How many concerns a check found at each level. */
export function countLevels(concerns: Concern[]): Counts {
  const counts: Counts = { bad: 0, warn: 0, info: 0 };
  for (const concern of concerns) counts[concern.level] += 1;
  return counts;
}

/** '2 problems · 3 warnings · 1 note', or 'nothing needs attention'. */
export function countsText(counts: Counts): string {
  const parts = LEVELS.filter((level) => counts[level] > 0).map((level) => {
    const [one, many] = LEVEL_WORDS[level];
    return `${counts[level]} ${counts[level] === 1 ? one : many}`;
  });
  return parts.length ? parts.join(" · ") : "nothing needs attention";
}

/** The worst level a check found, or '' when it found nothing. */
export function worstLevel(attention: Attention): Level | "" {
  const counts = countLevels(attention.concerns);
  return LEVELS.find((level) => counts[level] > 0) ?? "";
}

/** The concerns that name a resource. */
export function concernsAbout(attention: Attention | undefined, name: string): Concern[] {
  if (!attention) return [];
  return attention.concerns.filter((concern) => concern.targets.some((target) => target.name === name));
}

/** 'a warning', 'a problem', 'a note': how the page names one concern's level. */
export function levelWord(level: Level): string {
  return LEVEL_WORDS[level][0];
}
