import { describe, expect, it } from "vitest";
import type { Progress } from "../api/types";
import { duration, humanSize, progressShare, progressText } from "./progress";

const scanning = (count: number, total: number | null, seconds: number, unit = "objects"): Progress => ({
  label: "Scanning",
  unit,
  count,
  total,
  seconds,
});

describe("progress", () => {
  it("says how many of how many, the share, the rate and the time left", () => {
    expect(progressText(scanning(1200, 4000, 12))).toBe(
      "Scanning… 1,200 of 4,000 objects (30%) · 12 s · 100/s · about 28 s left",
    );
  });

  it("says only the count and the time while the total isn't known", () => {
    expect(progressText(scanning(42, null, 0.4))).toBe("Scanning… 42 objects · 0 s");
    expect(progressShare(scanning(42, null, 3))).toBeNull();
  });

  it("counts bytes in binary units", () => {
    expect(progressText(scanning(3 * 1024 * 1024, 6 * 1024 * 1024, 2, "B"))).toBe(
      "Scanning… 3.0 MB of 6.0 MB (50%) · 2 s · 1.5 MB/s · about 2 s left",
    );
  });

  it("never goes past the whole", () => {
    expect(progressShare(scanning(12, 10, 1))).toBe(1);
  });

  it("writes times and sizes the way the reports do", () => {
    expect(duration(75)).toBe("1 min 15 s");
    expect(duration(120)).toBe("2 min");
    expect(duration(4000)).toBe("1 h 6 min");
    expect(humanSize(1536)).toBe("1.5 KB");
    expect(humanSize(12)).toBe("12 B");
  });
});
