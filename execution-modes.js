/**
 * execution-modes.js
 *
 * Execution mode definitions and hard safety gate for Phase 1.
 *
 * Modes:
 *   scanner  — scan/rank/report only, no on-chain action
 *   simulate — build intended action + dry-run/simulate, no broadcast
 *   paper    — record hypothetical action only (no simulation, no broadcast)
 *   live     — broadcast only if ALL hard gates pass
 *
 * Live execution requires ALL of:
 *   1. DRY_RUN !== "true"
 *   2. ALLOW_LIVE_EXECUTION === "true"
 *   3. executionMode === "live"
 *   4. BOT_WALLET_PRIVATE_KEY present in env (dedicated bot wallet)
 *   5. Wallet balance >= deployAmount + gasReserve
 *   6. Candidate passes risk policy (position size cap, maxPositions)
 *   7. approvalRequired === false OR Telegram approval token present
 *
 * If any condition fails, returns a structured blocked result — never broadcasts.
 */

import { config } from "./config.js";
import { log } from "./logger.js";

// ─── Mode constants ────────────────────────────────────────────
export const EXECUTION_MODES = Object.freeze({
  SCANNER:  "scanner",
  SIMULATE: "simulate",
  PAPER:    "paper",
  LIVE:     "live",
});

/**
 * Read the current execution mode from env (checked at call time, not import time).
 * Reads process.env.EXECUTION_MODE directly so tests can override it at runtime.
 * Falls back to config.execution.mode, then "scanner".
 */
export function getExecutionMode() {
  // Read from env first — allows runtime override (e.g. in tests)
  const envMode = process.env.EXECUTION_MODE;
  if (envMode) {
    const normalized = envMode.toLowerCase().trim();
    if (Object.values(EXECUTION_MODES).includes(normalized)) return normalized;
    log("execution_mode_warn", `Unknown EXECUTION_MODE "${envMode}" — defaulting to scanner`);
    return EXECUTION_MODES.SCANNER;
  }
  // Fall back to config (loaded at startup from user-config.json)
  const configMode = config?.execution?.mode;
  if (configMode) {
    const normalized = String(configMode).toLowerCase().trim();
    if (Object.values(EXECUTION_MODES).includes(normalized)) return normalized;
  }
  return EXECUTION_MODES.SCANNER;
}

/**
 * Check whether live execution is currently allowed.
 * Returns { allowed: true } or { allowed: false, reason: string }.
 *
 * This is a pure check — it does NOT broadcast anything.
 */
export function checkLiveExecutionAllowed() {
  const mode = getExecutionMode();

  // Gate 1: executionMode must be "live"
  if (mode !== EXECUTION_MODES.LIVE) {
    return {
      allowed: false,
      gate: "executionMode",
      reason: `executionMode is "${mode}", not "live". Set executionMode=live in user-config.json to enable live execution.`,
    };
  }

  // Gate 2: DRY_RUN must be false
  if (process.env.DRY_RUN === "true") {
    return {
      allowed: false,
      gate: "DRY_RUN",
      reason: "DRY_RUN=true is set. Unset DRY_RUN or set it to false to allow live execution.",
    };
  }

  // Gate 3: ALLOW_LIVE_EXECUTION must be explicitly true
  if (process.env.ALLOW_LIVE_EXECUTION !== "true") {
    return {
      allowed: false,
      gate: "ALLOW_LIVE_EXECUTION",
      reason: "ALLOW_LIVE_EXECUTION is not set to true. Add ALLOW_LIVE_EXECUTION=true to .env to enable live execution.",
    };
  }

  // Gate 4: Dedicated bot wallet key must be present
  // We accept either BOT_WALLET_PRIVATE_KEY (dedicated bot wallet) or WALLET_PRIVATE_KEY
  // but BOT_WALLET_PRIVATE_KEY is strongly preferred for isolation.
  const hasBotKey = !!process.env.BOT_WALLET_PRIVATE_KEY;
  const hasMainKey = !!process.env.WALLET_PRIVATE_KEY;
  if (!hasBotKey && !hasMainKey) {
    return {
      allowed: false,
      gate: "wallet_key",
      reason: "No wallet private key found. Set BOT_WALLET_PRIVATE_KEY in .env (dedicated bot wallet) to enable live execution.",
    };
  }

  return { allowed: true };
}

