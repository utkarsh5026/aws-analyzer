import type { CSSProperties, FC, ReactNode } from "react";
import { useCurrentFrame } from "remotion";
import { Pop, progress, Rise, Scene, Sweep, Typed, windowBox } from "../motion";
import { colors, fonts } from "../theme";

const QUESTION = "Which Lambda failed in the last 24 hours, and why?";
const SENT_AT = 26;
const ANSWER_AT = 56;
const PULSE_FRAMES = 18;

const Code: FC<{ children: ReactNode }> = ({ children }) => (
  <span style={{ background: "#2a2c38", borderRadius: 6, padding: "0 8px", color: colors.ink }}>{children}</span>
);

const Tool: FC<{ at: number; call: string; service: string; tint: string }> = ({ at, call, service, tint }) => (
  <Rise at={at} style={{ display: "flex", alignItems: "center", gap: 14, fontSize: 27, color: colors.muted, marginBottom: 10, whiteSpace: "nowrap" }}>
    <span style={{ color: colors.ok }}>✓</span>
    <span>{call}</span>
    <Pop at={at + 3}>
      <span style={{ background: tint, color: "#10152a", fontWeight: 600, borderRadius: 6, padding: "0 10px", fontSize: 23 }}>{service}</span>
    </Pop>
  </Rise>
);

const Cause: FC<{ at: number; number: number; children: ReactNode }> = ({ at, number, children }) => (
  <Rise at={at} style={{ display: "flex", gap: 14, marginTop: 16, lineHeight: 1.3 }}>
    <span style={{ color: colors.muted }}>{number}.</span>
    <span>{children}</span>
  </Rise>
);

const Composer: FC = () => {
  const frame = useCurrentFrame();
  const composerStyle: CSSProperties = { padding: "18px 28px", borderTop: `1px solid ${colors.terminalLine}`, fontSize: 25, whiteSpace: "nowrap" };
  if (frame >= SENT_AT) {
    return <div style={{ ...composerStyle, color: colors.raw }}>› ask about your AWS account</div>;
  }
  return (
    <div style={{ ...composerStyle, color: colors.ink }}>
      <span style={{ color: colors.blue }}>› </span>
      <Typed text={QUESTION} at={1} framesPerChar={0.45} />
    </div>
  );
};

const Thinking: FC = () => {
  const frame = useCurrentFrame();
  const isThinking = frame >= SENT_AT && frame < ANSWER_AT;
  const fadedIn = progress(frame, SENT_AT, SENT_AT + 6);
  return isThinking ? <div style={{ color: colors.violet, fontSize: 27, opacity: fadedIn, marginBottom: 8 }}>✻ Reading AWS…</div> : null;
};

const Console: FC = () => {
  const frame = useCurrentFrame();
  const isThinking = frame >= SENT_AT && frame < ANSWER_AT;
  const pulse = isThinking ? 0.45 + 0.35 * Math.sin((frame / PULSE_FRAMES) * Math.PI * 2) : 0.3;
  return (
    <div style={{ ...windowBox(colors.terminal, colors.terminalLine, `rgba(163,173,255,${pulse})`), fontFamily: fonts.mono }}>
      <div style={{ display: "flex", alignItems: "center", gap: 16, padding: "14px 28px", borderBottom: `1px solid ${colors.terminalLine}`, fontSize: 25 }}>
        <span style={{ color: colors.ink, fontWeight: 600 }}>aws-agent</span>
        <span style={{ marginLeft: "auto", color: colors.blue }}>● read-only</span>
      </div>
      <div style={{ flex: 1, padding: "24px 28px", overflow: "hidden" }}>
        <Rise at={SENT_AT} style={{ display: "flex", justifyContent: "flex-end", marginBottom: 22 }}>
          <div style={{ border: `1px solid ${colors.terminalLine}`, borderRadius: 12, padding: "12px 20px", fontSize: 27, color: colors.ink, background: "#242632", maxWidth: 1000 }}>
            {QUESTION}
          </div>
        </Rise>
        <Tool at={SENT_AT + 6} call="function_health(since='24h')" service="lambda" tint="#ff9a3d" />
        <Tool at={SENT_AT + 16} call="function_errors('orders-etl', since='24h')" service="logs" tint="#f06ab0" />
        <Thinking />
        <Rise at={ANSWER_AT} style={{ borderLeft: `5px solid ${colors.blue}`, paddingLeft: 24, marginTop: 18, fontSize: 30, color: colors.ink }}>
          <div style={{ lineHeight: 1.3 }}>
            <b>orders-etl</b> failed 66 of its 2,226 calls (3.0%). Its logs show three causes:
          </div>
          <Cause at={ANSWER_AT + 8} number={1}>
            <Code>KeyError: 'customer_id'</Code> 8 times: <b>etl.py</b> line 22
          </Cause>
          <Cause at={ANSWER_AT + 14} number={2}>
            <b>Timeouts</b> 3 times: runs hit the 60 s limit
          </Cause>
          <Cause at={ANSWER_AT + 20} number={3}>
            <Code>AccessDeniedException</Code> 2 times: can't write to <Code>orders</Code>
          </Cause>
          <Rise at={ANSWER_AT + 30} style={{ marginTop: 20, lineHeight: 1.3 }}>
            Start with the KeyError:{" "}
            <Sweep at={ANSWER_AT + 38} frames={18}>
              errors('orders-etl', since='24h')
            </Sweep>
          </Rise>
        </Rise>
      </div>
      <Composer />
    </div>
  );
};

export const AgentScene: FC = () => (
  <Scene lead="Ask what" accent="to fix.">
    <Console />
  </Scene>
);
