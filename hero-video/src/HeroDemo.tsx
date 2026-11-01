import type { FC } from "react";
import { AbsoluteFill, Series, useCurrentFrame } from "remotion";
import { AgentScene } from "./scenes/AgentScene";
import { ColdOpenScene } from "./scenes/ColdOpenScene";
import { LogsScene } from "./scenes/LogsScene";
import { MONTAGE_FRAMES, MontageScene } from "./scenes/MontageScene";
import { NotebookScene } from "./scenes/NotebookScene";
import { OutroScene } from "./scenes/OutroScene";
import { TranslateScene } from "./scenes/TranslateScene";
import { colors, fonts } from "./theme";

const COLD_OPEN_FRAMES = 66;
const TRANSLATE_FRAMES = 96;
const NOTEBOOK_FRAMES = 150;
const LOGS_FRAMES = 114;
const AGENT_FRAMES = 150;
const OUTRO_FRAMES = 110;

export const HERO_FRAMES =
  COLD_OPEN_FRAMES + TRANSLATE_FRAMES + NOTEBOOK_FRAMES + LOGS_FRAMES + AGENT_FRAMES + MONTAGE_FRAMES + OUTRO_FRAMES;

const GLOW_DRIFT_PIXELS = 120;
const GLOW_PERIOD_FRAMES = 240;
const DOT_DRIFT_PIXELS_PER_FRAME = 0.4;

const Stage: FC = () => {
  const frame = useCurrentFrame();
  const phase = (frame / GLOW_PERIOD_FRAMES) * Math.PI * 2;
  const blueX = 900 + Math.cos(phase) * GLOW_DRIFT_PIXELS;
  const violetY = 720 + Math.sin(phase) * GLOW_DRIFT_PIXELS;
  return (
    <AbsoluteFill
      style={{
        backgroundColor: colors.stage,
        backgroundImage: [
          `radial-gradient(760px 560px at ${blueX}px 170px, rgba(163,173,255,.2), transparent 70%)`,
          `radial-gradient(700px 520px at 160px ${violetY}px, rgba(192,176,255,.14), transparent 70%)`,
          "radial-gradient(rgba(230,233,244,.09) 1px, transparent 1.4px)",
        ].join(", "),
        backgroundSize: "auto, auto, 22px 22px",
        backgroundPosition: `0 0, 0 0, 0 ${-frame * DOT_DRIFT_PIXELS_PER_FRAME}px`,
      }}
    />
  );
};

export const HeroDemo: FC = () => (
  <AbsoluteFill style={{ fontFamily: fonts.sans, overflow: "hidden" }}>
    <Stage />
    <Series>
      <Series.Sequence durationInFrames={COLD_OPEN_FRAMES} name="AWS gives you this">
        <ColdOpenScene />
      </Series.Sequence>
      <Series.Sequence durationInFrames={TRANSLATE_FRAMES} name="You needed this">
        <TranslateScene />
      </Series.Sequence>
      <Series.Sequence durationInFrames={NOTEBOOK_FRAMES} name="Spot the waste">
        <NotebookScene />
      </Series.Sequence>
      <Series.Sequence durationInFrames={LOGS_FRAMES} name="Find what broke">
        <LogsScene />
      </Series.Sequence>
      <Series.Sequence durationInFrames={AGENT_FRAMES} name="Ask what to fix">
        <AgentScene />
      </Series.Sequence>
      <Series.Sequence durationInFrames={MONTAGE_FRAMES} name="Every service">
        <MontageScene />
      </Series.Sequence>
      <Series.Sequence durationInFrames={OUTRO_FRAMES} name="Install">
        <OutroScene />
      </Series.Sequence>
    </Series>
  </AbsoluteFill>
);