/**
 * Full pre-broadcast safety gate.
 *
 * @param {object} opts
 * @param {number}  opts.deployAmountSol   - SOL amount to deploy
 * @param {number}  opts.walletBalanceSol  - Current wallet SOL balance
 * @param {boolean} opts.approvalRequired  - Whether Telegram approval is required
 * @param {boolean} opts.approvalPresent   - Whether approval token was provided
 * @param {number}  opts.openPositions     - Current number of open positions
 * @param {object}  opts.candidate         - Candidate pool object (must have passed risk policy)
 *
 * Returns { pass: true } or { pass: false, gate: string, reason: string, blocked: true }
 */
export function runExecutionGate({
  deployAmountSol,
  walletBalanceSol,
  approvalRequired = true,
  approvalPresent = false,
  openPositions = 0,
  candidate = null,
} = {}) {
  // Gate 1-3: live execution allowed at all?
  const liveCheck = checkLiveExecutionAllowed();
  if (!liveCheck.allowed) {
    return {
      pass: false,
      blocked: true,
      gate: liveCheck.gate,
      reason: liveCheck.reason,
    };
  }

  // Gate 4: position count cap
  const maxPositions = config.risk?.maxPositions ?? 3;
  if (openPositions >= maxPositions) {
    return {
      pass: false,
      blocked: true,
      gate: "maxPositions",
      reason: `Max positions (${maxPositions}) reached. Close a position before deploying.`,
    };
  }

  // Gate 5: position size cap
  const maxDeployAmount = config.risk?.maxDeployAmount ?? 50;
  if (deployAmountSol > maxDeployAmount) {
    return {
      pass: false,
      blocked: true,
      gate: "maxDeployAmount",
      reason: `Deploy amount ${deployAmountSol} SOL exceeds maxDeployAmount ${maxDeployAmount} SOL.`,
    };
  }

  // Gate 6: wallet balance check
  const gasReserve = config.management?.gasReserve ?? 0.2;
  const required = deployAmountSol + gasReserve;
  if (walletBalanceSol < required) {
    return {
      pass: false,
      blocked: true,
      gate: "insufficient_balance",
      reason: `Insufficient SOL: have ${walletBalanceSol} SOL, need ${required} SOL (${deployAmountSol} deploy + ${gasReserve} gas reserve).`,
    };
  }

  // Gate 7: Telegram approval
  if (approvalRequired && !approvalPresent) {
    return {
      pass: false,
      blocked: true,
      gate: "approval_required",
      reason: "approvalRequired=true but no approval token was provided. Send approval via Telegram before live execution.",
    };
  }

  return { pass: true };
}

/**
 * Build a structured "blocked" result for a tool call that was prevented by the gate.
 * Safe to return directly from executeTool — never contains private key material.
 */
export function buildBlockedResult(gateResult, intentType = "deploy") {
  return {
    blocked: true,
    execution_mode: getExecutionMode(),
    gate: gateResult.gate,
    reason: gateResult.reason,
    intent_type: intentType,
    dry_run: process.env.DRY_RUN === "true",
    allow_live_execution: process.env.ALLOW_LIVE_EXECUTION === "true",
    message: `Live execution blocked by gate "${gateResult.gate}": ${gateResult.reason}`,
  };
}

/**
 * Build an execution intent object representing an intended action.
 * Used in simulate/paper modes to record what would have happened.
 *
 * @param {string} type  - ADD_LIQUIDITY | REMOVE_LIQUIDITY | CLAIM_FEES | CLOSE_POSITION
 * @param {object} params - Action-specific parameters
 * @returns {object} intent
 */
export function buildExecutionIntent(type, params = {}) {
  const VALID_TYPES = ["ADD_LIQUIDITY", "REMOVE_LIQUIDITY", "CLAIM_FEES", "CLOSE_POSITION"];
  if (!VALID_TYPES.includes(type)) {
    throw new Error(`Invalid intent type "${type}". Must be one of: ${VALID_TYPES.join(", ")}`);
  }

  return {
    intent_type: type,
    execution_mode: getExecutionMode(),
    created_at: new Date().toISOString(),
    params: {
      ...params,
      // Never include private key material in intent objects
      private_key: undefined,
      wallet_private_key: undefined,
      bot_wallet_private_key: undefined,
    },
    simulated: false,
    broadcast: false,
  };
}
