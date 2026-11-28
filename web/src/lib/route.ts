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
