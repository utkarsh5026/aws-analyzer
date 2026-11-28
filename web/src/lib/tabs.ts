/**
 * The tabs along the top, as plain data: each tab is the address it shows, Home is always the first and can't be
 * closed. Moving inside a tab replaces its address; leaving Home, or asking for a new tab, adds one; an address
 * another tab already shows switches to it.
 */

export const HOME = "#/";
export const MOST_TABS = 12;

export interface Tabs {
  hashes: string[];
  active: number;
}

/** The address as the tabs keep it: '', '#' and '#/' are all Home. */
export function normalHash(hash: string): string {
  return hash === "" || hash === "#" ? HOME : hash;
}

/** Just Home. */
export function startingTabs(): Tabs {
  return { hashes: [HOME], active: 0 };
}

function withNewTab(tabs: Tabs, hash: string): Tabs {
  const kept = tabs.hashes.length >= MOST_TABS ? [HOME, ...tabs.hashes.slice(2)] : tabs.hashes;
  return { hashes: [...kept, hash], active: kept.length };
}

/**
 * The tabs once the address became `hash`: the tab that shows it already, else a new tab (from Home, or when
 * `asNewTab`), else the active tab now showing it. Past MOST_TABS, the oldest tab after Home makes room.
 */
export function afterNavigation(tabs: Tabs, hash: string, asNewTab = false): Tabs {
  const target = normalHash(hash);
  const already = tabs.hashes.indexOf(target);
  if (already >= 0) return { hashes: tabs.hashes, active: already };
  if (target === HOME) return { hashes: tabs.hashes, active: 0 };
  if (asNewTab || tabs.active === 0) return withNewTab(tabs, target);
  const hashes = tabs.hashes.map((each, index) => (index === tabs.active ? target : each));
  return { hashes, active: tabs.active };
}

/** The tabs without the one at `index` (Home stays): the one after it is shown, else the one before. */
export function closeTab(tabs: Tabs, index: number): Tabs {
  if (index <= 0 || index >= tabs.hashes.length) return tabs;
  const hashes = tabs.hashes.filter((_, each) => each !== index);
  if (index < tabs.active) return { hashes, active: tabs.active - 1 };
  if (index > tabs.active) return { hashes, active: tabs.active };
  return { hashes, active: Math.min(index, hashes.length - 1) };
}

/** Tabs read back from storage, or just Home when what's there isn't tabs. */
export function tabsFromText(text: string | null): Tabs {
  try {
    const kept: unknown = JSON.parse(text ?? "");
    if (!Array.isArray(kept)) return startingTabs();
    const hashes = kept.filter((each): each is string => typeof each === "string" && each.startsWith("#/"));
    const rest = hashes.filter((each) => each !== HOME).slice(0, MOST_TABS - 1);
    return { hashes: [HOME, ...new Set(rest)], active: 0 };
  } catch {
    return startingTabs();
  }
}
