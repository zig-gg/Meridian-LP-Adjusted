#!/usr/bin/env node
/**
 * scripts/config-doctor.js
 *
 * Config Doctor — validates effective runtime configuration before
 * any cron cycle starts and prints a concise diagnostic report.
 *
 * Usage (standalone):
 *   node scripts/config-doctor.js
 *   npm run config:check
 *
 * Exported function (for index.js startup integration):
 *   import { runConfigDoctor } from "./scripts/config-doctor.js";
 *   const result = runConfigDoctor();
 *   if (!result.valid) process.exit(1);
 *
 * Return value: { valid: boolean, errors: string[], warnings: string[], summary: string }
 *
 * - errors   → hard failures; process should not start in live/simulate mode
 * - warnings → noteworthy but non-fatal; process may continue in scanner/dry-run mode
 * - valid    → true when errors.length === 0
 *
 * In scanner/headless/dry-run mode the caller should log warnings and continue.
 * In live mode the caller should treat any error as a hard stop.
 */

import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.join(__dirname, "..");
const USER_CONFIG_PATH = path.join(ROOT, "user-config.json");

// ─── Known config keys ────────────────────────────────────────
// Derived from user-config.example.json + config.js.
// Any key in user-config.json that is NOT in this set triggers an unknown-key warning
// (catches typos like dryrun, maxBundlersPct, minFeeTvlRatio, etc.)
const KNOWN_KEYS = new Set([
  // meta / comments
  "preset",

  // Phase 1 execution scaffold
  "executionMode", "approvalRequired", "allowLiveExecution",

  // connection
  "rpcUrl", "llmBaseUrl", "llmApiKey", "llmModel", "dryRun",
  "walletKey",  // NOTE: silently injects WALLET_PRIVATE_KEY — triggers a warning

  // risk / deploy
  "deployAmountSol", "maxPositions", "minSolToOpen", "maxDeployAmount",
  "gasReserve", "positionSizePct",

  // strategy
  "strategy", "minBinsBelow", "maxBinsBelow", "defaultBinsBelow", "binsBelow",

  // screening
  "timeframe", "category", "excludeHighSupplyConcentration",
  "minTvl", "maxTvl", "minVolume", "minOrganic", "minQuoteOrganic",
  "minHolders", "minMcap", "maxMcap", "minBinStep", "maxBinStep",
  "minFeeActiveTvlRatio", "minTokenFeesSol",
  "useDiscordSignals", "discordSignalMode",
  "avoidPvpSymbols", "blockPvpSymbols",
  "maxBundlePct", "maxBotHoldersPct", "maxTop10Pct",
  "allowedLaunchpads", "blockedLaunchpads",
  "minTokenAgeHours", "maxTokenAgeHours", "athFilterPct",

  // management
  "minClaimAmount", "autoSwapAfterClaim",
  "outOfRangeBinsToClose", "outOfRangeWaitMinutes",
  "oorCooldownTriggerCount", "oorCooldownHours",
  "repeatDeployCooldownEnabled", "repeatDeployCooldownTriggerCount",
  "repeatDeployCooldownHours", "repeatDeployCooldownScope",
  "repeatDeployCooldownMinFeeEarnedPct", "repeatDeployCooldownMinFeeYieldPct",
  "minVolumeToRebalance",
  "stopLossPct", "takeProfitPct", "emergencyPriceDropPct", "takeProfitFeePct",
  "minFeePerTvl24h", "minAgeBeforeYieldCheck",
  "trailingTakeProfit", "trailingTriggerPct", "trailingDropPct",
  "pnlSanityMaxDiffPct", "solMode",
  "minSolToOpen",

  // schedule
  "managementIntervalMin", "screeningIntervalMin", "healthCheckIntervalMin",

  // LLM
  "llmEnabled", "temperature", "maxTokens", "maxSteps",
  "managementModel", "screeningModel", "generalModel",

  // darwin
  "darwinEnabled", "darwinWindowDays", "darwinRecalcEvery",
  "darwinBoost", "darwinDecay", "darwinFloor", "darwinCeiling", "darwinMinSamples",

  // api / relay
  "agentId", "publicApiKey", "agentMeridianApiUrl", "lpAgentRelayEnabled",

  // hivemind
  "hiveMindUrl", "hiveMindApiKey", "hiveMindPullMode",

  // chart indicators (nested object — only the key itself is checked here)
  "chartIndicators",

  // telegram
  "telegramChatId",
]);

