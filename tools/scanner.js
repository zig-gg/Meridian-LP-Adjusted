/**
 * tools/scanner.js
 *
 * Phase 1 pool scanner/ranker.
 *
 * Reuses existing getTopCandidates() from screening.js and adds:
 *   - Normalized scoring (volumeToTvl, feeToTvl, tvl quality, token risk, holder risk, narrative)
 *   - Suggested action per pool: MONITOR / SIMULATE / AVOID / MANUAL_REVIEW
 *   - Telegram-ready shortlist summary
 *   - No wallet required — scanner mode is read-only
 */

import { getTopCandidates } from "./screening.js";
import { config } from "../config.js";
import { getExecutionMode, EXECUTION_MODES } from "../execution-modes.js";
import { log } from "../logger.js";

// ─── Scoring weights ───────────────────────────────────────────
const SCORE_WEIGHTS = {
  feeToTvl:       0.35,  // primary yield signal
  volumeToTvl:    0.25,  // activity quality
  tvlQuality:     0.15,  // TVL in sweet spot (not too low, not too high)
  tokenRisk:      0.15,  // inverted risk flags
  holderQuality:  0.05,  // holder concentration
  narrative:      0.05,  // narrative/source tags
};

/**
 * Compute a normalized score [0, 100] for a candidate pool.
 * Each dimension is scored 0-100 then weighted.
 */
function scorePool(pool) {
  const scores = {};

  // ── Fee/TVL score ──────────────────────────────────────────
  // fee_active_tvl_ratio is already a % (e.g. 0.15 = 0.15%)
  // Excellent: >= 0.5%, Good: >= 0.1%, Poor: < 0.05%
  const feeRatio = Number(pool.fee_active_tvl_ratio ?? 0);
  scores.feeToTvl = Math.min(100, (feeRatio / 0.5) * 100);

  // ── Volume/TVL score ───────────────────────────────────────
  // volume_window / active_tvl — higher = more active relative to size
  const tvl = Number(pool.tvl ?? pool.active_tvl ?? 1);
  const volume = Number(pool.volume_window ?? 0);
  const volToTvl = tvl > 0 ? volume / tvl : 0;
  // Excellent: >= 2x TVL in window, Good: >= 0.5x, Poor: < 0.1x
  scores.volumeToTvl = Math.min(100, (volToTvl / 2) * 100);

  // ── TVL quality score ──────────────────────────────────────
  // Sweet spot: $10k-$100k. Too low = illiquid, too high = crowded.
  const minTvl = config.screening.minTvl ?? 10_000;
  const maxTvl = config.screening.maxTvl ?? 150_000;
  const sweetSpotMid = (minTvl + Math.min(maxTvl, 100_000)) / 2;
  const tvlDist = Math.abs(tvl - sweetSpotMid) / sweetSpotMid;
  scores.tvlQuality = Math.max(0, 100 - tvlDist * 100);

  // ── Token risk score (inverted — lower risk = higher score) ──
  // Penalize: wash trading, rugpull, high bundle %, high bot holders %
  let riskPenalty = 0;
  if (pool.is_wash)    riskPenalty += 100;
  if (pool.is_rugpull) riskPenalty += 60;
  if (pool.is_pvp)     riskPenalty += 30;
  const bundlePct = Number(pool.bundle_pct ?? 0);
  const botPct    = Number(pool.bot_pct ?? 0);
  riskPenalty += Math.min(40, bundlePct);
  riskPenalty += Math.min(30, botPct);
  scores.tokenRisk = Math.max(0, 100 - riskPenalty);

  // ── Holder quality score ───────────────────────────────────
  // organic_score already 0-100; penalize low holder count
  const organic = Number(pool.organic_score ?? 0);
  const holders = Number(pool.holders ?? 0);
  const holderScore = holders >= 1000 ? 100 : (holders / 1000) * 100;
  scores.holderQuality = (organic * 0.7 + holderScore * 0.3);

  // ── Narrative/source score ─────────────────────────────────
  // Bonus for discord signal, smart wallets, non-null narrative
  let narrativeScore = 50; // baseline
  if (pool.discord_signal)                narrativeScore += 20;
  if ((pool.smart_wallets_count ?? 0) > 0) narrativeScore += 20;
  if (pool.kol_in_clusters)               narrativeScore += 10;
  scores.narrative = Math.min(100, narrativeScore);

  // ── Weighted total ─────────────────────────────────────────
  const total = Object.entries(SCORE_WEIGHTS).reduce((sum, [key, weight]) => {
    return sum + (scores[key] ?? 0) * weight;
  }, 0);

  return {
    total: Math.round(total),
    breakdown: {
      feeToTvl:      Math.round(scores.feeToTvl),
      volumeToTvl:   Math.round(scores.volumeToTvl),
      tvlQuality:    Math.round(scores.tvlQuality),
      tokenRisk:     Math.round(scores.tokenRisk),
      holderQuality: Math.round(scores.holderQuality),
      narrative:     Math.round(scores.narrative),
    },
    raw: {
      feeToTvlRatio: feeRatio,
      volToTvlRatio: Math.round(volToTvl * 100) / 100,
    },
  };
}

