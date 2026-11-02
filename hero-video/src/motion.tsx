import type { CSSProperties, FC, ReactNode } from "react";
import { AbsoluteFill, Easing, interpolate, random, spring, useCurrentFrame, useVideoConfig } from "remotion";
import { colors, fonts } from "./theme";

const CLAMP = { extrapolateLeft: "clamp", extrapolateRight: "clamp" } as const;
const SMOOTH = Easing.bezier(0.16, 1, 0.3, 1);
const RISE_FRAMES = 10;
const RISE_PIXELS = 22;
const CARET_BLINK_FRAMES = 8;
const SHAKE_FRAMES = 14;
const FLASH_FRAMES = 12;
const BURST_FRAMES = 26;
const RING_FRAMES = 22;
const PUNCH_FRAMES = 12;

export const progress = (frame: number, from: number, to: number): number =>
  interpolate(frame, [from, to], [0, 1], { ...CLAMP, easing: SMOOTH });

export const Rise: FC<{ at: number; children: ReactNode; style?: CSSProperties }> = ({ at, children, style }) => {
  const frame = useCurrentFrame();
  const shown = progress(frame, at, at + RISE_FRAMES);
  return <div style={{ ...style, opacity: shown, translate: `0 ${(1 - shown) * RISE_PIXELS}px` }}>{children}</div>;
};

export const Slam: FC<{ at: number; from?: number; children: ReactNode; style?: CSSProperties }> = ({
  at,
  from = 2.4,
  children,
  style,
}) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const landed = spring({ frame: frame - at, fps, config: { damping: 13, stiffness: 240, mass: 0.7 } });
  const blurred = Math.max(0, 1 - landed) * 16;
  return (
    <div
      style={{
        ...style,
        opacity: frame >= at ? interpolate(frame, [at, at + 3], [0, 1], CLAMP) : 0,
        scale: from + (1 - from) * landed,
        filter: `blur(${blurred}px)`,
      }}
    >
      {children}
    </div>
  );
};

export const Pop: FC<{ at: number; children: ReactNode; style?: CSSProperties }> = ({ at, children, style }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const grown = spring({ frame: frame - at, fps, config: { damping: 10, stiffness: 220, mass: 0.6 } });
  return <div style={{ ...style, scale: grown, opacity: frame >= at ? 1 : 0 }}>{children}</div>;
};

export const Typed: FC<{ text: string; at: number; framesPerChar?: number }> = ({ text, at, framesPerChar = 1 }) => {
  const frame = useCurrentFrame();
  const count = Math.max(0, Math.floor((frame - at) / framesPerChar));
  const isTyping = count < text.length;
  const isCaretOn = Math.floor(frame / CARET_BLINK_FRAMES) % 2 === 0;
  return (
    <span>
      {text.slice(0, count)}
      {isTyping && isCaretOn ? <span style={{ color: colors.blue }}>▌</span> : null}
    </span>
  );
};

export const Sweep: FC<{ at: number; frames: number; children: ReactNode }> = ({ at, frames, children }) => {
  const frame = useCurrentFrame();
  const swept = progress(frame, at, at + frames) * 100;
  return (
    <span
      style={{
        color: colors.markFg,
        padding: "2px 8px",
        borderRadius: 6,
        backgroundImage: "linear-gradient(rgba(245,196,81,.36), rgba(245,196,81,.36))",
        backgroundRepeat: "no-repeat",
        backgroundSize: `${swept}% 100%`,
      }}
    >
      {children}
    </span>
  );
};

export const Shake: FC<{ hits: number[]; strength?: number; children: ReactNode }> = ({ hits, strength = 22, children }) => {
  const frame = useCurrentFrame();
  const decays = hits.map((at) => (frame >= at ? Math.max(0, 1 - (frame - at) / SHAKE_FRAMES) : 0));
  const power = Math.max(0, ...decays);
  const across = (random(`across${frame}`) - 0.5) * 2 * strength * power;
  const down = (random(`down${frame}`) - 0.5) * 2 * strength * power;
  return <AbsoluteFill style={{ translate: `${across}px ${down}px` }}>{children}</AbsoluteFill>;
};

export const Punch: FC<{ hits: number[]; amount?: number; children: ReactNode }> = ({ hits, amount = 0.06, children }) => {
  const frame = useCurrentFrame();
  const decays = hits.map((at) => (frame >= at ? Math.max(0, 1 - (frame - at) / PUNCH_FRAMES) : 0));
  return <AbsoluteFill style={{ scale: 1 + amount * Math.max(0, ...decays) ** 2 }}>{children}</AbsoluteFill>;
};

