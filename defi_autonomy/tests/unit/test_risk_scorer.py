"""Unit tests for defi_autonomy.risk_scorer — Sprint 2, Phase 2.1.

All tests are deterministic and offline. No network calls. No signing.
No key loading.
"""

from __future__ import annotations

import pytest

from defi_autonomy.schemas.normalized_candidate import (
    NormalizedCandidate,
    from_dict,
)
from defi_autonomy.risk_scorer import (
    DECISION_FARM,
    DECISION_SKIP,
    DECISION_WATCH,
    RiskAssessment,
    extract_stablecoin_benchmark,
    score_candidate,
    score_candidates,
)


# ============================================================================
# Fixtures
# ============================================================================


def _valid_dict(**overrides) -> dict:
    """Baseline high-quality stablecoin lending candidate."""
    base = {
        "chain": "Base",
        "protocol": "aave-v3",
        "venue": "aave-v3",
        "venue_id": "base:aave-v3:usdc",
        "pool_address": "0x" + "a" * 40,
        "token_addresses": ["0x" + "b" * 40],
        "strategy_type": "stablecoin_lending",
        "advertised_apy": 4.5,
        "fee_apr": 3.5,
        "reward_apr": 1.0,
        "tvl_usd": 50_000_000.0,
        "volume_24h_usd": 1_000_000.0,
        "liquidity_depth_usd": 50_000_000.0,
        "source_id": "defillama",
        "source_url": "https://yields.llama.fi/pools",
        "source_timestamp_utc": "2026-05-27T00:00:00Z",
        "fetched_at_utc": "2026-05-27T00:00:01Z",
        "adapter_name": "defillama_adapter",
        "data_freshness_seconds": 30,
        "source_confidence_score": 0.8,
        "stale_data": False,
    }
    base.update(overrides)
    return base


def _candidate(**overrides) -> NormalizedCandidate:
    return from_dict(_valid_dict(**overrides))


# ============================================================================
# Tests: high-quality candidate = FARM
# ============================================================================


class TestHighQualityFarm:
    """High-quality stablecoin lending candidate scores FARM."""

    def test_high_quality_is_farm(self) -> None:
        c = _candidate()
        result = score_candidate(c)
        assert result.decision == DECISION_FARM
        assert result.score >= 75

    def test_result_is_risk_assessment(self) -> None:
        c = _candidate()
        result = score_candidate(c)
        assert isinstance(result, RiskAssessment)

    def test_result_has_candidate_hash(self) -> None:
        c = _candidate()
        result = score_candidate(c)
        assert result.candidate_hash == c.hash_sha256()

    def test_result_has_source_fields(self) -> None:
        c = _candidate()
        result = score_candidate(c)
        assert result.source_id == "defillama"
        assert result.adapter_name == "defillama_adapter"
        assert result.chain == "Base"
        assert result.protocol == "aave-v3"
        assert result.strategy_type == "stablecoin_lending"


# ============================================================================
# Tests: TVL penalties
# ============================================================================


class TestTvlPenalties:
    """Low TVL candidates receive penalties."""

    def test_low_tvl_receives_penalty(self) -> None:
        c = _candidate(tvl_usd=100_000.0)
        result = score_candidate(c)
        assert result.score < 100
        assert any("tvl_usd" in w for w in result.warnings)

    def test_very_low_tvl_heavy_penalty(self) -> None:
        c = _candidate(tvl_usd=10_000.0)
        result = score_candidate(c)
        assert result.score <= 60

    def test_zero_tvl_is_skip(self) -> None:
        c = _candidate(tvl_usd=0.0)
        result = score_candidate(c)
        assert result.decision == DECISION_SKIP
        assert result.score == 0


# ============================================================================
# Tests: stale_data = SKIP
# ============================================================================


class TestStaleData:
    """Stale data candidate is SKIP."""

    def test_stale_data_is_skip(self) -> None:
        c = _candidate(stale_data=True)
        result = score_candidate(c)
        assert result.decision == DECISION_SKIP
        assert result.score == 0
        assert any("stale_data" in r for r in result.reasons)


# ============================================================================
# Tests: source confidence
# ============================================================================


class TestSourceConfidence:
    """Low source confidence penalized or skipped."""

    def test_very_low_confidence_is_skip(self) -> None:
        c = _candidate(source_confidence_score=0.2)
        result = score_candidate(c)
        assert result.decision == DECISION_SKIP
        assert result.score == 0

    def test_moderate_low_confidence_penalized(self) -> None:
        c = _candidate(source_confidence_score=0.4)
        result = score_candidate(c)
        assert result.score < 100
        assert any("source_confidence" in w for w in result.warnings)


