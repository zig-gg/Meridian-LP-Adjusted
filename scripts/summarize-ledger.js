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
 *
 * Exports:
 *  - summarizeLedger()                         -> { ok, path, total, firstTs, lastTs,
 *                                                  byResult, byMode, byOutcome,
 *                                                  topCandidates, topReasons, last, error? }
 *  - formatLedgerSummary(summary, options?)    -> string
 *    options.compact (default false)           -> compact multi-line summary,
 *                                                  suitable for Telegram.
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
  if (entry.cycleId != null) parts.push(`cycleId=${entry.cycleId}`);
  if (entry.triggerSource != null) parts.push(`triggerSource=${entry.triggerSource}`);
  if (entry.outcome != null) parts.push(`outcome=${entry.outcome}`);
  if (entry.result != null) parts.push(`result=${entry.result}`);
  if (entry.mode != null) parts.push(`mode=${entry.mode}`);
  if (entry.durationMs != null) parts.push(`durationMs=${entry.durationMs}`);
  if (entry.bestCandidate) parts.push(`bestCandidate=${entry.bestCandidate}`);
  if (entry.reason) parts.push(`reason=${String(entry.reason).slice(0, 160)}`);

  // tokenRisk summary for last entry
  const tr = entry?.tokenRisk;
  if (tr && typeof tr === "object") {
    const status = tr.status || "UNKNOWN";
    const identity = tr.identity || {};
    const sym = identity.baseSymbol || "?";
    const mint = identity.baseMint ? identity.baseMint.slice(0, 8) + ".." : "?";
    const copycat = identity.copycatRisk ? " copycat" : "";
    parts.push(`tokenRisk=[${status}] ${sym} (${mint})${copycat}`);
    const reasons = (tr.reasons || []).slice(0, 3);
    const warnings = (tr.warnings || []).slice(0, 3);
    if (reasons.length > 0) parts.push(`reasons=${reasons.join("; ")}`);
    if (warnings.length > 0) parts.push(`warnings=${warnings.join("; ")}`);
  }

  return parts.join(" | ");
}

export function summarizeLedger(filePath = LEDGER_PATH) {
  const result = {
    ok: false,
    path: filePath,
    total: 0,
    firstTs: null,
    lastTs: null,
    byResult: [],
    byMode: [],
    byOutcome: [],
    topCandidates: [],
    topReasons: [],
    last: null,
  };

  if (!fs.existsSync(filePath)) {
    result.error = `Ledger file not found: ${filePath}`;
    return result;
  }

  let text;
  try {
    text = fs.readFileSync(filePath, "utf8");
  } catch (error) {
    result.error = `Failed to read ledger: ${error.message}`;
    return result;
  }

  const entries = parseEntries(text);
  result.total = entries.length;
  const total = result.total;
  result.firstTs = total > 0 ? entries[0]?.timestamp || null : null;
  result.lastTs = total > 0 ? entries[total - 1]?.timestamp || null : null;
  result.byResult = countBy(entries, "result");
  result.byMode = countBy(entries, "mode");
  result.byOutcome = countBy(entries, "outcome");
  result.topCandidates = topStrings(
    entries,
    (e) => e?.bestCandidate || e?.bestCandidatePool || null,
    TOP_N
  );
  result.topReasons = topStrings(entries, (e) => e?.reason || null, TOP_N);
  result.last = entries[entries.length - 1] || null;
  result.ok = true;
  return result;
}