export const Flash: FC<{ at: number; color?: string; peak?: number }> = ({ at, color = colors.blue, peak = 0.7 }) => {
  const frame = useCurrentFrame();
  const opacity = interpolate(frame, [at, at + FLASH_FRAMES], [peak, 0], CLAMP);
  if (frame < at || opacity <= 0) {
    return null;
  }
  return (
    <AbsoluteFill
      style={{ background: `radial-gradient(circle at 50% 50%, ${color}, transparent 75%)`, opacity, mixBlendMode: "screen", zIndex: 20 }}
    />
  );
};

export const Burst: FC<{ at: number; color: string; x: number; y: number; count?: number; reach?: number }> = ({
  at,
  color,
  x,
  y,
  count = 44,
  reach = 620,
}) => {
  const frame = useCurrentFrame();
  const age = frame - at;
  if (age < 0 || age > BURST_FRAMES) {
    return null;
  }
  const travelled = progress(frame, at, at + BURST_FRAMES);
  const fading = 1 - age / BURST_FRAMES;
  const sparks = Array.from({ length: count }, (_, i) => i);
  return (
    <svg width="1280" height="960" style={{ position: "absolute", inset: 0, zIndex: 19 }}>
      {sparks.map((i) => {
        const angle = random(`angle${at}${i}`) * Math.PI * 2;
        const distance = (0.25 + random(`far${at}${i}`) * 0.75) * reach * travelled;
        const size = 3 + random(`size${at}${i}`) * 7;
        return (
          <circle
            key={i}
            cx={x + Math.cos(angle) * distance}
            cy={y + Math.sin(angle) * distance}
            r={size * fading}
            fill={color}
            opacity={fading}
          />
        );
      })}
    </svg>
  );
};

export const Ring: FC<{ at: number; color: string; x: number; y: number }> = ({ at, color, x, y }) => {
  const frame = useCurrentFrame();
  const age = frame - at;
  if (age < 0 || age > RING_FRAMES) {
    return null;
  }
  const radius = progress(frame, at, at + RING_FRAMES) * 760;
  return (
    <svg width="1280" height="960" style={{ position: "absolute", inset: 0, zIndex: 18 }}>
      <circle cx={x} cy={y} r={radius} fill="none" stroke={color} strokeWidth={14 * (1 - age / RING_FRAMES)} opacity={1 - age / RING_FRAMES} />
    </svg>
  );
};

export const Pointer: FC<{ from: [number, number]; to: [number, number]; at: number; arrive: number }> = ({
  from,
  to,
  at,
  arrive,
}) => {
  const frame = useCurrentFrame();
  const travelled = progress(frame, at, arrive);
  const pressed = interpolate(frame, [arrive, arrive + 3, arrive + 7], [1, 0.8, 1], CLAMP);
  const left = from[0] + (to[0] - from[0]) * travelled;
  const top = from[1] + (to[1] - from[1]) * travelled;
  return (
    <svg
      width="44"
      height="52"
      viewBox="0 0 22 26"
      style={{ position: "absolute", left, top, scale: pressed, opacity: frame >= at ? 1 : 0, zIndex: 5 }}
    >
      <path d="M2 2 L2 20 L7 15.5 L10.5 23 L14 21.5 L10.5 14 L17 14 Z" fill="#fff" stroke="#10152a" strokeWidth="1.6" />
    </svg>
  );
};

export const gradientText: CSSProperties = {
  backgroundImage: `linear-gradient(95deg, ${colors.blue}, ${colors.violet})`,
  WebkitBackgroundClip: "text",
  backgroundClip: "text",
  color: "transparent",
};

export const Scene: FC<{ lead: string; accent: string; hits?: number[]; children: ReactNode }> = ({
  lead,
  accent,
  hits = [],
  children,
}) => (
  <AbsoluteFill>
    <Punch hits={[0, ...hits]}>
      <Shake hits={hits}>
        <div style={{ position: "absolute", inset: 0, display: "flex", flexDirection: "column", padding: "36px 48px 48px" }}>
          <div style={{ display: "flex", gap: 22, fontFamily: fonts.display, fontSize: 84, fontWeight: 800, letterSpacing: "-.035em", marginBottom: 22, transformOrigin: "left" }}>
            <Slam at={0} from={1.8} style={{ color: colors.ink, transformOrigin: "left center" }}>
              {lead}
            </Slam>
            <Slam at={7} from={2.2} style={{ ...gradientText, transformOrigin: "left center" }}>
              {accent}
            </Slam>
          </div>
          <div style={{ flex: 1, position: "relative", minHeight: 0 }}>{children}</div>
        </div>
      </Shake>
    </Punch>
    <Flash at={0} peak={0.45} />
  </AbsoluteFill>
);

export const windowBox = (background: string, border: string, glow = "rgba(0,0,0,.7)"): CSSProperties => ({
  position: "absolute",
  inset: 0,
  background,
  border: `1px solid ${border}`,
  borderRadius: 22,
  overflow: "hidden",
  boxShadow: `0 30px 80px -10px ${glow}`,
  display: "flex",
  flexDirection: "column",
});
