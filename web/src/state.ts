/**
 * What the whole page shares: the catalog once it's loaded, where the page is (the hash), the tabs, the theme,
 * whether the finder is open, the reports already run (by the hash that shows them, so back and forward don't run
 * them again) and the services' resources already read (so searching a service again, or Ctrl K, asks nothing).
 * The tabs, the theme and the service last searched are kept in the browser, so they come back on reload; what each
 * service's check found is kept here and by the server, which hands it back after a reload.
 */

import { signal } from "@preact/signals";
import type { Attention, Catalog, Report, ResourceIndex } from "./api/types";
import { parseRoute } from "./lib/route";
import { afterNavigation, closeTab, normalHash, type Tabs, tabsFromText } from "./lib/tabs";
import { resolveTheme, type ThemeChoice, themeChoiceFrom } from "./lib/theme";

const TABS_KEY = "aa-tabs";
const THEME_KEY = "aa-theme";
const SEARCH_KEY = "aa-search-service";

function kept(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function keep(key: string, value: string) {
  try {
    localStorage.setItem(key, value);
  } catch {
    return;
  }
}

export const catalog = signal<Catalog | null>(null);
export const loadError = signal("");
export const hash = signal(normalHash(location.hash));
export const finderOpen = signal(false);
export const tabs = signal<Tabs>(afterNavigation(tabsFromText(kept(TABS_KEY)), hash.value));
export const themeChoice = signal<ThemeChoice>(themeChoiceFrom(kept(THEME_KEY)));
export const searchService = signal(kept(SEARCH_KEY) ?? "lambda_functions");

/** The services' resources already read, by service key. */
export const indexes = signal<Map<string, ResourceIndex>>(new Map());

/** What each service's check found, by service key. */
export const attentions = signal<Map<string, Attention>>(new Map());

/** The route the hash names. */
export function currentRoute() {
  return parseRoute(hash.value);
}

/** Reports by the hash that shows them. */
export const reports = new Map<string, Report>();

/**
 * A call run from a report's next step starts its job, then moves to its command's address: `handedOver` names the
 * job that address's page picks up, so it shows that job rather than starting another.
 */
export const runs = { handedOver: { target: "", id: "" } };

let nextIsNewTab = false;

function setTabs(next: Tabs) {
  tabs.value = next;
  keep(TABS_KEY, JSON.stringify(next.hashes));
}

/** Opens an address in a tab of its own. */
export function openInNewTab(target: string) {
  if (normalHash(target) === hash.value) return;
  nextIsNewTab = true;
  location.hash = target;
}

/** Closes the tab at `index`, showing the one beside it. */
export function closeTabAt(index: number) {
  const next = closeTab(tabs.value, index);
  setTabs(next);
  const shown = next.hashes[next.active] ?? "#/";
  if (shown !== hash.value) location.hash = shown;
}

/** Keeps the theme chosen and puts it on the page, where the reports' stylesheets read it too. */
export function chooseTheme(choice: ThemeChoice) {
  themeChoice.value = choice;
  keep(THEME_KEY, choice);
  applyTheme();
}

/** Remembers the service the search box is on. */
export function chooseSearchService(key: string) {
  searchService.value = key;
  keep(SEARCH_KEY, key);
}

/** Keeps a service's resources once read. */
export function keepIndex(index: ResourceIndex) {
  const next = new Map(indexes.value);
  next.set(index.service, index);
  indexes.value = next;
}

/** Keeps what a service's check found; one already kept is replaced only by a newer check. */
export function keepAttention(attention: Attention) {
  const earlier = attentions.value.get(attention.service);
  if (earlier && Date.parse(earlier.checked_at) > Date.parse(attention.checked_at)) return;
  const next = new Map(attentions.value);
  next.set(attention.service, attention);
  attentions.value = next;
}

const systemDark = matchMedia("(prefers-color-scheme: dark)");

/** Puts the theme on <html> (the page's own colours) and on <body> the way JupyterLab does (the reports'). */
export function applyTheme() {
  const theme = resolveTheme(themeChoice.value, systemDark.matches);
  document.documentElement.dataset.theme = theme;
  document.body.dataset.jpThemeLight = theme === "dark" ? "false" : "true";
}

systemDark.addEventListener("change", applyTheme);

addEventListener("hashchange", () => {
  hash.value = normalHash(location.hash);
  setTabs(afterNavigation(tabs.value, hash.value, nextIsNewTab));
  nextIsNewTab = false;
});
