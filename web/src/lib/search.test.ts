import { describe, expect, it } from "vitest";
import type { Resource } from "../api/types";
import { matchScore, rankResources, readPasted } from "./search";

function fn(name: string, detail = "python3.12"): Resource {
  return { kind: "function", name, detail, command: "function_info", arguments: { name } };
}

const FUNCTIONS = [fn("process-orders-v1"), fn("orders-backfill"), fn("orders"), fn("report-api"), fn("orders-etl")];

describe("rankResources", () => {
  it("puts the exact name first, then names that start with it, then words in them", () => {
    expect(rankResources(FUNCTIONS, "orders").map((each) => each.name)).toEqual([
      "orders",
      "orders-backfill",
      "orders-etl",
      "process-orders-v1",
    ]);
  });

  it("needs every word, and finds letters typed in order", () => {
    expect(rankResources(FUNCTIONS, "orders etl").map((each) => each.name)).toEqual(["orders-etl"]);
    expect(rankResources(FUNCTIONS, "oetl").map((each) => each.name)).toEqual(["orders-etl"]);
  });

  it("matches the line about a resource last, and lists everything when nothing is typed", () => {
    const node = fn("thumbs", "nodejs20.x");
    expect(rankResources([...FUNCTIONS, node], "nodejs")).toEqual([node]);
    expect(rankResources(FUNCTIONS, " ")).toHaveLength(FUNCTIONS.length);
    expect(rankResources(FUNCTIONS, "", 2)).toHaveLength(2);
    expect(matchScore(fn("orders-etl"), "zzz")).toBe(0);
  });
});

describe("readPasted", () => {
  it("reads an s3:// path as the bucket", () => {
    expect(readPasted("s3://acme-raw/incoming/2026/")).toEqual({ service: "s3", name: "acme-raw" });
  });

  it("reads ARNs of each service", () => {
    expect(readPasted("arn:aws:lambda:us-east-1:123456789012:function:orders-etl:prod")).toEqual({
      service: "lambda_functions",
      name: "orders-etl",
    });
    expect(readPasted("arn:aws:states:us-east-1:123:stateMachine:report-flow")).toEqual({
      service: "stepfunctions",
      name: "report-flow",
    });
    expect(readPasted("arn:aws:dynamodb:us-east-1:123:table/orders")).toEqual({ service: "dynamodb", name: "orders" });
    expect(readPasted("arn:aws:sqs:us-east-1:123:orders-dlq")).toEqual({ service: "messaging", name: "orders-dlq" });
    expect(readPasted("arn:aws:ec2:us-east-1:123:instance/i-1")).toBeNull();
  });

  it("reads console links, and leaves other text alone", () => {
    const link = "https://us-east-1.console.aws.amazon.com/lambda/home?region=us-east-1#/functions/orders-etl?tab=code";
    expect(readPasted(link)).toEqual({ service: "lambda_functions", name: "orders-etl" });
    expect(readPasted("https://example.com/lambda/")).toBeNull();
    expect(readPasted("orders")).toBeNull();
  });
});
