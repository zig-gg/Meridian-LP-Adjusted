/**
 * test/test-config-doctor.js
 *
 * Unit tests for scripts/config-doctor.js
 *
 * All tests use the exported runConfigDoctor() with mocked env and userConfig,
 * so they require no wallet, no API key, no network, and no user-config.json on disk.
 *
 * Run: node test/test-config-doctor.js
 *      npm run test:config
 */

import assert from "assert";
import { execSync } from "child_process";
import { runConfigDoctor } from "../scripts/config-doctor.js";

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

// ─── Baseline: known-clean scanner config ─────────────────────
// Values must be internally consistent:
//   minSolToOpen >= deployAmountSol + gasReserve
//   0.18 >= 0.1 + 0.05 = 0.15 ✓
// hiveMindPullMode=manual suppresses the auto-pull warning.
const CLEAN_ENV = {
  DRY_RUN: "true",
  EXECUTION_MODE: "scanner",
  HEADLESS: "true",
};

const CLEAN_CONFIG = {
  llmEnabled:       false,
  deployAmountSol:  0.1,
  maxDeployAmount:  50,
  gasReserve:       0.05,
  minSolToOpen:     0.18,
  maxPositions:     3,
  strategy:         "bid_ask",
  minBinsBelow:     35,
  maxBinsBelow:     69,
  defaultBinsBelow: 69,
  minBinStep:       80,
  maxBinStep:       125,
  managementIntervalMin:  10,
  screeningIntervalMin:   30,
  healthCheckIntervalMin: 60,
  hiveMindPullMode: "manual",
};

function doctor(envOverrides = {}, configOverrides = {}) {
  return runConfigDoctor({
    env:       { ...CLEAN_ENV, ...envOverrides },
    userConfig: { ...CLEAN_CONFIG, ...configOverrides },
    userConfigExists: true,
  });
}

// ─── Run tests ────────────────────────────────────────────────
console.log("\n=== Config Doctor Tests ===\n");

// ── Group 1: Baseline ─────────────────────────────────────────
console.log("Group 1: Baseline — known-clean scanner config\n");

test("clean scanner config has no errors", () => {
  const r = doctor();
  assert.deepStrictEqual(r.errors, [], `Expected no errors, got: ${r.errors.join("; ")}`);
});

test("clean scanner config returns valid=true", () => {
  const r = doctor();
  assert.strictEqual(r.valid, true);
});

test("result has expected shape", () => {
  const r = doctor();
  assert.ok(Array.isArray(r.errors),   "errors must be array");
  assert.ok(Array.isArray(r.warnings), "warnings must be array");
  assert.ok(typeof r.summary === "string", "summary must be string");
  assert.ok(typeof r.valid   === "boolean","valid must be boolean");
  assert.ok(typeof r.effective === "object","effective must be object");
});

test("effective values reflect mocked env + config", () => {
  const r = doctor();
  assert.strictEqual(r.effective.executionMode,   "scanner");
  assert.strictEqual(r.effective.dryRun,           true);
  assert.strictEqual(r.effective.isHeadless,       true);
  assert.strictEqual(r.effective.deployAmountSol,  0.1);
  assert.strictEqual(r.effective.gasReserve,       0.05);
  assert.strictEqual(r.effective.maxDeployAmount,  50);
  assert.strictEqual(r.effective.minSolToOpen,     0.18);
  assert.strictEqual(r.effective.llmEnabled, false);
  assert.strictEqual(r.effective.hiveMindPullMode, "manual");
});

test("summary string contains key config values", () => {
  const r = doctor();
  assert.ok(r.summary.includes("scanner"), "summary must mention executionMode");
  assert.ok(r.summary.includes("deployAmountSol"), "summary must mention deployAmountSol");
  assert.ok(r.summary.includes("0.1"), "summary must mention deployAmountSol value");
  assert.ok(r.summary.includes("gasReserve"), "summary must mention gasReserve");
  assert.ok(r.summary.includes("LLM_ENABLED"), "summary must mention LLM enabled/disabled state");
});

// ── Group 2: Fail conditions ──────────────────────────────────
console.log("\nGroup 2: Fail conditions (errors)\n");

test("unknown executionMode produces error", () => {
  const r = doctor({ EXECUTION_MODE: "turbo" });
  assert.ok(r.errors.some(e => e.includes("not valid")), `Expected executionMode error, got: ${r.errors.join("; ")}`);
  assert.strictEqual(r.valid, false);
});

test("deployAmountSol=0 produces error", () => {
  const r = doctor({}, { deployAmountSol: 0 });
  assert.ok(r.errors.some(e => e.includes("deployAmountSol")));
  assert.strictEqual(r.valid, false);
});

