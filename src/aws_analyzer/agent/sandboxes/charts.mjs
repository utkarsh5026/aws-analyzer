// The local sandbox's chart drawer (aws_analyzer.agent.sandboxes.local): one Vega-Lite chart drawn as SVG.
//
// Deno runs it like a run of the model's code, with read access to the cache folder only: no network, no environment,
// no other files. The request comes on stdin as JSON ({nonce, spec, config}): Vega-Lite compiles the spec with the
// config under its own, Vega draws it without a browser (renderer "none", toSVG), and the result goes back on stdout
// as one line that starts with the nonce: {ok, svg} or {ok: false, error}. The spec's data is inside it, and the
// loader refuses anything else, so drawing a chart fetches nothing.

const [cacheDir] = Deno.args;
const request = JSON.parse(await new Response(Deno.stdin.readable).text());

const loadScript = async (name) => (0, eval)(await Deno.readTextFile(`${cacheDir}/${name}`));

let result;
try {
  await loadScript("vega.min.js");
  await loadScript("vega-lite.min.js");
  const { vega, vegaLite } = globalThis;
  const refuse = () => Promise.reject(new Error("a chart is drawn from its own data: it can't load a URL or a file"));
  const loader = { load: refuse, sanitize: refuse, http: refuse, file: refuse };
  const compiled = vegaLite.compile(request.spec, { config: request.config }).spec;
  const view = new vega.View(vega.parse(compiled), { renderer: "none", loader, logLevel: vega.None });
  result = { ok: true, svg: await view.toSVG() };
} catch (error) {
  result = { ok: false, error: String(error?.message ?? error) };
}
console.log(request.nonce + JSON.stringify(result));
