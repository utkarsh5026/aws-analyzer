import type { FC } from "react";
import { AbsoluteFill, Img, Series, staticFile, useCurrentFrame } from "remotion";
import { Flash, gradientText, Pop, progress, Slam } from "../motion";
import { colors, fonts } from "../theme";

export const SERVICE_FRAMES = 24;

type Service = { name: string; icon: string; question: string; figure: string };

const SERVICES: Service[] = [
  { name: "Amazon S3", icon: "s3", question: "What is this bucket costing me?", figure: "overview" },
  { name: "Amazon DynamoDB", icon: "dynamodb", question: "How much did that scan just cost?", figure: "dynamodb-table-info" },
  { name: "Bedrock Knowledge Bases", icon: "bedrock", question: "Which files failed to index, and why?", figure: "kb-explorer-failed" },
  { name: "Amazon SageMaker", icon: "sagemaker", question: "What did I leave running?", figure: "sagemaker-instance" },
  { name: "Amazon OpenSearch", icon: "opensearch", question: "Do my vectors fit in memory?", figure: "opensearch-index-info" },
  { name: "AWS Lambda", icon: "lambda", question: "Which runtimes lose support soon?", figure: "lambda-functions" },
  { name: "AWS Step Functions", icon: "stepfunctions", question: "Which step of my workflow failed?", figure: "sfn-explorer-graph" },
  { name: "Amazon API Gateway", icon: "apigateway", question: "Which calls to my API fail, and why?", figure: "apigw-explorer" },
];

export const MONTAGE_FRAMES = SERVICES.length * SERVICE_FRAMES;

const Figure: FC<{ name: string; leansLeft: boolean }> = ({ name, leansLeft }) => {
  const frame = useCurrentFrame();
  const side = leansLeft ? -1 : 1;
  const arrived = progress(frame, 0, 10);
  return (
    <div style={{ position: "absolute", left: 90, right: 90, top: 262, height: 800, perspective: 1500 }}>
      <Img
        src={staticFile(`figures/${name}-dark.webp`)}
        style={{
          width: "100%",
          borderRadius: 18,
          border: `1px solid ${colors.line2}`,
          boxShadow: "0 40px 90px rgba(0,0,0,.75), 0 0 70px rgba(163,173,255,.25)",
          transform: `translateX(${side * (1 - arrived) * 420}px) rotateY(${side * -(10 + (1 - arrived) * 22)}deg) rotateX(6deg) scale(${1 + frame * 0.002})`,
          transformOrigin: "top center",
        }}
      />
    </div>
  );
};

const Dots: FC<{ current: number }> = ({ current }) => (
  <div style={{ display: "flex", gap: 8, marginLeft: "auto" }}>
    {SERVICES.map((service, i) => (
      <div key={service.name} style={{ width: 16, height: 16, borderRadius: "50%", background: i <= current ? colors.blue : colors.line2 }} />
    ))}
  </div>
);

const ServiceCut: FC<{ service: Service; index: number }> = ({ service, index }) => (
  <AbsoluteFill style={{ overflow: "hidden" }}>
    <Figure name={service.figure} leansLeft={index % 2 === 0} />
    <AbsoluteFill style={{ background: "linear-gradient(rgba(16,21,42,.98) 24%, transparent 40%, transparent 72%, rgba(16,21,42,.95))" }} />
    <div style={{ position: "absolute", left: 48, right: 48, top: 34 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 18, marginBottom: 14 }}>
        <Pop at={0}>
          <Img src={staticFile(`aws/${service.icon}.svg`)} style={{ width: 64, height: 64, display: "block" }} />
        </Pop>
        <span style={{ fontSize: 34, fontWeight: 600, color: colors.muted }}>{service.name}</span>
        <Dots current={index} />
      </div>
      <Slam at={1} from={1.25} style={{ fontFamily: fonts.display, fontSize: 67, fontWeight: 800, letterSpacing: "-.03em", lineHeight: 1.05, transformOrigin: "left center", ...gradientText }}>
        {service.question}
      </Slam>
    </div>
    <Flash at={0} peak={0.3} />
  </AbsoluteFill>
);

export const MontageScene: FC = () => (
  <Series>
    {SERVICES.map((service, index) => (
      <Series.Sequence key={service.name} durationInFrames={SERVICE_FRAMES} name={service.name} premountFor={SERVICE_FRAMES}>
        <ServiceCut service={service} index={index} />
      </Series.Sequence>
    ))}
  </Series>
);
