/**
 * What the server's API sends: the catalog of services and commands (GET /api/catalog), a report, one service's
 * resources for the search (GET /api/resources/<service>) and what needs attention in one (GET /api/attention/...).
 */

/** One of a command's parameters as its form shows it (server/catalog.py's Parameter). */
export interface Parameter {
  name: string;
  /** The default written as Python, '' when there's none (then it's required). */
  default: string;
  required: boolean;
  /** 'flag' (a checkbox, for a True / False default) or 'text'. */
  kind: "flag" | "text";
  /** False after a bare * in the signature: it can only be given by name. */
  positional: boolean;
}

/** A command the page can run (server/catalog.py's Command). */
export interface Command {
  name: string;
  signature: string;
  about: string;
  doc: string;
  group: string;
  parameters: Parameter[];
}

/** A service and the commands the page offers of its View. */
export interface Service {
  key: string;
  title: string;
  icon: string;
  /** Whether its reports come as data too (GET /api/data). */
  data: boolean;
  /** Whether the page can search its resources (GET /api/resources). */
  searchable: boolean;
  /** Whether its page checks what needs attention in it (GET /api/attention). */
  checkable: boolean;
  commands: Command[];
}

export interface Catalog {
  version: string;
  region: string;
  profile: string;
  services: Service[];
}

/** A command's report (server/runner.py's Report): the call it ran, its arguments as form texts, its HTML. */
export interface Report {
  service: string;
  command: string;
  call: string;
  arguments: Record<string, string>;
  html: string;
  seconds: number;
}

/** One thing a listing said about a resource ('Runtime' -> 'python3.12'). */
export interface Fact {
  label: string;
  value: string;
}

/** One thing the search finds (server/resources.py's Resource), with the command that opens its report. */
export interface Resource {
  kind: string;
  name: string;
  detail: string;
  command: string;
  arguments: Record<string, string>;
  /** What its listing call already said about it, for the quick look. */
  facts: Fact[];
}

/** One service's resources, as its listing call read them (server/resources.py's ResourceIndex). */
export interface ResourceIndex {
  service: string;
  resources: Resource[];
  /** When they were read, ISO 8601. */
  read_at: string;
  seconds: number;
  /** True when the server answered from what it kept rather than asking AWS. */
  from_memory: boolean;
}

/** How bad a concern is. */
export type Level = "bad" | "warn" | "info";

/** A resource a concern names, and the command that opens it ('' when there's none). */
export interface Target {
  name: string;
  command: string;
  arguments: Record<string, string>;
}

/** One thing that needs attention (server/attention.py's Concern). */
export interface Concern {
  level: Level;
  /** What's wrong, the sentence's lead. */
  headline: string;
  /** Why it matters and what to do. */
  detail: string;
  targets: Target[];
}

/** What one check of a service found (server/attention.py's Attention). */
export interface Attention {
  service: string;
  /** The overview it ran, as written in a notebook ('functions()'). */
  call: string;
  command: string;
  verdict: string;
  tone: string;
  /** [label, value, tone] */
  cards: string[][];
  concerns: Concern[];
  /** Why the service couldn't be read, when it couldn't. */
  note: string;
  checked_at: string;
  seconds: number;
  from_memory: boolean;
}

/** A check and the report it was read from (GET /api/attention/<service>). */
export interface Checked {
  attention: Attention;
  report: Report;
}