// Keys that look like common typos of real keys.
// Format: { typo: string, correct: string }
const LIKELY_TYPOS = [
  { typo: "maxBundlersPct",       correct: "maxBundlePct" },
  { typo: "maxBundlerPct",        correct: "maxBundlePct" },
  { typo: "minFeeTvlRatio",       correct: "minFeeActiveTvlRatio" },
  { typo: "minFeePerTvlRatio",    correct: "minFeeActiveTvlRatio" },
  { typo: "minFeeTvl24h",         correct: "minFeePerTvl24h" },
  { typo: "maxVolatility",        correct: "(unused — remove this key)" },
  { typo: "minVolatility",        correct: "(unused — remove this key)" },
  { typo: "dryrun",               correct: "dryRun" },
  { typo: "dry_run",              correct: "dryRun" },
  { typo: "executionmode",        correct: "executionMode" },
  { typo: "execution_mode",       correct: "executionMode" },
  { typo: "deployAmount",         correct: "deployAmountSol" },
  { typo: "deployAmountSOL",      correct: "deployAmountSol" },
  { typo: "maxDeploy",            correct: "maxDeployAmount" },
  { typo: "gasreserve",           correct: "gasReserve" },
  { typo: "gas_reserve",          correct: "gasReserve" },
  { typo: "minsoltoopen",         correct: "minSolToOpen" },
  { typo: "approvalrequired",     correct: "approvalRequired" },
  { typo: "allowLiveExec",        correct: "allowLiveExecution" },
  { typo: "hivemindpullmode",     correct: "hiveMindPullMode" },
  { typo: "hivemind_pull_mode",   correct: "hiveMindPullMode" },
  { typo: "llmenabled",           correct: "llmEnabled" },
  { typo: "llmEnable",            correct: "llmEnabled" },
  { typo: "llm_enabled",          correct: "llmEnabled" },
];

const TYPO_MAP = new Map(LIKELY_TYPOS.map(({ typo, correct }) => [typo.toLowerCase(), { typo, correct }]));

const VALID_EXECUTION_MODES = new Set(["scanner", "simulate", "paper", "live"]);

// ─── Helpers ──────────────────────────────────────────────────

function booleanConfig(value) {
  if (value === true || value === "true") return true;
  if (value === false || value === "false") return false;
  return null;
}

function isFinitePositive(v) {
  return typeof v === "number" && Number.isFinite(v) && v > 0;
}

function isFiniteNonNeg(v) {
  return typeof v === "number" && Number.isFinite(v) && v >= 0;
}

function loadUserConfig() {
  if (!fs.existsSync(USER_CONFIG_PATH)) return { exists: false, data: {} };
  try {
    return { exists: true, data: JSON.parse(fs.readFileSync(USER_CONFIG_PATH, "utf8")) };
  } catch (err) {
    return { exists: true, data: {}, parseError: err.message };
  }
}

// ─── Core validation ──────────────────────────────────────────

/**
 * Run all config checks.
 *
 * @param {object} opts
 * @param {object}  opts.env       - process.env (or a mock for testing)
 * @param {object}  opts.userConfig - parsed user-config.json contents (or a mock)
 * @param {boolean} opts.userConfigExists - whether user-config.json was present
 * @param {object}  opts.runtimeConfig - the live config object (optional; used for cross-checks)
 * @returns {{ valid: boolean, errors: string[], warnings: string[], summary: string }}
 */
