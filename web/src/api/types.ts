/** What the server's API sends: the catalog of services and commands (GET /api/catalog), and a report. */

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
