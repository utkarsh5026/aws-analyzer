import { describe, expect, it } from "vitest";
import { agoText, lastCalledText, minuteBars, watchCost } from "./live";

describe("live", () => {
  it("draws each minute against the busiest", () => {
    expect(minuteBars([0, 2, 4])).toEqual([0, 0.5, 1]);
    expect(minuteBars([0, 0])).toEqual([0, 0]);
  });

  it("says how long ago in words", () => {
    expect(agoText(2)).toBe("just now");
    expect(agoText(45)).toBe("45 s ago");
    expect(agoText(185)).toBe("3 min ago");
    expect(lastCalledText(0)).toBe("called this minute");
    expect(lastCalledText(4)).toBe("last called 4 min ago");
    expect(lastCalledText(null)).toBe("");
  });

  it("prices watching at CloudWatch's rate", () => {
    expect(watchCost(30, 60)).toBe("about $0.02 an hour");
    expect(watchCost(3, 60)).toBe("under $0.01 an hour");
  });
});
