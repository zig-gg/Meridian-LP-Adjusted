"""RiskScorer — Sprint 2, Phase 2.1.

Scores each NormalizedCandidate and classifies it as FARM / WATCH / SKIP
using deterministic risk rules. This is the first decision layer before
PolicyEngine.

No LLM calls. No network calls. No wallet calls. No signing/key-loading.
No mutation of risk_policy.json, allowlists, or candidates.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from defi_autonomy.schemas.normalized_candidate import (
    ALLOWED_CHAINS,
    ALLOWED_STRATEGY_TYPES,
    NormalizedCandidate,
)

# ============================================================================
# Constants
# ============================================================================

DECISION_FARM = "FARM"
DECISION_WATCH = "WATCH"
DECISION_SKIP = "SKIP"

# Score thresholds
_FARM_THRESHOLD = 75
_WATCH_THRESHOLD = 50

# TVL thresholds
_TVL_HEAVY_PENALTY_BELOW = 50_000
_TVL_MEDIUM_PENALTY_BELOW = 250_000
_TVL_LIGHT_PENALTY_BELOW = 1_000_000

# APY thresholds
_APY_HEAVY_PENALTY_ABOVE = 30.0
_APY_LIGHT_PENALTY_ABOVE = 15.0

# Source confidence
_MIN_SOURCE_CONFIDENCE = 0.25

# Penalties
_PENALTY_TVL_HEAVY = 40
_PENALTY_TVL_MEDIUM = 20
_PENALTY_TVL_LIGHT = 10
_PENALTY_LOW_CONFIDENCE = 15
_PENALTY_REWARD_HEAVY = 15
_PENALTY_REWARD_DOMINANT = 10
_PENALTY_APY_HEAVY = 30
_PENALTY_APY_LIGHT = 10
_PENALTY_BELOW_BENCHMARK = 15
_PENALTY_BARELY_BEATS_BENCHMARK_REWARD_HEAVY = 10
_PENALTY_STABLE_LP_VS_LENDING = 5
_PENALTY_XSTOCKS_CONSERVATIVE = 15


# ============================================================================
# RiskAssessment dataclass
# ============================================================================


@dataclass(frozen=True, slots=True)
class RiskAssessment:
    """Frozen assessment produced by the RiskScorer for one candidate."""

    candidate_hash: str
    source_id: str
    adapter_name: str
    chain: str
    protocol: str
    strategy_type: str
    decision: str  # FARM / WATCH / SKIP
    score: int  # 0–100
    reasons: tuple[str, ...]
    warnings: tuple[str, ...]
    benchmark_apy: float | None
    risk_adjusted_apy: float
    created_at_utc: str


# ============================================================================
# Public API
# ============================================================================


def extract_stablecoin_benchmark(
    candidates: list[NormalizedCandidate],
) -> float | None:
    """Extract a conservative stablecoin benchmark APY from candidates.

    Uses the median APY of stablecoin_lending candidates with reasonable
    source confidence. Returns None if no suitable benchmarks found.
    """
    benchmark_apys: list[float] = []
    for c in candidates:
        if c.strategy_type != "stablecoin_lending":
            continue
        if c.source_confidence_score < 0.5:
            continue
        if c.tvl_usd < 1_000_000:
            continue
        if c.advertised_apy < 0 or c.advertised_apy > 20:
            continue
        if c.stale_data:
            continue
        benchmark_apys.append(c.advertised_apy)

    if not benchmark_apys:
        return None

    # Return median
    sorted_apys = sorted(benchmark_apys)
    n = len(sorted_apys)
    if n % 2 == 0:
        return (sorted_apys[n // 2 - 1] + sorted_apys[n // 2]) / 2.0
    return sorted_apys[n // 2]


def score_candidate(
    candidate: NormalizedCandidate,
    benchmark_apy: float | None = None,
    learning_bias: Any = None,
) -> RiskAssessment:
    """Score a single NormalizedCandidate and return a RiskAssessment.

    Deterministic: same input always produces same output.
    Optional learning_bias (LearningBias) applies advisory adjustment
    after base scoring, clamped by learning_bias_clamp_points.
    """
    score = 100
    reasons: list[str] = []
    warnings: list[str] = []
    critical_skip = False

    # --- Critical SKIP checks ---

    if candidate.stale_data:
        critical_skip = True
        reasons.append("stale_data=true")
        score = 0

    if candidate.tvl_usd <= 0:
        critical_skip = True
        reasons.append("tvl_usd <= 0")
        score = 0

    if candidate.advertised_apy < 0:
        critical_skip = True
        reasons.append("advertised_apy < 0")
        score = 0

    if candidate.advertised_apy > 100:
        critical_skip = True
        reasons.append("advertised_apy > 100")
        score = 0

    if candidate.source_confidence_score < _MIN_SOURCE_CONFIDENCE:
        critical_skip = True
        reasons.append(
            f"source_confidence_score={candidate.source_confidence_score} < {_MIN_SOURCE_CONFIDENCE}"
        )
        score = 0

    if candidate.chain not in ALLOWED_CHAINS:
        critical_skip = True
        reasons.append(f"chain={candidate.chain!r} not in allowed chains")
        score = 0

    if candidate.strategy_type not in ALLOWED_STRATEGY_TYPES:
        critical_skip = True
        reasons.append(
            f"strategy_type={candidate.strategy_type!r} not in allowed strategies"
        )
        score = 0

    if critical_skip:
        return RiskAssessment(
            candidate_hash=candidate.hash_sha256(),
            source_id=candidate.source_id,
            adapter_name=candidate.adapter_name,
            chain=candidate.chain,
            protocol=candidate.protocol,
            strategy_type=candidate.strategy_type,
            decision=DECISION_SKIP,
            score=0,
            reasons=tuple(reasons),
            warnings=tuple(warnings),
            benchmark_apy=benchmark_apy,
            risk_adjusted_apy=0.0,
            created_at_utc=datetime.now(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            ),
        )

    # --- Penalty: low TVL ---

    if candidate.tvl_usd < _TVL_HEAVY_PENALTY_BELOW:
        score -= _PENALTY_TVL_HEAVY
        warnings.append(
            f"tvl_usd={candidate.tvl_usd:,.0f} < {_TVL_HEAVY_PENALTY_BELOW:,} (heavy penalty)"
        )
    elif candidate.tvl_usd < _TVL_MEDIUM_PENALTY_BELOW:
        score -= _PENALTY_TVL_MEDIUM
        warnings.append(
            f"tvl_usd={candidate.tvl_usd:,.0f} < {_TVL_MEDIUM_PENALTY_BELOW:,} (medium penalty)"
        )
    elif candidate.tvl_usd < _TVL_LIGHT_PENALTY_BELOW:
        score -= _PENALTY_TVL_LIGHT
        warnings.append(
            f"tvl_usd={candidate.tvl_usd:,.0f} < {_TVL_LIGHT_PENALTY_BELOW:,} (light penalty)"
        )

    # --- Penalty: low source confidence ---

    if candidate.source_confidence_score < 0.5:
        score -= _PENALTY_LOW_CONFIDENCE
        warnings.append(
            f"source_confidence_score={candidate.source_confidence_score} < 0.5"
        )

    # --- Penalty: reward-heavy yield ---

    if candidate.reward_apr > candidate.fee_apr + 5:
        score -= _PENALTY_REWARD_HEAVY
        warnings.append(
            f"reward_apr={candidate.reward_apr} > fee_apr+5={candidate.fee_apr + 5} (reward-heavy)"
        )

    if candidate.advertised_apy > 0 and candidate.reward_apr > 0.5 * candidate.advertised_apy:
        score -= _PENALTY_REWARD_DOMINANT
        warnings.append(
            f"reward_apr={candidate.reward_apr} > 50% of advertised_apy={candidate.advertised_apy}"
        )

    # --- Penalty: suspiciously high APY ---

    if candidate.advertised_apy > _APY_HEAVY_PENALTY_ABOVE:
        score -= _PENALTY_APY_HEAVY
        warnings.append(
            f"advertised_apy={candidate.advertised_apy} > {_APY_HEAVY_PENALTY_ABOVE} (heavy penalty)"
        )
    elif candidate.advertised_apy > _APY_LIGHT_PENALTY_ABOVE:
        score -= _PENALTY_APY_LIGHT
        warnings.append(
            f"advertised_apy={candidate.advertised_apy} > {_APY_LIGHT_PENALTY_ABOVE} (light penalty)"
        )

    # --- Penalty: below benchmark ---

    if benchmark_apy is not None and benchmark_apy > 0:
        if candidate.advertised_apy < benchmark_apy:
            score -= _PENALTY_BELOW_BENCHMARK
            warnings.append(
                f"advertised_apy={candidate.advertised_apy} < benchmark={benchmark_apy}"
            )
        elif candidate.advertised_apy < benchmark_apy * 1.2:
            # Barely beats benchmark
            if candidate.reward_apr > candidate.fee_apr:
                score -= _PENALTY_BARELY_BEATS_BENCHMARK_REWARD_HEAVY
                warnings.append(
                    "barely beats benchmark and reward-heavy"
                )

    # --- Penalty: strategy-specific ---

    if candidate.strategy_type == "stable_stable_lp":
        score -= _PENALTY_STABLE_LP_VS_LENDING
        warnings.append("stable_stable_lp carries more risk than stablecoin_lending")

    if candidate.strategy_type in ("xstocks_lp", "xstocks_points"):
        score -= _PENALTY_XSTOCKS_CONSERVATIVE
        warnings.append(
            f"strategy_type={candidate.strategy_type} penalized until xStocks risk engine exists"
        )

    # --- Clamp score ---

    score = max(0, min(100, score))

    # --- Apply learning bias (advisory only) ---

    if learning_bias is not None and hasattr(learning_bias, "score_adjustment"):
        bias_adj = learning_bias.score_adjustment
        if bias_adj != 0:
            score = max(0, min(100, score + bias_adj))
            if bias_adj < 0:
                warnings.append(
                    f"learning bias: {bias_adj} ({learning_bias.evidence_count} events)"
                )
            else:
                reasons.append(
                    f"learning bias: +{bias_adj} ({learning_bias.evidence_count} events)"
                )

    # --- Risk-adjusted APY ---

    risk_adjusted_apy = candidate.advertised_apy * (score / 100.0)

    # --- Decision ---

    if score >= _FARM_THRESHOLD and not any("critical" in w.lower() for w in warnings):
        decision = DECISION_FARM
        reasons.append(f"score={score} >= {_FARM_THRESHOLD}, no critical warnings")
    elif score >= _WATCH_THRESHOLD:
        decision = DECISION_WATCH
        reasons.append(f"score={score} >= {_WATCH_THRESHOLD} but < {_FARM_THRESHOLD}")
    else:
        decision = DECISION_SKIP
        reasons.append(f"score={score} < {_WATCH_THRESHOLD}")

    return RiskAssessment(
        candidate_hash=candidate.hash_sha256(),
        source_id=candidate.source_id,
        adapter_name=candidate.adapter_name,
        chain=candidate.chain,
        protocol=candidate.protocol,
        strategy_type=candidate.strategy_type,
        decision=decision,
        score=score,
        reasons=tuple(reasons),
        warnings=tuple(warnings),
        benchmark_apy=benchmark_apy,
        risk_adjusted_apy=round(risk_adjusted_apy, 4),
        created_at_utc=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )


def score_candidates(
    candidates: list[NormalizedCandidate],
) -> list[RiskAssessment]:
    """Score a list of candidates, auto-extracting benchmark APY.

    Returns one RiskAssessment per candidate.
    """
    benchmark_apy = extract_stablecoin_benchmark(candidates)
    return [score_candidate(c, benchmark_apy=benchmark_apy) for c in candidates]


__all__ = [
    "DECISION_FARM",
    "DECISION_SKIP",
    "DECISION_WATCH",
    "RiskAssessment",
    "extract_stablecoin_benchmark",
    "score_candidate",
    "score_candidates",
]
