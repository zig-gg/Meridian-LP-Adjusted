#!/usr/bin/env node
/**
 * scripts/summarize-ledger.js
 *
 * Read-only summary printer for data/decision-ledger.jsonl.
 * Reports total entries, first/last timestamps, counts by result and mode,
 * top bestCandidate names, top reason strings, and the last entry summary.
 *
 * Constraints:
 *  - No .env reads, no network, no exec/spawn.
 *  - Safe when the ledger file is missing or malformed.
 */

import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const LEDGER_PATH = path.join(
  path.dirname(fileURLToPath(import.meta.url)),
  "..",
  "data",
  "decision-ledger.jsonl"
);

const TOP_N = 5;

function parseEntries(text) {
  const entries = [];
  for (const raw of text.split("\n")) {
    const line = raw.trim();
    if (!line) continue;
    try {
      const obj = JSON.parse(line);
      if (obj && typeof obj === "object") entries.push(obj);
    } catch {
      // skip malformed lines silently
    }
  }
  return entries;
}

function countBy(entries, key) {
  const counts = new Map();
  for (const e of entries) {
    const v = e?.[key];
    const label = v == null || v === "" ? "(unset)" : String(v);
    counts.set(label, (counts.get(label) || 0) + 1);
  }
  return [...counts.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
}

function topStrings(entries, getter, n) {
  const counts = new Map();
  for (const e of entries) {
    const v = getter(e);
    if (v == null || v === "") continue;
    const key = String(v);
    counts.set(key, (counts.get(key) || 0) + 1);
  }
  return [...counts.entries()]
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .slice(0, n);
}

function summarizeLast(entry) {
  if (!entry) return null;
  const parts = [];
  if (entry.timestamp) parts.push(`timestamp=${entry.timestamp}`);
  if (entry.result != null) parts.push(`result=${entry.result}`);
  if (entry.mode != null) parts.push(`mode=${entry.mode}`);
  if (entry.bestCandidate) parts.push(`bestCandidate=${entry.bestCandidate}`);
  if (entry.reason) parts.push(`reason=${String(entry.reason).slice(0, 160)}`);
  return parts.join(" | ");
}

function printTable(title, rows) {
  console.log(`\n${title}`);
  if (rows.length === 0) {
    console.log("  (none)");
    return;
  }
  for (const [label, count] of rows) {
    console.log(`  ${count.toString().padStart(4)}  ${label}`);
  }
}

function main() {
  if (!fs.existsSync(LEDGER_PATH)) {
    console.log(`Ledger file not found: ${LEDGER_PATH}`);
    console.log("Nothing to summarize.");
    return;
  }

  let text;
  try {
    text = fs.readFileSync(LEDGER_PATH, "utf8");
  } catch (error) {
    console.log(`Failed to read ledger: ${error.message}`);
    return;
  }

  const entries = parseEntries(text);
  const total = entries.length;
  const firstTs = total > 0 ? entries[0]?.timestamp || null : null;
  const lastTs = total > 0 ? entries[total - 1]?.timestamp || null : null;

  console.log("Decision Ledger Summary");
  console.log(`path: ${LEDGER_PATH}`);
  console.log(`total entries: ${total}`);
  console.log(`first timestamp: ${firstTs ?? "(none)"}`);
  console.log(`last timestamp:  ${lastTs ?? "(none)"}`);

  printTable("count by result", countBy(entries, "result"));
  printTable("count by mode", countBy(entries, "mode"));

  const topCandidates = topStrings(
    entries,
    (e) => e?.bestCandidate || e?.bestCandidatePool || null,
    TOP_N
  );
  printTable(`top bestCandidate names (top ${TOP_N})`, topCandidates);

  const topReasons = topStrings(entries, (e) => e?.reason || null, TOP_N);
  printTable(`top reason strings (top ${TOP_N})`, topReasons);

  const last = entries[total - 1] || null;
  const lastSummary = summarizeLast(last);
  console.log("\nlast entry summary");
  console.log(lastSummary ? `  ${lastSummary}` : "  (none)");
}

main();
