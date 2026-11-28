/**
 * The proxy check's page: opens /api/ping-stream as an EventSource and as a streamed fetch, and /api/ping-socket as a
 * WebSocket, records when each line arrives, and says whether they came one by one (the proxy streams) or together
 * (it held the body back). Nothing it writes into the page comes from anywhere but the server's own pings, and all of
 * it goes in with textContent.
 */

const TOKEN = document.querySelector('meta[name="aws-analyzer-token"]').content;
const SERVER = JSON.parse(document.querySelector('meta[name="aws-analyzer-server"]').content);
const TOKEN_HEADER = "X-AWS-Analyzer-Token";
const CHECK_SECONDS = 25;
const STREAMED_SPREAD = 0.6;
const HELD_BACK_SECONDS = 1.5;
const SOCKET_PING_MS = 1500;
const NORMAL_CLOSES = [1000, 1005];
const STREAM_TYPE = "text/event-stream";
const blockedByPolicy = [];

document.addEventListener("securitypolicyviolation", (event) => {
  blockedByPolicy.push(`${event.violatedDirective} blocked ${event.blockedURI}`);
});

/** The address of one of the server's paths, relative to this page, so it works behind /proxy/<port>/. */
function address(path, protocol) {
  const url = new URL(path, window.location.href);
  url.search = "";
  if (protocol) url.protocol = protocol;
  return url;
}

/** A check's record: when it began, each line's arrival, and how it ended. */
function newRecord(name) {
  return { name, began: performance.now(), arrivals: [], ended: "", error: "", extra: {} };
}

function secondsSince(record) {
  return (performance.now() - record.began) / 1000;
}

function showLine(listId, text) {
  const item = document.createElement("li");
  item.textContent = text;
  document.getElementById(listId).append(item);
}

function arrived(record, listId, ping) {
  const at = secondsSince(record);
  record.arrivals.push({ n: ping.n, at, sent: ping.sent });
  showLine(listId, `line ${ping.n} of ${ping.of} arrived at ${at.toFixed(1)} s`);
}

/** How the lines arrived: 'streamed', 'held back', 'partly', or 'nothing' when none came. */
function arrivalKind(record) {
  const times = record.arrivals.map((arrival) => arrival.at);
  if (times.length === 0) return "nothing";
  const spread = Math.max(...times) - Math.min(...times);
  const sentSpread = (record.arrivals.at(-1).sent - record.arrivals[0].sent) / 1000;
  if (times.length > 1 && spread < HELD_BACK_SECONDS) return "held back";
  if (sentSpread > 0 && spread >= sentSpread * STREAMED_SPREAD) return "streamed";
  return "partly";
}

const VERDICTS = {
  streamed: "✓ Streams: each line arrived about when it was sent.",
  "held back": "✗ Held back: the lines arrived together, when the response ended.",
  partly: "~ Partly: the lines arrived in a few bunches, not one by one.",
  nothing: "✗ Nothing arrived.",
};

function sayVerdict(record, verdictId) {
  const element = document.getElementById(verdictId);
  const kind = arrivalKind(record);
  const problem = record.error ? ` ${record.error}` : "";
  element.textContent = `${VERDICTS[kind]}${problem}`;
  element.dataset.kind = kind;
  record.kind = kind;
}

/** Runs one check until it ends or CHECK_SECONDS pass, whichever is first. */
function withDeadline(record, run) {
  return new Promise((resolve) => {
    const finish = (ended) => {
      if (!record.ended) record.ended = ended;
      clearTimeout(timer);
      resolve(record);
    };
    const timer = setTimeout(() => finish(`gave up after ${CHECK_SECONDS} s`), CHECK_SECONDS * 1000);
    run(finish);
  });
}

function checkEventSource() {
  const record = newRecord("Server-Sent Events (EventSource)");
  const url = address("api/ping-stream");
  url.searchParams.set("token", TOKEN);
  return withDeadline(record, (finish) => {
    const source = new EventSource(url);
    source.addEventListener("open", () => {
      record.extra.opened_at = secondsSince(record).toFixed(1);
    });
    source.addEventListener("ping", (event) => arrived(record, "stream-lines", JSON.parse(event.data)));
    source.addEventListener("done", () => {
      source.close();
      finish(`done at ${secondsSince(record).toFixed(1)} s`);
    });
    source.addEventListener("error", () => {
      source.close();
      record.error = "The stream failed (the browser gives no reason; see its console).";
      finish("error");
    });
  });
}

/** Reads the stream's text as it comes and hands each complete event's data to `onPing`. */
async function readEvents(body, onPing) {
  const reader = body.pipeThrough(new TextDecoderStream()).getReader();
  let held = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) return;
    held += value;
    const events = held.split("\n\n");
    held = events.pop();
    for (const event of events) {
      const isPing = event.startsWith("event: ping");
      const data = event.split("\n").find((line) => line.startsWith("data: "));
      if (isPing && data) onPing(JSON.parse(data.slice("data: ".length)));
    }
  }
}

