/**
 * Builds the page into the Python package, `src/aws_analyzer/server/static/dist/`, which is committed so a user's
 * `pip install` needs no Node. The server reads `manifest.json` to find the hashed files, and `sources.sha256` lets
 * tests/test_server.py tell when the build is older than `web/`.
 */

import { writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { defineConfig, type Plugin } from "vite";
import { sourceHash } from "./sources.ts";

const WEB = fileURLToPath(new URL(".", import.meta.url));
const DIST = fileURLToPath(new URL("../src/aws_analyzer/server/static/dist/", import.meta.url));

function writeSourceHash(): Plugin {
  return {
    name: "aws-analyzer-source-hash",
    apply: "build",
    closeBundle() {
      writeFileSync(`${DIST}sources.sha256`, `${sourceHash(WEB)}\n`);
    },
  };
}

export default defineConfig({
  root: WEB,
  publicDir: false,
  oxc: { jsx: { runtime: "automatic", importSource: "preact" } },
  build: {
    outDir: DIST,
    emptyOutDir: true,
    manifest: "manifest.json",
    modulePreload: false,
    target: "es2022",
    assetsInlineLimit: 4096,
    rollupOptions: { input: "src/main.tsx" },
  },
  plugins: [writeSourceHash()],
});
