/**
 * token-risk.js
 *
 * Pure, read-only token risk classifier.
 *
 * The classifier does NOT perform any network I/O. It inspects a candidate
 * object (pool or tokenInfo) and returns a risk verdict:
 *   - PASS     candidate looks safe given available data
 *   - WARN     candidate has concerning signals but no hard block
 *   - BLOCK    candidate has a hard fail signal, should never be deployed
 *   - UNKNOWN  not enough identity/risk data to make a call
 *
 * Identity is determined by the mint address, not the symbol. A symbol that
 * matches a well-known token but whose mint does not match is treated as a
 * copycat and blocked.
 */

// Tiny known canonical mint map. Keep this small on purpose — full coverage
// belongs in a separate, auditable registry, not here.
const CANONICAL_MINTS = Object.freeze({
  // Wrapped SOL (native SOL on Solana is referenced by this mint)
  WSOL: "So11111111111111111111111111111111111111112",
  // TODO: add USDC mint here only once confirmed with high confidence
});

// Symbols we want to catch as impersonations. We compare the symbol the
// candidate *claims* to be against the actual on-chain mint. A mismatch is
// treated as copycat risk and is a hard block (or warn for non-critical
// stablecoins like USDC, depending on configuration).
const CANONICAL_SYMBOLS = Object.freeze({
  SOL:  "WSOL",
  WSOL: "WSOL",
  // USDC handled with a softer default since the canonical mint is TODO.
  USDC: "USDC",
});

function isTruthy(value) {
  if (value === true) return true;
  if (value === false || value == null) return false;
  if (typeof value === "string") {
    const v = value.trim().toLowerCase();
    return v === "true" || v === "1" || v === "yes";
  }
  if (typeof value === "number") return value !== 0;
  return Boolean(value);
}

