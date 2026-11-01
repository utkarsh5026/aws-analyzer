import type { FC } from "react";
import { AbsoluteFill, useCurrentFrame } from "remotion";
import { Burst, Flash, Punch, Shake, Slam } from "../motion";
import { colors, fonts } from "../theme";

const RAW_LINES = [
  '"Principal": {"AWS": "arn:aws:iam::210987654321:root"},',
  '"Action": ["s3:GetObject", "s3:ListBucket"],',
  '"Resource": ["arn:aws:s3:::acme-ml-data/curated/*"]',
  "[ERROR] ClientError: An error occurred (AccessDeniedException)",
  "when calling the PutItem operation: User: arn:aws:sts::",
  "123456789012:assumed-role/acme-lambda-role/orders-etl",
  "is not authorized to perform: dynamodb:PutItem on resource:",
  '{"pk": {"S": "USER#0"}, "sk": {"S": "ORDER#0000"},',
  ' "status": {"S": "shipped"}, "total": {"N": "69.83"},',
  ' "lines": {"L": [{"M": {"sku": {"S": "SKU-046"},',
  '"Effect": "Allow", "Sid": "PartnerRead",',
  "arn:aws:dynamodb:us-east-1:123456789012:table/orders",
];
const WALL_LINES = 90;
const LINE_PIXELS = 34;
const SCROLL_PIXELS_PER_FRAME = 11;
const THIS_AT = 26;

const Wall: FC = () => {
  const frame = useCurrentFrame();
  const lines = Array.from({ length: WALL_LINES }, (_, i) => RAW_LINES[i % RAW_LINES.length]);
  return (
    <div
      style={{
        position: "absolute",
        inset: "-260px -300px",
        rotate: "-8deg",
        fontFamily: fonts.mono,
        fontSize: 25,
        lineHeight: `${LINE_PIXELS}px`,
        whiteSpace: "nowrap",
        translate: `0 ${-frame * SCROLL_PIXELS_PER_FRAME}px`,
      }}
    >
      {lines.map((line, i) => (
        <div key={i} style={{ color: line.includes("ERROR") || line.includes("not authorized") ? colors.red : colors.raw, paddingLeft: (i * 37) % 170 }}>
          {line} &nbsp; {RAW_LINES[(i + 5) % RAW_LINES.length]}
        </div>
      ))}
    </div>
  );
};

export const ColdOpenScene: FC = () => (
  <AbsoluteFill style={{ overflow: "hidden" }}>
    <Punch hits={[THIS_AT]} amount={0.09}>
      <Shake hits={[THIS_AT]} strength={30}>
        <Wall />
        <AbsoluteFill style={{ background: "radial-gradient(ellipse at center, rgba(16,21,42,.94) 26%, rgba(16,21,42,.32) 82%)" }} />
        <AbsoluteFill style={{ alignItems: "center", justifyContent: "center", fontFamily: fonts.display, fontWeight: 800, letterSpacing: "-.04em", textAlign: "center" }}>
          <Slam at={4} from={2} style={{ fontSize: 112, color: colors.ink }}>
            AWS gives you
          </Slam>
          <Slam at={THIS_AT} from={3.2} style={{ fontSize: 270, lineHeight: 1, color: colors.red, textShadow: "0 0 70px rgba(255,107,99,.6)" }}>
            this.
          </Slam>
        </AbsoluteFill>
      </Shake>
    </Punch>
    <Flash at={THIS_AT} color={colors.red} peak={0.55} />
    <Burst at={THIS_AT} color={colors.red} x={640} y={560} />
  </AbsoluteFill>
);
