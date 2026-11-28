/**
 * The bar along the top: the name, the tabs, the search (Ctrl K), the theme switch and what the server reads.
 */

import { VERSION } from "../api/client";
import type { Catalog } from "../api/types";
import { parseRoute, type Route } from "../lib/route";
import { THEME_CHOICES, type ThemeChoice } from "../lib/theme";
import { catalog, chooseTheme, closeTabAt, finderOpen, tabs, themeChoice } from "../state";

const NAMING_ARGUMENTS = ["name", "bucket", "table", "uri", "kb"];
const MAIN_PAGES = new Set([
  "function_info",
  "bucket_info",
  "table_info",
  "state_machine",
  "api_info",
  "queue_info",
  "topic_info",
  "rule_info",
  "ls",
]);
const THEME_WORDS: Record<ThemeChoice, string> = { light: "Light", dark: "Dark", auto: "Auto" };

interface TabFace {
  label: string;
  about: string;
  icon: string | null;
}

function shortName(value: string): string {
  const parts = value.replace(/\/$/, "").split("/");
  return value.startsWith("s3://") && parts.length > 3 ? `…/${parts.at(-1)}/` : value;
}

/** 'orders-etl' on a function's main page, 'orders-etl · errors' on another of its commands, else the command. */
function tabLabel(route: Route, serviceTitle: string): string {
  const naming = NAMING_ARGUMENTS.map((name) => route.args[name]).find(Boolean);
  if (!naming) return route.command || serviceTitle;
  const name = shortName(naming);
  return MAIN_PAGES.has(route.command) ? name : `${name} · ${route.command}`;
}

/** What a tab shows for its address: what it's about (a function's name, a bucket...) or the page's name. */
function tabFace(target: string, loaded: Catalog | null): TabFace {
  const route = parseRoute(target);
  const service = loaded?.services.find((each) => each.key === route.service);
  if (!service) return { label: route.service ? route.service : "Home", about: "", icon: null };
  const label = tabLabel(route, service.title);
  const about = route.command ? `${service.title} · ${route.command}()` : service.title;
  return { label, about, icon: service.icon };
}

function HomeMark() {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 14 14"
      fill="none"
      stroke="currentColor"
      stroke-width="1.5"
      aria-hidden="true"
    >
      <path d="M2 6.5 7 2.5l5 4V12H2Z" />
    </svg>
  );
}

function TabStrip() {
  const { hashes, active } = tabs.value;
  const loaded = catalog.value;
  return (
    <div class="aa-tabs" role="tablist" aria-label="Open tabs">
      {hashes.map((target, index) => {
        const face = tabFace(target, loaded);
        const isOn = index === active;
        return (
          <div key={target} class={`aa-tab${isOn ? " aa-on" : ""}`} title={face.about || undefined}>
            <a role="tab" aria-selected={isOn} href={target} class="aa-tab-link">
              {face.icon ? <img src={`static/icons/${face.icon}.svg`} alt="" width={16} height={16} /> : <HomeMark />}
              <span>{index === 0 ? "Home" : face.label}</span>
            </a>
            {index === 0 ? null : (
              <button
                type="button"
                class="aa-tab-close"
                aria-label={`Close ${face.label}`}
                onClick={() => closeTabAt(index)}
              >
                ×
              </button>
            )}
          </div>
        );
      })}
    </div>
  );
}

function ThemeSwitch() {
  return (
    <fieldset class="aa-seg" aria-label="Theme">
      {THEME_CHOICES.map((choice) => (
        <button
          key={choice}
          type="button"
          class={themeChoice.value === choice ? "aa-on" : ""}
          aria-pressed={themeChoice.value === choice}
          onClick={() => chooseTheme(choice)}
        >
          {THEME_WORDS[choice]}
        </button>
      ))}
    </fieldset>
  );
}

function whereText(): string {
  if (!catalog.value) return "";
  const { region, profile } = catalog.value;
  return `Region: ${region || "boto3's default"}${profile ? ` · profile ${profile}` : ""} · read-only`;
}

export function Bar() {
  const openFinder = () => {
    if (catalog.value) finderOpen.value = true;
  };
  return (
    <header class="aa-bar">
      <a class="aa-brand" href="#/" title={`aws-analyzer ${VERSION}`}>
        <span class="aa-mark" aria-hidden="true" />
        aws-analyzer <span class="aa-ver">{VERSION}</span>
      </a>
      <TabStrip />
      <button class="aa-find" type="button" id="find" aria-keyshortcuts="Control+K Meta+K" onClick={openFinder}>
        <span>Search</span>
        <kbd>Ctrl K</kbd>
      </button>
      <ThemeSwitch />
      <span class="aa-where" id="where">
        {whereText()}
      </span>
    </header>
  );
}
