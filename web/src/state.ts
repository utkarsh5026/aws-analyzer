/**
 * What the whole page shares: the catalog once it's loaded, where the page is (the hash), whether the finder is
 * open, and the reports already run, by the hash that shows them, so back and forward don't run them again.
 */

import { signal } from "@preact/signals";
import type { Catalog, Report } from "./api/types";
import { parseRoute } from "./lib/route";

export const catalog = signal<Catalog | null>(null);
export const loadError = signal("");
export const hash = signal(location.hash);
export const finderOpen = signal(false);

/** The route the hash names. */
export function currentRoute() {
  return parseRoute(hash.value);
}

/** Reports by the hash that shows them. */
export const reports = new Map<string, Report>();

/**
 * The runs: only the newest run's answer is shown, wherever it was started, and a call run from a report's next
 * step lands on its own address marked `justRun`, so it isn't shown as "from earlier".
 */
export const runs = { latest: 0, justRun: "" };

addEventListener("hashchange", () => {
  hash.value = location.hash;
});
