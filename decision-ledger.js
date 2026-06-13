import fs from "fs";
import { log } from "./logger.js";

const LEDGER_FILE = "./data/decision-ledger.jsonl";

/**
 * Append a scanner decision entry to the JSONL ledger.
 * Each record is a single JSON line - append-only, no whole-file rewrite.
 *
 * @param {Object} summary - The decision summary object
 * @returns {Object} The written record with timestamp
 */
export function appendDecisionLedger(summary) {
  const record = {
    timestamp: new Date().toISOString(),
    source: "scanner_cycle",
    ...summary,
  };

  // Ensure directory exists
  const dir = LEDGER_FILE.replace(/[/\\][^/\\]+$/, "");
  if (!fs.existsSync(dir)) {
    fs.mkdirSync(dir, { recursive: true });
  }

  try {
    const line = JSON.stringify(record) + "\n";
    fs.appendFileSync(LEDGER_FILE, line, "utf8");
    log("ledger", `Appended decision ledger entry: ${record.result} (${record.mode})`);
    return record;
  } catch (error) {
    log("ledger_error", `Failed to write decision ledger: ${error.message}`);
    // Don't throw - ledger failure shouldn't crash the cycle
    return null;
  }
}

/**
 * Get the last ledger write timestamp
 * @returns {string|null} ISO timestamp or null if no entries
 */
export function getLastLedgerWrite() {
  try {
    if (!fs.existsSync(LEDGER_FILE)) return null;
    const content = fs.readFileSync(LEDGER_FILE, "utf8");
    const lines = content.trim().split("\n").filter(Boolean);
    if (lines.length === 0) return null;
    const last = JSON.parse(lines[lines.length - 1]);
    return last.timestamp || null;
  } catch (error) {
    return null;
  }
}

/**
 * Get the path to the ledger file
 * @returns {string} File path
 */
export function getLedgerPath() {
  return LEDGER_FILE;
}

/**
 * Safe, read-only stats for the decision ledger.
 * Counts JSONL rows, returns the last timestamp, and never reads secrets or
 * calls the network. Used by read-only Telegram reporting.
 *
 * @returns {{enabled: boolean, path: string, count: number, lastWrite: string|null, lastMode: string|null, lastResult: string|null}}
 */
export function getLedgerStats() {
  const path = LEDGER_FILE;
  const base = { enabled: true, path, count: 0, lastWrite: null, lastMode: null, lastResult: null };
  try {
    if (!fs.existsSync(path)) return base;
    const content = fs.readFileSync(path, "utf8");
    const lines = content.split("\n").map((l) => l.trim()).filter(Boolean);
    if (lines.length === 0) return base;
    const last = JSON.parse(lines[lines.length - 1]);
    return {
      ...base,
      count: lines.length,
      lastWrite: last?.timestamp || null,
      lastMode: last?.mode || null,
      lastResult: last?.result || null,
    };
  } catch (error) {
    log("ledger_warn", `getLedgerStats failed: ${error.message}`);
    return base;
  }
}
