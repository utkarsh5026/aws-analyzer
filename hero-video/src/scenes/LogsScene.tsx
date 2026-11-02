import type { FC } from "react";
import { AbsoluteFill, interpolate, useCurrentFrame } from "remotion";
import { Burst, Flash, Pointer, progress, Rise, Scene, Typed, windowBox } from "../motion";
import { colors, fonts } from "../theme";

const CLICK_AT = 46;
const EXPANDED_PIXELS = 268;
const BAR_HEIGHTS = [52, 78, 52, 78, 52, 52, 78, 52, 78, 52, 78, 52, 52, 78, 52, 52, 78, 52, 78, 52, 52, 78, 52, 52, 78, 52, 78];
const FAILED_BARS = new Set([1, 4, 7, 12, 15, 18, 20, 23, 26]);

const Bars: FC = () => {
  const frame = useCurrentFrame();
  return (
    <div style={{ display: "flex", alignItems: "flex-end", gap: 4, height: 104, padding: "0 28px" }}>
      {BAR_HEIGHTS.map((height, i) => (
        <div
          key={i}
          style={{
            flex: 1,
            height: height * 1.3 * progress(frame, 10 + i * 0.5, 20 + i * 0.5),
            background: FAILED_BARS.has(i) ? "#d94a42" : "#3d6090",
          }}
        />
      ))}
    </div>
  );
};

const Run: FC<{ time: string; took: string; text: string; failed?: boolean }> = ({ time, took, text, failed = false }) => (
  <div style={{ display: "flex", alignItems: "center", gap: 20, padding: "15px 28px", borderTop: `1px solid ${colors.terminalLine}`, fontSize: 27, whiteSpace: "nowrap" }}>
    <span style={{ color: failed ? colors.red : colors.ok, width: 26 }}>{failed ? "✕" : "✓"}</span>
    <span style={{ color: colors.ink }}>{time}</span>
    <span style={{ color: colors.ink, width: 100 }}>{took}</span>
    <span style={{ color: failed ? colors.red : colors.muted, overflow: "hidden", textOverflow: "ellipsis" }}>{text}</span>
  </div>
);

const Traceback: FC = () => {
  const frame = useCurrentFrame();
  const opened = progress(frame, CLICK_AT + 1, CLICK_AT + 12);
  return (
    <div style={{ height: opened * EXPANDED_PIXELS, overflow: "hidden" }}>
      <div style={{ padding: "16px 28px", background: colors.redWash, color: colors.red, fontSize: 25, lineHeight: 1.5, whiteSpace: "nowrap" }}>
        <div style={{ fontWeight: 600 }}>KeyError: 'customer_id'</div>
        <div>Traceback (most recent call last):</div>
        <div>&nbsp;&nbsp;File "/var/task/etl.py", line 22, in handler</div>
        <div>&nbsp;&nbsp;&nbsp;&nbsp;table.put_item(Item=&#123;"pk": row["customer_id"],...</div>
      </div>
      <div style={{ padding: "16px 28px", color: colors.blue, fontSize: 25 }}>Open etl.py, line 22, in the Code tab ›</div>
    </div>
  );
};

const Terminal: FC = () => {
  const frame = useCurrentFrame();
  const heat = interpolate(frame, [CLICK_AT, CLICK_AT + 8], [0, 0.65], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  return (
    <div style={{ ...windowBox(colors.terminal, colors.terminalLine, `rgba(255,107,99,${heat})`), fontFamily: fonts.mono }}>
      <div style={{ padding: "16px 28px", fontSize: 28, color: colors.blue, borderBottom: `1px solid ${colors.terminalLine}`, background: "#14161e" }}>
        <Typed text="LambdaView().explore('orders-etl')" at={2} framesPerChar={0.4} />
      </div>
      <Rise at={10} style={{ display: "flex", alignItems: "baseline", gap: 18, padding: "18px 28px 14px" }}>
        <span style={{ color: colors.red, fontSize: 22 }}>●</span>
        <span style={{ color: colors.ink, fontSize: 32, fontWeight: 600 }}>orders-etl</span>
        <span style={{ color: colors.raw, fontSize: 25 }}>python3.9</span>
        <span style={{ marginLeft: "auto", color: colors.blue, fontSize: 25, fontWeight: 600 }}>Logs · last 24 hours</span>
      </Rise>
      <Bars />
      <div style={{ height: 16 }} />
      <Rise at={20}>
        <Run time="12:42:32" took="5.70 s" text="reading s3://acme-uploads/orders/..." />
      </Rise>
      <Rise at={23}>
        <Run time="12:21:11" took="6.64 s" text="reading s3://acme-uploads/orders/..." />
      </Rise>
      <Rise at={26}>
        <Run time="11:59:26" took="1.73 s" text="KeyError: 'customer_id'" failed />
      </Rise>
      <Traceback />
    </div>
  );
};

export const LogsScene: FC = () => (
  <AbsoluteFill>
    <Scene lead="Find what" accent="broke." hits={[CLICK_AT]}>
      <Terminal />
      <Pointer from={[1040, 680]} to={[640, 442]} at={28} arrive={CLICK_AT} />
    </Scene>
    <Flash at={CLICK_AT} color={colors.red} peak={0.5} />
    <Burst at={CLICK_AT} color={colors.red} x={690} y={600} count={26} reach={420} />
  </AbsoluteFill>
);
