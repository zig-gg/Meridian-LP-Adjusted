"""Unit tests for defi_autonomy.tx_simulator — Sprint 3, Phase 3.1.

All tests are deterministic and offline. No real network calls. No signing.
No key loading. Uses only fake providers.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from defi_autonomy.policy_engine import ActionDescriptor, ApprovalToken
from defi_autonomy.tx_simulator import (
    ApprovalTokenInvalidError,
    PolicyApprovalRequiredError,
    SimulationResult,
    UnsupportedChainSimulationError,
    simulate_action,
    simulation_result_to_dict,
    validate_approval,
    write_simulation_ledger,
)


# ============================================================================
# Fixtures
# ============================================================================


def _future_utc(seconds: int = 300) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def _past_utc(seconds: int = 300) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _make_action(**overrides) -> ActionDescriptor:
    defaults = {
        "action_id": "act_sim_001",
        "candidate_hash": "a" * 64,
        "source_id": "defillama",
        "adapter_name": "defillama_adapter",
        "chain": "Base",
        "protocol": "aave-v3",
        "strategy_type": "stablecoin_lending",
        "action_type": "FARM",
        "pool_address": None,
        "token_addresses": (),
        "estimated_tx_usd": 5.0,
        "estimated_wallet_value_usd": 20.0,
        "estimated_daily_spend_usd": 5.0,
        "slippage_bps": 10,
        "risk_score": 90,
        "risk_decision": "FARM",
        "created_at_utc": "2026-05-27T00:00:00Z",
        "metadata": {},
    }
    defaults.update(overrides)
    return ActionDescriptor(**defaults)


def _make_token(**overrides) -> ApprovalToken:
    defaults = {
        "approval_id": "appr_sim_001",
        "action_id": "act_sim_001",
        "candidate_hash": "a" * 64,
        "policy_digest": "p" * 64,
        "allowlist_digest": "l" * 64,
        "approved": True,
        "reasons": ("all policy checks passed",),
        "warnings": (),
        "expires_at_utc": _future_utc(300),
        "created_at_utc": _now_utc(),
    }
    defaults.update(overrides)
    return ApprovalToken(**defaults)


def _risk_policy(**overrides) -> dict:
    base = {
        "max_tx_usd": 25,
        "max_slippage_bps": 50,
        "simulation_value_tolerance_bps": 50,
    }
    base.update(overrides)
    return base


class _FakeProviderPass:
    """Fake provider that always passes."""

    def simulate(self, action: ActionDescriptor, tx_bytes: bytes | None = None) -> dict:
        return {
            "gas_usd": 0.05,
            "expected_token_deltas": {"USDC": -5.0, "aUSDC": 5.0},
            "warnings": ["test warning"],
            "failure_reasons": [],
            "simulation_passed": True,
        }


class _FakeProviderFail:
    """Fake provider that always fails."""

    def simulate(self, action: ActionDescriptor, tx_bytes: bytes | None = None) -> dict:
        return {
            "gas_usd": 0.1,
            "expected_token_deltas": {},
            "warnings": ["revert detected"],
            "failure_reasons": ["EVM revert: insufficient balance"],
            "simulation_passed": False,
        }


class _FakeProviderError:
    """Fake provider that raises an exception."""

    def simulate(self, action: ActionDescriptor, tx_bytes: bytes | None = None) -> dict:
        raise RuntimeError("RPC connection failed")


# ============================================================================
# Tests: valid approved action simulates successfully
# ============================================================================


class TestValidSimulation:
    """Valid approved action simulates successfully."""

    def test_passes_with_provider(self) -> None:
        action = _make_action()
        token = _make_token()
        result = simulate_action(action, token, _risk_policy(), provider=_FakeProviderPass())
        assert isinstance(result, SimulationResult)
        assert result.simulation_passed is True
        assert result.approved_by_policy is True
        assert result.action_id == "act_sim_001"

    def test_passes_dry_run_no_provider(self) -> None:
        action = _make_action()
        token = _make_token()
        result = simulate_action(action, token, _risk_policy(), provider=None)
        assert result.simulation_passed is True
        assert any("dry-run" in w for w in result.warnings)

    def test_result_has_all_fields(self) -> None:
        action = _make_action()
        token = _make_token()
        result = simulate_action(action, token, _risk_policy(), provider=_FakeProviderPass())
        assert result.simulation_id.startswith("sim_")
        assert result.approval_id == "appr_sim_001"
        assert result.candidate_hash == "a" * 64
        assert result.chain == "Base"
        assert result.protocol == "aave-v3"
        assert result.action_type == "FARM"


# ============================================================================
# Tests: approval token validation
# ============================================================================


class TestApprovalValidation:
    """Approval token validation catches invalid tokens."""

    def test_none_token_rejected(self) -> None:
        action = _make_action()
        with pytest.raises(PolicyApprovalRequiredError):
            simulate_action(action, None, _risk_policy())  # type: ignore

    def test_unapproved_token_rejected(self) -> None:
        action = _make_action()
        token = _make_token(approved=False)
        with pytest.raises(ApprovalTokenInvalidError, match="approved is False"):
            simulate_action(action, token, _risk_policy())

    def test_mismatched_action_id_rejected(self) -> None:
        action = _make_action()
        token = _make_token(action_id="different_action")
        with pytest.raises(ApprovalTokenInvalidError, match="action_id mismatch"):
            simulate_action(action, token, _risk_policy())

    def test_mismatched_candidate_hash_rejected(self) -> None:
        action = _make_action()
        token = _make_token(candidate_hash="b" * 64)
        with pytest.raises(ApprovalTokenInvalidError, match="candidate_hash mismatch"):
            simulate_action(action, token, _risk_policy())

    def test_expired_token_rejected(self) -> None:
        action = _make_action()
        token = _make_token(expires_at_utc=_past_utc(60))
        with pytest.raises(ApprovalTokenInvalidError, match="expired"):
            simulate_action(action, token, _risk_policy())


# ============================================================================
# Tests: unsupported chain
# ============================================================================


class TestUnsupportedChain:
    """Unsupported chain raises UnsupportedChainSimulationError."""

    def test_unsupported_chain_rejected(self) -> None:
        action = _make_action(chain="Ethereum")
        token = _make_token()
        with pytest.raises(UnsupportedChainSimulationError):
            simulate_action(action, token, _risk_policy())


# ============================================================================
# Tests: policy cap failures
# ============================================================================


class TestPolicyCapFailures:
    """Policy caps produce failed SimulationResult."""

    def test_max_tx_usd_exceeded(self) -> None:
        action = _make_action(estimated_tx_usd=50.0)
        token = _make_token()
        result = simulate_action(action, token, _risk_policy(max_tx_usd=25))
        assert result.simulation_passed is False
        assert any("max_tx_usd" in r for r in result.failure_reasons)

    def test_slippage_cap_exceeded(self) -> None:
        action = _make_action(slippage_bps=100)
        token = _make_token()
        result = simulate_action(action, token, _risk_policy(max_slippage_bps=50))
        assert result.simulation_passed is False
        assert any("slippage" in r for r in result.failure_reasons)


# ============================================================================
# Tests: blocked metadata flags
# ============================================================================


class TestBlockedMetadata:
    """Blocked metadata flags produce failed SimulationResult."""

    def test_bridge_rejected(self) -> None:
        action = _make_action(metadata={"bridge": True})
        token = _make_token()
        result = simulate_action(action, token, _risk_policy())
        assert result.simulation_passed is False
        assert any("bridge" in r for r in result.failure_reasons)

    def test_borrow_rejected(self) -> None:
        action = _make_action(metadata={"borrow": True})
        token = _make_token()
        result = simulate_action(action, token, _risk_policy())
        assert result.simulation_passed is False
        assert any("borrow" in r for r in result.failure_reasons)

    def test_leverage_rejected(self) -> None:
        action = _make_action(metadata={"leverage": True})
        token = _make_token()
        result = simulate_action(action, token, _risk_policy())
        assert result.simulation_passed is False
        assert any("leverage" in r for r in result.failure_reasons)

    def test_unlimited_approval_rejected(self) -> None:
        action = _make_action(metadata={"unlimited_approval": True})
        token = _make_token()
        result = simulate_action(action, token, _risk_policy())
        assert result.simulation_passed is False
        assert any("unlimited_approval" in r for r in result.failure_reasons)


# ============================================================================
# Tests: provider behavior
# ============================================================================


class TestProviderBehavior:
    """Provider results are propagated correctly."""

    def test_provider_pass_accepted(self) -> None:
        action = _make_action()
        token = _make_token()
        result = simulate_action(action, token, _risk_policy(), provider=_FakeProviderPass())
        assert result.simulation_passed is True
        assert result.estimated_gas_usd == 0.05

    def test_provider_failure_becomes_failed_result(self) -> None:
        action = _make_action()
        token = _make_token()
        result = simulate_action(action, token, _risk_policy(), provider=_FakeProviderFail())
        assert result.simulation_passed is False
        assert any("revert" in r.lower() for r in result.failure_reasons)

    def test_provider_warnings_propagated(self) -> None:
        action = _make_action()
        token = _make_token()
        result = simulate_action(action, token, _risk_policy(), provider=_FakeProviderPass())
        assert "test warning" in result.warnings

    def test_expected_token_deltas_propagated(self) -> None:
        action = _make_action()
        token = _make_token()
        result = simulate_action(action, token, _risk_policy(), provider=_FakeProviderPass())
        assert result.expected_token_deltas == {"USDC": -5.0, "aUSDC": 5.0}

    def test_provider_exception_becomes_failure(self) -> None:
        action = _make_action()
        token = _make_token()
        result = simulate_action(action, token, _risk_policy(), provider=_FakeProviderError())
        assert result.simulation_passed is False
        assert any("provider error" in r for r in result.failure_reasons)


# ============================================================================
# Tests: simulation ledger
# ============================================================================


class TestSimulationLedger:
    """Simulation ledger appends JSONL records."""

    def test_ledger_appends_passed(self, tmp_path: Path) -> None:
        action = _make_action()
        token = _make_token()
        result = simulate_action(action, token, _risk_policy(), provider=_FakeProviderPass())
        ledger_path = tmp_path / "simulation_ledger.jsonl"
        write_simulation_ledger(ledger_path, result)
        assert ledger_path.exists()
        lines = ledger_path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["event_type"] == "SIMULATION_PASSED"
        assert record["simulation_id"] == result.simulation_id
        assert record["action_id"] == "act_sim_001"

    def test_ledger_appends_failed(self, tmp_path: Path) -> None:
        action = _make_action(estimated_tx_usd=50.0)
        token = _make_token()
        result = simulate_action(action, token, _risk_policy(max_tx_usd=25))
        ledger_path = tmp_path / "simulation_ledger.jsonl"
        write_simulation_ledger(ledger_path, result)
        lines = ledger_path.read_text(encoding="utf-8").strip().split("\n")
        record = json.loads(lines[0])
        assert record["event_type"] == "SIMULATION_FAILED"
        assert len(record["failure_reasons"]) > 0

    def test_ledger_appends_multiple(self, tmp_path: Path) -> None:
        action = _make_action()
        token = _make_token()
        r1 = simulate_action(action, token, _risk_policy(), provider=_FakeProviderPass())
        r2 = simulate_action(action, token, _risk_policy(), provider=_FakeProviderFail())
        ledger_path = tmp_path / "simulation_ledger.jsonl"
        write_simulation_ledger(ledger_path, r1)
        write_simulation_ledger(ledger_path, r2)
        lines = ledger_path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 2


# ============================================================================
# Tests: deterministic dry-run
# ============================================================================


class TestDryRun:
    """Deterministic dry-run works when provider is None."""

    def test_dry_run_passes(self) -> None:
        action = _make_action()
        token = _make_token()
        result = simulate_action(action, token, _risk_policy(), provider=None)
        assert result.simulation_passed is True
        assert result.estimated_gas_usd == 0.01

    def test_dry_run_has_warning(self) -> None:
        action = _make_action()
        token = _make_token()
        result = simulate_action(action, token, _risk_policy(), provider=None)
        assert any("dry-run" in w for w in result.warnings)


# ============================================================================
# Tests: serialization
# ============================================================================


class TestSerialization:
    """simulation_result_to_dict produces valid dict."""

    def test_to_dict(self) -> None:
        action = _make_action()
        token = _make_token()
        result = simulate_action(action, token, _risk_policy(), provider=_FakeProviderPass())
        d = simulation_result_to_dict(result)
        assert isinstance(d, dict)
        assert d["simulation_passed"] is True
        assert d["action_id"] == "act_sim_001"
        assert isinstance(d["warnings"], list)
        assert isinstance(d["failure_reasons"], list)


# ============================================================================
# Tests: no network calls, no signing
# ============================================================================


class TestNoNetworkNoSigning:
    """No real network calls or signing imports."""

    def test_no_network_calls(self) -> None:
        import socket
        from unittest.mock import patch

        action = _make_action()
        token = _make_token()
        with patch.object(
            socket,
            "create_connection",
            side_effect=AssertionError("network call detected"),
        ):
            result = simulate_action(action, token, _risk_policy(), provider=_FakeProviderPass())
            assert result.simulation_passed is True

    def test_no_signing_imports(self) -> None:
        import defi_autonomy.tx_simulator as mod

        with open(mod.__file__, "r") as f:
            source = f.read()
        forbidden = (
            "eth_account",
            "solders",
            "nacl",
            "cryptography.hazmat",
            "private_key",
            "seed_phrase",
            "mnemonic",
            "WalletExecutor",
            "sign_transaction",
            "send_transaction",
        )
        for term in forbidden:
            assert term not in source, f"forbidden term {term!r} found in module"

# ============================================================================
# Tests: tx_bytes parameter — Phase 9A.1
# ============================================================================


class TestTxBytesParameter:
    """SimulationProvider.simulate accepts optional tx_bytes parameter."""

    def test_simulate_with_tx_bytes_none(self) -> None:
        """Existing dry-run path unchanged when tx_bytes=None."""
        action = _make_action()
        token = _make_token()
        result = simulate_action(action, token, _risk_policy(), provider=None, tx_bytes=None)
        assert result.simulation_passed is True
        assert "dry-run simulation" in result.warnings[0]

    def test_simulate_provider_receives_tx_bytes(self) -> None:
        """Provider's simulate is called with tx_bytes when provided."""
        received_tx_bytes = []

        class CapturingProvider:
            def simulate(self, action, tx_bytes=None):
                received_tx_bytes.append(tx_bytes)
                return {"gas_usd": 0.01, "simulation_passed": True}

        action = _make_action()
        token = _make_token()
        fake_bytes = b"\xde\xad\xbe\xef" * 16
        result = simulate_action(
            action, token, _risk_policy(),
            provider=CapturingProvider(),
            tx_bytes=fake_bytes,
        )
        assert result.simulation_passed is True
        assert len(received_tx_bytes) == 1
        assert received_tx_bytes[0] == fake_bytes

    def test_simulate_provider_without_tx_bytes(self) -> None:
        """Provider works when tx_bytes is not passed (backward compat)."""
        action = _make_action()
        token = _make_token()
        result = simulate_action(
            action, token, _risk_policy(),
            provider=_FakeProviderPass(),
        )
        assert result.simulation_passed is True
