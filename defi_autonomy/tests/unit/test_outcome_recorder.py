"""Unit tests for defi_autonomy.outcome_recorder — Phase 5.4D.

All tests are deterministic and offline. No network calls. No signing.
No key loading. No broadcast. No policy/allowlist mutation.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from defi_autonomy.outcome_recorder import (
    outcome_event_from_policy_denial,
    outcome_event_from_simulation_failure,
    outcome_event_from_source_degradation,
    outcome_event_from_source_failure,
    record_cycle_outcomes,
)
from defi_autonomy.provenance import (
    OutcomeEvent,
    load_outcome_events,
    validate_outcome_event,
)


# ============================================================================
# Tests: policy denial events
# ============================================================================


class TestPolicyDenialEvents:
    """Policy denial generates low-confidence OutcomeEvent."""

    def test_generates_event(self) -> None:
        event = outcome_event_from_policy_denial(
            action_id="act_001",
            candidate_hash="a" * 64,
            source_id="defillama",
            protocol="aave-v3",
            strategy_type="stablecoin_lending",
            denial_reasons=["risk_score=30 < minimum=50"],
            risk_score=30,
            risk_decision="SKIP",
            provenance_id="prov_001",
        )
        assert isinstance(event, OutcomeEvent)
        validate_outcome_event(event)
        assert event.impact_direction == "NEGATIVE"
        assert event.outcome_type == "POLICY_BLOCKED"

    def test_cap_based_denial_low_confidence(self) -> None:
        event = outcome_event_from_policy_denial(
            denial_reasons=["estimated_tx_usd=50 > max_tx_usd=25"],
            provenance_id="p1",
        )
        assert event.confidence == 0.3
        assert event.factor_category == "WALLET_RISK"

    def test_risk_based_denial_moderate_confidence(self) -> None:
        event = outcome_event_from_policy_denial(
            denial_reasons=["risk_score=30 < minimum=50"],
            provenance_id="p1",
        )
        assert event.confidence == 0.5
        assert event.factor_category == "YIELD_RISK"

    def test_evidence_sanitized(self) -> None:
        event = outcome_event_from_policy_denial(
            denial_reasons=["secret: " + "a" * 64],
            provenance_id="p1",
        )
        # Evidence should be sanitized
        for v in event.evidence.values():
            if isinstance(v, list):
                for item in v:
                    assert "a" * 64 not in str(item)


# ============================================================================
# Tests: simulation failure events
# ============================================================================


class TestSimulationFailureEvents:
    """Simulation failure generates EXECUTION_RISK event."""

    def test_generates_event(self) -> None:
        event = outcome_event_from_simulation_failure(
            action_id="act_002",
            candidate_hash="b" * 64,
            source_id="defillama",
            protocol="aave-v3",
            strategy_type="stablecoin_lending",
            failure_reasons=["EVM revert: insufficient balance"],
            provenance_id="prov_002",
        )
        assert isinstance(event, OutcomeEvent)
        validate_outcome_event(event)
        assert event.factor_category == "EXECUTION_RISK"
        assert event.outcome_type == "SIMULATION_FAILED"

    def test_slippage_failure(self) -> None:
        event = outcome_event_from_simulation_failure(
            failure_reasons=["slippage_bps=100 > max_slippage_bps=50"],
            slippage_bps=100,
            provenance_id="p1",
        )
        assert "slippage" in event.factor_key

    def test_high_gas_failure(self) -> None:
        event = outcome_event_from_simulation_failure(
            failure_reasons=["estimated_total_usd exceeds tolerance"],
            estimated_gas_usd=5.0,
            provenance_id="p1",
        )
        assert "gas" in event.factor_key or "tolerance" in event.factor_key.replace("_", " ")


# ============================================================================
# Tests: source failure events
# ============================================================================


class TestSourceFailureEvents:
    """Source failure generates SOURCE_RISK event."""

    def test_generates_event(self) -> None:
        event = outcome_event_from_source_failure(
            source_id="defillama",
            error_type="SourceTimeoutError",
            error_message="timeout fetching https://api.llama.fi/pools",
            provenance_id="prov_003",
        )
        assert isinstance(event, OutcomeEvent)
        validate_outcome_event(event)
        assert event.factor_category == "SOURCE_RISK"
        assert event.source_id == "defillama"

    def test_protocol_is_none(self) -> None:
        event = outcome_event_from_source_failure(source_id="meteora")
        assert event.protocol is None
        assert event.strategy_type is None

    def test_evidence_sanitized(self) -> None:
        event = outcome_event_from_source_failure(
            source_id="test",
            error_message="Bearer sk-secret123 failed",
        )
        evidence_str = json.dumps(event.evidence)
        assert "sk-secret123" not in evidence_str


# ============================================================================
# Tests: source degradation events
# ============================================================================


class TestSourceDegradationEvents:
    """Source degradation generates SOURCE_RISK event."""

    def test_generates_event(self) -> None:
        event = outcome_event_from_source_degradation(
            source_id="defillama",
            consecutive_failures=5,
        )
        assert isinstance(event, OutcomeEvent)
        validate_outcome_event(event)
        assert event.factor_category == "SOURCE_RISK"
        assert "degradation" in event.factor_key

    def test_magnitude_scales_with_failures(self) -> None:
        e3 = outcome_event_from_source_degradation("src", 3)
        e8 = outcome_event_from_source_degradation("src", 8)
        assert e8.impact_magnitude > e3.impact_magnitude

    def test_magnitude_capped(self) -> None:
        e = outcome_event_from_source_degradation("src", 100)
        assert e.impact_magnitude <= 0.5


# ============================================================================
# Tests: record_cycle_outcomes
# ============================================================================


class TestRecordCycleOutcomes:
    """record_cycle_outcomes writes to outcome_events.jsonl."""

    def test_records_policy_denials(self, tmp_path: Path) -> None:
        denials = [
            {
                "action_id": "act_001",
                "candidate_hash": "a" * 64,
                "source_id": "defillama",
                "protocol": "aave-v3",
                "strategy_type": "stablecoin_lending",
                "denial_reasons": ["risk_score=30 < minimum=50"],
                "risk_score": 30,
                "risk_decision": "SKIP",
                "provenance_id": "prov_001",
            }
        ]
        events = record_cycle_outcomes(tmp_path, policy_denials=denials)
        assert len(events) == 1
        # Check file was written
        p = tmp_path / "data" / "outcome_events.jsonl"
        assert p.exists()

    def test_records_simulation_failures(self, tmp_path: Path) -> None:
        sim_fails = [
            {
                "action_id": "act_002",
                "failure_reasons": ["revert"],
                "protocol": "aave-v3",
                "provenance_id": "prov_002",
            }
        ]
        events = record_cycle_outcomes(tmp_path, simulation_failures=sim_fails)
        assert len(events) == 1

    def test_records_source_failures(self, tmp_path: Path) -> None:
        src_fails = [
            {"source_id": "defillama", "error_type": "timeout", "provenance_id": "p1"}
        ]
        events = record_cycle_outcomes(tmp_path, source_failures=src_fails)
        assert len(events) == 1

    def test_records_source_degradation(self, tmp_path: Path) -> None:
        health = {
            "defillama": {"consecutive_failures": 5},
            "meteora": {"consecutive_failures": 1},  # below threshold
        }
        events = record_cycle_outcomes(tmp_path, source_health=health)
        assert len(events) == 1  # Only defillama (>= 3)

    def test_generated_events_loadable(self, tmp_path: Path) -> None:
        denials = [{"denial_reasons": ["test"], "provenance_id": "p1"}]
        record_cycle_outcomes(tmp_path, policy_denials=denials)
        p = tmp_path / "data" / "outcome_events.jsonl"
        loaded = load_outcome_events(p)
        assert len(loaded) == 1

    def test_empty_inputs_no_events(self, tmp_path: Path) -> None:
        events = record_cycle_outcomes(tmp_path)
        assert events == []


# ============================================================================
# Tests: no mutation, no network, no signing
# ============================================================================


class TestNoMutationNoNetwork:
    """OutcomeRecorder does not mutate policy/allowlists."""

    def test_no_policy_writes(self) -> None:
        import defi_autonomy.outcome_recorder as mod
        with open(mod.__file__, "r") as f:
            source = f.read()
        # Should not open/write policy or allowlist files
        assert "risk_policy.json" not in source
        assert ".write_text" not in source
        assert "write_json_atomic" not in source

    def test_no_network_calls(self, tmp_path: Path) -> None:
        import socket
        from unittest.mock import patch

        with patch.object(
            socket, "create_connection",
            side_effect=AssertionError("network call detected"),
        ):
            denials = [{"denial_reasons": ["test"], "provenance_id": "p1"}]
            events = record_cycle_outcomes(tmp_path, policy_denials=denials)
            assert len(events) == 1

    def test_no_signing_imports(self) -> None:
        import defi_autonomy.outcome_recorder as mod
        with open(mod.__file__, "r") as f:
            source = f.read()
        forbidden = (
            "from eth_account", "import eth_account",
            "from solders", "import solders",
            "broadcast_transaction", "sign_transaction",
        )
        for term in forbidden:
            assert term not in source, f"forbidden: {term!r}"

    def test_no_daemon_modification(self) -> None:
        import defi_autonomy.outcome_recorder as mod
        with open(mod.__file__, "r") as f:
            source = f.read()
        assert "ecosystem.defi.cjs" not in source
        assert "pm2" not in source.lower()