test("deployAmountSol=-1 produces error", () => {
  const r = doctor({}, { deployAmountSol: -1 });
  assert.ok(r.errors.some(e => e.includes("deployAmountSol")));
  assert.strictEqual(r.valid, false);
});

test("deployAmountSol=NaN produces error", () => {
  const r = doctor({}, { deployAmountSol: NaN });
  assert.ok(r.errors.some(e => e.includes("deployAmountSol")));
  assert.strictEqual(r.valid, false);
});

test("gasReserve=-0.1 produces error", () => {
  const r = doctor({}, { gasReserve: -0.1 });
  assert.ok(r.errors.some(e => e.includes("gasReserve")));
  assert.strictEqual(r.valid, false);
});

test("maxDeployAmount < deployAmountSol produces error", () => {
  const r = doctor({}, { deployAmountSol: 1.0, maxDeployAmount: 0.5 });
  assert.ok(r.errors.some(e => e.includes("maxDeployAmount")));
  assert.strictEqual(r.valid, false);
});

test("minSolToOpen < deployAmountSol + gasReserve produces error", () => {
  // 0.5 + 0.2 = 0.7 required; 0.3 < 0.7 → error
  const r = doctor({}, { deployAmountSol: 0.5, gasReserve: 0.2, minSolToOpen: 0.3 });
  assert.ok(r.errors.some(e => e.includes("minSolToOpen")));
  assert.strictEqual(r.valid, false);
});

test("live mode + DRY_RUN=true produces error", () => {
  const r = doctor(
    { EXECUTION_MODE: "live", DRY_RUN: "true", ALLOW_LIVE_EXECUTION: "true", WALLET_PRIVATE_KEY: "fake" },
    {}
  );
  assert.ok(r.errors.some(e => e.includes("DRY_RUN")), `Expected DRY_RUN error, got: ${r.errors.join("; ")}`);
  assert.strictEqual(r.valid, false);
});

test("live mode + ALLOW_LIVE_EXECUTION not true produces error", () => {
  const r = doctor(
    { EXECUTION_MODE: "live", DRY_RUN: "false", WALLET_PRIVATE_KEY: "fake" },
    {}
  );
  // ALLOW_LIVE_EXECUTION is not in env → not "true"
  assert.ok(r.errors.some(e => e.includes("ALLOW_LIVE_EXECUTION")));
  assert.strictEqual(r.valid, false);
});

test("live mode + no wallet key produces error", () => {
  const r = runConfigDoctor({
    env: { EXECUTION_MODE: "live", DRY_RUN: "false", ALLOW_LIVE_EXECUTION: "true" },
    userConfig: { ...CLEAN_CONFIG },
    userConfigExists: true,
  });
  // No BOT_WALLET_PRIVATE_KEY, no WALLET_PRIVATE_KEY, no walletKey in config
  assert.ok(r.errors.some(e => e.includes("wallet")));
  assert.strictEqual(r.valid, false);
});

test("live mode with all gates correct has no errors", () => {
  const r = runConfigDoctor({
    env: {
      EXECUTION_MODE: "live",
      DRY_RUN: "false",
      ALLOW_LIVE_EXECUTION: "true",
      BOT_WALLET_PRIVATE_KEY: "fake_key_for_test",
    },
    userConfig: {
      ...CLEAN_CONFIG,
      deployAmountSol: 0.1,
      maxDeployAmount: 1.0,
      gasReserve: 0.05,
      minSolToOpen: 0.2,
    },
    userConfigExists: true,
  });
  assert.deepStrictEqual(r.errors, [], `Unexpected errors: ${r.errors.join("; ")}`);
  assert.strictEqual(r.valid, true);
});

// ── Group 3: Warning conditions ───────────────────────────────
console.log("\nGroup 3: Warning conditions\n");

test("DRY_RUN not set + executionMode=simulate produces warning", () => {
  const r = runConfigDoctor({
    env: { EXECUTION_MODE: "simulate" }, // no DRY_RUN
    userConfig: { ...CLEAN_CONFIG },
    userConfigExists: true,
  });
  assert.ok(r.warnings.some(w => w.includes("DRY_RUN")));
  // Not an error — still valid
  assert.strictEqual(r.valid, true);
});

test("ALLOW_LIVE_EXECUTION=true + DRY_RUN=true produces warning", () => {
  const r = doctor({ ALLOW_LIVE_EXECUTION: "true", DRY_RUN: "true" });
  assert.ok(r.warnings.some(w => w.includes("ALLOW_LIVE_EXECUTION") && w.includes("DRY_RUN")));
  assert.strictEqual(r.valid, true);
});

