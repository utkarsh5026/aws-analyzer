/**
 * Where the page is, kept in the address after #: `#/<service>/<command>?<name>=<text>&run=1`, so back, forward,
 * reload and bookmarks work.
 */

export interface Route {
  service: string;
  command: string;
  args: Record<string, string>;
}

/** The route an address's hash names ('#/s3/ls?uri=s3://acme/' -> s3, ls, {uri}). */
export function parseRoute(hash: string): Route {
  const [path = "", query = ""] = hash.replace(/^#\/?/, "").split("?");
  const [service = "", command = ""] = path.split("/").map(decodeURIComponent);
  return { service, command, args: Object.fromEntries(new URLSearchParams(query)) };
}

/** The hash for a command with its form's texts, the empty ones left out. */
export function routeFor(service: string, command: string, args: Record<string, string> = {}): string {
  const filled = Object.entries(args).filter(([, value]) => value !== "");
  const query = new URLSearchParams(filled).toString();
  return `#/${encodeURIComponent(service)}/${encodeURIComponent(command)}${query ? `?${query}` : ""}`;
}

function withoutRun(args: Record<string, string>): string {
  const kept = Object.entries(args).filter(([name, value]) => name !== "run" && value !== "");
  return JSON.stringify(kept.sort(([a], [b]) => a.localeCompare(b)));
}

/** Whether two addresses name the same command with the same arguments, however each is written (encoded, ordered). */
export function isSameRoute(first: string, second: string): boolean {
  const one = parseRoute(first);
  const other = parseRoute(second);
  const isSameCommand = one.service === other.service && one.command === other.command;
  return isSameCommand && withoutRun(one.args) === withoutRun(other.args);
}
