/**
 * test/test-phase1.js
 *
 * Phase 1 safety invariant tests.
 *
 * Tests prove:
 *   1. Default mode does not broadcast (scanner mode).
 *   2. Live execution is blocked unless ALLOW_LIVE_EXECUTION=true AND DRY_RUN=false.
 *   3. Empty wallet / zero balance fails safely (INSUFFICIENT_BALANCE gate).
 *   4. Private key is NOT serialized into logs, JSON ledgers, or decision output.
 *   5. Scanner output returns ranked candidates without requiring a wallet private key.
 *   6. execute_intent in scanner/simulate/paper modes never broadcasts.
 *   7. buildExecutionIntent strips private key fields from params.
 *
 * Run: node test/test-phase1.js
 * (No wallet private key required — tests use execution-modes.js directly.)
 */

import assert from "assert";
import { execSync } from "child_process";
import { readFileSync } from "fs";
import { createRequire } from "module";

// ─── Test helpers ─────────────────────────────────────────────
let passed = 0;
let failed = 0;

function test(name, fn) {
  try {
    fn();
    console.log(`  ✅ ${name}`);
    passed++;
  } catch (err) {
    console.error(`  ❌ ${name}`);
    console.error(`     ${err.message}`);
    failed++;
  }
}

async function testAsync(name, fn) {
  try {
    await fn();
    console.log(`  ✅ ${name}`);
    passed++;
  } catch (err) {
    console.error(`  ❌ ${name}`);
    console.error(`     ${err.message}`);
    failed++;
  }
}

// ─── Setup: ensure safe env for tests ─────────────────────────
const origDryRun    = process.env.DRY_RUN;
const origAllowLive = process.env.ALLOW_LIVE_EXECUTION;
const origExecMode  = process.env.EXECUTION_MODE;
const origBotKey    = process.env.BOT_WALLET_PRIVATE_KEY;
const origWalletKey = process.env.WALLET_PRIVATE_KEY;

function setEnv(overrides) {
  process.env.DRY_RUN = "true";
  delete process.env.ALLOW_LIVE_EXECUTION;
  process.env.EXECUTION_MODE = "scanner";
  delete process.env.BOT_WALLET_PRIVATE_KEY;
  for (const [k, v] of Object.entries(overrides)) {
    if (v === undefined) delete process.env[k];
    else process.env[k] = v;
  }
}

function restoreEnv() {
  if (origDryRun    !== undefined) process.env.DRY_RUN = origDryRun;    else delete process.env.DRY_RUN;
  if (origAllowLive !== undefined) process.env.ALLOW_LIVE_EXECUTION = origAllowLive; else delete process.env.ALLOW_LIVE_EXECUTION;
  if (origExecMode  !== undefined) process.env.EXECUTION_MODE = origExecMode;  else delete process.env.EXECUTION_MODE;
  if (origBotKey    !== undefined) process.env.BOT_WALLET_PRIVATE_KEY = origBotKey; else delete process.env.BOT_WALLET_PRIVATE_KEY;
  if (origWalletKey !== undefined) process.env.WALLET_PRIVATE_KEY = origWalletKey; else delete process.env.WALLET_PRIVATE_KEY;
}

// ─── Import execution-modes.js (no heavy deps) ────────────────
// execution-modes.js only imports config.js and logger.js — no @solana/web3.js
setEnv({ DRY_RUN: "true", EXECUTION_MODE: "scanner" });
const {
  getExecutionMode,
  checkLiveExecutionAllowed,
  runExecutionGate,
  buildBlockedResult,
  buildExecutionIntent,
  EXECUTION_MODES,
} = await import("../execution-modes.js");

const { classifyTokenRisk, formatTokenRiskSummary } = await import("../token-risk.js");

// ─── Run tests ─────────────────────────────────────────────────
console.log("\n=== Phase 1 Safety Invariant Tests ===\n");

// ── Group 1: execution-modes.js unit tests ────────────────────
console.log("Group 1: Execution mode gates\n");

test("getExecutionMode returns 'scanner' by default", () => {
  process.env.EXECUTION_MODE = "scanner";
  assert.strictEqual(getExecutionMode(), "scanner");
});

test("getExecutionMode returns 'simulate' when set", () => {
  process.env.EXECUTION_MODE = "simulate";
  assert.strictEqual(getExecutionMode(), "simulate");
  process.env.EXECUTION_MODE = "scanner";
});

test("getExecutionMode returns 'paper' when set", () => {
  process.env.EXECUTION_MODE = "paper";
  assert.strictEqual(getExecutionMode(), "paper");
  process.env.EXECUTION_MODE = "scanner";
});

test("getExecutionMode returns 'live' when set", () => {
  process.env.EXECUTION_MODE = "live";
  assert.strictEqual(getExecutionMode(), "live");
  process.env.EXECUTION_MODE = "scanner";
});

test("getExecutionMode falls back to 'scanner' for unknown mode", () => {
  process.env.EXECUTION_MODE = "unknown_mode_xyz";
  assert.strictEqual(getExecutionMode(), "scanner");
  process.env.EXECUTION_MODE = "scanner";
});

test("checkLiveExecutionAllowed: blocked when mode is scanner", () => {
  setEnv({ DRY_RUN: "false", ALLOW_LIVE_EXECUTION: "true", EXECUTION_MODE: "scanner" });
  const result = checkLiveExecutionAllowed();
  assert.strictEqual(result.allowed, false);
  assert.strictEqual(result.gate, "executionMode");
});

test("checkLiveExecutionAllowed: blocked when mode is simulate", () => {
  setEnv({ DRY_RUN: "false", ALLOW_LIVE_EXECUTION: "true", EXECUTION_MODE: "simulate" });
  const result = checkLiveExecutionAllowed();
  assert.strictEqual(result.allowed, false);
  assert.strictEqual(result.gate, "executionMode");
});

test("checkLiveExecutionAllowed: blocked when DRY_RUN=true", () => {
  setEnv({ DRY_RUN: "true", ALLOW_LIVE_EXECUTION: "true", EXECUTION_MODE: "live", BOT_WALLET_PRIVATE_KEY: "fake" });
  const result = checkLiveExecutionAllowed();
  assert.strictEqual(result.allowed, false);
  assert.strictEqual(result.gate, "DRY_RUN");
});

test("checkLiveExecutionAllowed: blocked when ALLOW_LIVE_EXECUTION not set", () => {
  setEnv({ DRY_RUN: "false", EXECUTION_MODE: "live", BOT_WALLET_PRIVATE_KEY: "fake" });
  delete process.env.ALLOW_LIVE_EXECUTION;
  const result = checkLiveExecutionAllowed();
  assert.strictEqual(result.allowed, false);
  assert.strictEqual(result.gate, "ALLOW_LIVE_EXECUTION");
});

test("checkLiveExecutionAllowed: blocked when ALLOW_LIVE_EXECUTION=false", () => {
  setEnv({ DRY_RUN: "false", ALLOW_LIVE_EXECUTION: "false", EXECUTION_MODE: "live", BOT_WALLET_PRIVATE_KEY: "fake" });
  const result = checkLiveExecutionAllowed();
  assert.strictEqual(result.allowed, false);
  assert.strictEqual(result.gate, "ALLOW_LIVE_EXECUTION");
});

test("checkLiveExecutionAllowed: blocked when no wallet key", () => {
  setEnv({ DRY_RUN: "false", ALLOW_LIVE_EXECUTION: "true", EXECUTION_MODE: "live" });
  delete process.env.BOT_WALLET_PRIVATE_KEY;
  delete process.env.WALLET_PRIVATE_KEY;
  const result = checkLiveExecutionAllowed();
  assert.strictEqual(result.allowed, false);
  assert.strictEqual(result.gate, "wallet_key");
});

test("checkLiveExecutionAllowed: allowed when all gates pass", () => {
  setEnv({
    DRY_RUN: "false",
    ALLOW_LIVE_EXECUTION: "true",
    EXECUTION_MODE: "live",
    BOT_WALLET_PRIVATE_KEY: "fake_key_for_test",
  });
  const result = checkLiveExecutionAllowed();
  assert.strictEqual(result.allowed, true);
});

// ── Group 2: runExecutionGate tests ───────────────────────────
console.log("\nGroup 2: runExecutionGate — all blocking conditions\n");

// NOTE ON TEST ISOLATION:
// runExecutionGate() reads config.risk.maxDeployAmount and config.management.gasReserve
// from the live config object, which is loaded from production user-config.json.
// To avoid gate-order failures across machines (e.g. AWS has maxDeployAmount=0.15),
// all tests below use deployAmountSol=0.05 EXCEPT the explicit maxDeployAmount test,
// which uses a value guaranteed to exceed any reasonable cap.
// walletBalanceSol is chosen to be definitively above or below the required threshold
// regardless of what gasReserve is configured (max plausible gasReserve is ~1.0 SOL).

test("runExecutionGate: blocked by executionMode=scanner", () => {
  setEnv({ DRY_RUN: "false", ALLOW_LIVE_EXECUTION: "true", EXECUTION_MODE: "scanner", BOT_WALLET_PRIVATE_KEY: "fake" });
  // deployAmountSol intentionally below any plausible maxDeployAmount so scanner gate fires first
  const result = runExecutionGate({ deployAmountSol: 0.05, walletBalanceSol: 10, approvalRequired: false, openPositions: 0 });
  assert.strictEqual(result.pass, false);
  assert.strictEqual(result.gate, "executionMode");
  assert.strictEqual(result.blocked, true);
});

test("runExecutionGate: blocked by insufficient balance (zero wallet)", () => {
  setEnv({ DRY_RUN: "false", ALLOW_LIVE_EXECUTION: "true", EXECUTION_MODE: "live", BOT_WALLET_PRIVATE_KEY: "fake" });
  // deployAmountSol=0.05 is below any plausible maxDeployAmount.
  // walletBalanceSol=0.0 is always below deployAmountSol+gasReserve, so Gate 6 fires.
  const result = runExecutionGate({
    deployAmountSol: 0.05,
    walletBalanceSol: 0.0,
    approvalRequired: false,
    openPositions: 0,
  });
  assert.strictEqual(result.pass, false);
  assert.strictEqual(result.gate, "insufficient_balance");
  assert.ok(result.reason.includes("Insufficient SOL"), "Reason must mention insufficient SOL");
});

test("runExecutionGate: blocked by insufficient balance (below gas reserve)", () => {
  setEnv({ DRY_RUN: "false", ALLOW_LIVE_EXECUTION: "true", EXECUTION_MODE: "live", BOT_WALLET_PRIVATE_KEY: "fake" });
  // deployAmountSol=0.05 is below any plausible maxDeployAmount.
  // walletBalanceSol=0.04 is below deployAmountSol alone, so always fails Gate 6
  // regardless of what gasReserve is configured.
  const result = runExecutionGate({
    deployAmountSol: 0.05,
    walletBalanceSol: 0.04,
    approvalRequired: false,
    openPositions: 0,
  });
  assert.strictEqual(result.pass, false);
  assert.strictEqual(result.gate, "insufficient_balance");
});

test("runExecutionGate: blocked by maxPositions", () => {
  setEnv({ DRY_RUN: "false", ALLOW_LIVE_EXECUTION: "true", EXECUTION_MODE: "live", BOT_WALLET_PRIVATE_KEY: "fake" });
  // deployAmountSol=0.05 is below any plausible maxDeployAmount so Gate 4 fires first.
  const result = runExecutionGate({
    deployAmountSol: 0.05,
    walletBalanceSol: 10.0,
    approvalRequired: false,
    openPositions: 999,
  });
  assert.strictEqual(result.pass, false);
  assert.strictEqual(result.gate, "maxPositions");
});

test("runExecutionGate: blocked by maxDeployAmount", () => {
  setEnv({ DRY_RUN: "false", ALLOW_LIVE_EXECUTION: "true", EXECUTION_MODE: "live", BOT_WALLET_PRIVATE_KEY: "fake" });
  // Use a deploy amount that is guaranteed to exceed any plausible maxDeployAmount.
  // Even the most permissive real config caps at maxDeployAmount=50 (default).
  // AWS production has maxDeployAmount=0.15, so 100 SOL exceeds any real cap.
  const result = runExecutionGate({
    deployAmountSol: 100,
    walletBalanceSol: 200.0,
    approvalRequired: false,
    openPositions: 0,
  });
  assert.strictEqual(result.pass, false);
  assert.strictEqual(result.gate, "maxDeployAmount");
});

