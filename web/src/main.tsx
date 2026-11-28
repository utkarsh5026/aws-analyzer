/**
 * aws-analyzer's page: pick a service and a command, fill in its form (or type the call), and the server runs it on
 * the same View a notebook uses and sends back the report's HTML. The address after # says what's open
 * (#/lambda_functions/errors?name=orders-etl&since=7d), so back, forward, reload and bookmarks work.
 */

import { render } from "preact";
import { api } from "./api/client";
import type { Catalog } from "./api/types";
import { catalog, loadError } from "./state";
import { App } from "./ui/App";
import "./styles/app.css";

/** Says the browser's light or dark the way JupyterLab does, which the reports' stylesheets read. */
function followTheme() {
  const dark = matchMedia("(prefers-color-scheme: dark)");
  const apply = () => {
    document.body.dataset.jpThemeLight = dark.matches ? "false" : "true";
  };
  dark.addEventListener("change", apply);
  apply();
}

async function loadCatalog() {
  try {
    catalog.value = await api<Catalog>("api/catalog");
  } catch (error) {
    loadError.value = error instanceof Error ? error.message : String(error);
  }
}

const root = document.getElementById("app");
if (root) {
  followTheme();
  root.replaceChildren();
  render(<App />, root);
  loadCatalog();
}
