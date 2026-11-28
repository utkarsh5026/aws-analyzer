import { describe, expect, it } from "vitest";
import type { Attention, Concern } from "../api/types";
import { concernsAbout, countLevels, countsText, worstLevel } from "./attention";

function concern(level: Concern["level"], ...names: string[]): Concern {
  return {
    level,
    headline: `${level} thing`,
    detail: "",
    targets: names.map((name) => ({ name, command: "function_info", arguments: { name } })),
  };
}

function attention(concerns: Concern[]): Attention {
  return {
    service: "lambda_functions",
    call: "functions()",
    command: "functions",
    verdict: "",
    tone: "",
    cards: [],
    concerns,
    note: "",
    checked_at: "2026-11-28T10:00:00Z",
    seconds: 1,
    from_memory: false,
  };
}

describe("counting what a check found", () => {
  it("counts each level and says it in words", () => {
    const counts = countLevels([concern("warn"), concern("warn"), concern("info")]);
    expect(counts).toEqual({ bad: 0, warn: 2, info: 1 });
    expect(countsText(counts)).toBe("2 warnings · 1 note");
  });

  it("says when nothing needs attention", () => {
    expect(countsText(countLevels([]))).toBe("nothing needs attention");
    expect(worstLevel(attention([]))).toBe("");
  });

  it("finds the worst level", () => {
    expect(worstLevel(attention([concern("info"), concern("bad")]))).toBe("bad");
  });
});

describe("concerns about one resource", () => {
  it("keeps the ones that name it", () => {
    const found = concernsAbout(
      attention([concern("warn", "orders-etl", "report-api"), concern("info", "other")]),
      "orders-etl",
    );
    expect(found).toHaveLength(1);
    expect(found[0]?.level).toBe("warn");
  });

  it("is empty when the service wasn't checked", () => {
    expect(concernsAbout(undefined, "orders-etl")).toEqual([]);
  });
});