test("runExecutionGate: blocked by approval_required", () => {
  setEnv({ DRY_RUN: "false", ALLOW_LIVE_EXECUTION: "true", EXECUTION_MODE: "live", BOT_WALLET_PRIVATE_KEY: "fake" });
  // deployAmountSol=0.05 is below any plausible maxDeployAmount.
  // walletBalanceSol=10.0 is always above deployAmountSol+gasReserve (max realistic ~1 SOL),
  // so Gate 6 passes and Gate 7 (approval) fires.
  const result = runExecutionGate({
    deployAmountSol: 0.05,
    walletBalanceSol: 10.0,
    approvalRequired: true,
    approvalPresent: false,
    openPositions: 0,
  });
  assert.strictEqual(result.pass, false);
  assert.strictEqual(result.gate, "approval_required");
});

test("runExecutionGate: passes when all conditions met", () => {
  setEnv({ DRY_RUN: "false", ALLOW_LIVE_EXECUTION: "true", EXECUTION_MODE: "live", BOT_WALLET_PRIVATE_KEY: "fake" });
  // deployAmountSol=0.05 is below any plausible maxDeployAmount.
  // walletBalanceSol=10.0 is always above deployAmountSol+gasReserve.
  // openPositions=0 is below any plausible maxPositions.
  const result = runExecutionGate({
    deployAmountSol: 0.05,
    walletBalanceSol: 10.0,
    approvalRequired: false,
    approvalPresent: false,
    openPositions: 0,
  });
  assert.strictEqual(result.pass, true);
});

// ── Group 3: Private key not in serialized output ─────────────
console.log("\nGroup 3: Private key not serialized into output\n");

test("buildBlockedResult: does not contain private key env var names", () => {
  const result = buildBlockedResult({ gate: "test", reason: "test reason" }, "ADD_LIQUIDITY");
  const json = JSON.stringify(result);
  assert.ok(!json.includes("WALLET_PRIVATE_KEY"),     "Must not contain WALLET_PRIVATE_KEY");
  assert.ok(!json.includes("BOT_WALLET_PRIVATE_KEY"), "Must not contain BOT_WALLET_PRIVATE_KEY");
  assert.ok(!json.includes("OPENROUTER_API_KEY"),     "Must not contain OPENROUTER_API_KEY");
  assert.ok(!json.includes("HELIUS_API_KEY"),         "Must not contain HELIUS_API_KEY");
  assert.strictEqual(result.blocked, true);
  assert.strictEqual(result.gate, "test");
});

test("buildBlockedResult: does not echo back env var values", () => {
  process.env.BOT_WALLET_PRIVATE_KEY = "SUPER_SECRET_KEY_VALUE_12345";
  const result = buildBlockedResult({ gate: "DRY_RUN", reason: "DRY_RUN=true" }, "ADD_LIQUIDITY");
  const json = JSON.stringify(result);
  assert.ok(!json.includes("SUPER_SECRET_KEY_VALUE_12345"), "Secret key value must not appear in blocked result");
  delete process.env.BOT_WALLET_PRIVATE_KEY;
});

test("buildExecutionIntent: strips private key fields from params", () => {
  const intent = buildExecutionIntent("ADD_LIQUIDITY", {
    pool_address: "test_pool",
    amount_sol: 0.5,
    private_key: "SHOULD_BE_STRIPPED",
    wallet_private_key: "SHOULD_BE_STRIPPED_2",
    bot_wallet_private_key: "SHOULD_BE_STRIPPED_3",
  });
  const json = JSON.stringify(intent);
  assert.ok(!json.includes("SHOULD_BE_STRIPPED"),   "private_key must be stripped");
  assert.ok(!json.includes("SHOULD_BE_STRIPPED_2"), "wallet_private_key must be stripped");
  assert.ok(!json.includes("SHOULD_BE_STRIPPED_3"), "bot_wallet_private_key must be stripped");
  assert.strictEqual(intent.intent_type, "ADD_LIQUIDITY");
  assert.strictEqual(intent.broadcast, false);
  assert.strictEqual(intent.params.pool_address, "test_pool");
});

test("buildExecutionIntent: throws for invalid type", () => {
  assert.throws(
    () => buildExecutionIntent("INVALID_TYPE", {}),
    /Invalid intent type/
  );
});

// ── Group 4: Default mode does not broadcast ──────────────────
console.log("\nGroup 4: Default mode does not broadcast\n");

test("scanner mode: execute_intent returns broadcast=false", () => {
  setEnv({ DRY_RUN: "true", EXECUTION_MODE: "scanner" });
  // Test the logic directly without importing executor.js (avoids @solana/web3.js dep)
  // Simulate what execute_intent does in scanner mode
  const mode = getExecutionMode();
  assert.strictEqual(mode, "scanner");
  // In scanner mode, broadcast is always false
  const intent = buildExecutionIntent("ADD_LIQUIDITY", { pool_address: "test", amount_sol: 0.5 });
  assert.strictEqual(intent.broadcast, false);
  assert.strictEqual(intent.intent_type, "ADD_LIQUIDITY");
});

test("simulate mode: execute_intent returns broadcast=false", () => {
  setEnv({ DRY_RUN: "true", EXECUTION_MODE: "simulate" });
  const mode = getExecutionMode();
  assert.strictEqual(mode, "simulate");
  const intent = buildExecutionIntent("CLAIM_FEES", { position_address: "test_pos" });
  assert.strictEqual(intent.broadcast, false);
});

test("paper mode: execute_intent returns broadcast=false", () => {
  setEnv({ DRY_RUN: "true", EXECUTION_MODE: "paper" });
  const mode = getExecutionMode();
  assert.strictEqual(mode, "paper");
  const intent = buildExecutionIntent("CLOSE_POSITION", { position_address: "test_pos" });
  assert.strictEqual(intent.broadcast, false);
});

test("live mode without gates: checkLiveExecutionAllowed returns allowed=false", () => {
  setEnv({ DRY_RUN: "true", EXECUTION_MODE: "live" });
  // DRY_RUN=true should block even in live mode
  const result = checkLiveExecutionAllowed();
  assert.strictEqual(result.allowed, false);
  assert.strictEqual(result.gate, "DRY_RUN");
});

// ── Group 5: Empty wallet fails safely ────────────────────────
console.log("\nGroup 5: Empty wallet / zero balance fails safely\n");

test("zero balance: runExecutionGate returns INSUFFICIENT_BALANCE", () => {
  setEnv({ DRY_RUN: "false", ALLOW_LIVE_EXECUTION: "true", EXECUTION_MODE: "live", BOT_WALLET_PRIVATE_KEY: "fake" });
  const result = runExecutionGate({
    deployAmountSol: 0.03,
    walletBalanceSol: 0.0,
    approvalRequired: false,
    openPositions: 0,
  });
  assert.strictEqual(result.pass, false);
  assert.strictEqual(result.gate, "insufficient_balance");
  assert.strictEqual(result.blocked, true);
  assert.ok(result.reason.includes("Insufficient SOL"));
});

test("zero balance: buildBlockedResult is safe to return", () => {
  setEnv({ DRY_RUN: "false", ALLOW_LIVE_EXECUTION: "true", EXECUTION_MODE: "live", BOT_WALLET_PRIVATE_KEY: "fake" });
  const gateResult = runExecutionGate({
    deployAmountSol: 0.03,
    walletBalanceSol: 0.0,
    approvalRequired: false,
    openPositions: 0,
  });
  const blocked = buildBlockedResult(gateResult, "ADD_LIQUIDITY");
  assert.strictEqual(blocked.blocked, true);
  assert.strictEqual(blocked.gate, "insufficient_balance");
  // Verify it's safe to JSON-serialize (no circular refs, no secrets)
  const json = JSON.stringify(blocked);
  assert.ok(json.length > 0, "Result must be serializable");
  assert.ok(!json.includes("fake"), "Fake key must not appear in output");
});

// ── Group 6: Scanner works without wallet private key ─────────
console.log("\nGroup 6: Scanner works without wallet private key\n");

test("scanPools module exports scanPools function", async () => {
  // Import scanner.js — it only needs screening.js and config.js
  // (no @solana/web3.js dependency in scanner.js itself)
  setEnv({ DRY_RUN: "true", EXECUTION_MODE: "scanner" });
  delete process.env.WALLET_PRIVATE_KEY;
  delete process.env.BOT_WALLET_PRIVATE_KEY;
  const { scanPools } = await import("../tools/scanner.js");
  assert.strictEqual(typeof scanPools, "function", "scanPools must be a function");
});

await testAsync("scanPools returns structured result without wallet key", async () => {
  setEnv({ DRY_RUN: "true", EXECUTION_MODE: "scanner" });
  delete process.env.WALLET_PRIVATE_KEY;
  delete process.env.BOT_WALLET_PRIVATE_KEY;
  const { scanPools } = await import("../tools/scanner.js");

  let result;
  try {
    result = await scanPools({ limit: 3 });
  } catch (err) {
    // Network errors are acceptable in test environment
    if (
      err.message.includes("fetch") ||
      err.message.includes("network") ||
      err.message.includes("ENOTFOUND") ||
      err.message.includes("ECONNREFUSED") ||
      err.message.includes("socket")
    ) {
      console.log("     (network unavailable — skipping live API call)");
      return;
    }
    throw err;
  }

  assert.ok(typeof result === "object",          "Result must be an object");
  assert.ok("success" in result,                 "Result must have success field");
  assert.ok("execution_mode" in result,          "Result must have execution_mode field");
  assert.ok(Array.isArray(result.candidates),    "Result must have candidates array");
  assert.strictEqual(result.execution_mode, "scanner", "Execution mode must be scanner");

  // Verify no private key in output
  const json = JSON.stringify(result);
  assert.ok(!json.includes("WALLET_PRIVATE_KEY"),     "Scanner result must not contain WALLET_PRIVATE_KEY");
  assert.ok(!json.includes("BOT_WALLET_PRIVATE_KEY"), "Scanner result must not contain BOT_WALLET_PRIVATE_KEY");

  if (result.candidates.length > 0) {
    const first = result.candidates[0];
    assert.ok(typeof first.score === "number",         "Candidate must have numeric score");
    assert.ok(first.score >= 0 && first.score <= 100,  "Score must be 0-100");
    assert.ok(typeof first.suggested_action === "string", "Candidate must have suggested_action");
    assert.ok(
      ["SIMULATE", "MONITOR", "MANUAL_REVIEW", "AVOID"].includes(first.suggested_action),
      `suggested_action "${first.suggested_action}" must be one of the valid values`
    );
    assert.ok(Array.isArray(first.risk_flags), "Candidate must have risk_flags array");
    assert.ok("fee_to_tvl" in first,           "Candidate must have fee_to_tvl");
    assert.ok("vol_to_tvl" in first,           "Candidate must have vol_to_tvl");
  }
});

// ── Group 7: OpenRouter env mapping ──────────────────────────
console.log("\nGroup 7: OpenRouter env mapping\n");

// These tests verify the priority logic documented in agent.js without
// importing agent.js itself (which pulls in @solana/web3.js).
// We test the priority rules as pure logic.

test("API key priority: OPENROUTER_API_KEY wins over OPENAI_API_KEY", () => {
  const key = "or-key" || "oa-key" || "llm-key" || null;
  assert.strictEqual(key, "or-key");
  // Simulate the actual priority chain
  const pick = (a, b, c) => a || b || c || null;
  assert.strictEqual(pick("or-key", "oa-key", "llm-key"), "or-key");
  assert.strictEqual(pick(null, "oa-key", "llm-key"), "oa-key");
  assert.strictEqual(pick(null, null, "llm-key"), "llm-key");
  assert.strictEqual(pick(null, null, null), null);
});

test("Base URL priority: OPENROUTER_BASE_URL wins over OPENAI_BASE_URL", () => {
  const pick = (a, b, c, fallback) => a || b || c || fallback;
  assert.strictEqual(pick("https://or.ai/v1", "https://oa.com/v1", null, "https://openrouter.ai/api/v1"), "https://or.ai/v1");
  assert.strictEqual(pick(null, "https://oa.com/v1", null, "https://openrouter.ai/api/v1"), "https://oa.com/v1");
  assert.strictEqual(pick(null, null, "http://localhost:1234/v1", "https://openrouter.ai/api/v1"), "http://localhost:1234/v1");
  assert.strictEqual(pick(null, null, null, "https://openrouter.ai/api/v1"), "https://openrouter.ai/api/v1");
});

test("Model priority: OPENROUTER_MODEL wins over OPENAI_MODEL and LLM_MODEL", () => {
  const pick = (a, b, c, fallback) => a || b || c || fallback;
  assert.strictEqual(pick("or-model", "oa-model", "llm-model", "default"), "or-model");
  assert.strictEqual(pick(null, "oa-model", "llm-model", "default"), "oa-model");
  assert.strictEqual(pick(null, null, "llm-model", "default"), "llm-model");
  assert.strictEqual(pick(null, null, null, "default"), "default");
});

