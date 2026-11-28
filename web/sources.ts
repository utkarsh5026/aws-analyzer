/**
 * The fingerprint of what the build is made from, written beside the build as `sources.sha256`, so a test in Python
 * (tests/test_server.py) can tell, without Node, that `src/aws_analyzer/server/static/dist/` is older than `web/`.
 * Both sides hash the same files the same way: every file under `src/` but tests, and the build's own settings,
 * sorted by their path from `web/`, each as its path, a newline, the SHA-256 of its bytes (with Windows line endings
 * made Unix ones) and a newline.
 */

import { createHash } from "node:crypto";
import { readdirSync, readFileSync } from "node:fs";
import { join, relative, sep } from "node:path";

export const BUILD_SETTINGS = ["package.json", "package-lock.json", "tsconfig.json", "vite.config.ts", "sources.ts"];
const TEST_FILE = /\.test\.tsx?$/;

function filesUnder(folder: string): string[] {
  return readdirSync(folder, { withFileTypes: true }).flatMap((entry) => {
    const path = join(folder, entry.name);
    return entry.isDirectory() ? filesUnder(path) : [path];
  });
}

/** The paths, from `web/`, of the files the build is made from, sorted as the fingerprint takes them. */
export function sourceFiles(web: string): string[] {
  const sources = filesUnder(join(web, "src"))
    .map((path) => relative(web, path).split(sep).join("/"))
    .filter((path) => !TEST_FILE.test(path));
  return [...sources, ...BUILD_SETTINGS].sort();
}

/** The SHA-256 of a file's bytes, with Windows line endings read as Unix ones. */
function fileDigest(path: string): string {
  const unix = readFileSync(path).toString("latin1").replaceAll("\r\n", "\n");
  return createHash("sha256").update(Buffer.from(unix, "latin1")).digest("hex");
}

/** The fingerprint of `web/`'s sources, as hex. */
export function sourceHash(web: string): string {
  const hash = createHash("sha256");
  for (const path of sourceFiles(web)) hash.update(`${path}\n${fileDigest(join(web, path))}\n`);
  return hash.digest("hex");
}