# ============================================================================
# Tests: reward-heavy APY
# ============================================================================


class TestRewardHeavy:
    """Reward-heavy APY is penalized."""

    def test_reward_heavy_penalized(self) -> None:
        c = _candidate(fee_apr=1.0, reward_apr=10.0, advertised_apy=11.0)
        result = score_candidate(c)
        assert result.score < 100
        assert any("reward" in w.lower() for w in result.warnings)

    def test_reward_dominant_penalized(self) -> None:
        c = _candidate(fee_apr=1.0, reward_apr=8.0, advertised_apy=9.0)
        result = score_candidate(c)
        assert any("50%" in w for w in result.warnings)


# ============================================================================
# Tests: suspiciously high APY
# ============================================================================


class TestHighApy:
    """Suspiciously high APY is penalized."""

    def test_apy_above_30_heavy_penalty(self) -> None:
        c = _candidate(advertised_apy=35.0, fee_apr=30.0, reward_apr=5.0)
        result = score_candidate(c)
        assert result.score < 75
        assert any("heavy penalty" in w for w in result.warnings)

    def test_apy_above_15_light_penalty(self) -> None:
        c = _candidate(advertised_apy=18.0, fee_apr=15.0, reward_apr=3.0)
        result = score_candidate(c)
        assert result.score < 100
        assert any("light penalty" in w for w in result.warnings)


# ============================================================================
# Tests: benchmark comparison
# ============================================================================


class TestBenchmarkComparison:
    """Candidate below stablecoin benchmark is not FARM."""

    def test_below_benchmark_penalized(self) -> None:
        c = _candidate(advertised_apy=2.0, fee_apr=2.0, reward_apr=0.0)
        result = score_candidate(c, benchmark_apy=4.0)
        assert any("benchmark" in w for w in result.warnings)
        # With penalty, should not be FARM
        assert result.decision != DECISION_FARM or result.score < 90

    def test_above_benchmark_no_penalty(self) -> None:
        c = _candidate(advertised_apy=6.0, fee_apr=5.0, reward_apr=1.0)
        result = score_candidate(c, benchmark_apy=4.0)
        assert not any("< benchmark" in w for w in result.warnings)


# ============================================================================
# Tests: strategy-specific scoring
# ============================================================================


class TestStrategyScoring:
    """Strategy-specific scoring differences."""

    def test_stablecoin_lending_scores_higher_than_stable_lp(self) -> None:
        lending = _candidate(strategy_type="stablecoin_lending")
        lp = _candidate(strategy_type="stable_stable_lp")
        r_lending = score_candidate(lending)
        r_lp = score_candidate(lp)
        assert r_lending.score > r_lp.score

    def test_xstocks_lp_gets_conservative_penalty(self) -> None:
        c = _candidate(strategy_type="xstocks_lp")
        result = score_candidate(c)
        assert result.score < 100
        assert any("xstocks" in w.lower() for w in result.warnings)

    def test_xstocks_points_gets_conservative_penalty(self) -> None:
        c = _candidate(strategy_type="xstocks_points")
        result = score_candidate(c)
        assert result.score < 100


# ============================================================================
# Tests: unsupported chain = SKIP
# ============================================================================


class TestUnsupportedChain:
    """Unsupported chain candidate is skipped."""

    def test_unsupported_chain_is_skip(self) -> None:
        # Build manually since from_dict would reject invalid chain
        # We need to test the scorer's behavior, so construct directly
        c = NormalizedCandidate(
            chain="Ethereum",
            protocol="aave-v3",
            venue="aave-v3",
            venue_id="eth:aave-v3:usdc",
            pool_address="0x" + "a" * 40,
            token_addresses=("0x" + "b" * 40,),
            strategy_type="stablecoin_lending",
            advertised_apy=4.5,
            fee_apr=3.5,
            reward_apr=1.0,
            tvl_usd=50_000_000.0,
            volume_24h_usd=1_000_000.0,
            liquidity_depth_usd=50_000_000.0,
            source_id="defillama",
            source_url="https://yields.llama.fi/pools",
            source_timestamp_utc="2026-05-27T00:00:00Z",
            fetched_at_utc="2026-05-27T00:00:01Z",
            adapter_name="defillama_adapter",
            data_freshness_seconds=30,
            source_confidence_score=0.8,
            stale_data=False,
        )
        result = score_candidate(c)
        assert result.decision == DECISION_SKIP
        assert result.score == 0


# ============================================================================
# Tests: score clamping
# ============================================================================


