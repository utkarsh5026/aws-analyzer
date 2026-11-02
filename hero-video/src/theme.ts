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
  stage: "#10152a",
  surface: "#141a31",
  paper: "#182039",
  paper2: "#1e2746",
  line: "#29314f",
  line2: "#36406a",
  ink: "#e6e9f4",
  muted: "#a2a9bf",
  raw: "#8088a3",
  blue: "#a3adff",
  violet: "#c0b0ff",
  amber: "#f5c451",
  markFg: "#fbe6ad",
  orange: "#f57c00",
  terminal: "#1b1d26",
  terminalLine: "#32343f",
  red: "#ff6b63",
  redWash: "#3a2022",
  ok: "#6ea8ff",
};

export const FPS = 30;
export const WIDTH = 1280;
export const HEIGHT = 960;
