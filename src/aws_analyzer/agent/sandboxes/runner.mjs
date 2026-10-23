// The local sandbox's runner (aws_analyzer.agent.sandboxes.local): one run of the model's Python in Pyodide.
//
// Deno runs it with read access to the Pyodide folder only: no network, no environment, no other files, no
// subprocesses. The request comes on stdin as JSON ({nonce, code, packages, files, limit}): the workspace's files
// (base64) go into Pyodide's in-memory file system at /workspace, the code runs there, and the result goes back on
// stdout as one line that starts with the nonce: what it printed, its traceback, and the files it changed or deleted.

const DRIVER = `
import linecache, sys, traceback
from pyodide.code import eval_code_async

async def _aws_analyzer_run(code):
    linecache.cache["<code>"] = (len(code), None, code.splitlines(True), "<code>")
    try:
        await eval_code_async(code, {"__name__": "__main__"}, filename="<code>", return_mode="none")
        return True
    except SystemExit as exc:
        return exc.code in (None, 0)
    except BaseException as exc:
        tb = exc.__traceback__
        while tb is not None and tb.tb_frame.f_code.co_filename != "<code>":
            tb = tb.tb_next
        traceback.print_exception(type(exc), exc, tb)
        return False
`;

const fromBase64 = (text) => Uint8Array.fromBase64
  ? Uint8Array.fromBase64(text)
  : Uint8Array.from(atob(text), (c) => c.charCodeAt(0));

const toBase64 = (bytes) => {
  if (bytes.toBase64) return bytes.toBase64();
  let text = "";
  for (let i = 0; i < bytes.length; i += 0x8000) text += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(text);
};

const sameBytes = (a, b) => a.length === b.length && a.every((value, i) => value === b[i]);

function collect(limit) {
  const lines = [];
  let size = 0;
  let cut = false;
  const add = (line) => {
    if (size > limit) { cut = true; return; }
    lines.push(line);
    size += line.length + 1;
  };
  return { add, text: () => lines.map((line) => line + "\n").join(""), cut: () => cut };
}

function walk(fs, folder, found) {
  for (const name of fs.readdir(folder)) {
    if (name === "." || name === "..") continue;
    const path = `${folder}/${name}`;
    if (fs.isDir(fs.stat(path).mode)) walk(fs, path, found);
    else found.push(path);
  }
  return found;
}

const [indexDir] = Deno.args;
const request = JSON.parse(await new Response(Deno.stdin.readable).text());
const out = collect(request.limit);
const err = collect(request.limit);
const lock = JSON.parse(await Deno.readTextFile(`${indexDir}/pyodide-lock.json`));
const offered = new Set(request.packages);
lock.packages = Object.fromEntries(Object.entries(lock.packages).filter(([name]) => offered.has(name)));

const fileUrl = (path) => new URL(`file:///${path.replace(/\\/g, "/").replace(/^\/+/, "")}`).href;
const { loadPyodide } = await import(fileUrl(`${indexDir}/pyodide.mjs`));
const py = await loadPyodide({
  indexURL: `${indexDir}/`,
  lockFileContents: lock,
  packageBaseUrl: `${indexDir}/`,
  stdin: () => null,
  stdout: out.add,
  stderr: err.add,
  env: { HOME: "/workspace" },
});

const ROOT = "/workspace";
const given = new Map();
py.FS.mkdirTree(ROOT);
for (const [path, data] of Object.entries(request.files)) {
  const target = `${ROOT}/${path}`;
  py.FS.mkdirTree(target.slice(0, target.lastIndexOf("/")));
  const bytes = fromBase64(data);
  py.FS.writeFile(target, bytes);
  given.set(path, bytes);
}
py.FS.chdir(ROOT);

let ok = false;
try {
  await py.loadPackagesFromImports(request.code, { messageCallback: () => {}, errorCallback: err.add });
  await py.runPythonAsync(DRIVER);
  ok = await py.globals.get("_aws_analyzer_run")(request.code);
} catch (error) {
  err.add(String(error?.message ?? error));
}

const changed = {};
const kept = new Set();
for (const target of walk(py.FS, ROOT, [])) {
  const path = target.slice(ROOT.length + 1);
  const bytes = py.FS.readFile(target);
  kept.add(path);
  if (!given.has(path) || !sameBytes(given.get(path), bytes)) changed[path] = toBase64(bytes);
}
const deleted = [...given.keys()].filter((path) => !kept.has(path));

const result = { ok, stdout: out.text(), stderr: err.text(), cut: out.cut() || err.cut(), changed, deleted };
console.log(request.nonce + JSON.stringify(result));
