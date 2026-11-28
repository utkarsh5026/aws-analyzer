/**
 * Following a stream this server pushes (a job's events, a live stream) as Server-Sent Events. An EventSource sends
 * `Accept: text/event-stream` by itself, which is what the notebook's proxy streams, and reconnects by itself after a
 * dropped connection, asking for what it missed (Last-Event-ID). A stream ends with a 'done' message, which closes it
 * here, since a closed connection alone would only make the browser open it again.
 */

import { streamAddress } from "./client";

/** What to do with each kind of message, given its data. */
export type Handlers = Record<string, (data: unknown) => void>;

/** A stream being followed, which the page closes when it's done with it. */
export interface Following {
  close: () => void;
}

const END = "done";

function dataOf(event: Event): unknown {
  try {
    return JSON.parse((event as MessageEvent<string>).data);
  } catch {
    return null;
  }
}

/**
 * Follows a stream from this server: each message goes to its kind's handler, 'done' closes it after its handler,
 * and `onLost` hears when the browser gives up on the connection (the server stopped, or the stream was refused).
 */
export function follow(path: string, handlers: Handlers, onLost?: () => void): Following {
  const source = new EventSource(streamAddress(path));
  const kinds = new Set([...Object.keys(handlers), END]);
  for (const kind of kinds) {
    source.addEventListener(kind, (event) => {
      handlers[kind]?.(dataOf(event));
      if (kind === END) source.close();
    });
  }
  source.onerror = () => {
    if (source.readyState === EventSource.CLOSED) onLost?.();
  };
  return { close: () => source.close() };
}
