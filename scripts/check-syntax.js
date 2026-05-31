#!/usr/bin/env node
/**
 * scripts/check-syntax.js
 *
 * Cross-platform replacement for:
 *   find . -path ./node_modules -prune -o -name '*.js' -exec node --check {} \;
 *
 * Walks the repo, skips node_modules and .git, runs `node --check` on every .js file.
 * Exits 1 if any file fails; prints the failing file and error.
 */

import { readdirSync, statSync } from "fs";
import { join, relative } from "path";
import { execFileSync } from "child_process";
import { fileURLToPath } from "url";

const ROOT = join(fileURLToPath(import.meta.url), "..", "..");

const SKIP_DIRS = new Set(["node_modules", ".git", ".kiro", "defi_autonomy"]);

let checked = 0;
let failed = 0;

function walk(dir) {
  let entries;
  try {
    entries = readdirSync(dir);
  } catch {
    return;
  }
  for (const entry of entries) {
    if (SKIP_DIRS.has(entry)) continue;
    const full = join(dir, entry);
    let stat;
    try { stat = statSync(full); } catch { continue; }
    if (stat.isDirectory()) {
      walk(full);
    } else if (entry.endsWith(".js")) {
      checked++;
      try {
        execFileSync(process.execPath, ["--check", full], { stdio: "pipe" });
      } catch (err) {
        failed++;
        const rel = relative(ROOT, full);
        process.stderr.write(`FAIL: ${rel}\n${err.stderr?.toString() || err.message}\n\n`);
      }
    }
  }
}

walk(ROOT);

if (failed > 0) {
  process.stderr.write(`\nSyntax check: ${failed} file(s) failed out of ${checked} checked.\n`);
  process.exit(1);
} else {
  process.stdout.write(`Syntax check: ${checked} files OK\n`);
}
