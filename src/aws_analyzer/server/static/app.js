// aws-analyzer's page: pick a service and a command, fill in its form (or type the call), and the server runs it
// on the same View a notebook uses and sends back the report's HTML. The address after # says what's open
// (#/lambda_functions/errors?name=orders-etl&since=7d), so back, forward, reload and bookmarks work.
//
// Everything the page builds is made with textContent; the one HTML it inserts is a report, drawn by the server's
// Views, which escape what AWS returns (and the Content-Security-Policy runs no script but this one).

const TOKEN = document.querySelector('meta[name="aws-analyzer-token"]').content;
const STEP_CALL = /^(?:[A-Za-z_]\w*\.)?([A-Za-z_]\w*)\(/;
const MOST_FOUND = 40;

const page = {
  catalog: null,
  services: new Map(),
  results: new Map(),
  justRun: "",
  running: 0,
};

// ---- small helpers

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [name, value] of Object.entries(attrs)) {
    if (name === "class") node.className = value;
    else if (name === "text") node.textContent = value;
    else if (name.startsWith("on")) node.addEventListener(name.slice(2), value);
    else if (value !== false && value !== undefined) node.setAttribute(name, value === true ? "" : value);
  }
  for (const child of children.flat()) {
    if (child !== null && child !== undefined) node.append(child);
  }
  return node;
}

function code(text) {
  return el("code", { text });
}

function iconOf(service) {
  return el("img", { src: `static/icons/${service.icon}.svg`, alt: "", width: 22, height: 22 });
}

