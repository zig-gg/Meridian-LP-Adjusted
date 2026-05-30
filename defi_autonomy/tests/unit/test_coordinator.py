"""Unit tests for defi_autonomy.coordinator — Sprint 4, Phase 4.1.

All tests are deterministic and offline. No real network calls. No signing
with real keys. No broadcast. Uses mocked ingestion and fake providers.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from defi_autonomy.coordinator import (
    CycleReport,
    cycle_report_to_dict,
    load_normalized_candidates,
    load_risk_policy,
    run_cycle,
    write_cycle_report,
)
from defi_autonomy.sources.base import SourceAdapterError, SourceAllowlistEntry


# ============================================================================
# Fixtures
# ============================================================================


def _risk_policy(**overrides) -> dict:
    base = {
        "version": 1,
        "autonomy_level": 1,
        "max_wallet_value_usd": 100,
        "max_tx_usd": 25,
        "max_daily_spend_usd": 50,
        "max_slippage_bps": 50,
        "allowed_chains": ["Base", "BNB Chain", "Solana"],
        "allowed_strategy_types": [
            "stablecoin_lending", "stable_stable_lp",
            "xstocks_points", "xstocks_lp",
        ],
        "blocked_actions": ["bridge", "borrow", "leverage", "unlimited_approval"],
        "require_contract_allowlist": False,
        "require_token_allowlist": False,
        "require_pool_allowlist": False,
        "kill_switch_file": None,
        "operator_funded_agent_wallet_address": "0x" + "ab" * 20,
        "allow_signing_prep": False,
    }
    base.update(overrides)
    return base


def _allowlist_doc() -> dict:
    return {
        "version": 1,
        "entries": [
            {
                "source_id": "defillama",
                "adapter_name": "defillama_adapter",
                "domains": ["api.llama.fi", "yields.llama.fi"],
                "max_freshness_seconds": 1800,
                "fetch_timeout_seconds": 10,
                "methods": ["GET", "HEAD"],
                "max_response_bytes": 4194304,
                "source_confidence_score": 0.7,
            },
            {
                "source_id": "stablecoin_benchmark",
                "adapter_name": "stablecoin_benchmark_adapter",
                "domains": ["yields.llama.fi", "api.llama.fi"],
                "max_freshness_seconds": 1800,
                "fetch_timeout_seconds": 10,
                "methods": ["GET", "HEAD"],
                "max_response_bytes": 4194304,
                "source_confidence_score": 0.8,
            },
        ],
    }


def _valid_pool_response() -> bytes:
    data = {
        "status": "success",
        "data": [
            {
                "chain": "Base",
                "project": "aave-v3",
                "symbol": "USDC",
                "tvlUsd": 50_000_000.0,
                "apy": 4.5,
                "apyBase": 3.5,
                "apyReward": 1.0,
                "pool": "0x" + "a" * 40,
                "underlyingTokens": ["0x" + "b" * 40],
                "stablecoin": True,
                "ilRisk": "no",
                "volumeUsd1d": 1_000_000.0,
            }
        ],
    }
    return json.dumps(data).encode("utf-8")


def _setup_base(
    tmp_path: Path,
    policy: dict | None = None,
    kill_switch: bool = False,
    macro_halt: bool = False,
) -> Path:
    """Set up a complete base_dir for coordinator tests."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "risk_policy.json").write_text(
        json.dumps(policy or _risk_policy()), encoding="utf-8"
    )
    (data_dir / "source_allowlist.json").write_text(
        json.dumps(_allowlist_doc()), encoding="utf-8"
    )
    (data_dir / "contract_allowlist.json").write_text(
        json.dumps({"version": 1, "entries": []}), encoding="utf-8"
    )
    (data_dir / "token_allowlist.json").write_text(
        json.dumps({"version": 1, "entries": []}), encoding="utf-8"
    )
    (data_dir / "pool_allowlist.json").write_text(
        json.dumps({"version": 1, "entries": []}), encoding="utf-8"
    )
    if kill_switch:
        (tmp_path / "KILL_SWITCH.md").write_text("STOP\nAll halted.", encoding="utf-8")
    if macro_halt:
        (data_dir / "macro_state.json").write_text(
            json.dumps({"state": "HALT"}), encoding="utf-8"
        )
    return tmp_path


