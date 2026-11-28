/**
 * aws-analyzer's page: search one service at a time for the things in an account, open each in a tab, or pick a
 * service and a command, fill in its form (or type the call), and the server runs it on the same View a notebook uses.
 * The address after # says what's open (#/lambda_functions/errors?name=orders-etl&since=7d), so back, forward,
 * reload and bookmarks work.
 */

import { render } from "preact";
import { api } from "./api/client";
import type { Catalog } from "./api/types";
import { applyTheme, catalog, loadError } from "./state";
import { App } from "./ui/App";
import "./styles/app.css";

async function loadCatalog() {
  try {
    catalog.value = await api<Catalog>("api/catalog");
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
