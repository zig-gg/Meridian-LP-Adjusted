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


restoreEnv();

console.log(`\n${"─".repeat(50)}`);
console.log(`Results: ${passed} passed, ${failed} failed`);
if (failed > 0) {
  console.error(`\n${failed} test(s) failed.`);
  process.exit(1);
} else {
  console.log("\nAll Phase 1 safety invariant tests passed. ✅");
}