async function api(path, body) {
  const options = { headers: { "X-AWS-Analyzer-Token": TOKEN } };
  if (body !== undefined) {
    options.method = "POST";
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  const response = await fetch(path, options);
  const answer = await response.json().catch(() => ({ message: `The server answered ${response.status}.` }));
  if (!response.ok) throw new Error(answer.message || `The server answered ${response.status}.`);
  return answer;
}

// ---- where we are: #/service/command?name=value

function parseRoute() {
  const [path, query = ""] = location.hash.replace(/^#\/?/, "").split("?");
  const [service = "", command = ""] = path.split("/").map(decodeURIComponent);
  return { service, command, args: Object.fromEntries(new URLSearchParams(query)) };
}

function routeFor(service, command, args = {}) {
  const filled = Object.entries(args).filter(([, value]) => value !== "");
  const query = new URLSearchParams(filled).toString();
  return `#/${encodeURIComponent(service)}/${encodeURIComponent(command)}${query ? `?${query}` : ""}`;
}

// ---- the side: every service, and the commands of the one open

function drawSide(route) {
  const side = document.getElementById("side");
  side.replaceChildren();
  for (const service of page.catalog.services) {
    const isOpen = service.key === route.service;
    side.append(el("a", { class: `aa-svc${isOpen ? " aa-on" : ""}`, href: `#/${service.key}` },
      iconOf(service), el("span", { text: service.title })));
    if (isOpen) side.append(commandList(service, route.command));
  }
}

function byGroup(commands) {
  const groups = new Map();
  for (const command of commands) {
    if (!groups.has(command.group)) groups.set(command.group, []);
    groups.get(command.group).push(command);
  }
  return groups;
}

function commandList(service, current) {
  const list = el("div", { class: "aa-cmds" });
  for (const [group, commands] of byGroup(service.commands)) {
    list.append(el("h4", { text: group }));
    for (const command of commands) {
      list.append(el("a", { class: `aa-cmd${command.name === current ? " aa-on" : ""}`,
        href: routeFor(service.key, command.name), title: command.about, text: command.name }));
    }
  }
  return list;
}

// ---- the pages on the right

function drawHome(main) {
  const cards = page.catalog.services.map((service) => el("a", { class: "aa-card", href: `#/${service.key}` },
    iconOf(service), code(service.title),
    el("p", { text: service.commands.slice(0, 5).map((command) => command.name).join(", ") + "…" })));
  main.append(el("div", { class: "aa-head" },
    el("h1", { text: "What do you want to look at?" }),
    el("p", { class: "aa-lede", text: "The notebook's reports with room to read them. Pick a service, then a " +
      "command; or press Ctrl K and type what you're after (errors, largest files, cost…)." })),
  el("div", { class: "aa-cards" }, cards));
}

function drawService(main, service) {
  main.append(el("div", { class: "aa-head" },
    el("p", { class: "aa-crumbs" }, el("a", { href: "#/", text: "Services" }), " › ", service.title),
    el("h1", { text: service.title }),
    el("p", { class: "aa-lede", text: `${service.commands.length} commands, the same as ` +
      `${service.title === "S3" ? "S3View" : "its View"} has in a notebook. Each one only reads.` })));
  for (const [group, commands] of byGroup(service.commands)) {
    main.append(el("h2", { class: "aa-group", text: group }), el("div", { class: "aa-cards" },
      commands.map((command) => el("a", { class: "aa-card", href: routeFor(service.key, command.name) },
        code(`${command.name}()`), el("p", { text: command.about })))));
  }
}

// ---- a command: its form, the call it makes, and its report

function looksLikePython(text) {
  return /^[[{('"]/.test(text) || ["True", "False", "None"].includes(text);
}

function pythonArgument(text, parameter) {
  if (parameter.kind === "flag") return ["true", "1", "yes", "on"].includes(text.toLowerCase()) ? "True" : "False";
  const takesNumber = parameter.default !== "" && !/^['"]/.test(parameter.default);
  if ((takesNumber && /^-?[\d,]+(\.\d+)?$/.test(text)) || looksLikePython(text)) return text.replace(/,/g, "");
  return `'${text.replace(/\\/g, "\\\\").replace(/'/g, "\\'")}'`;
}

function callText(command, values) {
  const parts = [];
  let inOrder = true;
  for (const parameter of command.parameters) {
    const text = (values[parameter.name] || "").trim();
    if (!text) {
      if (parameter.required) inOrder = false;
      continue;
    }
    const value = pythonArgument(text, parameter);
    const byPosition = parameter.required && parameter.positional && inOrder;
    parts.push(byPosition ? value : `${parameter.name}=${value}`);
  }
  return `${command.name}(${parts.join(", ")})`;
}

function fieldFor(parameter, value) {
  if (parameter.kind === "flag") {
    const checked = value ? value === "true" : parameter.default === "True";
    const box = el("input", { type: "checkbox", name: parameter.name, checked });
    return el("div", { class: "aa-field aa-flag" }, el("label", {}, box, el("span", { text: parameter.name })));
  }
  const label = el("label", { for: `f-${parameter.name}` }, parameter.required ? el("b", { text: parameter.name })
    : parameter.name, parameter.required ? " (needed)" : "");
  const input = el("input", { type: "text", id: `f-${parameter.name}`, name: parameter.name, value: value || "",
    placeholder: parameter.required ? "" : parameter.default, autocomplete: "off", spellcheck: "false" });
  return el("div", { class: "aa-field" }, label, input);
}

function formValues(form, command) {
  const values = {};
  for (const parameter of command.parameters) {
    const input = form.elements[parameter.name];
    if (!input) continue;
    if (parameter.kind === "flag") {
      const isDefault = input.checked === (parameter.default === "True");
      values[parameter.name] = isDefault ? "" : String(input.checked);
    } else {
      values[parameter.name] = input.value;
    }
  }
  return values;
}

function drawCommand(main, service, command, route) {
  const fields = command.parameters.map((parameter) => fieldFor(parameter, route.args[parameter.name]));
  const callInput = el("input", { type: "text", "aria-label": "The call, as you'd write it in a notebook",
    spellcheck: "false", autocomplete: "off" });
  const copy = el("button", { class: "aa-ghost", type: "button", text: "Copy",
    onclick: () => navigator.clipboard?.writeText(`ui.${callInput.value}`) });
  const run = el("button", { class: "aa-run", type: "submit", text: "▶ Run" });
  const form = el("form", { class: "aa-form", autocomplete: "off" },
    fields.length ? el("div", { class: "aa-fields" }, fields) : null,
    el("div", { class: "aa-actions" }, run, el("div", { class: "aa-callbox" }, callInput, copy)));
  const result = el("div", { class: "aa-result" });
  let callEdited = false;

  const refreshCall = () => {
    if (!callEdited) callInput.value = callText(command, formValues(form, command));
  };
  form.addEventListener("input", (event) => {
    if (event.target === callInput) callEdited = true;
    else refreshCall();
  });
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    if (callEdited) {
      runCall(service, callInput.value, result);
      return;
    }
    const target = routeFor(service.key, command.name, { ...formValues(form, command), run: "1" });
    if (target === location.hash) runForm(service, command, route.args, result, true);
    else location.hash = target;
  });

  main.append(el("div", { class: "aa-head" },
    el("p", { class: "aa-crumbs" }, el("a", { href: "#/", text: "Services" }), " › ",
      el("a", { href: `#/${service.key}`, text: service.title }), " › ", command.group),
    el("h1", {}, code(`${command.name}${command.signature}`)),
    el("p", { class: "aa-lede", text: command.about }),
    command.doc.includes("\n\n") ? el("details", { class: "aa-doc" },
      el("summary", { text: "The whole description" }), el("pre", { text: command.doc })) : null),
  form, result);
  refreshCall();

  if (route.args.run) runForm(service, command, route.args, result, false);
}

// ---- running, and showing what came back

function showStatus(result, parts, tone = "") {
  let status = result.querySelector(".aa-status");
  if (!status) {
    status = el("div", { class: "aa-status" });
    result.prepend(status);
  }
  status.className = `aa-status${tone ? ` aa-${tone}` : ""}`;
  status.replaceChildren(...parts);
}

function waitFor(result, call) {
  const started = Date.now();
  const id = ++page.running;
  const seconds = el("span", { text: "" });
  showStatus(result, [el("span", { class: "aa-spin", "aria-hidden": "true" }), "Running ", code(call), " ", seconds]);
  result.querySelector(".aa-report")?.classList.add("aa-waiting");
  const timer = setInterval(() => {
    seconds.textContent = `${Math.round((Date.now() - started) / 1000)} s`;
  }, 1000);
  return { id, done: () => clearInterval(timer) };
}

function stepButtons(report, service) {
  const offered = new Set(service.commands.map((command) => command.name));
  for (const step of report.querySelectorAll(".next .ni code")) {
    const call = step.textContent.trim();
    const match = STEP_CALL.exec(call);
    if (!match || !offered.has(match[1])) continue;
    const button = el("button", { class: "aa-step-run", type: "button", text: "▶ Run", title: `Run ${call} here`,
      onclick: () => runCall(service, call, report.closest(".aa-result")) });
    let why = step.parentElement.querySelector(".nw");
    if (!why) {
      why = el("span", { class: "nw" });
      step.after(why);
    }
    why.prepend(button);
  }
}

function showReport(result, service, answer, earlier) {
  const report = el("div", { class: "aa-report" });
  report.innerHTML = answer.html;
  result.querySelector(".aa-report")?.remove();
  result.append(report);
  stepButtons(report, service);
  const again = el("button", { class: "aa-ghost", type: "button", text: "↻ Run again",
    onclick: () => runForm(service, null, null, result, true) });
  const when = earlier ? "shown from earlier" : `${answer.seconds} s`;
  showStatus(result, [code(answer.call), ` · ${when} · `, again]);
}

async function fetchReport(result, call, body) {
  const wait = waitFor(result, call);
  try {
    const answer = await api("api/run", body);
    return wait.id === page.running ? answer : null;
  } catch (error) {
    if (wait.id === page.running) {
      result.querySelector(".aa-report")?.classList.remove("aa-waiting");
      showStatus(result, [`Couldn't run ${call}: ${error.message}`], "bad");
    }
    return null;
  } finally {
    wait.done();
  }
}

async function runForm(service, command, args, result, force) {
  const route = parseRoute();
  const key = location.hash;
  command = command || service.commands.find((each) => each.name === route.command);
  args = args || route.args;
  if (!force && page.results.has(key)) {
    showReport(result, service, page.results.get(key), page.justRun !== key);
    page.justRun = "";
    return;
  }
  const values = Object.fromEntries(Object.entries(args).filter(([name]) => name !== "run"));
  const answer = await fetchReport(result, callText(command, values),
    { service: service.key, command: command.name, arguments: values });
  if (!answer) return;
  page.results.set(key, answer);
  showReport(result, service, answer, false);
}

async function runCall(service, call, result) {
  const answer = await fetchReport(result, call, { service: service.key, call });
  if (!answer) return;
  const target = routeFor(service.key, answer.command, { ...answer.arguments, run: "1" });
  page.results.set(target, answer);
  if (target === location.hash) {
    showReport(result, service, answer, false);
    return;
  }
  page.justRun = target;
  location.hash = target;
}

// ---- finding a command: Ctrl K

function matches(query) {
  const words = query.toLowerCase().split(/\s+/).filter(Boolean);
  const found = [];
  for (const service of page.catalog.services) {
    for (const command of service.commands) {
      const name = command.name.toLowerCase();
      const text = `${service.title} ${name} ${command.about}`.toLowerCase();
      if (!words.every((word) => text.includes(word))) continue;
      const inName = words.filter((word) => name.includes(word) || service.title.toLowerCase().includes(word));
      found.push({ service, command, score: inName.length * 10 - command.about.length / 1000 });
    }
  }
  return found.sort((a, b) => b.score - a.score).slice(0, MOST_FOUND);
}

function openFinder() {
  const finder = document.getElementById("palette");
  const input = document.getElementById("palette-input");
  const list = document.getElementById("palette-list");
  let picked = 0;
  let found = [];

  const go = (index) => {
    const hit = found[index];
    if (!hit) return;
    close();
    location.hash = routeFor(hit.service.key, hit.command.name);
  };
  const draw = () => {
    found = matches(input.value);
    picked = Math.min(picked, Math.max(found.length - 1, 0));
    list.replaceChildren(...found.map((hit, index) => el("li", { class: index === picked ? "aa-on" : "",
      role: "option", onclick: () => go(index) }, iconOf(hit.service),
    el("div", { class: "aa-t" }, code(hit.command.name), el("span", { text: ` · ${hit.service.title}` })),
    el("p", { text: hit.command.about }))));
    list.children[picked]?.scrollIntoView({ block: "nearest" });
  };
  const keys = (event) => {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      picked = Math.max(0, Math.min(found.length - 1, picked + (event.key === "ArrowDown" ? 1 : -1)));
      draw();
    } else if (event.key === "Enter") {
      go(picked);
    } else if (event.key === "Escape") {
      close();
    }
  };
  const typed = () => {
    picked = 0;
    draw();
  };
  const outside = (event) => {
    if (event.target === finder) close();
  };
  function close() {
    finder.hidden = true;
    input.removeEventListener("input", typed);
    input.removeEventListener("keydown", keys);
    finder.removeEventListener("click", outside);
  }

  finder.hidden = false;
  input.value = "";
  input.addEventListener("input", typed);
  input.addEventListener("keydown", keys);
  finder.addEventListener("click", outside);
  draw();
  input.focus();
}

// ---- putting it together

function draw() {
  const route = parseRoute();
  const main = document.getElementById("main");
  main.replaceChildren();
  drawSide(route);
  const service = page.services.get(route.service);
  const command = service?.commands.find((each) => each.name === route.command);
  if (command) drawCommand(main, service, command, route);
  else if (service) drawService(main, service);
  else drawHome(main);
  document.title = command ? `${command.name} · ${service.title} · aws-analyzer`
    : service ? `${service.title} · aws-analyzer` : "aws-analyzer";
}

function followTheme() {
  const dark = matchMedia("(prefers-color-scheme: dark)");
  const apply = () => { document.body.dataset.jpThemeLight = dark.matches ? "false" : "true"; };
  dark.addEventListener("change", apply);
  apply();
}

async function start() {
  followTheme();
  try {
    page.catalog = await api("api/catalog");
  } catch (error) {
    document.getElementById("main").replaceChildren(el("p", { class: "aa-loading",
      text: `Couldn't load the services: ${error.message}` }));
    return;
  }
  for (const service of page.catalog.services) page.services.set(service.key, service);
  const { region, profile } = page.catalog;
  document.getElementById("where").textContent =
    `Region: ${region || "boto3's default"}${profile ? ` · profile ${profile}` : ""} · read-only`;
  document.getElementById("find").addEventListener("click", openFinder);
  document.addEventListener("keydown", (event) => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
      event.preventDefault();
      openFinder();
    }
  });
  addEventListener("hashchange", draw);
  draw();
}

start();
