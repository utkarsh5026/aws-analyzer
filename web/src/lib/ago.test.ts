import { describe, expect, it } from "vitest";
import { readAgo } from "./ago";

describe("how long ago", () => {
  const now = Date.parse("2026-11-28T10:00:00Z");

  it("says just now under a minute, and for a time it can't read", () => {
    expect(readAgo("2026-11-28T09:59:30Z", now)).toBe("just now");
    expect(readAgo("not a time", now)).toBe("just now");
  });

  it("counts minutes, then hours", () => {
    expect(readAgo("2026-11-28T09:56:00Z", now)).toBe("4 min ago");
    expect(readAgo("2026-11-28T08:00:00Z", now)).toBe("2 h ago");
  });
});