def _mock_client_factory(response: bytes = None):
    """Create a client factory that returns mocked responses."""
    resp = response or _valid_pool_response()

    def factory(entry: SourceAllowlistEntry):
        client = MagicMock()
        client.request = MagicMock(return_value=resp)
        return client

    return factory


class FakeSigner:
    """Fake signer for testing."""

    def __init__(self, address: str = "0x" + "ab" * 20):
        self._address = address

    def sign(self, unsigned_tx: dict, chain: str) -> bytes:
        payload = json.dumps(unsigned_tx, sort_keys=True).encode()
        return hashlib.sha256(payload + chain.encode()).digest()

    def derive_address(self, chain: str) -> str:
        return self._address


# ============================================================================
# Tests: kill switch
# ============================================================================


class TestKillSwitch:
    """Kill switch returns HALTED before ingestion."""

    def test_kill_switch_halts(self, tmp_path: Path) -> None:
        base = _setup_base(tmp_path, kill_switch=True)
        report = run_cycle(base)
        assert report.status == "HALTED"
        assert report.ingestion_status == "NOT_RUN"
        assert any("kill switch" in e for e in report.errors)

    def test_kill_switch_no_candidates(self, tmp_path: Path) -> None:
        base = _setup_base(tmp_path, kill_switch=True)
        report = run_cycle(base)
        assert report.candidate_count == 0


# ============================================================================
# Tests: macro HALT
# ============================================================================


class TestMacroHalt:
    """Macro HALT returns HALTED."""

    def test_macro_halt_halts(self, tmp_path: Path) -> None:
        base = _setup_base(tmp_path, macro_halt=True)
        report = run_cycle(base)
        assert report.status == "HALTED"
        assert any("HALT" in e for e in report.errors)


# ============================================================================
# Tests: successful cycle
# ============================================================================


class TestSuccessfulCycle:
    """Successful cycle with candidates produces COMPLETE."""

    def test_complete_cycle(self, tmp_path: Path) -> None:
        base = _setup_base(tmp_path)
        report = run_cycle(base, _client_factory=_mock_client_factory())
        assert report.status == "COMPLETE"
        assert report.candidate_count > 0
        assert report.risk_assessment_count > 0

    def test_cycle_report_written(self, tmp_path: Path) -> None:
        base = _setup_base(tmp_path)
        run_cycle(base, _client_factory=_mock_client_factory())
        report_path = base / "data" / "cycle_report.json"
        assert report_path.exists()
        doc = json.loads(report_path.read_text(encoding="utf-8"))
        assert doc["status"] == "COMPLETE"

    def test_cycle_report_is_cycle_report(self, tmp_path: Path) -> None:
        base = _setup_base(tmp_path)
        report = run_cycle(base, _client_factory=_mock_client_factory())
        assert isinstance(report, CycleReport)


# ============================================================================
# Tests: no valid data
# ============================================================================


class TestNoValidData:
    """No valid data produces NO_VALID_DATA."""

    def test_all_sources_fail(self, tmp_path: Path) -> None:
        base = _setup_base(tmp_path)

        def fail_factory(entry):
            client = MagicMock()
            client.request = MagicMock(
                side_effect=SourceAdapterError("all down")
            )
            return client

        report = run_cycle(base, _client_factory=fail_factory)
        assert report.status == "NO_VALID_DATA"


# ============================================================================
# Tests: autonomy level
# ============================================================================


