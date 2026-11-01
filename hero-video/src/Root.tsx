import type { FC } from "react";
import { Composition } from "remotion";
import { HERO_FRAMES, HeroDemo } from "./HeroDemo";
import { FPS, HEIGHT, WIDTH } from "./theme";

export const RemotionRoot: FC = () => (
  <Composition id="HeroDemo" component={HeroDemo} durationInFrames={HERO_FRAMES} fps={FPS} width={WIDTH} height={HEIGHT} />
);
