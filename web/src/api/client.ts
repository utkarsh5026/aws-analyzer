/**
 * The page's one way to the server. Every request carries the token from the page's <meta>, in a header another
 * website can't add, so only this page can make the server read AWS.
 */

const TOKEN_HEADER = "X-AWS-Analyzer-Token";

function pageMeta(name: string): string {
  return document.querySelector<HTMLMetaElement>(`meta[name="${name}"]`)?.content ?? "";
}

/** The version of aws-analyzer serving the page, from its <meta>. */
export const VERSION = pageMeta("aws-analyzer-version");

interface ServerError {
  message?: string;
}

async function answerOf(response: Response): Promise<unknown> {
  try {
    return await response.json();
  } catch {
    return { message: `The server answered ${response.status}.` };
  }
}

async function answered<T>(response: Response): Promise<T> {
  const answer = await answerOf(response);
  if (!response.ok) {
    throw new Error((answer as ServerError).message || `The server answered ${response.status}.`);
  }
  return answer as T;
}

/** The address of a stream from this server, with the token as ?token=, since an EventSource can't add a header. */
export function streamAddress(path: string): string {
  const joiner = path.includes("?") ? "&" : "?";
  return `${path}${joiner}token=${encodeURIComponent(pageMeta("aws-analyzer-token"))}`;
}

/** GETs `path`, or POSTs `body` to it as JSON, and returns the answer, or throws an Error with the server's words. */
export async function api<T>(path: string, body?: unknown): Promise<T> {
  const options: RequestInit = { headers: { [TOKEN_HEADER]: pageMeta("aws-analyzer-token") } };
  if (body !== undefined) {
    options.method = "POST";
    options.headers = { ...options.headers, "Content-Type": "application/json" };
    options.body = JSON.stringify(body);
  }
  return answered<T>(await fetch(path, options));
}

/** DELETEs `path` (stopping a job) and returns the answer, or throws an Error with the server's words. */
export async function apiDelete<T>(path: string): Promise<T> {
  const headers = { [TOKEN_HEADER]: pageMeta("aws-analyzer-token") };
  return answered<T>(await fetch(path, { method: "DELETE", headers }));
}
