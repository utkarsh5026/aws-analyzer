/** The bar along the top: the name and version, the finder's button and what the server reads. */

import { VERSION } from "../api/client";
import { catalog, finderOpen } from "../state";

function whereText(): string {
  if (!catalog.value) return "";
  const { region, profile } = catalog.value;
  return `Region: ${region || "boto3's default"}${profile ? ` · profile ${profile}` : ""} · read-only`;
}

export function Bar() {
  const openFinder = () => {
    if (catalog.value) finderOpen.value = true;
  };
  return (
    <header class="aa-bar">
      <a class="aa-brand" href="#/">
        <span class="aa-mark" aria-hidden="true" />
        aws-analyzer <span class="aa-ver">{VERSION}</span>
      </a>
      <button class="aa-find" type="button" id="find" aria-keyshortcuts="Control+K Meta+K" onClick={openFinder}>
        <span>Find a command…</span>
        <kbd>Ctrl K</kbd>
      </button>
      <span class="aa-where" id="where">
        {whereText()}
      </span>
    </header>
  );
}