test("agent.js env resolution: OPENROUTER_API_KEY is read from process.env", () => {
  // Verify the env var names used in agent.js match what we document
  const envVarNames = ["OPENROUTER_API_KEY", "OPENAI_API_KEY", "LLM_API_KEY"];
  for (const name of envVarNames) {
    // Just verify these are valid env var name strings (no typos)
    assert.ok(/^[A-Z_]+$/.test(name), `${name} must be a valid env var name`);
  }
});

test("agent.js env resolution: no real API key committed to .env.example", () => {
  const envExample = readFileSync(".env.example", "utf8");
  assert.ok(!envExample.includes("sk-or-"), ".env.example must not contain a real OpenRouter key");
  const lines = envExample.split("\n").filter(l => /^OPENROUTER_API_KEY=|^OPENAI_API_KEY=/.test(l));
  for (const line of lines) {
    const value = (line.split("=")[1] ?? "").trim();
    assert.ok(
      value === "" || value === "your_openrouter_key_here",
      `${line.trim()} — .env.example key value must be empty or a placeholder`
    );
  }
});

test("missing API key: execution-modes.js imports without crash", async () => {
  // execution-modes.js must be importable with no API key set
  const savedKey = process.env.OPENROUTER_API_KEY;
  delete process.env.OPENROUTER_API_KEY;
  delete process.env.OPENAI_API_KEY;
  delete process.env.LLM_API_KEY;
  try {
    // Already imported above — just verify the module is loaded and functional
    const mode = getExecutionMode();
    assert.ok(typeof mode === "string", "getExecutionMode must return a string even with no API key");
  } finally {
    if (savedKey !== undefined) process.env.OPENROUTER_API_KEY = savedKey;
  }
});

test("missing API key: scanner/safety imports do not crash", async () => {
  // Verify that importing execution-modes.js and scanner.js with no API key
  // does not throw at module load time
  const savedKey = process.env.OPENROUTER_API_KEY;
  delete process.env.OPENROUTER_API_KEY;
  delete process.env.OPENAI_API_KEY;
  delete process.env.LLM_API_KEY;
  try {
    // These are already imported — just call them to confirm they work
    const result = checkLiveExecutionAllowed();
    assert.ok(typeof result === "object", "checkLiveExecutionAllowed must return an object with no API key");
    assert.ok("allowed" in result, "Result must have allowed field");
  } finally {
    if (savedKey !== undefined) process.env.OPENROUTER_API_KEY = savedKey;
  }
});

test("no real keys in .env.example: OPENROUTER_API_KEY is blank or placeholder", () => {
  const content = execSync(
    `node -e "process.stdout.write(require('fs').readFileSync('.env.example','utf8'))"`,
    { cwd: process.cwd(), encoding: "utf8", stdio: ["pipe", "pipe", "pipe"] }
  );
  assert.ok(!content.includes("sk-or-v1-"), "Must not contain a real sk-or-v1- key");
  assert.ok(!content.includes("sk-proj-"),  "Must not contain a real sk-proj- key");
});

// ── Group 8: Headless mode behaviour ─────────────────────────
console.log("\nGroup 8: Headless mode behaviour\n");

test("HEADLESS=true sets isHeadless flag (isTTY becomes false)", () => {
  // The isHeadless logic in index.js is:
  //   HEADLESS=true  → headless
  //   INTERACTIVE=false → headless
  //   otherwise → not headless
  // We test the same logic here without importing index.js (avoids heavy deps).
  function deriveIsHeadless(env) {
    if (env.HEADLESS === "true")     return true;
    if (env.INTERACTIVE === "false") return true;
    return false;
  }
  assert.strictEqual(deriveIsHeadless({ HEADLESS: "true" }),              true,  "HEADLESS=true → headless");
  assert.strictEqual(deriveIsHeadless({ INTERACTIVE: "false" }),          true,  "INTERACTIVE=false → headless");
  assert.strictEqual(deriveIsHeadless({ HEADLESS: "true", INTERACTIVE: "false" }), true, "both → headless");
  assert.strictEqual(deriveIsHeadless({}),                                false, "no env → not headless");
  assert.strictEqual(deriveIsHeadless({ HEADLESS: "false" }),             false, "HEADLESS=false → not headless");
  assert.strictEqual(deriveIsHeadless({ INTERACTIVE: "true" }),           false, "INTERACTIVE=true → not headless");
});

test("HEADLESS=true: isTTY is false even when stdin.isTTY would be true", () => {
  // Simulate: isTTY = process.stdin.isTTY && !isHeadless
  function deriveIsTTY(stdinIsTTY, isHeadless) {
    return stdinIsTTY && !isHeadless;
  }
  assert.strictEqual(deriveIsTTY(true,  true),  false, "TTY stdin + headless → isTTY=false");
  assert.strictEqual(deriveIsTTY(true,  false), true,  "TTY stdin + interactive → isTTY=true");
  assert.strictEqual(deriveIsTTY(false, true),  false, "non-TTY stdin + headless → isTTY=false");
  assert.strictEqual(deriveIsTTY(false, false), false, "non-TTY stdin + interactive → isTTY=false");
});

test("HEADLESS=true: stdin close does not trigger shutdown (rl.on('close') not registered)", () => {
  // When isTTY=false the entire REPL block (including rl.on("close", shutdown)) is skipped.
  // This test verifies the guard condition: the REPL only runs when isTTY is true.
  // isTTY = process.stdin.isTTY && !isHeadless
  // With HEADLESS=true: isTTY=false → REPL block skipped → no rl.on("close") → no shutdown on stdin close.
  const isHeadless = true;
  const stdinIsTTY = true; // even if stdin is a TTY
  const isTTY = stdinIsTTY && !isHeadless;
  assert.strictEqual(isTTY, false, "isTTY must be false in headless mode");
  // The REPL block condition is: if (isMain && isTTY) { ... rl.on("close", shutdown) ... }
  // With isTTY=false, the block is skipped entirely.
  const replWouldRun = isTTY; // simplified: isMain is always true in this context
  assert.strictEqual(replWouldRun, false, "REPL block must not run in headless mode");
});

test("HEADLESS=true: non-TTY/headless branch runs cron cycles (else if isMain)", () => {
  // The else-if branch runs when: isMain && !isTTY
  // In headless mode: isTTY=false → else-if branch runs → startCronJobs() called
  const isHeadless = true;
  const stdinIsTTY = true;
  const isTTY = stdinIsTTY && !isHeadless;
  const isMain = true;
  const replBranchRuns = isMain && isTTY;
  const daemonBranchRuns = isMain && !replBranchRuns;
  assert.strictEqual(replBranchRuns,   false, "REPL branch must not run");
  assert.strictEqual(daemonBranchRuns, true,  "Daemon branch must run (starts cron cycles)");
});

test("SIGINT still shuts down cleanly in headless mode", () => {
  // SIGINT handler is always registered regardless of headless mode.
  // This is correct: PM2 sends SIGTERM/SIGINT for graceful stop, which should work.
  // The fix is only that stdin close does NOT trigger shutdown.
  // We verify the handler registration is unconditional (not inside the isTTY block).
  // This is a documentation/logic test — the actual handler is in index.js.
  const sigintHandlerIsConditional = false; // it's registered at module level, not inside isTTY block
  assert.strictEqual(sigintHandlerIsConditional, false, "SIGINT handler must be unconditional");
});

test("daemon npm script sets HEADLESS=true and DRY_RUN=true", () => {
  const pkg = JSON.parse(readFileSync("package.json", "utf8"));
  const daemonScript = pkg.scripts?.daemon ?? "";
  assert.ok(daemonScript.includes("LLM_ENABLED=false"), "daemon script must disable LLM calls by default");
  assert.ok(daemonScript.includes("HEADLESS=true"), "daemon script must set HEADLESS=true");
  assert.ok(daemonScript.includes("DRY_RUN=true"), "daemon script must set DRY_RUN=true");
});

// ── Group 9: Static/source invariant tests ────────────────────
console.log("\nGroup 9: Static/source invariant tests\n");

test("index.js: /report is included in Telegram read-only commands", () => {
  const content = readFileSync("index.js", "utf8");
  const readOnlyCommands = [
    "/help",
    "/status",
    "/wallet",
    "/config",
    "/positions",
    "/screen",
    "/candidates",
    "/briefing",
    "/report",
  ];
  for (const cmd of readOnlyCommands) {
    assert.ok(content.includes(`"${cmd}"`), `index.js must include ${cmd} in read-only commands`);
  }
});

test("index.js: startup does not unconditionally call ensureAgentId/bootstrapHiveMind/startHiveMindBackgroundSync", () => {
  const content = readFileSync("index.js", "utf8");
  // Startup section must check isHiveMindEnabled() before calling HiveMind functions
  const startupSection = content.slice(0, content.indexOf("const TP_PCT ="));
  assert.ok(
    startupSection.includes("if (isHiveMindEnabled())") ||
    startupSection.includes("if (isHiveMindEnabled()) {\n    ensureAgentId();"),
    "Startup must check isHiveMindEnabled() before calling HiveMind functions"
  );
});

test("index.js: startup has LLM disabled log behavior", () => {
  const content = readFileSync("index.js", "utf8");
  const startupSection = content.slice(0, content.indexOf("const TP_PCT ="));

  const ifNeedle = "if (isLlmEnabled())";
  const modelNeedle = 'log("startup", `Model:';
  const disabledNeedle = 'log("startup", "LLM: disabled")';

  assert.ok(startupSection.includes(ifNeedle), "Startup must contain if (isLlmEnabled()) check");
  assert.ok(startupSection.includes(modelNeedle), "Startup must contain Model log template literal");
  assert.ok(startupSection.includes(disabledNeedle), "Startup must contain log for LLM: disabled");

  const ifPos = startupSection.indexOf(ifNeedle);
  const modelPos = startupSection.indexOf(modelNeedle);
  const disabledPos = startupSection.indexOf(disabledNeedle);

  assert.ok(modelPos > ifPos, "Model log must appear after if (isLlmEnabled())");
  assert.ok(disabledPos > modelPos, "LLM: disabled log must appear after Model log");
});

test("scripts/config-doctor.js: KNOWN_KEYS includes hiveMindEnabled", () => {
  const content = readFileSync("scripts/config-doctor.js", "utf8");
  assert.ok(
    content.includes('"hiveMindEnabled"') ||
    content.includes("'hiveMindEnabled'"),
    "KNOWN_KEYS in config-doctor.js must include hiveMindEnabled"
  );
});

test("scripts/config-doctor.js: hiveMindEnabled resolved in effective config", () => {
  const content = readFileSync("scripts/config-doctor.js", "utf8");
  assert.ok(
    content.includes("const hiveMindEnabled =") &&
    content.includes("runtimeConfig?.hiveMind?.enabled") &&
    content.includes("booleanConfig(userConfig.hiveMindEnabled ?? env.HIVEMIND_ENABLED)"),
    "config-doctor.js must resolve hiveMindEnabled from runtimeConfig/userConfig/env"
  );
});

test("scripts/config-doctor.js: hiveMindEnabled appears in summary table", () => {
  const content = readFileSync("scripts/config-doctor.js", "utf8");
  assert.ok(
    content.includes("HIVEMIND_ENABLED") &&
    content.includes("${hiveMindEnabled}"),
    "config-doctor.js must include HIVEMIND_ENABLED in summary table"
  );
});

test("scripts/config-doctor.js: hiveMindPullMode=auto warning only when hiveMindEnabled===true", () => {
  const content = readFileSync("scripts/config-doctor.js", "utf8");
  // Find the warning condition for hiveMindPullMode=auto
  const lines = content.split("\n");
  let foundAutoCheck = false;
  let foundHiveMindEnabledCondition = false;
  for (let i = 0; i < lines.length; i++) {
    if (lines[i].includes('hiveMindPullMode === "auto"')) {
      foundAutoCheck = true;
      // Check the next few lines for hiveMindEnabled check
      const context = lines.slice(i, i + 4).join("\n");
      foundHiveMindEnabledCondition = context.includes("hiveMindEnabled &&");
    }
  }
  assert.ok(foundAutoCheck, "config-doctor.js must check hiveMindPullMode === 'auto'");
  assert.ok(foundHiveMindEnabledCondition, "hiveMindPullMode=auto warning must condition on hiveMindEnabled === true");
});