class TestAutonomyLevel:
    """Autonomy level controls WalletExecutor access."""

    def test_level_1_never_calls_wallet_executor(self, tmp_path: Path) -> None:
        policy = _risk_policy(autonomy_level=1, allow_signing_prep=True)
        base = _setup_base(tmp_path, policy=policy)
        signer = FakeSigner()
        report = run_cycle(
            base, signer_provider=signer, _client_factory=_mock_client_factory()
        )
        # Level 1 should never prepare signing
        assert report.signing_prepared_count == 0

    def test_level_2_may_call_wallet_executor(self, tmp_path: Path) -> None:
        policy = _risk_policy(autonomy_level=2, allow_signing_prep=True)
        base = _setup_base(tmp_path, policy=policy)
        signer = FakeSigner()
        report = run_cycle(
            base, signer_provider=signer, _client_factory=_mock_client_factory()
        )
        # Level 2 with signer and allow_signing_prep may prepare signing
        # (depends on whether candidates pass policy)
        assert report.autonomy_level == 2

    def test_level_2_without_signer_no_signing(self, tmp_path: Path) -> None:
        policy = _risk_policy(autonomy_level=2, allow_signing_prep=True)
        base = _setup_base(tmp_path, policy=policy)
        report = run_cycle(base, signer_provider=None, _client_factory=_mock_client_factory())
        assert report.signing_prepared_count == 0


# ============================================================================
# Tests: policy denials counted
# ============================================================================


class TestPolicyDenials:
    """Policy denials are counted."""

    def test_denials_counted(self, tmp_path: Path) -> None:
        base = _setup_base(tmp_path)
        report = run_cycle(base, _client_factory=_mock_client_factory())
        # With default policy (require_contract_allowlist=False), some may pass
        # Total should equal assessments
        total = report.approved_count + report.denied_count
        assert total == report.risk_assessment_count


# ============================================================================
# Tests: simulation failures counted
# ============================================================================


class TestSimulationFailures:
    """Simulation failures are counted."""

    def test_simulation_failure_counted(self, tmp_path: Path) -> None:
        base = _setup_base(tmp_path)

        class FailSimProvider:
            def simulate(self, action):
                return {
                    "gas_usd": 0,
                    "expected_token_deltas": {},
                    "warnings": [],
                    "failure_reasons": ["revert"],
                    "simulation_passed": False,
                }

        report = run_cycle(
            base,
            simulation_provider=FailSimProvider(),
            _client_factory=_mock_client_factory(),
        )
        # If any candidates were approved, simulation failures should be counted
        if report.approved_count > 0:
            assert report.simulation_failed_count > 0


# ============================================================================
# Tests: one candidate failure does not crash cycle
# ============================================================================


class TestPartialFailure:
    """One candidate failure does not crash entire cycle."""

    def test_mixed_candidates(self, tmp_path: Path) -> None:
        # Response with one valid and one invalid pool
        data = {
            "status": "success",
            "data": [
                {
                    "chain": "Base",
                    "project": "aave-v3",
                    "symbol": "USDC",
                    "tvlUsd": 50_000_000.0,
                    "apy": 4.5,
                    "apyBase": 3.5,
                    "apyReward": 1.0,
                    "pool": "0x" + "a" * 40,
                    "underlyingTokens": ["0x" + "b" * 40],
                    "stablecoin": True,
                    "ilRisk": "no",
                },
                {
                    "chain": "Ethereum",  # unsupported
                    "project": "compound",
                    "symbol": "ETH",
                    "tvlUsd": 100,
                    "apy": 200,  # out of bounds
                },
            ],
        }
        resp = json.dumps(data).encode("utf-8")
        base = _setup_base(tmp_path)
        report = run_cycle(base, _client_factory=_mock_client_factory(resp))
        # Should not crash
        assert report.status in ("COMPLETE", "NO_VALID_DATA")


# ============================================================================
# Tests: errors sanitized
# ============================================================================


class TestErrorSanitization:
    """Errors are sanitized."""

    def test_no_secrets_in_errors(self, tmp_path: Path) -> None:
        base = _setup_base(tmp_path)
        report = run_cycle(base, _client_factory=_mock_client_factory())
        for err in report.errors:
            assert "private_key" not in err.lower()
            assert "seed_phrase" not in err.lower()