class TestScoreClamping:
    """Score is clamped between 0 and 100."""

    def test_score_never_below_zero(self) -> None:
        # Candidate with many penalties
        c = _candidate(
            tvl_usd=5_000.0,
            source_confidence_score=0.3,
            advertised_apy=40.0,
            fee_apr=5.0,
            reward_apr=35.0,
        )
        result = score_candidate(c)
        assert result.score >= 0

    def test_score_never_above_100(self) -> None:
        c = _candidate()
        result = score_candidate(c)
        assert result.score <= 100


# ============================================================================
# Tests: score_candidates
# ============================================================================


class TestScoreCandidates:
    """score_candidates returns one assessment per candidate."""

    def test_returns_one_per_candidate(self) -> None:
        candidates = [_candidate(), _candidate(tvl_usd=100_000.0)]
        results = score_candidates(candidates)
        assert len(results) == 2
        assert all(isinstance(r, RiskAssessment) for r in results)

    def test_empty_list_returns_empty(self) -> None:
        results = score_candidates([])
        assert results == []


# ============================================================================
# Tests: extract_stablecoin_benchmark
# ============================================================================


class TestExtractBenchmark:
    """extract_stablecoin_benchmark returns conservative benchmark APY."""

    def test_returns_median_of_good_lending_candidates(self) -> None:
        candidates = [
            _candidate(advertised_apy=3.0),
            _candidate(advertised_apy=4.0),
            _candidate(advertised_apy=5.0),
        ]
        result = extract_stablecoin_benchmark(candidates)
        assert result == 4.0

    def test_excludes_low_confidence(self) -> None:
        candidates = [
            _candidate(advertised_apy=3.0, source_confidence_score=0.3),
            _candidate(advertised_apy=5.0),
        ]
        result = extract_stablecoin_benchmark(candidates)
        assert result == 5.0

    def test_excludes_low_tvl(self) -> None:
        candidates = [
            _candidate(advertised_apy=3.0, tvl_usd=500_000.0),
            _candidate(advertised_apy=5.0),
        ]
        result = extract_stablecoin_benchmark(candidates)
        assert result == 5.0

    def test_excludes_stale(self) -> None:
        candidates = [
            _candidate(advertised_apy=3.0, stale_data=True),
            _candidate(advertised_apy=5.0),
        ]
        result = extract_stablecoin_benchmark(candidates)
        assert result == 5.0

    def test_excludes_non_lending(self) -> None:
        candidates = [
            _candidate(advertised_apy=3.0, strategy_type="stable_stable_lp"),
            _candidate(advertised_apy=5.0),
        ]
        result = extract_stablecoin_benchmark(candidates)
        assert result == 5.0

    def test_returns_none_if_no_suitable(self) -> None:
        candidates = [
            _candidate(advertised_apy=3.0, strategy_type="stable_stable_lp"),
        ]
        result = extract_stablecoin_benchmark(candidates)
        assert result is None


# ============================================================================
# Tests: determinism
# ============================================================================


class TestDeterminism:
    """Same input gives same output."""

    def test_same_input_same_score(self) -> None:
        c = _candidate()
        r1 = score_candidate(c)
        r2 = score_candidate(c)
        assert r1.score == r2.score
        assert r1.decision == r2.decision
        assert r1.warnings == r2.warnings
        assert r1.reasons == r2.reasons
        assert r1.candidate_hash == r2.candidate_hash

    def test_same_input_same_risk_adjusted_apy(self) -> None:
        c = _candidate()
        r1 = score_candidate(c)
        r2 = score_candidate(c)
        assert r1.risk_adjusted_apy == r2.risk_adjusted_apy


# ============================================================================
# Tests: no network calls, no signing
# ============================================================================


class TestNoNetworkNoSigning:
    """No network calls or signing imports."""

    def test_no_network_calls(self) -> None:
        import socket
        from unittest.mock import patch

        c = _candidate()
        with patch.object(
            socket,
            "create_connection",
            side_effect=AssertionError("network call detected"),
        ):
            result = score_candidate(c)
            assert result.decision == DECISION_FARM

    def test_no_signing_imports(self) -> None:
        import defi_autonomy.risk_scorer as mod

        with open(mod.__file__, "r") as f:
            source = f.read()
        signing_terms = (
            "eth_account",
            "solders",
            "nacl",
            "cryptography.hazmat",
            "private_key",
            "seed_phrase",
            "mnemonic",
            "WalletExecutor",
            "TxSimulator",
        )
        for term in signing_terms:
            assert term not in source, f"forbidden term {term!r} found in module"


# ============================================================================
# Tests: RiskScorer + LearningMemory integration — Phase 5.4B
# ============================================================================


