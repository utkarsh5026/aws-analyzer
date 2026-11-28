/**
 * Searching one service's resources, as plain data: ranking names against what's typed, and reading a pasted
 * `s3://` path, ARN or console link as the service and name it points at.
 */

import type { Resource } from "../api/types";

export const MOST_SHOWN = 50;

const EXACT = 1000;
const STARTS = 500;
const WORD_STARTS = 300;
const CONTAINS = 200;
const IN_ORDER = 50;
const IN_DETAIL = 10;

/** True when every letter of `wanted` appears in `text` in the same order ('oet' in 'orders-etl'). */
function hasInOrder(text: string, wanted: string): boolean {
  let at = 0;
  for (const letter of text) {
    if (letter === wanted[at]) at++;
    if (at === wanted.length) return true;
  }
  return wanted.length === 0;
}

function startsAWord(name: string, wanted: string): boolean {
  return name.split(/[-_.:/ ]/).some((word) => word.startsWith(wanted));
}

/** How well a resource matches one word: 0 when it doesn't. */
function wordScore(resource: Resource, word: string): number {
  const name = resource.name.toLowerCase();
  if (name === word) return EXACT;
  if (name.startsWith(word)) return STARTS;
  if (startsAWord(name, word)) return WORD_STARTS;
  if (name.includes(word)) return CONTAINS;
  if (hasInOrder(name, word)) return IN_ORDER;
  return `${resource.kind} ${resource.detail}`.toLowerCase().includes(word) ? IN_DETAIL : 0;
}

/** How well a resource matches every word typed: 0 when one of them doesn't match. */
export function matchScore(resource: Resource, query: string): number {
  const words = query.toLowerCase().split(/\s+/).filter(Boolean);
  let total = 0;
  for (const word of words) {
    const score = wordScore(resource, word);
    if (score === 0) return 0;
    total += score;
  }
  return total;
}

/**
 * The resources that match, best first, then by name, at most `most`; with nothing typed, all of them in order.
 */
export function rankResources(resources: Resource[], query: string, most = MOST_SHOWN): Resource[] {
  if (!query.trim()) return resources.slice(0, most);
  const scored = resources
    .map((resource) => ({ resource, score: matchScore(resource, query) }))
    .filter((each) => each.score > 0);
  scored.sort((a, b) => b.score - a.score || a.resource.name.localeCompare(b.resource.name));
  return scored.slice(0, most).map((each) => each.resource);
}

/** What a pasted address points at: the service and the name to search for. */
export interface Pasted {
  service: string;
  name: string;
}

const ARN_SERVICES: Record<string, string> = {
  lambda: "lambda_functions",
  s3: "s3",
  dynamodb: "dynamodb",
  states: "stepfunctions",
  apigateway: "apigateway",
  "execute-api": "apigateway",
  sqs: "messaging",
  sns: "messaging",
};

const CONSOLE_SERVICES: [RegExp, string][] = [
  [/\/lambda\//, "lambda_functions"],
  [/\/s3\//, "s3"],
  [/\/dynamodbv2\//, "dynamodb"],
  [/\/states\//, "stepfunctions"],
  [/\/apigateway\//, "apigateway"],
  [/\/sqs\//, "messaging"],
  [/\/sns\//, "messaging"],
];

function lastPart(text: string): string {
  return text.split(/[:/]/).filter(Boolean).pop() ?? "";
}

function fromArn(text: string): Pasted | null {
  const [, , service = "", , , ...rest] = text.split(":");
  const key = ARN_SERVICES[service];
  if (!key) return null;
  const resource = rest.join(":");
  const name = key === "lambda_functions" ? (resource.split(":")[1] ?? "") : lastPart(resource);
  return { service: key, name };
}

function fromConsole(text: string): Pasted | null {
  const url = new URL(text);
  const found = CONSOLE_SERVICES.find(([pattern]) => pattern.test(url.pathname));
  if (!found) return null;
  const route = url.hash.replace(/^#/, "");
  const named = route.match(/(?:functions|tables|statemachines|queues|topics|buckets)\/([^/?#]+)/i);
  const fromQuery = url.searchParams.get("name") ?? url.searchParams.get("bucket") ?? "";
  return { service: found[1], name: decodeURIComponent(named?.[1] ?? fromQuery) };
}

/** The service and name a pasted `s3://` path, ARN or AWS console link points at, else null. */
export function readPasted(text: string): Pasted | null {
  const trimmed = text.trim();
  if (trimmed.startsWith("s3://")) return { service: "s3", name: trimmed.slice(5).split("/")[0] ?? "" };
  if (trimmed.startsWith("arn:aws")) return fromArn(trimmed);
  if (!/^https:\/\/[^/]*console\.aws\.amazon\.com\//.test(trimmed)) return null;
  try {
    return fromConsole(trimmed);
  } catch {
    return null;
  }
}