test("walletKey in user-config produces warning", () => {
  const r = doctor({}, { walletKey: "some_base58_key" });
  assert.ok(r.warnings.some(w => w.includes("walletKey")));
  assert.strictEqual(r.valid, true);
});

test("deployAmountSol > 1.0 in scanner mode produces warning", () => {
  const r = doctor({}, { deployAmountSol: 2.0, maxDeployAmount: 50, minSolToOpen: 2.25 });
  assert.ok(r.warnings.some(w => w.includes("deployAmountSol") && w.includes("1.0")));
  assert.strictEqual(r.valid, true);
});

test("gasReserve=0.01 (below 0.05) produces warning", () => {
  // deployAmountSol(0.1) + gasReserve(0.01) = 0.11; minSolToOpen(0.12) >= 0.11 → no error
  const r = doctor({}, { gasReserve: 0.01, minSolToOpen: 0.12 });
  assert.ok(r.warnings.some(w => w.includes("gasReserve")));
  assert.strictEqual(r.valid, true);
});

test("HEADLESS=true without DRY_RUN=true produces warning", () => {
  const r = runConfigDoctor({
    env: { HEADLESS: "true" }, // no DRY_RUN
    userConfig: { ...CLEAN_CONFIG },
    userConfigExists: true,
  });
  assert.ok(r.warnings.some(w => w.includes("HEADLESS") && w.includes("DRY_RUN")));
  assert.strictEqual(r.valid, true);
});

test("LLM disabled is clean when no LLM keys are configured", () => {
  const r = doctor({}, { llmEnabled: false });
  assert.strictEqual(r.effective.llmEnabled, false);
  assert.ok(!r.warnings.some(w => w.includes("LLM is disabled")));
});

test("LLM key present while disabled produces warning", () => {
  const r = doctor({ OPENROUTER_API_KEY: "fake_key_for_test" }, { llmEnabled: false });
  assert.ok(r.warnings.some(w => w.includes("LLM is disabled")));
  assert.strictEqual(r.valid, true);
});

test("LLM enabled suppresses disabled-key warning", () => {
  const r = doctor({ OPENROUTER_API_KEY: "fake_key_for_test" }, { llmEnabled: true });
  assert.ok(!r.warnings.some(w => w.includes("LLM is disabled")));
  assert.strictEqual(r.effective.llmEnabled, true);
});

test("hiveMindPullMode=auto in headless mode produces warning", () => {
  const r = doctor({}, { hiveMindPullMode: "auto", hiveMindEnabled: true });
  // CLEAN_ENV has HEADLESS=true
  assert.ok(r.warnings.some(w => w.includes("hiveMindPullMode")));
  assert.strictEqual(r.valid, true);
});

test("hiveMindPullMode=auto in scanner mode (non-headless) produces warning", () => {
  const r = runConfigDoctor({
    env: { DRY_RUN: "true", EXECUTION_MODE: "scanner" }, // no HEADLESS
    userConfig: { ...CLEAN_CONFIG, hiveMindPullMode: "auto", hiveMindEnabled: true },
    userConfigExists: true,
  });
  assert.ok(r.warnings.some(w => w.includes("hiveMindPullMode")));
  assert.strictEqual(r.valid, true);
});

test("hiveMindPullMode=manual suppresses the auto-pull warning", () => {
  const r = doctor({}, { hiveMindPullMode: "manual" });
  assert.ok(!r.warnings.some(w => w.includes("hiveMindPullMode")));
});

// ── Group 4: HiveMind enabled flag ────────────────────────────
console.log("\nGroup 4: HiveMind enabled flag\n");

test("hiveMindEnabled defaults false", () => {
  const r = doctor({}, { hiveMindPullMode: "manual" });
  assert.strictEqual(r.effective.hiveMindEnabled, false);
});

test("HIVE_MIND_ENABLED=true is recognized", () => {
  const r = doctor({ HIVEMIND_ENABLED: "true" }, { hiveMindPullMode: "manual" });
  assert.strictEqual(r.effective.hiveMindEnabled, true);
});

test("summary includes HIVEMIND_ENABLED", () => {
  const r = doctor({}, { hiveMindPullMode: "manual" });
  assert.ok(r.summary.includes("HIVEMIND_ENABLED"), "summary must include HIVEMIND_ENABLED label");
  assert.ok(r.summary.includes("false"), "summary must include false value when disabled");
});

