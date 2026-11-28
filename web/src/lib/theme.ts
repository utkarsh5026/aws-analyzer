/** Light, dark, or the system's: the choice the top bar's switch keeps, and what it comes to. */

export type ThemeChoice = "light" | "dark" | "auto";
export type Theme = "light" | "dark";

export const THEME_CHOICES: ThemeChoice[] = ["light", "dark", "auto"];

/** A kept choice read back: anything that isn't one is the system's. */
export function themeChoiceFrom(text: string | null): ThemeChoice {
  return THEME_CHOICES.find((choice) => choice === text) ?? "auto";
}

/** The theme a choice comes to, given whether the system is dark. */
export function resolveTheme(choice: ThemeChoice, systemIsDark: boolean): Theme {
  if (choice === "auto") return systemIsDark ? "dark" : "light";
  return choice;
}