/**
 * Determine suggested action for a pool based on its score and risk flags.
 *
 * AVOID        — hard disqualifiers present
 * MANUAL_REVIEW — borderline or conflicting signals
 * MONITOR      — good signals but not ready to deploy (scanner mode)
 * SIMULATE     — strong candidate, worth simulating
 */
function suggestAction(pool, scoring) {
  // Hard AVOID conditions
  if (pool.is_wash)                                    return "AVOID";
  if (pool.is_rugpull && !pool.smart_wallets_count)    return "AVOID";
  if (scoring.breakdown.tokenRisk < 20)                return "AVOID";

  // MANUAL_REVIEW conditions
  if (pool.is_pvp)                                     return "MANUAL_REVIEW";
  if (scoring.breakdown.feeToTvl < 20)                 return "MANUAL_REVIEW";
  if (scoring.breakdown.holderQuality < 30)            return "MANUAL_REVIEW";
  if (scoring.total < 35)                              return "MANUAL_REVIEW";

  // SIMULATE — strong candidate
  if (scoring.total >= 60 && scoring.breakdown.feeToTvl >= 50) return "SIMULATE";

  // Default: MONITOR
  return "MONITOR";
}

/**
 * Collect risk flags for display.
 */
function collectRiskFlags(pool) {
  const flags = [];
  if (pool.is_wash)                                    flags.push("WASH_TRADING");
  if (pool.is_rugpull)                                 flags.push("RUGPULL_RISK");
  if (pool.is_pvp)                                     flags.push("PVP_CONFLICT");
  if (Number(pool.bundle_pct ?? 0) > 20)               flags.push(`BUNDLE_${Math.round(pool.bundle_pct)}%`);
  if (Number(pool.sniper_pct ?? 0) > 15)               flags.push(`SNIPER_${Math.round(pool.sniper_pct)}%`);
  if (Number(pool.suspicious_pct ?? 0) > 20)           flags.push(`SUSPICIOUS_${Math.round(pool.suspicious_pct)}%`);
  if (pool.discord_signal)                             flags.push("DISCORD_SIGNAL");
  if (pool.kol_in_clusters)                            flags.push("KOL_PRESENT");
  if (pool.smart_money_buy)                            flags.push("SMART_MONEY_BUY");
  return flags;
}

/**
 * Format a single candidate for Telegram output.
 */
function formatCandidateForTelegram(ranked, index) {
  const { pool, scoring, action, riskFlags } = ranked;
  const actionEmoji = {
    SIMULATE:      "🟢",
    MONITOR:       "🟡",
    MANUAL_REVIEW: "🟠",
    AVOID:         "🔴",
  }[action] ?? "⚪";

  const tvl = Number(pool.tvl ?? pool.active_tvl ?? 0);
  const vol = Number(pool.volume_window ?? 0);
  const feeRatio = Number(pool.fee_active_tvl_ratio ?? 0);

  const lines = [
    `${index + 1}. ${actionEmoji} <b>${pool.name || "Unknown"}</b> — Score: ${scoring.total}/100`,
    `   Pool: <code>${pool.pool}</code>`,
    `   Fee/TVL: ${feeRatio.toFixed(3)}% | Vol/TVL: ${scoring.raw.volToTvlRatio}x`,
    `   TVL: $${Math.round(tvl).toLocaleString()} | Vol: $${Math.round(vol).toLocaleString()}`,
    riskFlags.length > 0 ? `   ⚠️ ${riskFlags.join(", ")}` : `   ✅ No risk flags`,
    `   Action: <b>${action}</b>`,
  ];

  return lines.join("\n");
}