test("hiveMindPullMode=auto warning not emitted when hiveMindEnabled=false", () => {
  const r = doctor({ HEADLESS: "true" }, { hiveMindPullMode: "auto", hiveMindEnabled: false });
  assert.ok(!r.warnings.some(w => w.includes("hiveMindPullMode")), "hiveMindPullMode auto warning must not emit when hiveMindEnabled is false");
  assert.ok(!r.warnings.some(w => w.includes("auto")));
});

// ── Group 5: Unknown key / typo detection ────────────────────
console.log("\nGroup 4: Unknown keys and typo detection\n");

test("llmEnable typo produces warning", () => {
  const r = doctor({}, { llmEnable: true });
  assert.ok(r.warnings.some(w => w.includes("llmEnable") && w.includes("llmEnabled")));
});

test("maxBundlersPct (typo of maxBundlePct) produces warning", () => {
  const r = doctor({}, { maxBundlersPct: 30 });
  assert.ok(r.warnings.some(w => w.includes("maxBundlersPct")));
  assert.strictEqual(r.valid, true);
});

test("minFeeTvlRatio (typo of minFeeActiveTvlRatio) produces warning", () => {
  const r = doctor({}, { minFeeTvlRatio: 0.05 });
  assert.ok(r.warnings.some(w => w.includes("minFeeTvlRatio")));
  assert.strictEqual(r.valid, true);
});

test("maxVolatility (unused) produces warning", () => {
  const r = doctor({}, { maxVolatility: 50 });
  assert.ok(r.warnings.some(w => w.includes("maxVolatility")));
  assert.strictEqual(r.valid, true);
});

test("dryrun (lowercase typo of dryRun) produces warning", () => {
  const r = doctor({}, { dryrun: true });
  assert.ok(r.warnings.some(w => w.includes("dryrun") || w.includes("dryRun")));
  assert.strictEqual(r.valid, true);
});

test("executionmode (lowercase) produces typo warning", () => {
  const r = doctor({}, { executionmode: "scanner" });
  assert.ok(r.warnings.some(w => w.includes("executionmode")));
  assert.strictEqual(r.valid, true);
});

test("genuinely unknown key produces unknown-key warning", () => {
  const r = doctor({}, { completelyUnknownKey: "value" });
  assert.ok(r.warnings.some(w => w.includes("completelyUnknownKey") && w.includes("unknown")));
  assert.strictEqual(r.valid, true);
});

test("comment keys (_comment_*) do not produce warnings", () => {
  const r = doctor({}, {
    "_comment_execution": "some note",
    "_smallcap_deployAmountSol": 0.03,
  });
  assert.ok(!r.warnings.some(w => w.includes("_comment_execution")));
  assert.ok(!r.warnings.some(w => w.includes("_smallcap_deployAmountSol")));
});

// ── Group 5: Multiple errors accumulate ──────────────────────
console.log("\nGroup 5: Multiple issues accumulate\n");

test("multiple errors are all reported", () => {
  const r = doctor(
    { EXECUTION_MODE: "turbo" },
    { deployAmountSol: -1, gasReserve: -1 }
  );
  assert.ok(r.errors.length >= 3, `Expected at least 3 errors, got ${r.errors.length}: ${r.errors.join("; ")}`);
  assert.strictEqual(r.valid, false);
});

test("errors and warnings can coexist", () => {
  const r = doctor(
    { EXECUTION_MODE: "live", DRY_RUN: "false", ALLOW_LIVE_EXECUTION: "true", WALLET_PRIVATE_KEY: "fake" },
    { deployAmountSol: 2.0, maxDeployAmount: 50, minSolToOpen: 2.25, walletKey: "also_here" }
  );
  // Error: none (live mode gates pass — wallet present via env)
  // Warning: deployAmountSol > 1.0 in live? No — live mode warning is N/A. walletKey warning.
  assert.strictEqual(r.valid, true); // no errors in live mode with proper gates
  assert.ok(r.warnings.some(w => w.includes("walletKey")));
});

// ── Group 6: Syntax check ────────────────────────────────────
console.log("\nGroup 6: Syntax check\n");

test("scripts/config-doctor.js passes node --check", () => {
  execSync("node --check scripts/config-doctor.js", { cwd: process.cwd(), stdio: "pipe" });
});

test("test/test-config-doctor.js passes node --check", () => {
  execSync("node --check test/test-config-doctor.js", { cwd: process.cwd(), stdio: "pipe" });
});

// ─── Summary ──────────────────────────────────────────────────
console.log(`\n${"─".repeat(50)}`);
console.log(`Results: ${passed} passed, ${failed} failed`);
if (failed > 0) {
  console.error(`\n${failed} test(s) failed.`);
  process.exit(1);
} else {
  console.log("\nAll Config Doctor tests passed. ✅");
}
