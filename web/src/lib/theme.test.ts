import { describe, expect, it } from "vitest";
import { resolveTheme, themeChoiceFrom } from "./theme";

describe("theme", () => {
  it("follows the system on auto, else the choice", () => {
    expect(resolveTheme("auto", true)).toBe("dark");
    expect(resolveTheme("auto", false)).toBe("light");
    expect(resolveTheme("light", true)).toBe("light");
    expect(resolveTheme("dark", false)).toBe("dark");
  });

  it("reads a kept choice back, anything else as auto", () => {
    expect(themeChoiceFrom("dark")).toBe("dark");
    expect(themeChoiceFrom(null)).toBe("auto");
    expect(themeChoiceFrom("purple")).toBe("auto");
  });
});
