"""Unit tests for defi_autonomy.learning_memory — Phase 5.4B.

All tests are deterministic and offline. No network calls. No signing.
No key loading. No broadcast. No policy/allowlist mutation.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from defi_autonomy.learning_memory import (
    LearningBias,
    apply_learning_bias,
    derive_learning_bias,
    get_bias_for_candidate,
    learning_bias_to_dict,
    load_outcome_events,
)
from defi_autonomy.schemas.normalized_candidate import from_dict


# ============================================================================
# Fixtures
# ============================================================================


def _negative_event(
    source_id: str = "defillama",
    protocol: str = "aave-v3",
    strategy_type: str = "stablecoin_lending",
    factor_key: str = "apr_decay",
    magnitude: float = 0.8,
    confidence: float = 0.9,
) -> dict:
    return {
        "event_id": "evt_neg",
        "provenance_id": "prov_001",
        "provenance_source": "PIPELINE",
        "outcome_type": "REALIZED_PNL",
        "factor_category": "YIELD_RISK",
        "factor_key": factor_key,
        "factor_label": "APR decay",
        "impact_direction": "NEGATIVE",
        "impact_magnitude": magnitude,
        "confidence": confidence,
        "source_id": source_id,
        "protocol": protocol,
        "strategy_type": strategy_type,
        "evidence": {},
        "notes": "",
        "created_at_utc": "2026-05-27T00:00:00Z",
    }


def _positive_event(
    source_id: str = "defillama",
    protocol: str = "aave-v3",
    strategy_type: str = "stablecoin_lending",
    factor_key: str = "realized_gain",
    magnitude: float = 0.6,
    confidence: float = 0.7,
) -> dict:
    return {
        "event_id": "evt_pos",
        "provenance_id": "prov_002",
        "provenance_source": "PIPELINE",
        "outcome_type": "REALIZED_PNL",
        "factor_category": "YIELD_RISK",
        "factor_key": factor_key,
        "factor_label": "Realized gain",
        "impact_direction": "POSITIVE",
        "impact_magnitude": magnitude,
        "confidence": confidence,
        "source_id": source_id,
        "protocol": protocol,
        "strategy_type": strategy_type,
        "evidence": {},
        "notes": "",
        "created_at_utc": "2026-05-27T00:00:00Z",
    }


def _neutral_event() -> dict:
    return {
        "event_id": "evt_neu",
        "provenance_id": "prov_003",
        "provenance_source": "PIPELINE",
        "outcome_type": "MANUAL_OBSERVATION",
        "factor_category": "USER_BEHAVIOR",
        "factor_key": "observation",
        "factor_label": "Neutral observation",
        "impact_direction": "NEUTRAL",
        "impact_magnitude": 0.5,
        "confidence": 0.5,
        "source_id": "defillama",
        "protocol": "aave-v3",
        "strategy_type": "stablecoin_lending",
        "evidence": {},
        "notes": "",
        "created_at_utc": "2026-05-27T00:00:00Z",
    }


def _candidate(**overrides):
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
    return from_dict(base)


# ============================================================================
# Tests: load events
# ============================================================================


class TestLoadEvents:
    """Load outcome events."""

    def test_loads_empty(self, tmp_path: Path) -> None:
        p = tmp_path / "events.jsonl"
        assert load_outcome_events(p) == []

    def test_loads_events(self, tmp_path: Path) -> None:
        p = tmp_path / "events.jsonl"
        p.write_text(
            json.dumps(_negative_event()) + "\n" + json.dumps(_positive_event()) + "\n",
            encoding="utf-8",
        )
        events = load_outcome_events(p)
        assert len(events) == 2


# ============================================================================
# Tests: derive_learning_bias
# ============================================================================


class TestDeriveLearningBias:
    """Derive learning bias from events."""

    def test_zero_bias_from_no_events(self) -> None:
        bias = derive_learning_bias([])
        assert bias.score_adjustment == 0
        assert bias.evidence_count == 0

    def test_negative_event_creates_negative_bias(self) -> None:
        events = [_negative_event(magnitude=0.8, confidence=0.9)]
        bias = derive_learning_bias(events, source_id="defillama")
        assert bias.score_adjustment < 0
        assert bias.evidence_count == 1

    def test_positive_event_creates_mild_positive_bias(self) -> None:
        events = [_positive_event(magnitude=0.8, confidence=0.9)]
        bias = derive_learning_bias(events, source_id="defillama")
        # Positive is milder (0.5x)
        assert bias.score_adjustment >= 0

    def test_neutral_event_no_meaningful_bias(self) -> None:
        events = [_neutral_event()]
        bias = derive_learning_bias(events, source_id="defillama")
        assert bias.score_adjustment == 0

    def test_repeated_negative_increases_bias(self) -> None:
        events = [
            _negative_event(magnitude=0.8, confidence=0.9),
            _negative_event(magnitude=0.7, confidence=0.8),
            _negative_event(magnitude=0.9, confidence=0.9),
        ]
        bias = derive_learning_bias(events, source_id="defillama")
        assert bias.score_adjustment < 0
        assert bias.evidence_count == 3

    def test_repeated_positive_mild_increase(self) -> None:
        events = [
            _positive_event(magnitude=0.9, confidence=0.9),
            _positive_event(magnitude=0.8, confidence=0.8),
            _positive_event(magnitude=0.9, confidence=0.9),
        ]
        bias = derive_learning_bias(events, source_id="defillama")
        assert bias.score_adjustment >= 0

    def test_dynamic_unknown_factor_key_accepted(self) -> None:
        events = [_negative_event(factor_key="completely_novel_risk_factor")]
        bias = derive_learning_bias(events, source_id="defillama",
                                    factor_key="completely_novel_risk_factor")
        assert bias.evidence_count == 1

    def test_source_specific_bias(self) -> None:
        events = [
            _negative_event(source_id="defillama"),
            _negative_event(source_id="meteora"),
        ]
        bias = derive_learning_bias(events, source_id="defillama")
        assert bias.evidence_count == 1  # Only defillama event

    def test_protocol_specific_bias(self) -> None:
        events = [
            _negative_event(protocol="aave-v3"),
            _negative_event(protocol="compound-v3"),
        ]
        bias = derive_learning_bias(events, protocol="aave-v3")
        assert bias.evidence_count == 1

    def test_strategy_specific_bias(self) -> None:
        events = [
            _negative_event(strategy_type="stablecoin_lending"),
            _negative_event(strategy_type="stable_stable_lp"),
        ]
        bias = derive_learning_bias(events, strategy_type="stablecoin_lending")
        assert bias.evidence_count == 1

    def test_unrelated_source_does_not_affect(self) -> None:
        events = [_negative_event(source_id="meteora")]
        bias = derive_learning_bias(events, source_id="defillama")
        assert bias.score_adjustment == 0
        assert bias.evidence_count == 0

    def test_clamped_to_default_5(self) -> None:
        # Many strong negative events
        events = [_negative_event(magnitude=1.0, confidence=1.0) for _ in range(20)]
        bias = derive_learning_bias(events, source_id="defillama")
        assert bias.score_adjustment >= -5

    def test_custom_clamp_points(self) -> None:
        events = [_negative_event(magnitude=1.0, confidence=1.0) for _ in range(20)]
        bias = derive_learning_bias(events, source_id="defillama", clamp_points=3)
        assert bias.score_adjustment >= -3


# ============================================================================
# Tests: get_bias_for_candidate
# ============================================================================


class TestGetBiasForCandidate:
    """Get bias for a specific candidate."""

    def test_returns_bias(self) -> None:
        c = _candidate()
        events = [_negative_event()]
        bias = get_bias_for_candidate(c, events)
        assert isinstance(bias, LearningBias)
        assert bias.evidence_count == 1

    def test_unrelated_events_ignored(self) -> None:
        c = _candidate(source_id="defillama", protocol="aave-v3")
        events = [_negative_event(source_id="meteora", protocol="raydium")]
        bias = get_bias_for_candidate(c, events)
        assert bias.evidence_count == 0
        assert bias.score_adjustment == 0


# ============================================================================
# Tests: apply_learning_bias
# ============================================================================


class TestApplyLearningBias:
    """Apply learning bias to base score."""

    def test_negative_reduces_score(self) -> None:
        bias = LearningBias(
            source_id=None, protocol=None, strategy_type=None, factor_key=None,
            score_adjustment=-3, confidence_adjustment=0.8, evidence_count=2,
            reasons=(), updated_at_utc="",
        )
        result = apply_learning_bias(80, bias)
        assert result == 77

    def test_positive_increases_score(self) -> None:
        bias = LearningBias(
            source_id=None, protocol=None, strategy_type=None, factor_key=None,
            score_adjustment=2, confidence_adjustment=0.5, evidence_count=1,
            reasons=(), updated_at_utc="",
        )
        result = apply_learning_bias(80, bias)
        assert result == 82

    def test_clamped_to_0(self) -> None:
        bias = LearningBias(
            source_id=None, protocol=None, strategy_type=None, factor_key=None,
            score_adjustment=-5, confidence_adjustment=0.9, evidence_count=5,
            reasons=(), updated_at_utc="",
        )
        result = apply_learning_bias(3, bias)
        assert result == 0

    def test_clamped_to_100(self) -> None:
        bias = LearningBias(
            source_id=None, protocol=None, strategy_type=None, factor_key=None,
            score_adjustment=5, confidence_adjustment=0.9, evidence_count=5,
            reasons=(), updated_at_utc="",
        )
        result = apply_learning_bias(98, bias)
        assert result == 100

    def test_custom_clamp(self) -> None:
        bias = LearningBias(
            source_id=None, protocol=None, strategy_type=None, factor_key=None,
            score_adjustment=-10, confidence_adjustment=0.9, evidence_count=10,
            reasons=(), updated_at_utc="",
        )
        result = apply_learning_bias(80, bias, clamp_points=3)
        assert result == 77  # Only -3 applied


# ============================================================================
# Tests: serialization
# ============================================================================


class TestSerialization:
    """learning_bias_to_dict works correctly."""

    def test_to_dict(self) -> None:
        bias = LearningBias(
            source_id="defillama", protocol="aave-v3",
            strategy_type="stablecoin_lending", factor_key="apr_decay",
            score_adjustment=-3, confidence_adjustment=0.85, evidence_count=2,
            reasons=("learning: 2 negative signal(s), adjustment=-3",),
            updated_at_utc="2026-05-27T00:00:00Z",
        )
        d = learning_bias_to_dict(bias)
        assert d["score_adjustment"] == -3
        assert d["evidence_count"] == 2
        assert isinstance(d["reasons"], list)


# ============================================================================
# Tests: no mutation, no network, no signing
# ============================================================================


class TestNoMutationNoNetwork:
    """LearningMemory cannot mutate policy/allowlists."""

    def test_no_policy_writes(self) -> None:
        import defi_autonomy.learning_memory as mod
        with open(mod.__file__, "r") as f:
            source = f.read()
        assert ".write_text" not in source
        assert "write_json_atomic" not in source

    def test_no_network_calls(self) -> None:
        import socket
        from unittest.mock import patch

        events = [_negative_event()]
        with patch.object(
            socket, "create_connection",
            side_effect=AssertionError("network call detected"),
        ):
            bias = derive_learning_bias(events, source_id="defillama")
            assert bias.evidence_count == 1

    def test_no_signing_imports(self) -> None:
        import defi_autonomy.learning_memory as mod
        with open(mod.__file__, "r") as f:
            source = f.read()
        forbidden_imports = (
            "from eth_account", "import eth_account",
            "from solders", "import solders",
            "from nacl", "import nacl",
            "broadcast_transaction", "sign_transaction",
        )
        for term in forbidden_imports:
            assert term not in source, f"forbidden: {term!r}"

    def test_bias_reasons_no_secrets(self) -> None:
        events = [_negative_event()]
        bias = derive_learning_bias(events, source_id="defillama")
        for reason in bias.reasons:
            assert "private_key" not in reason.lower()
            assert "bearer" not in reason.lower()
            assert "mnemonic" not in reason.lower()

    def test_no_daemon_modification(self) -> None:
        import defi_autonomy.learning_memory as mod
        with open(mod.__file__, "r") as f:
            source = f.read()
        assert "ecosystem.defi.cjs" not in source
        assert "pm2" not in source.lower()
