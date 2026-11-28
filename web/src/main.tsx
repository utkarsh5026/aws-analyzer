/**
 * aws-analyzer's page: search one service at a time for the things in an account, open each in a tab, or pick a
 * service and a command, fill in its form (or type the call), and the server runs it on the same View a notebook uses.
 * The address after # says what's open (#/lambda_functions/errors?name=orders-etl&since=7d), so back, forward,
 * reload and bookmarks work. Commands run as jobs the bar lists while they run, including the ones still running
 * from before a reload.
 */

import { render } from "preact";
import { api } from "./api/client";
import type { Catalog } from "./api/types";
import { followServerJobs } from "./jobs";
import { applyTheme, catalog, loadError } from "./state";
import { App } from "./ui/App";
import "./styles/app.css";

/** The catalog, once the jobs already running are known, so a command's page picks up its job rather than run it again. */
async function loadCatalog() {
  try {
    const [loaded] = await Promise.all([api<Catalog>("api/catalog"), followServerJobs().catch(() => undefined)]);
    catalog.value = loaded;
  } catch (error) {
    loadError.value = error instanceof Error ? error.message : String(error);
  }
}

const root = document.getElementById("app");
if (root) {
  applyTheme();
  root.replaceChildren();
  render(<App />, root);
  loadCatalog();
}