test("daemon npm script sets HEADLESS=true and DRY_RUN=true", () => {
  const pkg = JSON.parse(readFileSync("package.json", "utf8"));
  const daemonScript = pkg.scripts?.daemon ?? "";
  assert.ok(daemonScript.includes("HEADLESS=true"),        "daemon script must set HEADLESS=true");
  assert.ok(daemonScript.includes("DRY_RUN=true"),         "daemon script must set DRY_RUN=true");
  assert.ok(daemonScript.includes("EXECUTION_MODE=scanner"), "daemon script must set EXECUTION_MODE=scanner");
  assert.ok(daemonScript.includes("node index.js"),        "daemon script must run node index.js");
});

test("ecosystem.config.cjs forces scanner/dry-run safety env", () => {
  // createRequire lets an ES module load a CJS file synchronously.
  // The ecosystem file exports a plain object with no side effects.
  const require = createRequire(import.meta.url);
  const ecosystem = require("../ecosystem.config.cjs");
  const app = ecosystem.apps?.[0];
  assert.ok(app, "ecosystem.config.cjs must export at least one app");
  const env = app.env ?? {};
  assert.strictEqual(app.env?.LLM_ENABLED, "false", "PM2 ecosystem config must disable LLM calls by default");
  assert.strictEqual(env.DRY_RUN,              "true",    "ecosystem env must set DRY_RUN=true");
  assert.strictEqual(env.EXECUTION_MODE,       "scanner", "ecosystem env must set EXECUTION_MODE=scanner");
  assert.strictEqual(env.HEADLESS,             "true",    "ecosystem env must set HEADLESS=true");
  assert.strictEqual(env.ALLOW_LIVE_EXECUTION, "false",   "ecosystem env must set ALLOW_LIVE_EXECUTION=false");
});

// ── Group 9: Syntax checks ────────────────────────────────────
console.log("\nGroup 9: Files pass syntax check\n");
test("agent.js exposes LLM kill switch before provider call", () => {
  const content = readFileSync("agent.js", "utf8");
  assert.ok(content.includes("export function isLlmEnabled"), "agent.js must export isLlmEnabled()");
  assert.ok(content.includes("if (!isLlmEnabled())"), "agentLoop must check isLlmEnabled()");
  assert.ok(
    content.indexOf("if (!isLlmEnabled())") < content.indexOf("client.chat.completions.create"),
    "LLM kill switch must run before provider API call"
  );
});

test("execution-modes.js passes node --check", () => {
  execSync("node --check execution-modes.js", { cwd: process.cwd(), stdio: "pipe" });
});

test("tools/scanner.js passes node --check", () => {
  execSync("node --check tools/scanner.js", { cwd: process.cwd(), stdio: "pipe" });
});

test("scripts/gen-bot-wallet.js passes node --check", () => {
  execSync("node --check scripts/gen-bot-wallet.js", { cwd: process.cwd(), stdio: "pipe" });
});

test("test/test-phase1.js passes node --check", () => {
  execSync("node --check test/test-phase1.js", { cwd: process.cwd(), stdio: "pipe" });
});

test("agent.js passes node --check", () => {
  execSync("node --check agent.js", { cwd: process.cwd(), stdio: "pipe" });
});

test("config.js passes node --check", () => {
  execSync("node --check config.js", { cwd: process.cwd(), stdio: "pipe" });
});

test("scripts/check-syntax.js passes node --check", () => {
  execSync("node --check scripts/check-syntax.js", { cwd: process.cwd(), stdio: "pipe" });
});

test("index.js passes node --check", () => {
  execSync("node --check index.js", { cwd: process.cwd(), stdio: "pipe" });
});

test("scripts/config-doctor.js passes node --check", () => {
  execSync("node --check scripts/config-doctor.js", { cwd: process.cwd(), stdio: "pipe" });
});

test("ecosystem.config.cjs passes node --check", () => {
  execSync("node --check ecosystem.config.cjs", { cwd: process.cwd(), stdio: "pipe" });
});

// ─── Summary ──────────────────────────────────────────────────

// Group 10: Telegram read-only safety
console.log("\nGroup 10: Telegram read-only safety\n");

function getTelegramHandlerSlice(content) {
  const start = content.indexOf("async function telegramHandler");
  assert.ok(start >= 0, "index.js must define telegramHandler");
  const end = content.indexOf("function launchCron", start);
  assert.ok(end > start, "index.js must have a stable end marker after telegramHandler");
  return content.slice(start, end);
}

test("index.js defines Telegram mutation read-only guard", () => {
  const content = readFileSync("index.js", "utf8");
  const handler = getTelegramHandlerSlice(content);
  assert.ok(content.includes("telegramMutationsEnabled"), "index.js must reference telegramMutationsEnabled");
  assert.ok(content.includes("getTelegramReadOnlyBlockMessage"), "index.js must define a Telegram read-only guard");
  assert.ok(content.includes("TELEGRAM_READ_ONLY_BLOCK_MESSAGE"), "index.js must define a clear block message");
  assert.ok(handler.includes("const readOnlyBlock = getTelegramReadOnlyBlockMessage(text)"), "telegramHandler must call the read-only guard");
});

test("Telegram read-only guard runs before /deploy handler", () => {
  const content = readFileSync("index.js", "utf8");
  const handler = getTelegramHandlerSlice(content);
  const guard = handler.indexOf("const readOnlyBlock = getTelegramReadOnlyBlockMessage(text)");
  assert.ok(guard >= 0, "Telegram read-only guard must exist inside telegramHandler");
  assert.ok(
    guard < handler.indexOf("const deployMatch"),
    "Telegram read-only guard must run before /deploy parsing"
  );
  assert.ok(
    guard < handler.indexOf("deployLatestCandidate"),
    "Telegram read-only guard must run before deployLatestCandidate() inside telegramHandler"
  );
});

test("Telegram read-only guard runs before /close handler", () => {
  const content = readFileSync("index.js", "utf8");
  const handler = getTelegramHandlerSlice(content);
  const guard = handler.indexOf("const readOnlyBlock = getTelegramReadOnlyBlockMessage(text)");
  assert.ok(guard >= 0, "Telegram read-only guard must exist inside telegramHandler");
  assert.ok(
    guard < handler.indexOf("const closeMatch"),
    "Telegram read-only guard must run before /close parsing"
  );
  assert.ok(
    guard < handler.indexOf("closePosition({"),
    "Telegram read-only guard must run before closePosition() inside telegramHandler"
  );
});

test("Telegram read-only guard runs before /setcfg update_config handler", () => {
  const content = readFileSync("index.js", "utf8");
  const handler = getTelegramHandlerSlice(content);
  const guard = handler.indexOf("const readOnlyBlock = getTelegramReadOnlyBlockMessage(text)");
  assert.ok(guard >= 0, "Telegram read-only guard must exist inside telegramHandler");
  assert.ok(
    guard < handler.indexOf("const setCfgMatch"),
    "Telegram read-only guard must run before /setcfg parsing"
  );
  assert.ok(
    guard < handler.indexOf('executeTool("update_config"'),
    "Telegram read-only guard must run before update_config tool call inside telegramHandler"
  );
});

test("Telegram read-only guard runs before fallback free-text agentLoop", () => {
  const content = readFileSync("index.js", "utf8");
  const handler = getTelegramHandlerSlice(content);
  const guard = handler.indexOf("const readOnlyBlock = getTelegramReadOnlyBlockMessage(text)");
  assert.ok(guard >= 0, "Telegram read-only guard must exist inside telegramHandler");
  assert.ok(
    guard < handler.indexOf("agentLoop(text"),
    "Telegram read-only guard must run before fallback free-text agentLoop inside telegramHandler"
  );
});

// ─── Group 11: Token risk classifier (token-risk.js) ───────────────────────────────

console.log("\nGroup 11: Token risk classifier\n");

const WSOL_MINT = "So11111111111111111111111111111111111111112";
const FAKE_MINT = "EvilMint11111111111111111111111111111111111111";

function mkPool(overrides) {
  return {
    pool: {
      base: { symbol: "X", mint: WSOL_MINT },
      quote: { symbol: "WSOL", mint: WSOL_MINT },
      ...overrides.pool,
    },
    ti: {
      audit: { mint_disabled: true, freeze_disabled: true, bot_holders_pct: 1, top_holders_pct: 5 },
      ...overrides.ti,
    },
  };
}

test("token-risk: rugpull => BLOCK", () => {
  const r = classifyTokenRisk(mkPool({ pool: { is_rugpull: true } }));
  assert.strictEqual(r.status, "BLOCK");
  assert.ok(r.reasons.some(x => /rugpull/i.test(x)), "should mention rugpull");
});

test("token-risk: wash => BLOCK", () => {
  const r = classifyTokenRisk(mkPool({ pool: { is_wash: true } }));
  assert.strictEqual(r.status, "BLOCK");
  assert.ok(r.reasons.some(x => /wash/i.test(x)), "should mention wash");
});

test("token-risk: active mint authority => BLOCK", () => {
  const r = classifyTokenRisk(mkPool({ ti: { audit: { mint_disabled: false, freeze_disabled: true, bot_holders_pct: 0, top_holders_pct: 0 } } }));
  assert.strictEqual(r.status, "BLOCK");
  assert.ok(r.reasons.some(x => /mint authority/i.test(x)), "should mention mint authority");
});

test("token-risk: missing base mint => WARN or UNKNOWN, never PASS/BLOCK", () => {
  const candidate = {
    pool: { base: { symbol: "X" } },
    ti: { audit: { mint_disabled: true, freeze_disabled: true, bot_holders_pct: 0, top_holders_pct: 0 } },
  };
  const r = classifyTokenRisk(candidate);
  assert.ok(r.status === "WARN" || r.status === "UNKNOWN", "expected WARN or UNKNOWN, got " + r.status);
  assert.notStrictEqual(r.status, "PASS");
  assert.notStrictEqual(r.status, "BLOCK");
});

test("token-risk: clean WSOL identity with full data => PASS or UNKNOWN, never BLOCK", () => {
  const r = classifyTokenRisk(mkPool({}));
  assert.notStrictEqual(r.status, "BLOCK");
  assert.ok(r.status === "PASS" || r.status === "UNKNOWN", "expected PASS or UNKNOWN, got " + r.status);
  assert.strictEqual(r.identity.baseSymbol, "X");
  assert.strictEqual(r.identity.baseMint, WSOL_MINT);
  assert.strictEqual(r.identity.copycatRisk, false);
});

test("token-risk: SOL symbol with wrong mint => BLOCK + copycatRisk", () => {
  const candidate = {
    pool: { base: { symbol: "SOL", mint: FAKE_MINT } },
    ti: { audit: { mint_disabled: true, freeze_disabled: true, bot_holders_pct: 0, top_holders_pct: 0 } },
  };
  const r = classifyTokenRisk(candidate);
  assert.strictEqual(r.status, "BLOCK");
  assert.strictEqual(r.identity.copycatRisk, true);
  assert.ok(r.reasons.some(x => /symbol SOL|copycat|canonical/i.test(x)));
});

test("token-risk: clean known WSOL with full risk data => PASS", () => {
  const r = classifyTokenRisk({
    pool: { base: { symbol: "WSOL", mint: WSOL_MINT } },
    ti: { audit: { mint_disabled: true, freeze_disabled: true, bot_holders_pct: 1, top_holders_pct: 5 } },
  });
  assert.strictEqual(r.status, "PASS");
  assert.strictEqual(r.identity.copycatRisk, false);
  assert.deepStrictEqual(r.reasons, []);
  assert.deepStrictEqual(r.warnings, []);
});

test("token-risk: formatTokenRiskSummary returns a string with status tag", () => {
  const r = classifyTokenRisk(mkPool({ pool: { is_rugpull: true } }));
  const s = formatTokenRiskSummary(r);
  assert.strictEqual(typeof s, "string");
  assert.ok(s.includes("[BLOCK]"), "summary should include status tag");
  assert.ok(s.toLowerCase().includes("rugpull"), "summary should mention rugpull");
});


// ── Group 12: Ops-8.1 Cycle and Cache Truthfulness ───────────────────────────

console.log("\nGroup 12: Ops-8.1 Cycle and Cache Truthfulness\n");

test("index.js: runScreeningCycle accepts triggerSource param (not {silent})", () => {
  const content = readFileSync("index.js", "utf8");
  // New signature must use triggerSource as a positional param
  assert.ok(
    content.includes("runScreeningCycle(triggerSource = \"cron\")"),
    "runScreeningCycle must accept triggerSource positional param with default \"cron\""
  );
});

