import { describe, expect, it } from "vitest";
import { afterNavigation, closeTab, HOME, MOST_TABS, startingTabs, tabsFromText } from "./tabs";

const FUNCTION = "#/lambda_functions/function_info?name=orders-etl&run=1";
const ERRORS = "#/lambda_functions/errors?name=orders-etl";
const BUCKET = "#/s3/bucket_info?bucket=acme&run=1";

describe("afterNavigation", () => {
  it("opens a new tab when leaving Home", () => {
    expect(afterNavigation(startingTabs(), FUNCTION)).toEqual({ hashes: [HOME, FUNCTION], active: 1 });
  });

  it("moves inside the active tab, not into a new one", () => {
    const opened = afterNavigation(startingTabs(), FUNCTION);
    expect(afterNavigation(opened, ERRORS)).toEqual({ hashes: [HOME, ERRORS], active: 1 });
  });

  it("adds a tab when asked to, and switches to a tab that shows the address already", () => {
    const two = afterNavigation(afterNavigation(startingTabs(), FUNCTION), BUCKET, true);
    expect(two).toEqual({ hashes: [HOME, FUNCTION, BUCKET], active: 2 });
    expect(afterNavigation(two, FUNCTION)).toEqual({ hashes: [HOME, FUNCTION, BUCKET], active: 1 });
    expect(afterNavigation(two, "")).toEqual({ hashes: [HOME, FUNCTION, BUCKET], active: 0 });
  });

  it("makes room by closing the oldest tab after Home", () => {
    let tabs = startingTabs();
    for (let index = 1; index <= MOST_TABS; index++) tabs = afterNavigation(tabs, `#/s3/ls?uri=${index}`, true);
    expect(tabs.hashes).toHaveLength(MOST_TABS);
    expect(tabs.hashes[0]).toBe(HOME);
    expect(tabs.hashes[1]).toBe("#/s3/ls?uri=2");
    expect(tabs.active).toBe(MOST_TABS - 1);
  });
});

describe("closeTab", () => {
  const three = { hashes: [HOME, FUNCTION, BUCKET], active: 1 };

  it("shows the next tab when the open one closes, else the one before", () => {
    expect(closeTab(three, 1)).toEqual({ hashes: [HOME, BUCKET], active: 1 });
    expect(closeTab({ ...three, active: 2 }, 2)).toEqual({ hashes: [HOME, FUNCTION], active: 1 });
  });

  it("keeps the open tab open when another closes, and never closes Home", () => {
    expect(closeTab({ ...three, active: 2 }, 1)).toEqual({ hashes: [HOME, BUCKET], active: 1 });
    expect(closeTab(three, 0)).toBe(three);
  });
});

describe("tabsFromText", () => {
  it("reads kept tabs back, Home first, without repeats or strangers", () => {
    const kept = JSON.stringify([FUNCTION, HOME, FUNCTION, 42, "javascript:alert(1)", BUCKET]);
    expect(tabsFromText(kept)).toEqual({ hashes: [HOME, FUNCTION, BUCKET], active: 0 });
  });

  it("starts with Home alone from nothing or from rubbish", () => {
    expect(tabsFromText(null)).toEqual(startingTabs());
    expect(tabsFromText("{not json")).toEqual(startingTabs());
    expect(tabsFromText('{"a": 1}')).toEqual(startingTabs());
  });
});