export function formatLedgerSummary(summary, options = {}) {
  const compact = options.compact === true;
  const lines = [];
  const total = summary?.total ?? 0;

  if (summary?.error) {
    if (compact) return `📒 Ledger\n\n${summary.error}`;
    return `Ledger error: ${summary.error}`;
  }

  if (!summary || total === 0) {
    if (compact) return "📒 Ledger\n\nNo ledger entries found.";
    return "Decision Ledger Summary\nNo entries to summarize.";
  }

  const firstTs = summary.firstTs ?? "(none)";
  const lastTs = summary.lastTs ?? "(none)";
  const lastSummary = summarizeLast(summary.last);

  if (compact) {
    lines.push("📒 Decision Ledger");
    lines.push(`entries: ${total}`);
    lines.push(`first: ${firstTs}`);
    lines.push(`last:  ${lastTs}`);
    if (summary.byResult?.length) {
      lines.push("");
      lines.push("by result:");
      for (const [label, count] of summary.byResult.slice(0, TOP_N)) {
        lines.push(`  ${count}x ${label}`);
      }
    }
    if (summary.byMode?.length) {
      lines.push("");
      lines.push("by mode:");
      for (const [label, count] of summary.byMode.slice(0, TOP_N)) {
        lines.push(`  ${count}x ${label}`);
      }
    }
    if (summary.byOutcome?.length) {
      lines.push("");
      lines.push("by outcome:");
      for (const [label, count] of summary.byOutcome.slice(0, TOP_N)) {
        lines.push(`  ${count}x ${label}`);
      }
    }
    if (summary.topCandidates?.length) {
      lines.push("");
      lines.push("top candidates:");
      for (const [label, count] of summary.topCandidates) {
        lines.push(`  ${count}x ${label}`);
      }
    }
    if (summary.topReasons?.length) {
      lines.push("");
      lines.push("top reasons:");
      for (const [label, count] of summary.topReasons) {
        const trimmed = label.length > 80 ? label.slice(0, 77) + "..." : label;
        lines.push(`  ${count}x ${trimmed}`);
      }
    }
    if (lastSummary) {
      lines.push("");
      lines.push("last entry:");
      lines.push(`  ${lastSummary}`);
    }
    // tokenRisk from last entry
    const tr = summary.last?.tokenRisk;
    if (tr && typeof tr === "object") {
      lines.push("");
      lines.push("token risk:");
      const identity = tr.identity || {};
      const status = tr.status || "UNKNOWN";
      const sym = identity.baseSymbol || "?";
      const mint = identity.baseMint ? identity.baseMint.slice(0, 8) + ".." : "?";
      const copycat = identity.copycatRisk ? " copycat=true" : "";
      lines.push(`  status=${status} ${sym} (${mint})${copycat}`);
      const reasons = (tr.reasons || []).slice(0, 3);
      const warnings = (tr.warnings || []).slice(0, 3);
      if (reasons.length > 0) lines.push(`  reasons=${reasons.join("; ")}`);
      if (warnings.length > 0) lines.push(`  warnings=${warnings.join("; ")}`);
    }
    return lines.join("\n");
  }

  // Default: CLI-style multi-line table
  lines.push("Decision Ledger Summary");
  lines.push(`path: ${summary.path ?? "(unknown)"}`);
  lines.push(`total entries: ${total}`);
  lines.push(`first timestamp: ${firstTs}`);
  lines.push(`last timestamp:  ${lastTs}`);

  const block = (title, rows) => {
    lines.push("");
    lines.push(title);
    if (!rows || rows.length === 0) {
      lines.push("  (none)");
      return;
    }
    for (const [label, count] of rows) {
      lines.push(`  ${count.toString().padStart(4)}  ${label}`);
    }
  };
  block("count by result", summary.byResult);
  block("count by mode", summary.byMode);
  block("count by outcome", summary.byOutcome);
  block(`top bestCandidate names (top ${TOP_N})`, summary.topCandidates);
  block(`top reason strings (top ${TOP_N})`, summary.topReasons);
  lines.push("");
  lines.push("last entry summary");
  lines.push(lastSummary ? `  ${lastSummary}` : "  (none)");

  // tokenRisk from last entry (non-compact)
  const tr = summary.last?.tokenRisk;
  if (tr && typeof tr === "object") {
    lines.push("");
    lines.push("token risk (last entry):");
    const identity = tr.identity || {};
    const status = tr.status || "UNKNOWN";
    const sym = identity.baseSymbol || "?";
    const mint = identity.baseMint ? identity.baseMint.slice(0, 8) + ".." : "?";
    const copycat = identity.copycatRisk ? " copycat=true" : "";
    lines.push(`  status: ${status} | ${sym} (${mint})${copycat}`);
    const reasons = (tr.reasons || []).slice(0, 3);
    const warnings = (tr.warnings || []).slice(0, 3);
    if (reasons.length > 0) lines.push(`  reasons: ${reasons.join("; ")}`);
    if (warnings.length > 0) lines.push(`  warnings: ${warnings.join("; ")}`);
  }

  return lines.join("\n");
}

function main() {
  const summary = summarizeLedger();
  console.log(formatLedgerSummary(summary));
  if (summary.error) {
    console.log("\nNothing to summarize.");
  }
}

const isMain = (() => {
  try {
    return process.argv[1] && fileURLToPath(import.meta.url) === path.resolve(process.argv[1]);
  } catch {
    return false;
  }
})();

if (isMain) {
  main();
}