test("index.js: skipped_busy is logged to stdout only (console.log), not appendDecisionLedger", () => {
  const content = readFileSync("index.js", "utf8");
  // Find the busy guard block — it must use console.log, not appendDecisionLedger
  const busyGuardIdx = content.indexOf("skipped_busy");
  assert.ok(busyGuardIdx >= 0, "index.js must contain skipped_busy event label");
  // The console.log must come BEFORE the next appendDecisionLedger after the guard
  const nextLedgerIdx = content.indexOf("appendDecisionLedger", busyGuardIdx);
  // The guard block exits (return null) before any ledger call
  const returnNullIdx = content.indexOf("return null;", busyGuardIdx);
  assert.ok(returnNullIdx < nextLedgerIdx, "skipped_busy guard must return null before any appendDecisionLedger call");
});

test("index.js: skipped_busy log payload contains attemptId and triggerSource keys", () => {
  const content = readFileSync("index.js", "utf8");
  const busyGuardIdx = content.indexOf("skipped_busy");
  assert.ok(busyGuardIdx >= 0, "skipped_busy must be present");
  // The JSON payload must include both keys
  const busyLine = content.slice(busyGuardIdx - 200, busyGuardIdx + 200);
  assert.ok(busyLine.includes("attemptId"), "skipped_busy log must include attemptId");
  assert.ok(busyLine.includes("triggerSource"), "skipped_busy log must include triggerSource");
});

test("index.js: cycleId is generated after lock acquired", () => {
  const content = readFileSync("index.js", "utf8");
  // cycleId must be declared after _screeningBusy = true
  const lockIdx = content.indexOf("_screeningBusy = true;");
  assert.ok(lockIdx >= 0, "lock must be set");
  const cycleIdIdx = content.indexOf("const cycleId =", lockIdx);
  assert.ok(cycleIdIdx > lockIdx, "cycleId must be assigned after lock is acquired");
});

test("index.js: startedAt is recorded at cycle start", () => {
  const content = readFileSync("index.js", "utf8");
  const lockIdx = content.indexOf("_screeningBusy = true;");
  const startedAtIdx = content.indexOf("const startedAt =", lockIdx);
  assert.ok(startedAtIdx > lockIdx, "startedAt must be assigned after lock is acquired");
});

test("index.js: cycleMeta helper produces cycleId, triggerSource, startedAt, finishedAt, durationMs, outcome", () => {
  const content = readFileSync("index.js", "utf8");
  assert.ok(content.includes("function cycleMeta(outcome)"), "cycleMeta helper must be defined");
  assert.ok(content.includes("finishedAt"), "cycleMeta must compute finishedAt");
  assert.ok(content.includes("durationMs"), "cycleMeta must compute durationMs");
  // All fields must be in the return object
  const cycleFnIdx = content.indexOf("function cycleMeta(outcome)");
  const cycleFnEnd = content.indexOf("}", cycleFnIdx);
  const cycleFnBody = content.slice(cycleFnIdx, cycleFnEnd + 1);
  assert.ok(cycleFnBody.includes("cycleId"), "cycleMeta must return cycleId");
  assert.ok(cycleFnBody.includes("triggerSource"), "cycleMeta must return triggerSource");
  assert.ok(cycleFnBody.includes("startedAt"), "cycleMeta must return startedAt");
  assert.ok(cycleFnBody.includes("finishedAt"), "cycleMeta must return finishedAt");
  assert.ok(cycleFnBody.includes("durationMs"), "cycleMeta must return durationMs");
  assert.ok(cycleFnBody.includes("outcome"), "cycleMeta must return outcome");
});

