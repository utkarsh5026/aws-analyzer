import type { FC } from "react";
import { useCurrentFrame } from "remotion";
import { Burst, Pop, progress, Rise, Scene, Slam, Sweep } from "../motion";
import { colors, fonts } from "../theme";

const SAID_AT = 30;
const SCAN_FRAMES = 22;

const Scanner: FC = () => {
  const frame = useCurrentFrame();
  const scanned = progress(frame, 6, 6 + SCAN_FRAMES);
  const isVisible = frame >= 6 && frame < 6 + SCAN_FRAMES;
  return isVisible ? (
    <div
      style={{
        position: "absolute",
        left: 0,
        right: 0,
        top: `${scanned * 100}%`,
        height: 6,
        background: colors.blue,
        boxShadow: `0 0 32px 10px ${colors.blue}`,
      }}
    />
  ) : null;
};

export const TranslateScene: FC = () => (
  <Scene lead="You needed" accent="this." hits={[SAID_AT]}>
    <Rise at={2} style={{ position: "relative", overflow: "hidden", border: `1px dashed ${colors.line2}`, borderRadius: 18, padding: "22px 26px", background: colors.paper, fontFamily: fonts.mono, fontSize: 25, lineHeight: 1.5, color: colors.raw }}>
      <div>[ERROR] ClientError: An error occurred (AccessDeniedException)</div>
      <div>when calling the PutItem operation: User: arn:aws:sts::123456789012:</div>
      <div>assumed-role/acme-lambda-role/orders-etl is not authorized to perform:</div>
      <div>dynamodb:PutItem on resource: arn:aws:dynamodb:us-east-1:123456789012:</div>
      <div>table/orders</div>
      <Scanner />
    </Rise>
    <Pop at={SAID_AT - 4} style={{ textAlign: "center", fontSize: 72, color: colors.blue, lineHeight: 1.2 }}>
      ↓
    </Pop>
    <Slam at={SAID_AT} from={1.35} style={{ background: colors.surface, border: `2px solid ${colors.blue}`, borderRadius: 22, padding: "30px 36px", boxShadow: "0 0 70px rgba(163,173,255,.35)" }}>
      <div style={{ fontFamily: fonts.mono, fontSize: 27, color: colors.blue, marginBottom: 14 }}>aws-analyzer says</div>
      <div style={{ fontSize: 50, lineHeight: 1.26, color: colors.ink }}>
        Its role acme-lambda-role{" "}
        <Sweep at={SAID_AT + 10} frames={18}>
          isn't allowed dynamodb:PutItem
        </Sweep>{" "}
        on table orders. <b>Add the permission to the role's policy in IAM.</b>
      </div>
    </Slam>
    <Burst at={SAID_AT} color={colors.blue} x={590} y={480} count={30} reach={520} />
  </Scene>
);