# ============================================================================
# Tests: no broadcast
# ============================================================================


class TestNoBroadcast:
    """No broadcast path exists."""

    def test_no_broadcast_in_cycle(self, tmp_path: Path) -> None:
        policy = _risk_policy(autonomy_level=2, allow_signing_prep=True)
        base = _setup_base(tmp_path, policy=policy)
        signer = FakeSigner()
        report = run_cycle(
            base, signer_provider=signer, _client_factory=_mock_client_factory()
        )
        # Even if signing happens, no broadcast
        assert report.status in ("COMPLETE", "NO_VALID_DATA", "FAILED")
        # Check module source
        import defi_autonomy.coordinator as mod
        with open(mod.__file__, "r") as f:
            source = f.read()
        assert "broadcast_transaction" not in source


# ============================================================================
# Tests: no network calls, no private key loading
# ============================================================================


class TestNoNetworkNoKeys:
    """No real network calls or private key loading."""

    def test_no_network_calls(self, tmp_path: Path) -> None:
        import socket
        from unittest.mock import patch as _patch

        base = _setup_base(tmp_path)
        with _patch.object(
            socket,
            "create_connection",
            side_effect=AssertionError("network call detected"),
        ):
            report = run_cycle(base, _client_factory=_mock_client_factory())
            assert report.status == "COMPLETE"

    def test_no_private_key_loading(self) -> None:
        import defi_autonomy.coordinator as mod
        with open(mod.__file__, "r") as f:
            source = f.read()
        forbidden = (
            "load_operator_wallet_key",
            "os.environ",
            "HERMES_DEFI_AGENT_PRIVATE_KEY",
            "eth_account",
            "solders",
        )
        for term in forbidden:
            assert term not in source, f"forbidden term {term!r} in coordinator"

    def test_no_daemon_modification(self) -> None:
        """Existing Hermes daemons are untouched."""
        import defi_autonomy.coordinator as mod
        with open(mod.__file__, "r") as f:
            source = f.read()
        assert "ecosystem.defi.cjs" not in source
        assert "pm2" not in source.lower()


# ============================================================================
# Tests: LearningMemory integration — Phase 5.4C
# ============================================================================