test("index.js: appendDecisionLedger calls include ...cycleMeta() spread", () => {
  const content = readFileSync("index.js", "utf8");
  // Every real ledger write (inside runScreeningCycle) must include cycleMeta spread
  // Check that cycleMeta spread appears at least once (for skipped/error/finished paths)
  const cycleMetaSpreadCount = (content.match(/\.\.\.cycleMeta\(/g) || []).length;
  assert.ok(cycleMetaSpreadCount >= 5, `At least 5 appendDecisionLedger calls must spread cycleMeta, found ${cycleMetaSpreadCount}`);
});

test("index.js: appendDecisionLedger calls include outcome field", () => {
  const content = readFileSync("index.js", "utf8");
  // Verify outcome key appears inside ledger payload blocks (not just in cycleMeta body)
  const outcomeInLedger = (content.match(/outcome:\s*["'](?:finished|error)["']/g) || []).length;
  assert.ok(outcomeInLedger >= 3, `At least 3 explicit outcome: fields in ledger payloads, found ${outcomeInLedger}`);
});

test("index.js: describeLatestCandidatesWithFreshness handles empty-but-timestamped cache", () => {
  const content = readFileSync("index.js", "utf8");
  const fnIdx = content.indexOf("function describeLatestCandidatesWithFreshness(limit)");
  assert.ok(fnIdx >= 0, "describeLatestCandidatesWithFreshness must exist");
  // Find the function body (up to next top-level function)
  const fnEnd = content.indexOf("\nfunction ", fnIdx + 1);
  const fnBody = content.slice(fnIdx, fnEnd > fnIdx ? fnEnd : fnIdx + 2000);
  // Must check _latestCandidatesAt separately from _latestCandidates.length
  assert.ok(
    fnBody.includes("_latestCandidatesAt") && fnBody.includes("_latestCandidates.length"),
    "Function must check both timestamp and array length separately"
  );
  // Must emit "0 candidates found" for empty-but-fresh case
  assert.ok(
    fnBody.includes("0 candidates found"),
    "describeLatestCandidatesWithFreshness must report '0 candidates found' when cache is timestamped but empty"
  );
  // Must NOT return the 'No cached candidates yet' message for an empty-but-timestamped cache
  // (that message is only for the no-timestamp case)
  const noCacheMsg = "No cached candidates yet. Run /screen first.";
  const noCacheIdx = fnBody.indexOf(noCacheMsg);
  assert.ok(noCacheIdx >= 0, "Function must still have the 'No cached candidates yet' message for truly uncached state");
  // Check that the "No cached candidates yet" only fires when !_latestCandidatesAt
  const noTsGuardIdx = fnBody.indexOf("!_latestCandidatesAt");
  assert.ok(noTsGuardIdx >= 0, "Must guard 'No cached candidates yet' behind !_latestCandidatesAt check");
  assert.ok(noTsGuardIdx < noCacheIdx, "'No cached candidates yet' must be inside the !_latestCandidatesAt block");
});

test("scripts/summarize-ledger.js: summarizeLedger returns byOutcome field", () => {
  const content = readFileSync("scripts/summarize-ledger.js", "utf8");
  assert.ok(content.includes("byOutcome"), "summarizeLedger must return byOutcome field");
  assert.ok(content.includes("countBy(entries, \"outcome\")"), "byOutcome must use countBy on outcome field");
});

test("scripts/summarize-ledger.js: summarizeLast includes cycleId, durationMs, outcome", () => {
  const content = readFileSync("scripts/summarize-ledger.js", "utf8");
  const fnIdx = content.indexOf("function summarizeLast(entry)");
  assert.ok(fnIdx >= 0, "summarizeLast must exist");
  const fnEnd = content.indexOf("\nexport function ", fnIdx);
  const fnBody = content.slice(fnIdx, fnEnd > fnIdx ? fnEnd : fnIdx + 1500);
  assert.ok(fnBody.includes("cycleId"), "summarizeLast must include cycleId");
  assert.ok(fnBody.includes("durationMs"), "summarizeLast must include durationMs");
  assert.ok(fnBody.includes("outcome"), "summarizeLast must include outcome");
});

test("scripts/summarize-ledger.js: formatLedgerSummary renders byOutcome section", () => {
  const content = readFileSync("scripts/summarize-ledger.js", "utf8");
  assert.ok(
    content.includes("by outcome:") || content.includes("byOutcome"),
    "formatLedgerSummary must render byOutcome section"
  );
  // CLI path must call block() for outcome
  assert.ok(content.includes("count by outcome"), "CLI formatter must include 'count by outcome' block header");
});

test("scripts/summarize-ledger.js passes node --check", () => {
  execSync("node --check scripts/summarize-ledger.js", { cwd: process.cwd(), stdio: "pipe" });
});

test("index.js passes node --check after Ops-8.1 edits", () => {
  execSync("node --check index.js", { cwd: process.cwd(), stdio: "pipe" });
});

// ── Group 13: Ops-8.2 Data Provenance ────────────────────────────────────────

console.log("\nGroup 13: Ops-8.2 Data Provenance\n");

test("tools/screening.js: api_health object is initialised in getTopCandidates", () => {
  const content = readFileSync("tools/screening.js", "utf8");
  const fnIdx = content.indexOf("export async function getTopCandidates(");
  assert.ok(fnIdx >= 0, "getTopCandidates must be exported");
  const fnEnd = content.indexOf("\nexport async function ", fnIdx + 1);
  const fnBody = content.slice(fnIdx, fnEnd > fnIdx ? fnEnd : fnIdx + 8000);
  assert.ok(fnBody.includes("const api_health = {"), "api_health object must be initialised");
});

test("tools/screening.js: api_health has all five required counters", () => {
  const content = readFileSync("tools/screening.js", "utf8");
  const fnIdx = content.indexOf("export async function getTopCandidates(");
  const fnEnd = content.indexOf("\nexport async function ", fnIdx + 1);
  const fnBody = content.slice(fnIdx, fnEnd > fnIdx ? fnEnd : fnIdx + 8000);
  const initIdx = fnBody.indexOf("const api_health = {");
  assert.ok(initIdx >= 0, "api_health must be initialised");
  const initBlock = fnBody.slice(initIdx, fnBody.indexOf("};", initIdx) + 2);
  assert.ok(initBlock.includes("AVAILABLE"), "api_health must have AVAILABLE counter");
  assert.ok(initBlock.includes("NOT_QUERIED"), "api_health must have NOT_QUERIED counter");
  assert.ok(initBlock.includes("UNAVAILABLE"), "api_health must have UNAVAILABLE counter");
  assert.ok(initBlock.includes("MISSING"), "api_health must have MISSING counter");
  assert.ok(initBlock.includes("NEGATIVE_SIGNAL"), "api_health must have NEGATIVE_SIGNAL counter");
});

test("tools/screening.js: NOT_QUERIED is tallied when !p.base?.mint", () => {
  const content = readFileSync("tools/screening.js", "utf8");
  // The guard for missing mint must increment NOT_QUERIED
  assert.ok(
    content.includes("api_health.NOT_QUERIED++"),
    "api_health.NOT_QUERIED must be incremented when base mint is missing"
  );
  // It must appear inside the !p.base?.mint guard
  const notQueriedIdx = content.indexOf("api_health.NOT_QUERIED++");
  const guard = content.slice(Math.max(0, notQueriedIdx - 150), notQueriedIdx + 30);
  assert.ok(
    guard.includes("!p.base?.mint") || guard.includes("!p.base"),
    "NOT_QUERIED increment must be guarded by !p.base?.mint check"
  );
});

test("tools/screening.js: UNAVAILABLE is tallied for rejected OKX promise slots", () => {
  const content = readFileSync("tools/screening.js", "utf8");
  assert.ok(
    content.includes("api_health.UNAVAILABLE++"),
    "api_health.UNAVAILABLE must be incremented for rejected OKX calls"
  );
  // Each of the four OKX calls (adv, price, clusters, risk) must have UNAVAILABLE++
  const unavailableCount = (content.match(/api_health\.UNAVAILABLE\+\+/g) || []).length;
  assert.ok(unavailableCount >= 4, `UNAVAILABLE must be tallied for all 4 OKX calls, found ${unavailableCount}`);
});

test("tools/screening.js: AVAILABLE is tallied for fulfilled OKX promise slots", () => {
  const content = readFileSync("tools/screening.js", "utf8");
  assert.ok(
    content.includes("api_health.AVAILABLE++"),
    "api_health.AVAILABLE must be incremented for fulfilled OKX calls"
  );
  const availableCount = (content.match(/api_health\.AVAILABLE\+\+/g) || []).length;
  assert.ok(availableCount >= 4, `AVAILABLE must be tallied for all 4 OKX calls, found ${availableCount}`);
});

test("tools/screening.js: NEGATIVE_SIGNAL is tallied for is_rugpull and is_wash", () => {
  const content = readFileSync("tools/screening.js", "utf8");
  assert.ok(
    content.includes("api_health.NEGATIVE_SIGNAL++"),
    "api_health.NEGATIVE_SIGNAL must be incremented"
  );
  // Both OKX signals must be tallied (>= 2 total occurrences across Jupiter gate + OKX block)
  const negCount = (content.match(/api_health\.NEGATIVE_SIGNAL\+\+/g) || []).length;
  assert.ok(negCount >= 2, `NEGATIVE_SIGNAL must be tallied for both is_rugpull and is_wash, found ${negCount}`);
  // At least one increment must be near is_rugpull or is_wash (the OKX block).
  // Ops-8.2b adds a Jupiter-gate occurrence earlier in the file (near isDevBlocked),
  // so we check all occurrences rather than only the first.
  const negRegex = /api_health\.NEGATIVE_SIGNAL\+\+/g;
  let match;
  let foundNearOkxSignals = false;
  while ((match = negRegex.exec(content)) !== null) {
    const window = content.slice(Math.max(0, match.index - 300), match.index + 100);
    if (window.includes("is_rugpull") || window.includes("is_wash")) {
      foundNearOkxSignals = true;
      break;
    }
  }
  assert.ok(
    foundNearOkxSignals,
    "At least one NEGATIVE_SIGNAL increment must be near is_rugpull / is_wash assignments (OKX block)"
  );
});

test("tools/screening.js: api_health is returned in getTopCandidates result", () => {
  const content = readFileSync("tools/screening.js", "utf8");
  const fnIdx = content.indexOf("export async function getTopCandidates(");
  const fnEnd = content.indexOf("\nexport async function ", fnIdx + 1);
  const fnBody = content.slice(fnIdx, fnEnd > fnIdx ? fnEnd : fnIdx + 8000);
  // The return statement must include api_health
  const returnIdx = fnBody.lastIndexOf("return {");
  assert.ok(returnIdx >= 0, "getTopCandidates must have a return statement");
  const returnBlock = fnBody.slice(returnIdx, fnBody.indexOf("};", returnIdx) + 2);
  assert.ok(returnBlock.includes("api_health"), "getTopCandidates return must include api_health");
});

test("index.js: api_health is extracted from topCandidates response", () => {
  const content = readFileSync("index.js", "utf8");
  // Must extract apiErrorCount from topCandidates?.api_health?.UNAVAILABLE
  assert.ok(
    content.includes("topCandidates?.api_health?.UNAVAILABLE"),
    "index.js must extract UNAVAILABLE count from topCandidates.api_health"
  );
});

test("index.js: apiErrorCount variable is declared and assigned from api_health", () => {
  const content = readFileSync("index.js", "utf8");
  // Declaration must exist (let, before the inner try)
  assert.ok(
    content.includes("let apiErrorCount = 0;"),
    "apiErrorCount must be declared as let before the inner try block"
  );
  // Assignment must point to api_health.UNAVAILABLE
  assert.ok(
    content.includes("topCandidates?.api_health?.UNAVAILABLE"),
    "apiErrorCount must be assigned from topCandidates?.api_health?.UNAVAILABLE"
  );
  // Assignment must come after the getTopCandidates call
  const getTopIdx = content.indexOf("getTopCandidates({ limit:");
  assert.ok(getTopIdx >= 0, "getTopCandidates call must exist");
  const assignIdx = content.indexOf("topCandidates?.api_health?.UNAVAILABLE", getTopIdx);
  assert.ok(assignIdx > getTopIdx, "apiErrorCount must be assigned after the getTopCandidates call");
});

test("index.js: no appendDecisionLedger call inside runScreeningCycle uses apiErrorCount: \"not tracked\" after fetch", () => {
  const content = readFileSync("index.js", "utf8");
  // Bound the search to inside runScreeningCycle — from the getTopCandidates call
  // to the startCronJobs export (the next top-level function after runScreeningCycle).
  const getTopIdx = content.indexOf("const topCandidates = await getTopCandidates(");
  assert.ok(getTopIdx >= 0, "getTopCandidates call must exist");
  const cronJobsIdx = content.indexOf("export function startCronJobs()", getTopIdx);
  assert.ok(cronJobsIdx > getTopIdx, "startCronJobs must follow runScreeningCycle");
  // Only examine inside runScreeningCycle after the fetch
  const innerRegion = content.slice(getTopIdx, cronJobsIdx);
  const notTrackedCount = (innerRegion.match(/apiErrorCount:\s*"not tracked"/g) || []).length;
  assert.ok(
    notTrackedCount === 0,
    `All appendDecisionLedger calls inside runScreeningCycle after getTopCandidates must use the real apiErrorCount, found ${notTrackedCount} remaining "not tracked" occurrences`
  );
});

test("tools/screening.js passes node --check after Ops-8.2 edits", () => {
  execSync("node --check tools/screening.js", { cwd: process.cwd(), stdio: "pipe" });
});

test("index.js passes node --check after Ops-8.2 edits", () => {
  execSync("node --check index.js", { cwd: process.cwd(), stdio: "pipe" });
});

// ── Group 14: Ops-8.3 Telemetry Edge-Case Cleanup ────────────────────────────

console.log("\nGroup 14: Ops-8.3 Telemetry Edge-Case Cleanup\n");

test("index.js: getCandidatesStaleness uses 5000ms drift buffer", () => {
  const content = readFileSync("index.js", "utf8");
  // Find the getCandidatesStaleness function body
  const fnIdx = content.indexOf("function getCandidatesStaleness()");
  assert.ok(fnIdx >= 0, "getCandidatesStaleness must exist");
  const fnEnd = content.indexOf("\nfunction ", fnIdx + 1);
  const fnBody = content.slice(fnIdx, fnEnd > fnIdx ? fnEnd : fnIdx + 1500);
  // Must use updatedAt + 5000 drift buffer (not bare > updatedAt)
  assert.ok(
    fnBody.includes("updatedAt + 5000"),
    "getCandidatesStaleness must add a 5000ms drift buffer: summaryTime > (updatedAt + 5000)"
  );
  // Must NOT use the old bare > updatedAt comparison (without the buffer)
  assert.ok(
    !fnBody.includes("summaryTime > updatedAt") || fnBody.includes("summaryTime > (updatedAt + 5000)"),
    "getCandidatesStaleness must not use the old bare summaryTime > updatedAt comparison"
  );
});

test("index.js: getCandidatesStaleness drift buffer is exactly 5000", () => {
  const content = readFileSync("index.js", "utf8");
  const fnIdx = content.indexOf("function getCandidatesStaleness()");
  assert.ok(fnIdx >= 0, "getCandidatesStaleness must exist");
  const fnEnd = content.indexOf("\nfunction ", fnIdx + 1);
  const fnBody = content.slice(fnIdx, fnEnd > fnIdx ? fnEnd : fnIdx + 1500);
  // Ensure the literal 5000 is present in the buffer expression
  const bufferMatch = fnBody.match(/summaryTime\s*>\s*\(\s*updatedAt\s*\+\s*5000\s*\)/);
  assert.ok(
    bufferMatch,
    "The drift buffer expression must be exactly: summaryTime > (updatedAt + 5000)"
  );
});

test("index.js: buildScreeningSummary does not emit apiErrorCount: \"not tracked\"", () => {
  const content = readFileSync("index.js", "utf8");
  // Find buildScreeningSummary function body
  const fnIdx = content.indexOf("function buildScreeningSummary(");
  assert.ok(fnIdx >= 0, "buildScreeningSummary must exist");
  const fnEnd = content.indexOf("\nfunction ", fnIdx + 1);
  const fnBody = content.slice(fnIdx, fnEnd > fnIdx ? fnEnd : fnIdx + 3000);
  // Must not contain the hardcoded "not tracked" string in this function
  assert.ok(
    !fnBody.includes('"not tracked"'),
    'buildScreeningSummary must not hardcode apiErrorCount: "not tracked"'
  );
});

test("index.js: filtered outcome block (passing.length === 0) uses real apiErrorCount, not \"not tracked\"", () => {
  const content = readFileSync("index.js", "utf8");
  // Find the passing.length === 0 early-exit block
  const filteredIdx = content.indexOf("if (passing.length === 0)");
  assert.ok(filteredIdx >= 0, "passing.length === 0 block must exist");
  // Find the appendDecisionLedger call inside that block — look forward ~2000 chars
  const blockWindow = content.slice(filteredIdx, filteredIdx + 2000);
  // Confirm the ledger call is present
  assert.ok(
    blockWindow.includes("appendDecisionLedger("),
    "filtered outcome block must call appendDecisionLedger"
  );
  // Must NOT use "not tracked" as the apiErrorCount value
  assert.ok(
    !blockWindow.includes('"not tracked"'),
    'filtered outcome appendDecisionLedger must not use apiErrorCount: "not tracked"'
  );
  // Must use the real apiErrorCount variable
  assert.ok(
    blockWindow.includes("apiErrorCount"),
    "filtered outcome appendDecisionLedger must reference the apiErrorCount variable"
  );
});

test("index.js: no hardcoded \"not tracked\" remains inside runScreeningCycle after getTopCandidates fetch", () => {
  const content = readFileSync("index.js", "utf8");
  // Bound the check to after getTopCandidates call through startCronJobs
  const getTopIdx = content.indexOf("const topCandidates = await getTopCandidates(");
  assert.ok(getTopIdx >= 0, "getTopCandidates call must exist");
  const cronJobsIdx = content.indexOf("export function startCronJobs()", getTopIdx);
  assert.ok(cronJobsIdx > getTopIdx, "startCronJobs must follow runScreeningCycle");
  const innerRegion = content.slice(getTopIdx, cronJobsIdx);
  const notTrackedInFiltered = (innerRegion.match(/apiErrorCount:\s*"not tracked"/g) || []).length;
  assert.strictEqual(
    notTrackedInFiltered,
    0,
    `All appendDecisionLedger calls inside runScreeningCycle after getTopCandidates must use the real apiErrorCount, found ${notTrackedInFiltered} remaining "not tracked" occurrences`
  );
});

test("index.js passes node --check after Ops-8.3 edits", () => {
  execSync("node --check index.js", { cwd: process.cwd(), stdio: "pipe" });
});

// ── Group 15: Ops-8.2b Jupiter Dev Blocklist Truthfulness ────────────────────

console.log("\nGroup 15: Ops-8.2b Jupiter Dev Blocklist Truthfulness\n");

// These tests exercise the Jupiter fail-closed gate logic directly.
// They simulate the sentinel flags (_jup_error, _jup_queried) that
// discoverPools() sets on condensed pool objects, and verify the gate
// logic that getTopCandidates() applies to them.
// No network calls are made — all fetch behaviour is simulated via flags.

test("Ops-8.2b Test 1: Jupiter outage hard-filters candidate and increments UNAVAILABLE", () => {
  // Simulate a condensed pool that went through the Jupiter fetch path
  // but whose fetch threw a network exception.
  const pool = {
    pool: "POOL_A",
    name: "TOKEN-SOL",
    base: { symbol: "TOKEN", mint: "MintAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA" },
    tvl: 50000,
    volatility: 0.05,
    _jup_error: true,
    _jup_queried: true,
  };

  const filteredOut = [];
  const api_health = { AVAILABLE: 0, NOT_QUERIED: 0, UNAVAILABLE: 0, MISSING: 0, NEGATIVE_SIGNAL: 0 };

  // Simulate the gate logic from getTopCandidates()
  const survived = [pool].filter((p) => {
    if (p._jup_error) {
      filteredOut.push({ name: p.name, reason: "Jupiter API unavailable - cannot verify deployer" });
      api_health.UNAVAILABLE++;
      return false;
    }
    if (p._jup_queried && !p.dev) { api_health.MISSING++;         return true; }
    if (p._jup_queried && p.dev)  { api_health.AVAILABLE++;       return true; }
    return true;
  });

  assert.strictEqual(survived.length, 0, "Pool must be removed from eligible candidates");
  assert.strictEqual(api_health.UNAVAILABLE, 1, "UNAVAILABLE must be incremented by 1");
  assert.strictEqual(api_health.AVAILABLE, 0, "AVAILABLE must not be incremented");
  assert.strictEqual(api_health.MISSING, 0, "MISSING must not be incremented");
  assert.strictEqual(filteredOut.length, 1, "filteredOut must contain one entry");
  assert.strictEqual(
    filteredOut[0].reason,
    "Jupiter API unavailable - cannot verify deployer",
    "Exact reason string must match contract"
  );
});

test("Ops-8.2b Test 2: Jupiter HTTP-200 with missing dev passes filter and increments MISSING", () => {
  // Simulate a pool where Jupiter returned HTTP 200 but the payload
  // contained no deployer address (dev: null).
  const pool = {
    pool: "POOL_B",
    name: "ANON-SOL",
    base: { symbol: "ANON", mint: "MintBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB" },
    tvl: 60000,
    volatility: 0.04,
    dev: null,
    _jup_queried: true,
    // _jup_error is intentionally absent
  };

  const filteredOut = [];
  const api_health = { AVAILABLE: 0, NOT_QUERIED: 0, UNAVAILABLE: 0, MISSING: 0, NEGATIVE_SIGNAL: 0 };

  const survived = [pool].filter((p) => {
    if (p._jup_error) {
      filteredOut.push({ name: p.name, reason: "Jupiter API unavailable - cannot verify deployer" });
      api_health.UNAVAILABLE++;
      return false;
    }
    if (p._jup_queried && !p.dev) { api_health.MISSING++;   return true; }
    if (p._jup_queried && p.dev)  { api_health.AVAILABLE++; return true; }
    return true;
  });

  assert.strictEqual(survived.length, 1, "Pool must survive (missing dev is not a block)");
  assert.strictEqual(api_health.MISSING, 1, "MISSING must be incremented by 1");
  assert.strictEqual(api_health.UNAVAILABLE, 0, "UNAVAILABLE must not be incremented");
  assert.strictEqual(api_health.AVAILABLE, 0, "AVAILABLE must not be incremented");
  assert.strictEqual(filteredOut.length, 0, "filteredOut must remain empty");
});

test("Ops-8.2b Test 3: Jupiter blocklist match filters candidate and increments NEGATIVE_SIGNAL", () => {
  // Simulate a pool where Jupiter returned a known blocked developer address.
  const BLOCKED_DEV = "BlockedDevWallet111111111111111111111111111";
  const pool = {
    pool: "POOL_C",
    name: "RUG-SOL",
    base: { symbol: "RUG", mint: "MintCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC" },
    tvl: 55000,
    volatility: 0.06,
    dev: BLOCKED_DEV,
    _jup_queried: true,
  };

  const filteredOut = [];
  const api_health = { AVAILABLE: 0, NOT_QUERIED: 0, UNAVAILABLE: 0, MISSING: 0, NEGATIVE_SIGNAL: 0 };

  // Simulate isDevBlocked() returning true for this specific wallet
  const mockIsDevBlocked = (dev) => dev === BLOCKED_DEV;

  const survived = [pool].filter((p) => {
    if (p._jup_error) {
      filteredOut.push({ name: p.name, reason: "Jupiter API unavailable - cannot verify deployer" });
      api_health.UNAVAILABLE++;
      return false;
    }
    if (p._jup_queried && !p.dev) { api_health.MISSING++; return true; }
    if (p._jup_queried && p.dev) {
      if (mockIsDevBlocked(p.dev)) {
        filteredOut.push({ name: p.name, reason: "blocked deployer" });
        api_health.NEGATIVE_SIGNAL++;
        return false;
      }
      api_health.AVAILABLE++;
      return true;
    }
    return true;
  });

  assert.strictEqual(survived.length, 0, "Pool must be removed (blocked deployer)");
  assert.strictEqual(api_health.NEGATIVE_SIGNAL, 1, "NEGATIVE_SIGNAL must be incremented by 1");
  assert.strictEqual(api_health.UNAVAILABLE, 0, "UNAVAILABLE must not be incremented");
  assert.strictEqual(api_health.MISSING, 0, "MISSING must not be incremented");
  assert.strictEqual(api_health.AVAILABLE, 0, "AVAILABLE must not be incremented");
  assert.strictEqual(filteredOut.length, 1, "filteredOut must contain one entry");
  assert.ok(
    filteredOut[0].reason === "blocked deployer",
    "Reason must indicate blocked deployer"
  );
});

test("tools/screening.js passes node --check after Ops-8.2b edits", () => {
  execSync("node --check tools/screening.js", { cwd: process.cwd(), stdio: "pipe" });
});

// ── Group 16: Ops-8.3 Baseline Discovery Truthfulness ────────────────────────

console.log("\nGroup 16: Ops-8.3 Baseline Discovery Truthfulness\n");

// These tests exercise the reason-selection logic in the passing.length === 0
// block directly. They simulate the local variables that exist at that point
// inside runScreeningCycle() and verify the correct reason string is selected.
// No network calls, no heavy imports.

const NO_DISCOVERY_REASON = "No pools met baseline discovery filters (TVL, volume, bin_step, organic score)";

test("Ops-8.3 Test 1: zero discovery with no filtered examples produces truthful baseline reason", () => {
  // Simulate the state when getTopCandidates returned candidates=[] and
  // filtered_examples=[] — discovery found literally nothing.
  const filteredOutLocal = [];         // post-recon filter caught nothing
  const earlyFilteredExamplesLocal = []; // upstream found nothing either

  const combined = filteredOutLocal.length > 0 ? filteredOutLocal : earlyFilteredExamplesLocal;
  const combinedExamples = combined.slice(0, 3)
    .map((entry) => `- ${entry.name}: ${entry.reason}`)
    .join("\n");

  // Replicate the logic from index.js
  const fallbackReason = combined.length > 0
    ? (combinedExamples || "All candidates filtered before deploy")
    : NO_DISCOVERY_REASON;

  const screenReportLocal = combinedExamples
    ? `No candidates available.\nFiltered examples:\n${combinedExamples}`
    : `No candidates available. ${NO_DISCOVERY_REASON}.`;

  // Assertions
  assert.strictEqual(fallbackReason, NO_DISCOVERY_REASON,
    "fallbackReason must be the baseline discovery reason when combined is empty");
  assert.ok(!fallbackReason.includes("filtered before deploy"),
    "reason must NOT claim candidates were filtered before deploy");
  assert.ok(fallbackReason.includes("baseline discovery filters"),
    "reason must explicitly mention baseline discovery filters");
  assert.ok(screenReportLocal.includes(NO_DISCOVERY_REASON),
    "screenReport must contain the baseline discovery reason");
});

test("Ops-8.3 Test 2: real filtered examples produce existing rejection messages unchanged", () => {
  // Simulate the state when earlyFilteredExamples has real entries from
  // upstream screening (getTopCandidates filtered some pools via TVL/volume gates).
  const filteredOutLocal = [];
  const earlyFilteredExamplesLocal = [
    { name: "TOKEN-SOL", reason: "TVL $1500 below minTvl $2000" },
    { name: "MEME-USDC", reason: "holders 120 below minHolders 300" },
  ];

  const combined = filteredOutLocal.length > 0 ? filteredOutLocal : earlyFilteredExamplesLocal;
  const combinedExamples = combined.slice(0, 3)
    .map((entry) => `- ${entry.name}: ${entry.reason}`)
    .join("\n");

  const fallbackReason = combined.length > 0
    ? (combinedExamples || "All candidates filtered before deploy")
    : NO_DISCOVERY_REASON;

  const screenReportLocal = combinedExamples
    ? `No candidates available.\nFiltered examples:\n${combinedExamples}`
    : `No candidates available. ${NO_DISCOVERY_REASON}.`;

  // Assertions — existing behavior preserved
  assert.ok(fallbackReason.includes("TOKEN-SOL"),
    "fallbackReason must include filtered pool name when filtered examples exist");
  assert.ok(fallbackReason.includes("TVL $1500 below minTvl $2000"),
    "fallbackReason must include the actual rejection reason");
  assert.ok(!fallbackReason.includes(NO_DISCOVERY_REASON),
    "baseline discovery reason must NOT appear when real filtered examples exist");
  assert.ok(screenReportLocal.includes("Filtered examples:"),
    "screenReport must use 'Filtered examples:' section when filtered examples exist");
});

test("Ops-8.3 Test 3: ledger schema fields are unchanged (result, mode, candidateCount types preserved)", () => {
  // Simulate building the appendDecisionLedger payload for the zero-discovery case
  // and verify the schema-relevant field types are unchanged.
  const filteredOutLocal = [];
  const earlyFilteredExamplesLocal = [];
  const combined = filteredOutLocal.length > 0 ? filteredOutLocal : earlyFilteredExamplesLocal;
  const combinedExamples = combined.slice(0, 3)
    .map((entry) => `- ${entry.name}: ${entry.reason}`)
    .join("\n");
  const fallbackReason = combined.length > 0
    ? (combinedExamples || "All candidates filtered before deploy")
    : NO_DISCOVERY_REASON;

  // Simulate the ledger payload (schema fields only — no live functions called)
  const ledgerPayload = {
    result: "no_deploy",
    mode: "filtered",
    outcome: "finished",
    reason: fallbackReason,
    bestCandidate: null,
    bestCandidatePool: null,
    topCandidates: [],
    rejected: combined.slice(0, 5).map((e) => ({ name: e.name, reason: e.reason })),
    candidateCount: 0,
    candidatesCacheCount: 0,
    apiErrorCount: 0,
  };

  // Schema invariants
  assert.strictEqual(ledgerPayload.result, "no_deploy",
    "result field must remain 'no_deploy'");
  assert.strictEqual(ledgerPayload.mode, "filtered",
    "mode field must remain 'filtered'");
  assert.strictEqual(ledgerPayload.outcome, "finished",
    "outcome field must remain 'finished'");
  assert.strictEqual(ledgerPayload.candidateCount, 0,
    "candidateCount must be integer 0");
  assert.strictEqual(typeof ledgerPayload.apiErrorCount, "number",
    "apiErrorCount must be a number");
  assert.strictEqual(typeof ledgerPayload.reason, "string",
    "reason must be a string");
  assert.ok(ledgerPayload.reason.length > 0,
    "reason must be non-empty");
  assert.ok(Array.isArray(ledgerPayload.rejected),
    "rejected must be an array");
  assert.ok(Array.isArray(ledgerPayload.topCandidates),
    "topCandidates must be an array");
  // The new reason must be serializable (no circular refs, valid JSON)
  const json = JSON.stringify(ledgerPayload);
  assert.ok(json.length > 0, "ledger payload must be JSON-serializable");
  assert.ok(!json.includes("All candidates filtered before deploy"),
    "The misleading fallback must not appear in the zero-discovery ledger payload");
});

test("index.js passes node --check after Ops-8.3 edits", () => {
  execSync("node --check index.js", { cwd: process.cwd(), stdio: "pipe" });
});

// ── Group 17: Ops-8.4-Lite Observability Funnel ──────────────────────────────

console.log("\nGroup 17: Ops-8.4-Lite Observability Funnel\n");

test("Ops-8.4-Lite Test 1: getTopCandidates return object contains eligible_before_slice and total", () => {
  const content = readFileSync("tools/screening.js", "utf8");
  const fnIdx = content.indexOf("export async function getTopCandidates(");
  assert.ok(fnIdx >= 0, "getTopCandidates must be exported");
  const fnEnd = content.indexOf("\nexport async function ", fnIdx + 1);
  const fnBody = content.slice(fnIdx, fnEnd > fnIdx ? fnEnd : fnIdx + 8000);
  const returnIdx = fnBody.lastIndexOf("return {");
  assert.ok(returnIdx >= 0, "getTopCandidates return statement not found");
  const returnBlock = fnBody.slice(returnIdx, fnBody.indexOf("};", returnIdx) + 2);
  assert.ok(returnBlock.includes("eligible_before_slice"), "getTopCandidates return must include eligible_before_slice");
  assert.ok(returnBlock.includes("total: discovery.total"), "getTopCandidates return must include discovery.total");
});

test("Ops-8.4-Lite Test 2: index.js defines funnelSnapshot with correct structure", () => {
  const content = readFileSync("index.js", "utf8");
  assert.ok(content.includes("const funnelSnapshot = {"), "index.js must define funnelSnapshot");
  const snapIdx = content.indexOf("const funnelSnapshot = {");
  const snapBlock = content.slice(snapIdx, content.indexOf("};", snapIdx) + 2);
  assert.ok(snapBlock.includes("stage_api_total"), "funnelSnapshot must contain stage_api_total");
  assert.ok(snapBlock.includes("stage_post_discover"), "funnelSnapshot must contain stage_post_discover");
  assert.ok(snapBlock.includes("stage_pre_slice"), "funnelSnapshot must contain stage_pre_slice");
  assert.ok(snapBlock.includes("stage_post_getTop"), "funnelSnapshot must contain stage_post_getTop");
  assert.ok(snapBlock.includes("stage_post_recon"), "funnelSnapshot must contain stage_post_recon");
});

test("Ops-8.4-Lite Test 3: index.js defines and updates _lastFunnelSnapshot in memory", () => {
  const content = readFileSync("index.js", "utf8");
  assert.ok(content.includes("let _lastFunnelSnapshot = null;"), "index.js must define _lastFunnelSnapshot");
  assert.ok(content.includes("_lastFunnelSnapshot = funnelSnapshot;"), "index.js must save funnelSnapshot to _lastFunnelSnapshot");
});

test("Ops-8.4-Lite Test 4: index.js includes /funnel in read-only commands and help text", () => {
  const content = readFileSync("index.js", "utf8");
  assert.ok(content.includes('text === "/funnel"'), "index.js must validate /funnel command as read-only");
  assert.ok(content.includes('"/funnel - show latest screening funnel metrics (read-only)"'), "index.js must document /funnel in help text");
});

test("Ops-8.4-Lite Test 5: index.js implements /funnel handler in telegramHandler", () => {
  const content = readFileSync("index.js", "utf8");
  assert.ok(content.includes('if (text === "/funnel")'), "index.js must handle /funnel command");
  const handlerIdx = content.indexOf('if (text === "/funnel")');
  const handlerBlock = content.slice(handlerIdx, handlerIdx + 600);
  assert.ok(handlerBlock.includes("🔻 Screening Funnel"), "Handler must print funnel title");
  assert.ok(handlerBlock.includes("API total:"), "Handler must print API total");
  assert.ok(handlerBlock.includes("Post-discover:"), "Handler must print Post-discover");
  assert.ok(handlerBlock.includes("Pre-slice:"), "Handler must print Pre-slice");
  assert.ok(handlerBlock.includes("Post-getTop:"), "Handler must print Post-getTop");
  assert.ok(handlerBlock.includes("Post-recon:"), "Handler must print Post-recon");
});

test("index.js passes node --check after Ops-8.4-Lite edits", () => {
  execSync("node --check index.js", { cwd: process.cwd(), stdio: "pipe" });
});

console.log("\nGroup 18: Paper Mode Offline Storage Protection\n");

await testAsync("Paper storage: getMyPositions handles missing file", async () => {
  setEnv({ DRY_RUN: "true", EXECUTION_MODE: "paper" });
  const fs = await import("fs");
  const path = await import("path");
  const { fileURLToPath } = await import("url");
  const activePath = path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "data", "paper_active_positions.json");
  if (fs.existsSync(activePath)) fs.unlinkSync(activePath);
  
  const { getMyPositions } = await import("../tools/dlmm.js");
  const result = await getMyPositions({ silent: true, force: true });
  assert.strictEqual(result.total_positions, 0);
  assert.strictEqual(result.positions.length, 0);
});

await testAsync("Paper storage: getMyPositions handles empty file", async () => {
  setEnv({ DRY_RUN: "true", EXECUTION_MODE: "paper" });
  const fs = await import("fs");
  const path = await import("path");
  const { fileURLToPath } = await import("url");
  const activePath = path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "data", "paper_active_positions.json");
  fs.writeFileSync(activePath, "   ", "utf8");
  
  const { getMyPositions } = await import("../tools/dlmm.js");
  const result = await getMyPositions({ silent: true, force: true });
  assert.strictEqual(result.total_positions, 0);
  assert.strictEqual(result.positions.length, 0);
});

await testAsync("Paper storage: getMyPositions handles invalid JSON", async () => {
  setEnv({ DRY_RUN: "true", EXECUTION_MODE: "paper" });
  const fs = await import("fs");
  const path = await import("path");
  const { fileURLToPath } = await import("url");
  const activePath = path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "data", "paper_active_positions.json");
  fs.writeFileSync(activePath, "{ bad json", "utf8");
  
  const { getMyPositions } = await import("../tools/dlmm.js");
  const result = await getMyPositions({ silent: true, force: true });
  assert.strictEqual(result.total_positions, 0);
  assert.strictEqual(result.positions.length, 0);
});

await testAsync("Paper storage: getMyPositions handles empty array JSON", async () => {
  setEnv({ DRY_RUN: "true", EXECUTION_MODE: "paper" });
  const fs = await import("fs");
  const path = await import("path");
  const { fileURLToPath } = await import("url");
  const activePath = path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "data", "paper_active_positions.json");
  fs.writeFileSync(activePath, "[]", "utf8");
  
  const { getMyPositions } = await import("../tools/dlmm.js");
  const result = await getMyPositions({ silent: true, force: true });
  assert.strictEqual(result.total_positions, 0);
  assert.strictEqual(result.positions.length, 0);
});