function checkFetch() {
  const record = newRecord(`Streamed fetch (Accept: ${STREAM_TYPE})`);
  return withDeadline(record, async (finish) => {
    try {
      const response = await fetch(address("api/ping-stream"), { headers: { [TOKEN_HEADER]: TOKEN, Accept: STREAM_TYPE } });
      record.extra.status = response.status;
      record.extra.content_encoding = response.headers.get("content-encoding") || "none";
      if (!response.ok) throw new Error(`The server answered ${response.status}.`);
      await readEvents(response.body, (ping) => arrived(record, "fetch-lines", ping));
      finish(`done at ${secondsSince(record).toFixed(1)} s`);
    } catch (error) {
      record.error = String(error.message || error);
      finish("error");
    }
  });
}

function checkWebSocket() {
  const record = newRecord("WebSocket");
  record.extra.pongs = [];
  return withDeadline(record, (finish) => {
    let socket;
    try {
      socket = new WebSocket(address("api/ping-socket", window.location.protocol === "https:" ? "wss:" : "ws:"));
    } catch (error) {
      record.error = `It couldn't be opened: ${error.message || error}`;
      finish("error");
      return;
    }
    const sentAt = new Map();
    let pinger = 0;
    let asked = 0;
    socket.addEventListener("open", () => {
      record.extra.opened_at = secondsSince(record).toFixed(1);
      socket.send(JSON.stringify({ token: TOKEN }));
      pinger = setInterval(() => {
        asked += 1;
        sentAt.set(String(asked), performance.now());
        socket.send(String(asked));
      }, SOCKET_PING_MS);
    });
    socket.addEventListener("message", (event) => {
      const message = JSON.parse(event.data);
      if (message.kind === "tick") arrived(record, "socket-lines", message);
      if (message.kind === "pong" && sentAt.has(message.said)) {
        const roundTrip = performance.now() - sentAt.get(message.said);
        record.extra.pongs.push(Math.round(roundTrip));
      }
    });
    socket.addEventListener("close", (event) => {
      clearInterval(pinger);
      record.extra.close_code = event.code;
      if (!NORMAL_CLOSES.includes(event.code)) record.error = `It closed with code ${event.code}${event.reason ? `: ${event.reason}` : ""}.`;
      finish(`closed at ${secondsSince(record).toFixed(1)} s`);
    });
    socket.addEventListener("error", () => {
      record.error = record.error || "It failed (the browser gives no reason; see its console).";
    });
  });
}

function sayWebSocket(record) {
  sayVerdict(record, "socket-verdict");
  const pongs = record.extra.pongs;
  const element = document.getElementById("socket-verdict");
  if (pongs.length > 0) {
    element.textContent += ` Messages go both ways: ${pongs.length} answered, in ${Math.min(...pongs)}–${Math.max(...pongs)} ms.`;
  } else if (record.arrivals.length > 0) {
    element.textContent += " No answer came back to the page's messages.";
  }
  if (record.arrivals.length === 0 && SERVER.socket_library === "none") {
    element.textContent += " The server has no WebSocket library (pip install websockets), so this is the server, not the proxy.";
  }
}

/** The address's kind without its name ('notebook.eu-west-1.sagemaker.aws'), or 'this machine' for a local one. */
function hostKind(hostname) {
  const isLocal = hostname === "localhost" || /^[\d.]+$/.test(hostname) || hostname.includes(":");
  return isLocal ? "this machine" : hostname.split(".").slice(1).join(".");
}

/** The results as text to paste back: no token, no address beyond the machine's kind of path. */
function resultsText(records) {
  const lines = [
    `aws-analyzer proxy check, ${new Date().toISOString()}`,
    `page path: ${window.location.pathname}`,
    `host kind: ${hostKind(window.location.hostname)}`,
    `browser: ${navigator.userAgent}`,
    `server: uvicorn ${SERVER.uvicorn}, WebSocket library ${SERVER.socket_library}`,
    "",
  ];
  for (const record of records) {
    const times = record.arrivals.map((arrival) => arrival.at.toFixed(1)).join(", ") || "none";
    lines.push(`${record.name}: ${record.kind}; ${record.ended}${record.error ? `; ${record.error}` : ""}`);
    lines.push(`  arrivals (s): ${times}`);
    if (Object.keys(record.extra).length > 0) lines.push(`  details: ${JSON.stringify(record.extra)}`);
  }
  if (blockedByPolicy.length > 0) lines.push("", `Blocked by the page's policy: ${blockedByPolicy.join("; ")}`);
  return lines.join("\n");
}

async function runChecks() {
  const again = document.getElementById("again");
  again.disabled = true;
  again.textContent = "Checking…";
  for (const id of ["stream-lines", "fetch-lines", "socket-lines"]) document.getElementById(id).replaceChildren();
  const [stream, streamedFetch, socket] = await Promise.all([checkEventSource(), checkFetch(), checkWebSocket()]);
  sayVerdict(stream, "stream-verdict");
  sayVerdict(streamedFetch, "fetch-verdict");
  sayWebSocket(socket);
  document.getElementById("results").textContent = resultsText([stream, streamedFetch, socket]);
  again.disabled = false;
  again.textContent = "Check again";
}

async function copyResults() {
  const results = document.getElementById("results");
  const button = document.getElementById("copy");
  try {
    await navigator.clipboard.writeText(results.textContent);
    button.textContent = "Copied";
  } catch {
    window.getSelection().selectAllChildren(results);
    button.textContent = "Selected: press Ctrl C";
  }
}

document.getElementById("again").addEventListener("click", runChecks);
document.getElementById("copy").addEventListener("click", copyResults);
runChecks();