export function runConfigDoctor({
  env = process.env,
  userConfig = null,
  userConfigExists = true,
  runtimeConfig = null,
} = {}) {
  const errors   = [];
  const warnings = [];

  // Load user-config.json if caller did not provide it
  if (userConfig === null) {
    const loaded = loadUserConfig();
    userConfigExists = loaded.exists;
    userConfig = loaded.data;
    if (loaded.parseError) {
      errors.push(`user-config.json is not valid JSON: ${loaded.parseError}`);
      // Can't do further config-file checks; continue with env-only checks
      userConfig = {};
    }
  }

  // ── Resolve effective values ──────────────────────────────────
  // Mirror config.js resolution logic so the doctor sees exactly what runs.

  const effectiveExecutionMode = (() => {
    const fromEnv = env.EXECUTION_MODE;
    if (fromEnv) return String(fromEnv).toLowerCase().trim();
    const fromConfig = userConfig.executionMode;
    if (fromConfig) return String(fromConfig).toLowerCase().trim();
    return "scanner";
  })();

  const effectiveDryRun = (() => {
    // config.js: if (u.dryRun !== undefined) process.env.DRY_RUN ||= String(u.dryRun);
    // env wins via ||= only if already set before config loads.
    // The doctor warns when neither env nor config forces dry-run.
    if (env.DRY_RUN === "true")  return true;
    if (env.DRY_RUN === "false") return false;
    // DRY_RUN not in env — check user-config
    if (userConfig.dryRun === true  || userConfig.dryRun === "true")  return true;
    if (userConfig.dryRun === false || userConfig.dryRun === "false") return false;
    return null; // not set anywhere
  })();

  const allowLiveExecution = env.ALLOW_LIVE_EXECUTION === "true";
  const isHeadless = env.HEADLESS === "true" || env.INTERACTIVE === "false";

  // Numeric config values — prefer runtime config when available
  const num = (runtimeKey, userConfigKey, defaultVal) => {
    if (runtimeConfig) {
      // Walk dotted key: "management.deployAmountSol"
      const parts = runtimeKey.split(".");
      let val = runtimeConfig;
      for (const p of parts) val = val?.[p];
      if (typeof val === "number" && Number.isFinite(val)) return val;
    }
    const fromUser = userConfig[userConfigKey];
    if (typeof fromUser === "number" && Number.isFinite(fromUser)) return fromUser;
    return defaultVal;
  };

  const deployAmountSol = num("management.deployAmountSol", "deployAmountSol", 0.5);
  const maxDeployAmount = num("risk.maxDeployAmount",       "maxDeployAmount", 50);
  const gasReserve      = num("management.gasReserve",      "gasReserve",      0.2);
  const minSolToOpen    = num("management.minSolToOpen",    "minSolToOpen",    0.55);
  const maxPositions    = num("risk.maxPositions",          "maxPositions",    3);
  const minBinsBelow    = num("strategy.minBinsBelow",      "minBinsBelow",    35);
  const maxBinsBelow    = num("strategy.maxBinsBelow",      "maxBinsBelow",    69);
  const defaultBinsBelow = num("strategy.defaultBinsBelow", "defaultBinsBelow", 69);
  const minBinStep      = num("screening.minBinStep",       "minBinStep",      80);
  const maxBinStep      = num("screening.maxBinStep",       "maxBinStep",      125);
  const mgmtInterval    = num("schedule.managementIntervalMin",  "managementIntervalMin",  10);
  const screenInterval  = num("schedule.screeningIntervalMin",   "screeningIntervalMin",   30);
  const healthInterval  = num("schedule.healthCheckIntervalMin", "healthCheckIntervalMin", 60);
  const hiveMindPullMode = runtimeConfig?.hiveMind?.pullMode
    ?? userConfig.hiveMindPullMode
    ?? "auto";

  const llmEnabled = runtimeConfig?.llm?.enabled
    ?? booleanConfig(userConfig.llmEnabled ?? env.LLM_ENABLED)
    ?? false;

  const llmKeyPresent = Boolean(
    env.OPENROUTER_API_KEY ||
    env.OPENAI_API_KEY ||
    env.LLM_API_KEY ||
    userConfig.llmApiKey
  );

  const strategy = runtimeConfig?.strategy?.strategy ?? userConfig.strategy ?? "bid_ask";

  // ── FAIL CONDITIONS ───────────────────────────────────────────

  // 1. executionMode must be a known value
  if (!VALID_EXECUTION_MODES.has(effectiveExecutionMode)) {
    errors.push(
      `executionMode "${effectiveExecutionMode}" is not valid. ` +
      `Must be one of: ${[...VALID_EXECUTION_MODES].join(", ")}.`
    );
  }

  // 2. deployAmountSol must be a finite positive number
  if (!isFinitePositive(deployAmountSol)) {
    errors.push(
      `deployAmountSol "${deployAmountSol}" is not a valid positive number.`
    );
  }

  // 3. gasReserve must be >= 0
  if (!isFiniteNonNeg(gasReserve)) {
    errors.push(
      `gasReserve "${gasReserve}" is negative or invalid. Must be >= 0.`
    );
  }

  // 4. maxDeployAmount must be >= deployAmountSol
  if (
    isFinitePositive(deployAmountSol) &&
    isFiniteNonNeg(gasReserve) &&
    typeof maxDeployAmount === "number" &&
    maxDeployAmount < deployAmountSol
  ) {
    errors.push(
      `maxDeployAmount (${maxDeployAmount}) is less than deployAmountSol (${deployAmountSol}). ` +
      `Every deploy attempt will be blocked.`
    );
  }

  // 5. minSolToOpen must be >= deployAmountSol + gasReserve
  if (
    isFinitePositive(deployAmountSol) &&
    isFiniteNonNeg(gasReserve)
  ) {
    const required = deployAmountSol + gasReserve;
    if (typeof minSolToOpen === "number" && minSolToOpen < required) {
      errors.push(
        `minSolToOpen (${minSolToOpen} SOL) is less than deployAmountSol + gasReserve ` +
        `(${deployAmountSol} + ${gasReserve} = ${required.toFixed(4)} SOL). ` +
        `Screening will trigger but deploy will always fail the balance gate.`
      );
    }
  }

  // 6. Live-mode gate conflicts (hard errors only when executionMode=live)
  if (effectiveExecutionMode === "live") {
    if (effectiveDryRun === true) {
      errors.push(
        `executionMode=live but DRY_RUN=true. ` +
        `Live execution is impossible while DRY_RUN is set. ` +
        `Unset DRY_RUN or change executionMode.`
      );
    }
    if (!allowLiveExecution) {
      errors.push(
        `executionMode=live but ALLOW_LIVE_EXECUTION is not "true". ` +
        `Set ALLOW_LIVE_EXECUTION=true in .env to enable live execution.`
      );
    }
    const hasWallet = !!(
      env.BOT_WALLET_PRIVATE_KEY ||
      env.WALLET_PRIVATE_KEY ||
      userConfig.walletKey
    );
    if (!hasWallet) {
      errors.push(
        `executionMode=live but no wallet private key is configured. ` +
        `Set BOT_WALLET_PRIVATE_KEY in .env (dedicated bot wallet).`
      );
    }
  }

  // ── WARNING CONDITIONS ────────────────────────────────────────

  // 7. DRY_RUN not set and executionMode is not scanner
  if (effectiveDryRun === null && effectiveExecutionMode !== "scanner") {
    warnings.push(
      `DRY_RUN is not set anywhere (env or user-config) and executionMode="${effectiveExecutionMode}". ` +
      `In non-scanner modes this may attempt live operations. ` +
      `Set DRY_RUN=true to be explicit.`
    );
  }

  // 8. executionMode=live without ALLOW_LIVE_EXECUTION=true (warning if not already an error)
  if (effectiveExecutionMode === "live" && !allowLiveExecution && effectiveDryRun !== true) {
    // Already covered by error #6 when it's a hard conflict; this is the warning-only case
    // (already errors if live mode active) — skip duplicate
  }

  // 9. ALLOW_LIVE_EXECUTION=true while DRY_RUN=true (contradictory)
  if (allowLiveExecution && effectiveDryRun === true) {
    warnings.push(
      `ALLOW_LIVE_EXECUTION=true but DRY_RUN=true. ` +
      `Live execution is blocked by DRY_RUN — the ALLOW_LIVE_EXECUTION flag has no effect. ` +
      `This combination is confusing; consider unsetting ALLOW_LIVE_EXECUTION in dry-run mode.`
    );
  }

  // 10. walletKey in user-config.json silently injects WALLET_PRIVATE_KEY
  if (userConfig.walletKey) {
    warnings.push(
      `user-config.json contains "walletKey". This silently injects WALLET_PRIVATE_KEY ` +
      `into the process environment at import time. ` +
      `Prefer keeping private keys exclusively in .env (gitignored). ` +
      `If this is intentional, confirm the key is not committed to version control.`
    );
  }

  // 11. deployAmountSol > 1.0 SOL in scanner/dry-run mode
  if (
    isFinitePositive(deployAmountSol) &&
    deployAmountSol > 1.0 &&
    (effectiveExecutionMode === "scanner" || effectiveDryRun === true)
  ) {
    warnings.push(
      `deployAmountSol is ${deployAmountSol} SOL — this is above 1.0 SOL in ` +
      `${effectiveDryRun === true ? "dry-run" : effectiveExecutionMode} mode. ` +
      `This is likely a misconfiguration for scanner/dry-run use. ` +
      `Consider setting deployAmountSol to 0.03–0.1 SOL for conservative testing.`
    );
  }

  // 12. gasReserve < 0.05
  if (isFiniteNonNeg(gasReserve) && gasReserve < 0.05) {
    warnings.push(
      `gasReserve is ${gasReserve} SOL, which is below the recommended minimum of 0.05 SOL. ` +
      `Low gas reserve may cause deploy attempts to fail the balance gate unexpectedly.`
    );
  }

  // 13. HEADLESS=true without DRY_RUN=true
  if (isHeadless && effectiveDryRun !== true) {
    warnings.push(
      `HEADLESS=true but DRY_RUN is not "true". ` +
      `Daemon mode running without forced dry-run — execution mode is "${effectiveExecutionMode}". ` +
      `The "daemon" npm script sets DRY_RUN=true automatically; ` +
      `if running PM2 directly, add DRY_RUN=true to the PM2 env.`
    );
  }

  // 14. hiveMindPullMode=auto in headless/scanner mode
  if (
    hiveMindPullMode === "auto" &&
    (isHeadless || effectiveExecutionMode === "scanner")
  ) {
    warnings.push(
      `hiveMindPullMode="auto" in ${isHeadless ? "headless" : "scanner"} mode. ` +
      `Lessons and presets will be pulled from the shared HiveMind network on every heartbeat. ` +
      `This affects agent reasoning in future cycles. ` +
      `Set hiveMindPullMode="manual" in user-config.json for an isolated deployment.`
    );
  }

  // 15.1 LLM keys present while LLM is disabled
  if (!llmEnabled && llmKeyPresent) {
    warnings.push(
      `LLM is disabled, but an LLM API key is configured. ` +
      `The key should not be used while llmEnabled=false / LLM_ENABLED=false. ` +
      `Remove OPENROUTER_API_KEY, OPENAI_API_KEY, LLM_API_KEY, or llmApiKey unless you intentionally enable LLM calls.`
    );
  }

  // 15.2 Unknown keys in user-config.json (catches typos)
  for (const key of Object.keys(userConfig)) {
    if (key.startsWith("_")) continue; // comment/annotation keys are intentional
    if (KNOWN_KEYS.has(key)) continue;
    // Check if it looks like a known typo
    const typoMatch = TYPO_MAP.get(key.toLowerCase());
    if (typoMatch) {
      warnings.push(
        `user-config.json key "${key}" looks like a typo for "${typoMatch.correct}". ` +
        `This key has no effect — it is silently ignored.`
      );
    } else {
      warnings.push(
        `user-config.json contains unknown key "${key}". ` +
        `This key has no effect and may indicate a typo or outdated config. ` +
        `Known keys are listed in user-config.example.json.`
      );
    }
  }

  // ── SUMMARY TABLE ─────────────────────────────────────────────
  const lines = [
    "── Config Doctor ──────────────────────────────────────",
    `  executionMode      : ${effectiveExecutionMode}`,
    `  DRY_RUN            : ${effectiveDryRun === null ? "(not set)" : effectiveDryRun}`,
    `  ALLOW_LIVE_EXEC    : ${allowLiveExecution}`,
    `  HEADLESS           : ${isHeadless}`,
    `  deployAmountSol    : ${deployAmountSol} SOL`,
    `  maxDeployAmount    : ${maxDeployAmount} SOL`,
    `  gasReserve         : ${gasReserve} SOL`,
    `  minSolToOpen       : ${minSolToOpen} SOL`,
    `  maxPositions       : ${maxPositions}`,
    `  strategy           : ${strategy}`,
    `  minBinsBelow       : ${minBinsBelow}  defaultBinsBelow: ${defaultBinsBelow}  maxBinsBelow: ${maxBinsBelow}`,
    `  minBinStep         : ${minBinStep}  maxBinStep: ${maxBinStep}`,
    `  mgmtIntervalMin    : ${mgmtInterval}  screenIntervalMin: ${screenInterval}  healthIntervalMin: ${healthInterval}`,
    `  LLM_ENABLED        : ${llmEnabled}`,
    `  hiveMindPullMode   : ${hiveMindPullMode}`,
    "───────────────────────────────────────────────────────",
  ];

  if (errors.length > 0) {
    lines.push(`  ❌ ${errors.length} ERROR(S):`);
    for (const e of errors) lines.push(`     • ${e}`);
  }
  if (warnings.length > 0) {
    lines.push(`  ⚠️  ${warnings.length} WARNING(S):`);
    for (const w of warnings) lines.push(`     • ${w}`);
  }
  if (errors.length === 0 && warnings.length === 0) {
    lines.push("  ✅ Config looks clean.");
  }
  lines.push("───────────────────────────────────────────────────────");

  const summary = lines.join("\n");
  return {
    valid: errors.length === 0,
    errors,
    warnings,
    summary,
    // Expose effective values for callers / tests
    effective: {
      executionMode: effectiveExecutionMode,
      dryRun: effectiveDryRun,
      allowLiveExecution,
      isHeadless,
      deployAmountSol,
      maxDeployAmount,
      gasReserve,
      minSolToOpen,
      maxPositions,
      strategy,
      minBinsBelow,
      defaultBinsBelow,
      maxBinsBelow,
      minBinStep,
      maxBinStep,
      mgmtInterval,
      screenInterval,
      healthInterval,
      llmEnabled,
      hiveMindPullMode,
    },
  };
}

// ─── CLI entrypoint ───────────────────────────────────────────
// Run as: node scripts/config-doctor.js
// Exit 0 = clean or warnings only.
// Exit 1 = hard errors found.
const isDirectRun = process.argv[1] &&
  path.resolve(process.argv[1]) === path.resolve(fileURLToPath(import.meta.url));

if (isDirectRun) {
  // Dynamically import config so the doctor sees the real runtime config
  let runtimeConfig = null;
  try {
    const mod = await import("../config.js");
    runtimeConfig = mod.config;
  } catch {
    // config.js may fail to load without user-config.json — treat gracefully
  }

  const { data: userConfig, exists: userConfigExists, parseError } = loadUserConfig();
  const result = runConfigDoctor({
    env: process.env,
    userConfig: parseError ? {} : userConfig,
    userConfigExists,
    runtimeConfig,
  });

  console.log(result.summary);

  if (!result.valid) {
    console.error("\n[CONFIG_DOCTOR] Hard errors found — review the above before starting.\n");
    process.exit(1);
  }
  if (result.warnings.length > 0) {
    console.warn("\n[CONFIG_DOCTOR] Warnings found — review before running in live mode.\n");
  }
  // Exit 0 even with warnings — warnings are informational
  process.exit(0);
}