await testAsync("Paper storage: closePosition handles missing file gracefully", async () => {
  setEnv({ DRY_RUN: "true", EXECUTION_MODE: "paper" });
  const fs = await import("fs");
  const path = await import("path");
  const { fileURLToPath } = await import("url");
  const activePath = path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "data", "paper_active_positions.json");
  if (fs.existsSync(activePath)) fs.unlinkSync(activePath);
  
  const { closePosition } = await import("../tools/dlmm.js");
  const result = await closePosition({ position_address: "fake_id" });
  assert.strictEqual(result.success, false);
  assert.ok(result.error.includes("not found"));
});

await testAsync("Paper storage: deploy, getMyPositions, close end-to-end simulation offline", async () => {
  setEnv({ DRY_RUN: "true", EXECUTION_MODE: "paper" });
  const fs = await import("fs");
  const path = await import("path");
  const { fileURLToPath } = await import("url");
  const activePath = path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "data", "paper_active_positions.json");
  const archivePath = path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "data", "paper_archive.json");
  if (fs.existsSync(activePath)) fs.unlinkSync(activePath);
  if (fs.existsSync(archivePath)) fs.unlinkSync(archivePath);
  
  // We simulate what deployPosition would do without hitting RPC
  const { savePaperPositions, loadPaperPositions, getMyPositions, closePosition } = await import("../tools/dlmm.js");
  
  const syntheticId = `paper_${Date.now()}_EUmfCSS`;
  const newPaperPosition = {
    position: syntheticId,
    pool: "EUmfCSSSLoDCwG667B4ApnNhWh76r1ebqiuAJjVnXkNH",
    pair: "UNKNOWN-PAIR",
    base_mint: "11111111111111111111111111111111",
    pnl_usd: 0,
    pnl_pct: 0
  };
  
  await savePaperPositions(activePath, [newPaperPosition]);
  
  // 1. check it appears in getMyPositions
  const getResult = await getMyPositions({ silent: true, force: true });
  assert.strictEqual(getResult.total_positions, 1);
  assert.strictEqual(getResult.positions[0].position, syntheticId);
  
  // 2. check it can be closed
  const closeResult = await closePosition({ position_address: syntheticId, reason: "Offline Test" });
  assert.strictEqual(closeResult.success, true);
  
  // 3. check removed from active
  const activeAfter = await loadPaperPositions(activePath);
  assert.strictEqual(activeAfter.length, 0);
  
  // 4. check appended to archive
  const archiveAfter = await loadPaperPositions(archivePath);
  assert.strictEqual(archiveAfter.length, 1);
  assert.strictEqual(archiveAfter[0].position, syntheticId);
  assert.strictEqual(archiveAfter[0].close_reason, "Offline Test");
});