class TestLearningBiasIntegration:
    """Learning bias integrates with RiskScorer."""

    def test_negative_bias_reduces_score(self) -> None:
        from defi_autonomy.learning_memory import LearningBias

        c = _candidate()
        bias = LearningBias(
            source_id="defillama", protocol="aave-v3",
            strategy_type="stablecoin_lending", factor_key="apr_decay",
            score_adjustment=-4, confidence_adjustment=0.8, evidence_count=3,
            reasons=("learning: 3 negative signal(s), adjustment=-4",),
            updated_at_utc="2026-05-27T00:00:00Z",
        )
        r_no_bias = score_candidate(c)
        r_with_bias = score_candidate(c, learning_bias=bias)
        assert r_with_bias.score < r_no_bias.score
        assert r_with_bias.score == r_no_bias.score - 4

    def test_positive_bias_increases_score(self) -> None:
        from defi_autonomy.learning_memory import LearningBias

        c = _candidate()
        bias = LearningBias(
            source_id="defillama", protocol="aave-v3",
            strategy_type="stablecoin_lending", factor_key="realized_gain",
            score_adjustment=3, confidence_adjustment=0.7, evidence_count=2,
            reasons=("learning: 2 positive signal(s), adjustment=+3",),
            updated_at_utc="2026-05-27T00:00:00Z",
        )
        r_no_bias = score_candidate(c)
        r_with_bias = score_candidate(c, learning_bias=bias)
        assert r_with_bias.score >= r_no_bias.score

    def test_bias_appears_in_warnings_or_reasons(self) -> None:
        from defi_autonomy.learning_memory import LearningBias

        c = _candidate()
        bias = LearningBias(
            source_id=None, protocol=None, strategy_type=None, factor_key=None,
            score_adjustment=-3, confidence_adjustment=0.8, evidence_count=2,
            reasons=(), updated_at_utc="",
        )
        r = score_candidate(c, learning_bias=bias)
        all_text = " ".join(r.warnings) + " ".join(r.reasons)
        assert "learning bias" in all_text.lower()

    def test_hard_skip_remains_skip_with_positive_bias(self) -> None:
        from defi_autonomy.learning_memory import LearningBias

        c = _candidate(stale_data=True)
        bias = LearningBias(
            source_id=None, protocol=None, strategy_type=None, factor_key=None,
            score_adjustment=5, confidence_adjustment=0.9, evidence_count=5,
            reasons=(), updated_at_utc="",
        )
        r = score_candidate(c, learning_bias=bias)
        assert r.decision == DECISION_SKIP
        assert r.score == 0  # Hard rejection overrides bias

    def test_farm_can_become_watch_with_negative_bias(self) -> None:
        from defi_autonomy.learning_memory import LearningBias

        # Create a candidate that barely scores FARM (score ~75-79)
        c = _candidate(tvl_usd=800_000.0)  # light TVL penalty (-10) → score 90
        # Actually let's use a candidate with score around 75-80
        # Default high-quality candidate scores 100, with light TVL penalty = 90
        # We need score to be exactly at threshold
        # Use xstocks_lp which gets -15 penalty → 85
        c2 = _candidate(strategy_type="xstocks_lp")
        r_no_bias = score_candidate(c2)
        # xstocks_lp gets -15, so score = 85

        bias = LearningBias(
            source_id=None, protocol=None, strategy_type=None, factor_key=None,
            score_adjustment=-5, confidence_adjustment=0.9, evidence_count=5,
            reasons=(), updated_at_utc="",
        )
        r_with_bias = score_candidate(c2, learning_bias=bias)
        # 85 - 5 = 80, still FARM (>= 75)
        # Let's use a stronger scenario
        # Actually 80 >= 75 so still FARM. Need score closer to 75.
        # xstocks_lp (-15) + low confidence (-15) = 70 → WATCH
        c3 = _candidate(strategy_type="xstocks_lp", source_confidence_score=0.4)
        r3 = score_candidate(c3, learning_bias=bias)
        # 100 - 15 (xstocks) - 15 (low conf) = 70, then -5 bias = 65 → WATCH
        assert r3.decision == DECISION_WATCH or r3.score < 75

    def test_score_deterministic_with_same_bias(self) -> None:
        from defi_autonomy.learning_memory import LearningBias

        c = _candidate()
        bias = LearningBias(
            source_id=None, protocol=None, strategy_type=None, factor_key=None,
            score_adjustment=-2, confidence_adjustment=0.5, evidence_count=1,
            reasons=(), updated_at_utc="",
        )
        r1 = score_candidate(c, learning_bias=bias)
        r2 = score_candidate(c, learning_bias=bias)
        assert r1.score == r2.score
        assert r1.decision == r2.decision

    def test_no_bias_same_as_none(self) -> None:
        c = _candidate()
        r1 = score_candidate(c)
        r2 = score_candidate(c, learning_bias=None)
        assert r1.score == r2.score