/**
 * Main scanner function.
 *
 * Fetches top candidates, scores them, ranks them, and returns:
 *   - ranked candidates with scores and suggested actions
 *   - Telegram-ready summary
 *   - execution mode (always scanner in Phase 1 default)
 *
 * Does NOT require a wallet private key.
 */
export async function scanPools({ limit = 5 } = {}) {
  const executionMode = getExecutionMode();
  log("scanner", `Scanning pools (mode: ${executionMode}, limit: ${limit})`);

  let rawCandidates;
  try {
    const result = await getTopCandidates({ limit: Math.max(limit, 10) });
    rawCandidates = result.candidates || [];
  } catch (error) {
    log("scanner_error", `Failed to fetch candidates: ${error.message}`);
    return {
      success: false,
      error: error.message,
      execution_mode: executionMode,
      candidates: [],
    };
  }

  if (rawCandidates.length === 0) {
    return {
      success: true,
      execution_mode: executionMode,
      candidates: [],
      total_scanned: 0,
      telegram_summary: "🔍 Scanner: No candidates found matching current thresholds.",
    };
  }

  // Score and rank
  const ranked = rawCandidates.map((pool) => {
    const scoring = scorePool(pool);
    const riskFlags = collectRiskFlags(pool);
    const action = suggestAction(pool, scoring);
    return { pool, scoring, action, riskFlags };
  });

  // Sort by score descending, then by action priority
  const actionPriority = { SIMULATE: 0, MONITOR: 1, MANUAL_REVIEW: 2, AVOID: 3 };
  ranked.sort((a, b) => {
    const actionDiff = (actionPriority[a.action] ?? 9) - (actionPriority[b.action] ?? 9);
    if (actionDiff !== 0) return actionDiff;
    return b.scoring.total - a.scoring.total;
  });

  const topN = ranked.slice(0, limit);

  // Build Telegram summary
  const modeLabel = {
    [EXECUTION_MODES.SCANNER]:  "Scanner Mode — read-only, no execution",
    [EXECUTION_MODES.SIMULATE]: "Simulate Mode — dry-run only",
    [EXECUTION_MODES.PAPER]:    "Paper Mode — hypothetical only",
    [EXECUTION_MODES.LIVE]:     "⚠️ Live Mode — execution enabled",
  }[executionMode] ?? executionMode;

  const candidateLines = topN.map((r, i) => formatCandidateForTelegram(r, i));

  const simulateCount = topN.filter(r => r.action === "SIMULATE").length;
  const monitorCount  = topN.filter(r => r.action === "MONITOR").length;
  const avoidCount    = topN.filter(r => r.action === "AVOID").length;

  const telegramSummary = [
    `🔍 <b>Pool Scanner</b> — ${modeLabel}`,
    `Found ${topN.length} candidates (${simulateCount} SIMULATE, ${monitorCount} MONITOR, ${avoidCount} AVOID)`,
    "",
    candidateLines.join("\n\n"),
    "",
    `<i>No automatic live deploy. Use /deploy &lt;n&gt; to manually deploy a candidate.</i>`,
  ].join("\n");

  return {
    success: true,
    execution_mode: executionMode,
    total_scanned: rawCandidates.length,
    candidates: topN.map(({ pool, scoring, action, riskFlags }) => ({
      pool: pool.pool,
      name: pool.name,
      score: scoring.total,
      score_breakdown: scoring.breakdown,
      fee_to_tvl: scoring.raw.feeToTvlRatio,
      vol_to_tvl: scoring.raw.volToTvlRatio,
      tvl: Number(pool.tvl ?? pool.active_tvl ?? 0),
      volume: Number(pool.volume_window ?? 0),
      organic_score: pool.organic_score,
      holders: pool.holders,
      bin_step: pool.bin_step,
      fee_pct: pool.fee_pct,
      volatility: pool.volatility,
      risk_flags: riskFlags,
      suggested_action: action,
      base_symbol: pool.base?.symbol,
      base_mint: pool.base?.mint,
      discord_signal: pool.discord_signal ?? false,
    })),
    telegram_summary: telegramSummary,
  };
}