function asNumber(value) {
  if (value == null) return null;
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

function normalizeSymbol(symbol) {
  return String(symbol || "").trim().toUpperCase();
}

function normalizeMint(mint) {
  if (!mint) return null;
  const m = String(mint).trim();
  return m.length > 0 ? m : null;
}

function pickBase(candidate) {
  // candidate may be the pool itself, candidate.pool, or candidate.ti.tokenInfo
  const pool = candidate?.pool || candidate;
  const ti = candidate?.ti?.tokenInfo
    || candidate?.ti
    || candidate?.tokenInfo
    || null;
  return {
    pool,
    ti,
    base: pool?.base || null,
    quote: pool?.quote || null,
    // Common pool-level fields (some pools store them flat on the pool object)
    poolFlat: pool || null,
  };
}

function resolveIdentity(parts) {
  const base = parts.base || {};
  const pool = parts.poolFlat || {};
  const baseSymbol = normalizeSymbol(base.symbol || pool.base_symbol || pool.symbol || null);
  const baseMint = normalizeMint(base.mint || pool.base_mint || pool.mint || null);
  const quoteSymbol = normalizeSymbol(parts.quote?.symbol || pool.quote_symbol || null);
  const quoteMint = normalizeMint(parts.quote?.mint || pool.quote_mint || null);
  return { baseSymbol, baseMint, quoteSymbol, quoteMint };
}

function resolveAudit(parts) {
  const ti = parts.ti || {};
  const audit = ti.audit || {};
  const pool = parts.poolFlat || {};
  // Support both nested audit object and flattened pool fields.
  const mintDisabled = audit.mint_disabled
    ?? pool.mint_disabled
    ?? pool.audit_mint_disabled
    ?? null;
  const freezeDisabled = audit.freeze_disabled
    ?? pool.freeze_disabled
    ?? pool.audit_freeze_disabled
    ?? null;
  const botHoldersPct = asNumber(
    audit.bot_holders_pct
    ?? pool.bot_holders_pct
    ?? null
  );
  const topHoldersPct = asNumber(
    audit.top_holders_pct
    ?? pool.top_holders_pct
    ?? null
  );
  return { mintDisabled, freezeDisabled, botHoldersPct, topHoldersPct };
}

function resolveRiskLevel(parts) {
  const pool = parts.poolFlat || {};
  const ti = parts.ti || {};
  const v = asNumber(pool.risk_level ?? ti.risk_level ?? null);
  return v;
}

function resolvePct(parts, key) {
  const pool = parts.poolFlat || {};
  const ti = parts.ti || {};
  return asNumber(pool[key] ?? ti[key] ?? null);
}

function resolvePvp(parts) {
  const pool = parts.poolFlat || {};
  return {
    isPvp: isTruthy(pool.is_pvp),
    pvpRisk: String(pool.pvp_risk || "").trim().toLowerCase() || null,
    pvpSymbol: pool.pvp_symbol || null,
    pvpRivalMint: pool.pvp_rival_mint || null,
    pvpRivalPool: pool.pvp_rival_pool || null,
  };
}

function detectCopycat(identity) {
  const symbol = identity.baseSymbol;
  const mint = identity.baseMint;
  if (!symbol || !mint) return false;

  // Symbol claims SOL/WSOL — verify mint matches WSOL.
  if (symbol === "SOL" || symbol === "WSOL") {
    return mint !== CANONICAL_MINTS.WSOL;
  }

  // Symbol claims USDC — if the canonical mint is not registered yet, treat
  // any USDC-named token as suspicious. Once USDC is added to CANONICAL_MINTS
  // the comparison will be exact.
  if (symbol === "USDC") {
    if (!CANONICAL_MINTS.USDC) return true;
    return mint !== CANONICAL_MINTS.USDC;
  }

  return false;
}

/**
 * Classify a candidate (pool or tokenInfo wrapper) into a risk verdict.
 *
 * Pure / no-network / no-IO. Inspects only fields already on the candidate.
 *
 * @param {object} candidate
 * @returns {{
 *   status: "PASS"|"WARN"|"BLOCK"|"UNKNOWN",
 *   identity: { baseSymbol: string|null, baseMint: string|null, quoteSymbol: string|null, quoteMint: string|null, copycatRisk: boolean },
 *   reasons: string[],
 *   warnings: string[],
 *   signals: object
 * }}
 */
export function classifyTokenRisk(candidate) {
  const parts = pickBase(candidate);
  const identity = resolveIdentity(parts);
  const audit = resolveAudit(parts);
  const riskLevel = resolveRiskLevel(parts);
  const bundlePct = resolvePct(parts, "bundle_pct");
  const sniperPct = resolvePct(parts, "sniper_pct");
  const suspiciousPct = resolvePct(parts, "suspicious_pct");
  const newWalletPct = resolvePct(parts, "new_wallet_pct");
  const pvp = resolvePvp(parts);

  const isRugpull = isTruthy(parts.poolFlat?.is_rugpull);
  const isWash = isTruthy(parts.poolFlat?.is_wash);
  const devSoldAll = isTruthy(parts.poolFlat?.dev_sold_all);

  const copycatRisk = detectCopycat(identity);

  const reasons = [];
  const warnings = [];
  const signals = {
    riskLevel,
    bundlePct,
    sniperPct,
    suspiciousPct,
    newWalletPct,
    botHoldersPct: audit.botHoldersPct,
    topHoldersPct: audit.topHoldersPct,
    mintDisabled: audit.mintDisabled,
    freezeDisabled: audit.freezeDisabled,
    isRugpull,
    isWash,
    devSoldAll,
    isPvp: pvp.isPvp,
    pvpRisk: pvp.pvpRisk,
  };

  // ── BLOCK conditions ────────────────────────────────────────────────────
  if (isRugpull) {
    reasons.push("flagged as rugpull");
  }
  if (isWash) {
    reasons.push("flagged as wash trading");
  }
  if (pvp.isPvp && pvp.pvpRisk === "high") {
    reasons.push("PVP rivalry with high risk");
  }
  if (riskLevel != null && riskLevel >= 4) {
    reasons.push(`risk_level ${riskLevel} >= 4`);
  }
  if (audit.mintDisabled === false) {
    reasons.push("mint authority is still active");
  }
  if (audit.freezeDisabled === false) {
    reasons.push("freeze authority is still active");
  }
  if (copycatRisk) {
    reasons.push(`symbol ${identity.baseSymbol} claims canonical identity but mint is unknown/mismatched`);
  }

  if (reasons.length > 0) {
    return {
      status: "BLOCK",
      identity: { ...identity, copycatRisk },
      reasons,
      warnings,
      signals,
    };
  }

  // ── WARN conditions ────────────────────────────────────────────────────
  if (riskLevel === 3) {
    warnings.push("risk_level 3 (elevated)");
  }
  if (bundlePct != null && bundlePct >= 25) {
    warnings.push(`bundle_pct ${bundlePct} >= 25`);
  }
  if (sniperPct != null && sniperPct >= 25) {
    warnings.push(`sniper_pct ${sniperPct} >= 25`);
  }
  if (suspiciousPct != null && suspiciousPct >= 25) {
    warnings.push(`suspicious_pct ${suspiciousPct} >= 25`);
  }
  if (newWalletPct != null && newWalletPct >= 50) {
    warnings.push(`new_wallet_pct ${newWalletPct} >= 50`);
  }
  if (audit.mintDisabled == null) {
    warnings.push("mint authority status unknown");
  }
  if (audit.freezeDisabled == null) {
    warnings.push("freeze authority status unknown");
  }
  if (audit.botHoldersPct != null && audit.botHoldersPct >= 25) {
    warnings.push(`bot_holders_pct ${audit.botHoldersPct} >= 25`);
  }
  if (!identity.baseMint) {
    warnings.push("base mint missing — cannot verify token identity");
  }

  // ── PASS / UNKNOWN decision ────────────────────────────────────────────
  // PASS requires:
  //   1. a base mint exists
  //   2. no BLOCK reasons
  //   3. no WARN signals
  //   4. at least some risk/audit data exists (so we are not guessing safe)
  const hasRiskData = (
    riskLevel != null
    || bundlePct != null
    || sniperPct != null
    || suspiciousPct != null
    || newWalletPct != null
    || audit.mintDisabled != null
    || audit.freezeDisabled != null
    || audit.botHoldersPct != null
    || audit.topHoldersPct != null
  );

  if (
    identity.baseMint
    && warnings.length === 0
    && hasRiskData
  ) {
    return {
      status: "PASS",
      identity: { ...identity, copycatRisk },
      reasons: [],
      warnings: [],
      signals,
    };
  }

  if (!identity.baseMint || !hasRiskData) {
    return {
      status: "UNKNOWN",
      identity: { ...identity, copycatRisk },
      reasons: [],
      warnings,
      signals,
    };
  }

  // We have a mint and risk data, but warnings were raised.
  return {
    status: "WARN",
    identity: { ...identity, copycatRisk },
    reasons: [],
    warnings,
    signals,
  };
}

/**
 * Format a classifyTokenRisk() result as a short, human-readable summary.
 *
 * @param {object} risk
 * @returns {string}
 */
export function formatTokenRiskSummary(risk) {
  if (!risk || typeof risk !== "object") return "UNKNOWN (no risk data)";
  const sym = risk.identity?.baseSymbol || "?";
  const mint = risk.identity?.baseMint
    ? `${risk.identity.baseMint.slice(0, 4)}…${risk.identity.baseMint.slice(-4)}`
    : "no-mint";
  const tag = risk.identity?.copycatRisk ? " [COPYCAT]" : "";
  const status = risk.status || "UNKNOWN";

  const lines = [`[${status}] ${sym} (${mint})${tag}`];
  if (Array.isArray(risk.reasons) && risk.reasons.length > 0) {
    lines.push(`  reasons: ${risk.reasons.join("; ")}`);
  }
  if (Array.isArray(risk.warnings) && risk.warnings.length > 0) {
    lines.push(`  warnings: ${risk.warnings.join("; ")}`);
  }
  return lines.join("\n");
}
