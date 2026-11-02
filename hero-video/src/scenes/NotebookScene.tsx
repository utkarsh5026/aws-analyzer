import type { FC, ReactNode } from "react";
import { AbsoluteFill, useCurrentFrame } from "remotion";
import { Burst, Flash, gradientText, Punch, progress, Rise, Ring, Scene, Shake, Slam, Sweep, Typed, windowBox } from "../motion";
import { colors, fonts } from "../theme";

const CARDS: { label: string; value: string; isWarning: boolean }[] = [
  { label: "Instance", value: "ml.g5.2xlarge", isWarning: false },
  { label: "Price", value: "$1.52/hour", isWarning: false },
  { label: "Idle shutdown", value: "off", isWarning: true },
  { label: "GPU", value: "idle", isWarning: true },
];
const MONTHLY_SAVING = 773.07;
const FOCUS_AT = 40;
const SLAM_AT = 72;
const LANDED_AT = 92;

const Saving: FC<{ from: number; to: number }> = ({ from, to }) => {
  const frame = useCurrentFrame();
  return <span>${(MONTHLY_SAVING * progress(frame, from, to)).toFixed(2)}</span>;
};

const Dimmed: FC<{ children: ReactNode }> = ({ children }) => {
  const frame = useCurrentFrame();
  return <div style={{ opacity: 1 - 0.6 * progress(frame, FOCUS_AT, FOCUS_AT + 14) }}>{children}</div>;
};

const Card: FC<{ label: string; value: string; isWarning: boolean }> = ({ label, value, isWarning }) => (
  <div
    style={{
      flex: 1,
      padding: "12px 16px",
      borderRadius: 12,
      border: `1px solid ${isWarning ? colors.amber : colors.line}`,
      background: isWarning ? "rgba(245,196,81,.16)" : colors.paper,
    }}
  >
    <div style={{ color: colors.muted, fontSize: 24 }}>{label}</div>
    <div style={{ color: colors.ink, fontSize: 31, fontWeight: 700, whiteSpace: "nowrap" }}>{value}</div>
  </div>
);

const Curtain: FC = () => {
  const frame = useCurrentFrame();
  return <AbsoluteFill style={{ background: "rgba(8,11,24,.9)", opacity: progress(frame, SLAM_AT, SLAM_AT + 8) }} />;
};

const BigMoney: FC = () => (
  <AbsoluteFill style={{ alignItems: "center", justifyContent: "center", fontFamily: fonts.display, textAlign: "center" }}>
    <Punch hits={[LANDED_AT]} amount={0.14}>
      <AbsoluteFill style={{ alignItems: "center", justifyContent: "center" }}>
        <Slam at={SLAM_AT} from={0.5}>
          <div style={{ fontSize: 62, fontWeight: 700, color: colors.ink }}>up to</div>
          <div style={{ fontSize: 212, lineHeight: 1.05, fontWeight: 800, letterSpacing: "-.04em", color: colors.amber, textShadow: "0 0 80px rgba(245,196,81,.55)" }}>
            <Saving from={SLAM_AT} to={LANDED_AT} />
          </div>
          <div style={{ fontSize: 76, fontWeight: 700, color: colors.ink }}>a month less</div>
        </Slam>
        <Rise at={LANDED_AT + 6} style={{ marginTop: 34, fontSize: 44, fontWeight: 700 }}>
          <span style={gradientText}>One idle GPU. One line of Python.</span>
        </Rise>
      </AbsoluteFill>
    </Punch>
  </AbsoluteFill>
);

export const NotebookScene: FC = () => (
  <AbsoluteFill>
    <Scene lead="Spot the" accent="waste.">
      <div style={windowBox(colors.surface, colors.line)}>
        <div style={{ display: "flex", alignItems: "center", padding: "14px 24px", background: colors.paper2, borderBottom: `1px solid ${colors.line}`, fontSize: 22, color: colors.muted }}>
          <span style={{ width: 14, height: 14, borderRadius: 3, background: colors.orange, marginRight: 12 }} />
          <span style={{ color: colors.ink, fontWeight: 600 }}>churn-analysis.ipynb</span>
          <span style={{ marginLeft: "auto" }}>Python 3</span>
        </div>
        <div style={{ display: "flex", gap: 22, padding: "18px 28px", background: colors.paper, borderBottom: `1px solid ${colors.line}`, fontFamily: fonts.mono, fontSize: 32 }}>
          <span style={{ color: colors.blue }}>[1]:</span>
          <span style={{ color: colors.ink }}>
            <Typed text="ui.instance()" at={2} framesPerChar={0.7} />
          </span>
        </div>
        <div style={{ padding: "24px 32px", flex: 1 }}>
          <Dimmed>
            <Rise at={12}>
              <div style={{ fontSize: 34, fontWeight: 700, color: colors.ink }}>JupyterLab space churn-analysis</div>
              <div style={{ fontSize: 23, color: colors.muted, margin: "2px 0 18px" }}>us-east-1 · running for 2d 5h</div>
            </Rise>
            <div style={{ display: "flex", gap: 12, marginBottom: 30 }}>
              {CARDS.map((card, i) => (
                <Rise key={card.label} at={16 + i * 2} style={{ flex: 1, display: "flex" }}>
                  <Card {...card} />
                </Rise>
              ))}
            </div>
          </Dimmed>
          <Rise at={26} style={{ borderLeft: `6px solid ${colors.amber}`, paddingLeft: 24 }}>
            <div style={{ fontSize: 35, fontWeight: 700, color: colors.ink, lineHeight: 1.22 }}>
              This ml.g5.2xlarge costs $1.52/hour, and its GPU is idle right now.
            </div>
            <div style={{ fontSize: 31, color: colors.muted, lineHeight: 1.36, marginTop: 14 }}>
              If this notebook doesn't use the GPU, ml.m5.2xlarge has as many vCPUs and as much memory without one:
            </div>
            <Rise at={FOCUS_AT} style={{ fontSize: 58, marginTop: 18, fontWeight: 700 }}>
              <Sweep at={FOCUS_AT} frames={18}>
                up to $773.07/month less
              </Sweep>
            </Rise>
          </Rise>
        </div>
      </div>
    </Scene>
    <Curtain />
    <Shake hits={[LANDED_AT]} strength={26}>
      <BigMoney />
    </Shake>
    <Flash at={LANDED_AT} color={colors.amber} peak={0.6} />
    <Ring at={LANDED_AT} color={colors.amber} x={640} y={450} />
    <Burst at={LANDED_AT} color={colors.amber} x={640} y={450} count={60} reach={760} />
  </AbsoluteFill>
);