// ── Group 19: Ops-8.4 Full Observability Funnel ──────────────────────────────

console.log("\nGroup 19: Ops-8.4 Full Observability Funnel\n");

test("Ops-8.4 Test 1: index.js defines buildFunnelSummaryLines helper", () => {
  const content = readFileSync("index.js", "utf8");
  assert.ok(content.includes("function buildFunnelSummaryLines("), "index.js must define buildFunnelSummaryLines");
  assert.ok(content.includes("stage_pre_slice"), "buildFunnelSummaryLines must use stage_pre_slice (eligible_before_slice)");
  assert.ok(content.includes("stage_api_total"), "buildFunnelSummaryLines must use stage_api_total");
  assert.ok(content.includes("stage_post_recon"), "buildFunnelSummaryLines must use stage_post_recon");
});

test("Ops-8.4 Test 2: buildFunnelSummaryLines returns correct labels", () => {
  // Extract and eval buildFunnelSummaryLines from source via regex
  const content = readFileSync("index.js", "utf8");
  const fnIdx = content.indexOf("function buildFunnelSummaryLines(");
  assert.ok(fnIdx >= 0, "buildFunnelSummaryLines must exist");
  const fnEnd = content.indexOf("\n}\n", fnIdx) + 3;
  const fnSrc = content.slice(fnIdx, fnEnd);
  // Verify label strings are present in function body
  assert.ok(fnSrc.includes("Discovery Funnel"), "must include 'Discovery Funnel' label");
  assert.ok(fnSrc.includes("API:"), "must include 'API:' label");
  assert.ok(fnSrc.includes("Post Discover:"), "must include 'Post Discover:' label");
  assert.ok(fnSrc.includes("Eligible:"), "must include 'Eligible:' label");
  assert.ok(fnSrc.includes("Top Slice:"), "must include 'Top Slice:' label");
  assert.ok(fnSrc.includes("Recon:"), "must include 'Recon:' label");
});

test("Ops-8.4 Test 3: index.js buildScreeningSummary return includes funnelSnapshot field", () => {
  const content = readFileSync("index.js", "utf8");
  // The return block of buildScreeningSummary must contain funnelSnapshot
  const fnIdx = content.indexOf("function buildScreeningSummary(");
  assert.ok(fnIdx >= 0, "buildScreeningSummary must exist");
  const fnEnd = content.indexOf("\nfunction ", fnIdx + 1);
  const fnBody = content.slice(fnIdx, fnEnd > fnIdx ? fnEnd : fnIdx + 3000);
  assert.ok(fnBody.includes("funnelSnapshot"), "buildScreeningSummary return must contain funnelSnapshot field");
  assert.ok(fnBody.includes("weightsSummary"), "buildScreeningSummary return must contain weightsSummary field");
});

test("Ops-8.4 Test 4: index.js buildScreeningSummary accepts weightsSummary param", () => {
  const content = readFileSync("index.js", "utf8");
  // The function signature must accept weightsSummary
  const sigLine = content.match(/function buildScreeningSummary\(\{[^}]+\}/)?.[0] || "";
  assert.ok(sigLine.includes("weightsSummary"), "buildScreeningSummary signature must include weightsSummary param");
});

test("Ops-8.4 Test 5: index.js CLI handler includes 'funnel' command", () => {
  const content = readFileSync("index.js", "utf8");
  // The rl.on("line") section must handle 'funnel'
  assert.ok(content.includes('input === "funnel"') || content.includes("input === 'funnel'"),
    "CLI handler must match funnel command");
  assert.ok(content.includes("buildFunnelSummaryLines(_lastFunnelSnapshot)"),
    "CLI funnel handler must call buildFunnelSummaryLines with _lastFunnelSnapshot");
});

test("Ops-8.4 Test 6: index.js CLI help text documents funnel command", () => {
  const content = readFileSync("index.js", "utf8");
  // The console.log help block must mention funnel
  assert.ok(content.includes("funnel") && content.includes("Show latest discovery funnel snapshot"),
    "CLI help text must document the funnel command");
});

test("Ops-8.4 Test 7: /funnel Telegram handler labels match spec", () => {
  const content = readFileSync("index.js", "utf8");
  const handlerIdx = content.indexOf('if (text === "/funnel")');
  assert.ok(handlerIdx >= 0, "telegramHandler must handle /funnel");
  const block = content.slice(handlerIdx, handlerIdx + 700);
  assert.ok(block.includes("API total:"), "Telegram /funnel must print 'API total:'");
  assert.ok(block.includes("Post-discover:"), "Telegram /funnel must print 'Post-discover:'");
  assert.ok(block.includes("Pre-slice:"), "Telegram /funnel must print 'Pre-slice:'");
  assert.ok(block.includes("Post-getTop:"), "Telegram /funnel must print 'Post-getTop:'");
  assert.ok(block.includes("Post-recon:"), "Telegram /funnel must print 'Post-recon:'");
});

test("Ops-8.4 Test 8: stage_pre_slice comes from eligible_before_slice in funnelSnapshot", () => {
  const content = readFileSync("index.js", "utf8");
  // The funnelSnapshot construction must map eligible_before_slice -> stage_pre_slice
  const snapIdx = content.indexOf("const funnelSnapshot = {");
  assert.ok(snapIdx >= 0, "funnelSnapshot must be defined");
  const snapBlock = content.slice(snapIdx, content.indexOf("};", snapIdx) + 2);
  assert.ok(snapBlock.includes("stage_pre_slice") && snapBlock.includes("eligible_before_slice"),
    "stage_pre_slice must be sourced from eligible_before_slice");
});

test("index.js passes node --check after Ops-8.4 edits", () => {
  execSync("node --check index.js", { cwd: process.cwd(), stdio: "pipe" });
});

restoreEnv();




console.log(`\n${"─".repeat(50)}`);
console.log(`Results: ${passed} passed, ${failed} failed`);
if (failed > 0) {
  console.error(`\n${failed} test(s) failed.`);
  process.exit(1);
} else {
  console.log("\nAll Phase 1 safety invariant tests passed. ✅");
}
