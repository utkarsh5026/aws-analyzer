import type { FC } from "react";
import { AbsoluteFill, Img, spring, staticFile, useCurrentFrame, useVideoConfig } from "remotion";
import { Burst, Flash, gradientText, Pop, Ring, Rise, Shake, Slam, Typed } from "../motion";
import { colors, fonts } from "../theme";

const ICONS = ["s3", "dynamodb", "bedrock", "sagemaker", "opensearch", "lambda", "stepfunctions", "apigateway"];
const TITLE_AT = 16;
const PILLS = ["Open source", "Read-only", "boto3 is all it needs"];

const FlyingIcon: FC<{ name: string; index: number }> = ({ name, index }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const landed = spring({ frame: frame - index * 1.2, fps, config: { damping: 14, stiffness: 150 } });
  const angle = (index / ICONS.length) * Math.PI * 2;
  const across = Math.cos(angle) * 900 * (1 - landed);
  const down = Math.sin(angle) * 760 * (1 - landed);
  return (
    <Img
      src={staticFile(`aws/${name}.svg`)}
      style={{
        width: 84,
        height: 84,
        display: "block",
        translate: `${across}px ${down}px`,
        rotate: `${(1 - landed) * 220}deg`,
        boxShadow: "0 12px 34px rgba(0,0,0,.55)",
      }}
    />
  );
};

export const OutroScene: FC = () => (
  <AbsoluteFill>
    <Shake hits={[TITLE_AT]} strength={18}>
      <AbsoluteFill style={{ alignItems: "center", justifyContent: "center", textAlign: "center", padding: "0 60px" }}>
        <div style={{ display: "flex", gap: 22, marginBottom: 54 }}>
          {ICONS.map((name, index) => (
            <FlyingIcon key={name} name={name} index={index} />
          ))}
        </div>
        <Slam at={TITLE_AT} from={2.1} style={{ fontFamily: fonts.display, fontWeight: 800, fontSize: 122, lineHeight: 1.02, letterSpacing: "-.04em", color: colors.ink, marginBottom: 54 }}>
          AWS, <span style={gradientText}>explained</span>
          <br />
          in your notebook
        </Slam>
        <Pop at={TITLE_AT + 14}>
          <div style={{ fontFamily: fonts.mono, fontSize: 46, color: colors.ink, background: colors.surface, border: `2px solid ${colors.blue}`, borderRadius: 16, padding: "20px 38px", boxShadow: "0 0 70px rgba(163,173,255,.45)", minWidth: 780, textAlign: "left" }}>
            <span style={{ color: colors.blue }}>$ </span>
            <Typed text="%pip install aws-analyzer" at={TITLE_AT + 18} framesPerChar={0.7} />
          </div>
        </Pop>
        <div style={{ display: "flex", gap: 14, marginTop: 40 }}>
          {PILLS.map((pill, i) => (
            <Rise key={pill} at={TITLE_AT + 38 + i * 4} style={{ fontSize: 31, fontWeight: 600, color: colors.ink, background: colors.paper2, border: `1px solid ${colors.line2}`, borderRadius: 999, padding: "10px 24px" }}>
              {pill}
            </Rise>
          ))}
        </div>
      </AbsoluteFill>
    </Shake>
    <Flash at={TITLE_AT} peak={0.55} />
    <Ring at={TITLE_AT} color={colors.blue} x={640} y={420} />
    <Burst at={TITLE_AT} color={colors.blue} x={640} y={420} count={60} reach={760} />
  </AbsoluteFill>
);
