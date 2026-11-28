/** How long ago something was read, in the words the page uses. */

const SECOND = 1000;
const MINUTE = 60 * SECOND;
const HOUR_MINUTES = 60;

/** 'just now', '4 min ago', '2 h ago': how long ago `iso` was. */
export function readAgo(iso: string, now = Date.now()): string {
  const gone = now - Date.parse(iso);
  if (Number.isNaN(gone) || gone < MINUTE) return "just now";
  const minutes = Math.round(gone / MINUTE);
  return minutes < HOUR_MINUTES ? `${minutes} min ago` : `${Math.round(minutes / HOUR_MINUTES)} h ago`;
}
