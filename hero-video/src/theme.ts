import { loadFont as loadDisplay } from "@remotion/google-fonts/BricolageGrotesque";
import { loadFont as loadMono } from "@remotion/google-fonts/IBMPlexMono";
import { loadFont as loadSans } from "@remotion/google-fonts/IBMPlexSans";

const latin = { subsets: ["latin"] as ("latin")[] };

export const fonts = {
  display: loadDisplay("normal", { weights: ["700", "800"], ...latin }).fontFamily,
  sans: loadSans("normal", { weights: ["400", "600", "700"], ...latin }).fontFamily,
  mono: loadMono("normal", { weights: ["400", "600"], ...latin }).fontFamily,
};

export const colors = {
  stage: "#0d0e10",
  surface: "#131417",
  paper: "#18191c",
  paper2: "#1f2024",
  line: "#2a2b30",
  line2: "#3a3c42",
  ink: "#eeeeec",
  muted: "#a9abb1",
  raw: "#84868d",
  accent: "#ff9900",
  accent2: "#ffb84d",
  amber: "#f5c451",
  markFg: "#fbe6ad",
  orange: "#f57c00",
  terminal: "#16171a",
  terminalLine: "#2e3035",
  red: "#ff6b63",
  redWash: "#3a2022",
  ok: "#7cc28a",
};

export const FPS = 30;
export const WIDTH = 1280;
export const HEIGHT = 960;