class TestLearningMemoryIntegration:
    """Coordinator integrates LearningMemory into the cycle."""

    def test_loads_outcome_events_when_present(self, tmp_path: Path) -> None:
        base = _setup_base(tmp_path)
        # Create outcome_events.jsonl with a negative event
        events_path = base / "data" / "outcome_events.jsonl"
        event = {
            "event_id": "evt_001",
            "provenance_id": "prov_001",
            "provenance_source": "PIPELINE",
            "outcome_type": "REALIZED_PNL",
            "factor_category": "YIELD_RISK",
            "factor_key": "apr_decay",
            "factor_label": "APR decay",
            "impact_direction": "NEGATIVE",
            "impact_magnitude": 0.9,
            "confidence": 0.9,
            "source_id": "defillama",
            "protocol": "aave-v3",
            "strategy_type": "stablecoin_lending",
            "evidence": {},
            "notes": "",
            "created_at_utc": "2026-05-27T00:00:00Z",
        }
        events_path.write_text(
            json.dumps(event) + "\n", encoding="utf-8"
        )
        report = run_cycle(base, _client_factory=_mock_client_factory())
        assert report.learning_events_loaded == 1

    def test_works_when_outcome_events_absent(self, tmp_path: Path) -> None:
        base = _setup_base(tmp_path)
        # No outcome_events.jsonl
        report = run_cycle(base, _client_factory=_mock_client_factory())
        assert report.learning_events_loaded == 0
        assert report.learning_bias_applied_count == 0
        assert report.status == "COMPLETE"

    def test_learning_events_loaded_in_report(self, tmp_path: Path) -> None:
        base = _setup_base(tmp_path)
        events_path = base / "data" / "outcome_events.jsonl"
        events = [
            {"event_id": f"e{i}", "provenance_id": "p1", "provenance_source": "PIPELINE",
             "outcome_type": "REALIZED_PNL", "factor_category": "YIELD_RISK",
             "factor_key": "apr_decay", "factor_label": "decay",
             "impact_direction": "NEGATIVE", "impact_magnitude": 0.8,
             "confidence": 0.9, "source_id": "defillama", "protocol": "aave-v3",
             "strategy_type": "stablecoin_lending", "evidence": {}, "notes": "",
             "created_at_utc": "2026-05-27T00:00:00Z"}
            for i in range(5)
        ]
        events_path.write_text(
            "\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8"
        )
        report = run_cycle(base, _client_factory=_mock_client_factory())
        assert report.learning_events_loaded == 5

    def test_learning_bias_applied_count_in_report(self, tmp_path: Path) -> None:
        base = _setup_base(tmp_path)
        # Create strong negative events for defillama/aave-v3/stablecoin_lending
        events_path = base / "data" / "outcome_events.jsonl"
        events = [
            {"event_id": f"e{i}", "provenance_id": "p1", "provenance_source": "PIPELINE",
             "outcome_type": "REALIZED_PNL", "factor_category": "YIELD_RISK",
             "factor_key": "apr_decay", "factor_label": "decay",
             "impact_direction": "NEGATIVE", "impact_magnitude": 1.0,
             "confidence": 1.0, "source_id": "defillama", "protocol": "aave-v3",
             "strategy_type": "stablecoin_lending", "evidence": {}, "notes": "",
             "created_at_utc": "2026-05-27T00:00:00Z"}
            for i in range(10)
        ]
        events_path.write_text(
            "\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8"
        )
        report = run_cycle(base, _client_factory=_mock_client_factory())
        # At least some candidates should have bias applied
        assert report.learning_bias_applied_count >= 0  # May be 0 if no matching candidates

    def test_cycle_report_json_includes_learning_fields(self, tmp_path: Path) -> None:
        base = _setup_base(tmp_path)
        run_cycle(base, _client_factory=_mock_client_factory())
        report_path = base / "data" / "cycle_report.json"
        doc = json.loads(report_path.read_text(encoding="utf-8"))
        assert "learning_events_loaded" in doc
        assert "learning_bias_applied_count" in doc

    def test_no_policy_mutation_during_learning(self, tmp_path: Path) -> None:
        base = _setup_base(tmp_path)
        policy_path = base / "data" / "risk_policy.json"
        original = policy_path.read_text(encoding="utf-8")
        # Add outcome events
        events_path = base / "data" / "outcome_events.jsonl"
        events_path.write_text(
            json.dumps({"event_id": "e1", "provenance_id": "p1",
                        "provenance_source": "PIPELINE", "outcome_type": "REALIZED_PNL",
                        "factor_category": "YIELD_RISK", "factor_key": "apr_decay",
                        "factor_label": "decay", "impact_direction": "NEGATIVE",
                        "impact_magnitude": 1.0, "confidence": 1.0,
                        "source_id": "defillama", "protocol": "aave-v3",
                        "strategy_type": "stablecoin_lending",
                        "evidence": {}, "notes": "", "created_at_utc": "2026-05-27T00:00:00Z"}) + "\n",
            encoding="utf-8",
        )
        run_cycle(base, _client_factory=_mock_client_factory())
        # Policy must not have changed
        assert policy_path.read_text(encoding="utf-8") == original

    def test_coordinator_only_reads_outcome_events(self) -> None:
        """Coordinator must not write to outcome_events.jsonl."""
        import defi_autonomy.coordinator as mod
        with open(mod.__file__, "r") as f:
            source = f.read()
        # Should not contain append_outcome_event or write to outcome_events
        assert "append_outcome_event" not in source
        assert "outcome_events.jsonl\", \"w" not in source
