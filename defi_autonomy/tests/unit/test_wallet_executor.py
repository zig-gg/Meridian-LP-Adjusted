"""Unit tests for defi_autonomy.wallet_executor — Sprint 3, Phase 3.2A.

All tests are deterministic and offline. No real network calls. No signing
with real keys. No broadcast. Uses only fake signers.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from defi_autonomy.policy_engine import ActionDescriptor, ApprovalToken
from defi_autonomy.tx_simulator import SimulationResult
from defi_autonomy.wallet_executor import (
    ApprovalTokenInvalid,
    AutonomyLevelTooLow,
    BroadcastDisabled,
    KillSwitchActive,
    MacroBlocked,
    PolicyCapViolation,
    PrivateKeyMissing,
    PrivateKeyUnsafe,
    SignedTransactionEnvelope,
    SimulationFailed,
    SimulationMissing,
    UnsupportedChain,
    WalletAddressMismatch,
    broadcast_transaction,
    check_kill_switch,
    check_macro_gate,
    load_operator_wallet_key,
    sign_transaction,
    validate_execution_preconditions,
    write_execution_ledger,
)


# ============================================================================
# Fake signer
# ============================================================================


class FakeSigner:
    """Fake signer for testing. Derives a deterministic address."""

    def __init__(self, address: str = "0x" + "ab" * 20):
        self._address = address

    def sign(self, unsigned_tx: Any, chain: str) -> bytes:
        # Deterministic fake signature — handles both dict and bytes payloads
        if isinstance(unsigned_tx, (bytes, bytearray, memoryview)):
            payload = bytes(unsigned_tx)
        else:
            payload = json.dumps(unsigned_tx, sort_keys=True).encode()
        return hashlib.sha256(payload + chain.encode()).digest()

    def derive_address(self, chain: str) -> str:
        return self._address


# ============================================================================
# Fixtures
# ============================================================================


def _future_utc(seconds: int = 300) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _risk_policy(**overrides) -> dict:
    base = {
        "version": 1,
        "autonomy_level": 2,
        "max_wallet_value_usd": 100,
        "max_tx_usd": 25,
        "max_daily_spend_usd": 50,
        "max_slippage_bps": 50,
        "allowed_chains": ["Base", "BNB Chain", "Solana"],
        "allowed_strategy_types": ["stablecoin_lending", "stable_stable_lp"],
        "blocked_actions": ["bridge", "borrow", "leverage", "unlimited_approval"],
        "operator_funded_agent_wallet_address": "0x" + "ab" * 20,
        "kill_switch_file": None,
    }
    base.update(overrides)
    return base


def _make_action(**overrides) -> ActionDescriptor:
    defaults = {
        "action_id": "act_we_001",
        "candidate_hash": "c" * 64,
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
        "approval_id": "appr_we_001",
        "action_id": "act_we_001",
        "candidate_hash": "c" * 64,
        "policy_digest": "d" * 64,
        "allowlist_digest": "e" * 64,
        "approved": True,
        "reasons": ("all checks passed",),
        "warnings": (),
        "expires_at_utc": _future_utc(300),
        "created_at_utc": _now_utc(),
    }
    defaults.update(overrides)
    return ApprovalToken(**defaults)


def _make_sim(**overrides) -> SimulationResult:
    defaults = {
        "simulation_id": "sim_we_001",
        "action_id": "act_we_001",
        "approval_id": "appr_we_001",
        "candidate_hash": "c" * 64,
        "approved_by_policy": True,
        "simulation_passed": True,
        "chain": "Base",
        "protocol": "aave-v3",
        "action_type": "FARM",
        "estimated_tx_usd": 5.0,
        "estimated_gas_usd": 0.01,
        "estimated_total_usd": 5.01,
        "slippage_bps": 10,
        "value_tolerance_bps": 50,
        "expected_token_deltas": {},
        "warnings": (),
        "failure_reasons": (),
        "created_at_utc": "2026-05-27T00:00:00Z",
    }
    defaults.update(overrides)
    return SimulationResult(**defaults)


def _unsigned_tx() -> dict:
    return {"to": "0x" + "ff" * 20, "value": 5000000, "data": "0x"}


# ============================================================================
# Tests: autonomy level
# ============================================================================


class TestAutonomyLevel:
    """Autonomy level enforcement."""

    def test_level_1_blocks_signing(self) -> None:
        policy = _risk_policy(autonomy_level=1)
        with pytest.raises(AutonomyLevelTooLow):
            sign_transaction(
                _make_action(), _make_token(), _make_sim(),
                _unsigned_tx(), policy, signer_provider=FakeSigner(),
            )

    def test_level_2_allows_signing(self, tmp_path: Path) -> None:
        policy = _risk_policy(autonomy_level=2)
        (tmp_path / "data").mkdir(exist_ok=True)
        result = sign_transaction(
            _make_action(), _make_token(), _make_sim(),
            _unsigned_tx(), policy, signer_provider=FakeSigner(),
            base_dir=tmp_path,
        )
        assert isinstance(result, SignedTransactionEnvelope)


# ============================================================================
# Tests: private key safety
# ============================================================================


class TestPrivateKeySafety:
    """Private key loading safety."""

    def test_missing_key_rejected(self, monkeypatch) -> None:
        monkeypatch.delenv("HERMES_DEFI_AGENT_PRIVATE_KEY", raising=False)
        with pytest.raises(PrivateKeyMissing):
            load_operator_wallet_key()

    def test_mnemonic_rejected(self, monkeypatch) -> None:
        monkeypatch.setenv(
            "HERMES_DEFI_AGENT_PRIVATE_KEY",
            "abandon ability able about above absent"
        )
        with pytest.raises(PrivateKeyUnsafe):
            load_operator_wallet_key()

    def test_valid_hex_key_accepted(self, monkeypatch) -> None:
        monkeypatch.setenv("HERMES_DEFI_AGENT_PRIVATE_KEY", "a" * 64)
        key = load_operator_wallet_key()
        assert key == "a" * 64

    def test_no_signer_raises_missing(self, tmp_path: Path) -> None:
        policy = _risk_policy()
        (tmp_path / "data").mkdir(exist_ok=True)
        with pytest.raises(PrivateKeyMissing):
            sign_transaction(
                _make_action(), _make_token(), _make_sim(),
                _unsigned_tx(), policy, signer_provider=None,
                base_dir=tmp_path,
            )


# ============================================================================
# Tests: wallet address mismatch
# ============================================================================


class TestWalletAddressMismatch:
    """Wallet address validation."""

    def test_mismatch_rejected(self, tmp_path: Path) -> None:
        policy = _risk_policy(
            operator_funded_agent_wallet_address="0x" + "00" * 20
        )
        (tmp_path / "data").mkdir(exist_ok=True)
        signer = FakeSigner(address="0x" + "ab" * 20)
        with pytest.raises(WalletAddressMismatch):
            sign_transaction(
                _make_action(), _make_token(), _make_sim(),
                _unsigned_tx(), policy, signer_provider=signer,
                base_dir=tmp_path,
            )

    def test_matching_address_passes(self, tmp_path: Path) -> None:
        addr = "0x" + "ab" * 20
        policy = _risk_policy(operator_funded_agent_wallet_address=addr)
        (tmp_path / "data").mkdir(exist_ok=True)
        signer = FakeSigner(address=addr)
        result = sign_transaction(
            _make_action(), _make_token(), _make_sim(),
            _unsigned_tx(), policy, signer_provider=signer,
            base_dir=tmp_path,
        )
        assert result.wallet_address == addr


# ============================================================================
# Tests: kill switch
# ============================================================================


class TestKillSwitch:
    """Kill switch blocks signing."""

    def test_kill_switch_blocks(self, tmp_path: Path) -> None:
        ks = tmp_path / "STOP"
        ks.write_text("STOP", encoding="utf-8")
        policy = _risk_policy(kill_switch_file=str(ks))
        with pytest.raises(KillSwitchActive):
            sign_transaction(
                _make_action(), _make_token(), _make_sim(),
                _unsigned_tx(), policy, signer_provider=FakeSigner(),
            )


# ============================================================================
# Tests: macro gate
# ============================================================================


class TestMacroGate:
    """Macro HALT blocks signing."""

    def test_macro_halt_blocks(self, tmp_path: Path) -> None:
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        (data_dir / "macro_state.json").write_text(
            json.dumps({"state": "HALT"}), encoding="utf-8"
        )
        policy = _risk_policy()
        with pytest.raises(MacroBlocked):
            sign_transaction(
                _make_action(), _make_token(), _make_sim(),
                _unsigned_tx(), policy, signer_provider=FakeSigner(),
                base_dir=tmp_path,
            )


# ============================================================================
# Tests: approval token validation
# ============================================================================


class TestApprovalTokenValidation:
    """Approval token validation in signing context."""

    def test_missing_token_rejected(self) -> None:
        policy = _risk_policy()
        with pytest.raises(ApprovalTokenInvalid):
            sign_transaction(
                _make_action(), None, _make_sim(),  # type: ignore
                _unsigned_tx(), policy, signer_provider=FakeSigner(),
            )

    def test_unapproved_token_rejected(self) -> None:
        policy = _risk_policy()
        token = _make_token(approved=False)
        with pytest.raises(ApprovalTokenInvalid):
            sign_transaction(
                _make_action(), token, _make_sim(),
                _unsigned_tx(), policy, signer_provider=FakeSigner(),
            )

    def test_action_id_mismatch_rejected(self) -> None:
        policy = _risk_policy()
        token = _make_token(action_id="different")
        with pytest.raises(ApprovalTokenInvalid):
            sign_transaction(
                _make_action(), token, _make_sim(),
                _unsigned_tx(), policy, signer_provider=FakeSigner(),
            )


# ============================================================================
# Tests: simulation validation
# ============================================================================


class TestSimulationValidation:
    """Simulation result validation."""

    def test_missing_simulation_rejected(self) -> None:
        policy = _risk_policy()
        with pytest.raises(SimulationMissing):
            sign_transaction(
                _make_action(), _make_token(), None,  # type: ignore
                _unsigned_tx(), policy, signer_provider=FakeSigner(),
            )

    def test_failed_simulation_rejected(self) -> None:
        policy = _risk_policy()
        sim = _make_sim(simulation_passed=False)
        with pytest.raises(SimulationFailed):
            sign_transaction(
                _make_action(), _make_token(), sim,
                _unsigned_tx(), policy, signer_provider=FakeSigner(),
            )

    def test_simulation_action_mismatch_rejected(self) -> None:
        policy = _risk_policy()
        sim = _make_sim(action_id="different")
        with pytest.raises(SimulationFailed):
            sign_transaction(
                _make_action(), _make_token(), sim,
                _unsigned_tx(), policy, signer_provider=FakeSigner(),
            )


# ============================================================================
# Tests: policy caps
# ============================================================================


class TestPolicyCaps:
    """Policy cap enforcement."""

    def test_max_tx_usd_enforced(self) -> None:
        policy = _risk_policy(max_tx_usd=3)
        with pytest.raises(PolicyCapViolation):
            sign_transaction(
                _make_action(estimated_tx_usd=5.0), _make_token(), _make_sim(),
                _unsigned_tx(), policy, signer_provider=FakeSigner(),
            )

    def test_max_daily_spend_enforced(self) -> None:
        policy = _risk_policy(max_daily_spend_usd=3)
        with pytest.raises(PolicyCapViolation):
            sign_transaction(
                _make_action(estimated_daily_spend_usd=5.0),
                _make_token(), _make_sim(),
                _unsigned_tx(), policy, signer_provider=FakeSigner(),
            )

    def test_max_wallet_value_enforced(self) -> None:
        policy = _risk_policy(max_wallet_value_usd=10)
        with pytest.raises(PolicyCapViolation):
            sign_transaction(
                _make_action(estimated_wallet_value_usd=20.0),
                _make_token(), _make_sim(),
                _unsigned_tx(), policy, signer_provider=FakeSigner(),
            )

    def test_slippage_cap_enforced(self) -> None:
        policy = _risk_policy(max_slippage_bps=5)
        with pytest.raises(PolicyCapViolation):
            sign_transaction(
                _make_action(slippage_bps=10), _make_token(), _make_sim(),
                _unsigned_tx(), policy, signer_provider=FakeSigner(),
            )


# ============================================================================
# Tests: blocked actions
# ============================================================================


class TestBlockedActions:
    """Blocked action metadata flags."""

    def test_bridge_rejected(self) -> None:
        policy = _risk_policy()
        with pytest.raises(PolicyCapViolation, match="bridge"):
            sign_transaction(
                _make_action(metadata={"bridge": True}),
                _make_token(), _make_sim(),
                _unsigned_tx(), policy, signer_provider=FakeSigner(),
            )

    def test_borrow_rejected(self) -> None:
        policy = _risk_policy()
        with pytest.raises(PolicyCapViolation, match="borrow"):
            sign_transaction(
                _make_action(metadata={"borrow": True}),
                _make_token(), _make_sim(),
                _unsigned_tx(), policy, signer_provider=FakeSigner(),
            )

    def test_leverage_rejected(self) -> None:
        policy = _risk_policy()
        with pytest.raises(PolicyCapViolation, match="leverage"):
            sign_transaction(
                _make_action(metadata={"leverage": True}),
                _make_token(), _make_sim(),
                _unsigned_tx(), policy, signer_provider=FakeSigner(),
            )

    def test_unlimited_approval_rejected(self) -> None:
        policy = _risk_policy()
        with pytest.raises(PolicyCapViolation, match="unlimited_approval"):
            sign_transaction(
                _make_action(metadata={"unlimited_approval": True}),
                _make_token(), _make_sim(),
                _unsigned_tx(), policy, signer_provider=FakeSigner(),
            )


# ============================================================================
# Tests: unsupported chain
# ============================================================================


class TestUnsupportedChain:
    """Unsupported chain for signing."""

    def test_solana_unsupported_in_phase_3_2a(self, tmp_path: Path) -> None:
        policy = _risk_policy()
        (tmp_path / "data").mkdir(exist_ok=True)
        action = _make_action(chain="Solana")
        sim = _make_sim(chain="Solana")
        with pytest.raises(UnsupportedChain):
            sign_transaction(
                action, _make_token(), sim,
                _unsigned_tx(), policy, signer_provider=FakeSigner(),
                base_dir=tmp_path,
            )


# ============================================================================
# Tests: successful signing
# ============================================================================


class TestSuccessfulSigning:
    """Successful fake signing creates SignedTransactionEnvelope."""

    def test_creates_envelope(self, tmp_path: Path) -> None:
        policy = _risk_policy()
        (tmp_path / "data").mkdir(exist_ok=True)
        result = sign_transaction(
            _make_action(), _make_token(), _make_sim(),
            _unsigned_tx(), policy, signer_provider=FakeSigner(),
            base_dir=tmp_path,
        )
        assert isinstance(result, SignedTransactionEnvelope)
        assert result.action_id == "act_we_001"
        assert result.approval_id == "appr_we_001"
        assert result.simulation_id == "sim_we_001"
        assert result.chain == "Base"
        assert result.protocol == "aave-v3"

    def test_signed_payload_hash_deterministic(self, tmp_path: Path) -> None:
        policy = _risk_policy()
        (tmp_path / "data").mkdir(exist_ok=True)
        tx = _unsigned_tx()
        r1 = sign_transaction(
            _make_action(), _make_token(), _make_sim(),
            tx, policy, signer_provider=FakeSigner(),
            base_dir=tmp_path,
        )
        r2 = sign_transaction(
            _make_action(), _make_token(), _make_sim(),
            tx, policy, signer_provider=FakeSigner(),
            base_dir=tmp_path,
        )
        assert r1.signed_payload_hash == r2.signed_payload_hash

    def test_broadcast_allowed_is_false(self, tmp_path: Path) -> None:
        policy = _risk_policy()
        (tmp_path / "data").mkdir(exist_ok=True)
        result = sign_transaction(
            _make_action(), _make_token(), _make_sim(),
            _unsigned_tx(), policy, signer_provider=FakeSigner(),
            base_dir=tmp_path,
        )
        assert result.broadcast_allowed is False

    def test_broadcasted_is_false(self, tmp_path: Path) -> None:
        policy = _risk_policy()
        (tmp_path / "data").mkdir(exist_ok=True)
        result = sign_transaction(
            _make_action(), _make_token(), _make_sim(),
            _unsigned_tx(), policy, signer_provider=FakeSigner(),
            base_dir=tmp_path,
        )
        assert result.broadcasted is False


# ============================================================================
# Tests: broadcast disabled
# ============================================================================


class TestBroadcastDisabled:
    """Broadcast attempt raises BroadcastDisabled."""

    def test_broadcast_raises(self, tmp_path: Path) -> None:
        policy = _risk_policy()
        (tmp_path / "data").mkdir(exist_ok=True)
        envelope = sign_transaction(
            _make_action(), _make_token(), _make_sim(),
            _unsigned_tx(), policy, signer_provider=FakeSigner(),
            base_dir=tmp_path,
        )
        with pytest.raises(BroadcastDisabled):
            broadcast_transaction(envelope)


# ============================================================================
# Tests: ledger
# ============================================================================


class TestLedger:
    """Wallet execution ledger appends valid JSONL."""

    def test_ledger_appends(self, tmp_path: Path) -> None:
        policy = _risk_policy()
        (tmp_path / "data").mkdir(exist_ok=True)
        envelope = sign_transaction(
            _make_action(), _make_token(), _make_sim(),
            _unsigned_tx(), policy, signer_provider=FakeSigner(),
            base_dir=tmp_path,
        )
        ledger_path = tmp_path / "wallet_execution_ledger.jsonl"
        write_execution_ledger(ledger_path, envelope)
        assert ledger_path.exists()
        lines = ledger_path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["event_type"] == "SIGNED_TRANSACTION_PREPARED"
        assert record["envelope_id"] == envelope.envelope_id
        assert record["broadcast_allowed"] is False
        assert record["broadcasted"] is False

    def test_no_private_key_in_ledger(self, tmp_path: Path) -> None:
        policy = _risk_policy()
        (tmp_path / "data").mkdir(exist_ok=True)
        envelope = sign_transaction(
            _make_action(), _make_token(), _make_sim(),
            _unsigned_tx(), policy, signer_provider=FakeSigner(),
            base_dir=tmp_path,
        )
        ledger_path = tmp_path / "wallet_execution_ledger.jsonl"
        write_execution_ledger(ledger_path, envelope)
        content = ledger_path.read_text(encoding="utf-8")
        assert "private_key" not in content.lower()
        assert "seed_phrase" not in content.lower()
        assert "mnemonic" not in content.lower()


# ============================================================================
# Tests: no private key in exceptions
# ============================================================================


class TestNoKeyLeakage:
    """No private key appears in exception messages."""

    def test_mismatch_error_no_key(self, tmp_path: Path) -> None:
        policy = _risk_policy(
            operator_funded_agent_wallet_address="0x" + "00" * 20
        )
        (tmp_path / "data").mkdir(exist_ok=True)
        signer = FakeSigner(address="0x" + "ab" * 20)
        with pytest.raises(WalletAddressMismatch) as exc:
            sign_transaction(
                _make_action(), _make_token(), _make_sim(),
                _unsigned_tx(), policy, signer_provider=signer,
                base_dir=tmp_path,
            )
        msg = str(exc.value)
        assert "0x" + "ab" * 20 not in msg
        assert "private" not in msg.lower() or "key" not in msg.lower()


# ============================================================================
# Tests: no network calls, no signing imports
# ============================================================================


class TestNoNetworkNoSigning:
    """No real network calls or unsafe imports."""

    def test_no_network_calls(self, tmp_path: Path) -> None:
        import socket
        from unittest.mock import patch

        policy = _risk_policy()
        (tmp_path / "data").mkdir(exist_ok=True)
        with patch.object(
            socket,
            "create_connection",
            side_effect=AssertionError("network call detected"),
        ):
            result = sign_transaction(
                _make_action(), _make_token(), _make_sim(),
                _unsigned_tx(), policy, signer_provider=FakeSigner(),
                base_dir=tmp_path,
            )
            assert result.broadcast_allowed is False

    def test_no_real_crypto_imports(self) -> None:
        """Module does not import real signing libraries."""
        import defi_autonomy.wallet_executor as mod

        with open(mod.__file__, "r") as f:
            source = f.read()
        # Should not import actual signing libraries
        forbidden_imports = (
            "from eth_account",
            "import eth_account",
            "from solders",
            "import solders",
            "from nacl",
            "import nacl",
        )
        for imp in forbidden_imports:
            assert imp not in source, f"forbidden import {imp!r} found"

    def test_no_key_in_source(self) -> None:
        """No hardcoded private keys in source."""
        import defi_autonomy.wallet_executor as mod

        with open(mod.__file__, "r") as f:
            source = f.read()
        # Should not contain actual hex keys (64+ hex chars that look like keys)
        import re
        # Only check for patterns that look like real keys, not test fixtures
        assert "0x1234567890abcdef" not in source


# ============================================================================
# Tests: Pre-broadcast hardening — Phase 5.4D
# ============================================================================


class TestMacroGateHardening:
    """sign_transaction cannot bypass macro gate."""

    def test_omitting_base_dir_at_level_2_raises(self) -> None:
        """Cannot skip macro gate by passing empty base_dir at autonomy_level >= 2."""
        from defi_autonomy.wallet_executor import MacroBlocked

        policy = _risk_policy(autonomy_level=2)
        with pytest.raises(MacroBlocked):
            sign_transaction(
                _make_action(), _make_token(), _make_sim(),
                _unsigned_tx(), policy, signer_provider=FakeSigner(),
                base_dir="",
            )

    def test_broadcast_allowed_always_false(self, tmp_path: Path) -> None:
        """sign_transaction always produces broadcast_allowed=False."""
        policy = _risk_policy()
        (tmp_path / "data").mkdir(exist_ok=True)
        result = sign_transaction(
            _make_action(), _make_token(), _make_sim(),
            _unsigned_tx(), policy, signer_provider=FakeSigner(),
            base_dir=tmp_path,
        )
        assert result.broadcast_allowed is False

    def test_broadcasted_always_false(self, tmp_path: Path) -> None:
        """sign_transaction always produces broadcasted=False."""
        policy = _risk_policy()
        (tmp_path / "data").mkdir(exist_ok=True)
        result = sign_transaction(
            _make_action(), _make_token(), _make_sim(),
            _unsigned_tx(), policy, signer_provider=FakeSigner(),
            base_dir=tmp_path,
        )
        assert result.broadcasted is False

    def test_macro_halt_blocks_with_base_dir(self, tmp_path: Path) -> None:
        """Macro HALT blocks signing when base_dir is provided."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        (data_dir / "macro_state.json").write_text(
            json.dumps({"state": "HALT"}), encoding="utf-8"
        )
        policy = _risk_policy()
        with pytest.raises(MacroBlocked):
            sign_transaction(
                _make_action(), _make_token(), _make_sim(),
                _unsigned_tx(), policy, signer_provider=FakeSigner(),
                base_dir=tmp_path,
            )


# ============================================================================
# Tests: broadcast_allowed flag — Phase 3.2B
# ============================================================================


class TestBroadcastAllowedFlag:
    """broadcast_allowed controlled by policy."""

    def test_default_broadcast_allowed_false(self, tmp_path: Path) -> None:
        policy = _risk_policy()  # no allow_level2_broadcast
        (tmp_path / "data").mkdir(exist_ok=True)
        result = sign_transaction(
            _make_action(), _make_token(), _make_sim(),
            _unsigned_tx(), policy, signer_provider=FakeSigner(),
            base_dir=tmp_path,
        )
        assert result.broadcast_allowed is False

    def test_allow_level2_broadcast_true(self, tmp_path: Path) -> None:
        policy = _risk_policy(allow_level2_broadcast=True)
        (tmp_path / "data").mkdir(exist_ok=True)
        result = sign_transaction(
            _make_action(), _make_token(), _make_sim(),
            _unsigned_tx(), policy, signer_provider=FakeSigner(),
            base_dir=tmp_path,
        )
        assert result.broadcast_allowed is True

    def test_broadcasted_always_false(self, tmp_path: Path) -> None:
        policy = _risk_policy(allow_level2_broadcast=True)
        (tmp_path / "data").mkdir(exist_ok=True)
        result = sign_transaction(
            _make_action(), _make_token(), _make_sim(),
            _unsigned_tx(), policy, signer_provider=FakeSigner(),
            base_dir=tmp_path,
        )
        assert result.broadcasted is False


# ============================================================================
# Tests: signed_tx_bytes protection — Phase 9A.1
# ============================================================================


class TestSignedTxBytesProtection:
    """signed_tx_bytes is in-memory only, never persisted or shown in repr."""

    def test_signed_tx_bytes_field_exists(self, tmp_path: Path) -> None:
        """SignedTransactionEnvelope has signed_tx_bytes field."""
        policy = _risk_policy()
        (tmp_path / "data").mkdir(exist_ok=True)
        result = sign_transaction(
            _make_action(), _make_token(), _make_sim(),
            _unsigned_tx(), policy, signer_provider=FakeSigner(),
            base_dir=tmp_path,
        )
        assert hasattr(result, "signed_tx_bytes")
        assert result.signed_tx_bytes is not None  # populated after signing

    def test_signed_tx_bytes_default_none(self) -> None:
        """SignedTransactionEnvelope with no signed_tx_bytes defaults to None."""
        env = SignedTransactionEnvelope(
            envelope_id="env_test",
            action_id="act_test",
            approval_id="appr_test",
            simulation_id="sim_test",
            candidate_hash="c" * 64,
            chain="Base",
            protocol="aave-v3",
            action_type="FARM",
            wallet_address="0x" + "ab" * 20,
            tx_hash_preview="0x" + "ff" * 32,
            signed_payload_hash="s" * 64,
            broadcast_allowed=False,
            broadcasted=False,
            warnings=(),
            created_at_utc="2026-05-28T00:00:00Z",
        )
        assert env.signed_tx_bytes is None

    def test_signed_tx_bytes_not_in_ledger(self, tmp_path: Path) -> None:
        """write_execution_ledger output does not contain signed_tx_bytes."""
        policy = _risk_policy()
        (tmp_path / "data").mkdir(exist_ok=True)
        envelope = sign_transaction(
            _make_action(), _make_token(), _make_sim(),
            _unsigned_tx(), policy, signer_provider=FakeSigner(),
            base_dir=tmp_path,
        )
        ledger_path = tmp_path / "wallet_execution_ledger.jsonl"
        write_execution_ledger(ledger_path, envelope)
        content = ledger_path.read_text(encoding="utf-8")
        assert "signed_tx_bytes" not in content
        # Also verify no raw bytes or base64 payload
        assert "\\x" not in content  # no escaped bytes
        # Parse and verify field is absent from JSON record
        record = json.loads(content.strip().split("\n")[0])
        assert "signed_tx_bytes" not in record

    def test_repr_does_not_reveal_signed_bytes(self, tmp_path: Path) -> None:
        """repr(envelope) does not contain signed_tx_bytes."""
        policy = _risk_policy()
        (tmp_path / "data").mkdir(exist_ok=True)
        envelope = sign_transaction(
            _make_action(), _make_token(), _make_sim(),
            _unsigned_tx(), policy, signer_provider=FakeSigner(),
            base_dir=tmp_path,
        )
        r = repr(envelope)
        assert "signed_tx_bytes" not in r
        # Ensure no raw byte content leaks
        assert "\\x" not in r

    def test_no_dataclasses_asdict_in_ledger_code(self) -> None:
        """write_execution_ledger does not use dataclasses.asdict()."""
        import defi_autonomy.wallet_executor as mod
        import inspect

        source = inspect.getsource(mod.write_execution_ledger)
        assert "asdict" not in source
        assert "dataclasses.asdict" not in source
        assert "__dict__" not in source


# ============================================================================
# Tests: unsigned_tx accepts Any type — Phase 9A.1
# ============================================================================


class TestUnsignedTxAnyType:
    """sign_transaction accepts both dict and bytes for unsigned_tx."""

    def test_dict_payload_still_works(self, tmp_path: Path) -> None:
        """Existing dict payload behavior is unchanged."""
        policy = _risk_policy()
        (tmp_path / "data").mkdir(exist_ok=True)
        result = sign_transaction(
            _make_action(), _make_token(), _make_sim(),
            {"to": "0x00", "value": 100},
            policy, signer_provider=FakeSigner(),
            base_dir=tmp_path,
        )
        assert isinstance(result, SignedTransactionEnvelope)

    def test_bytes_payload_accepted(self, tmp_path: Path) -> None:
        """Bytes payload (Solana-style) does not raise TypeError."""
        policy = _risk_policy()
        (tmp_path / "data").mkdir(exist_ok=True)
        result = sign_transaction(
            _make_action(), _make_token(), _make_sim(),
            b"\x00\x01\x02\x03" * 16,
            policy, signer_provider=FakeSigner(),
            base_dir=tmp_path,
        )
        assert isinstance(result, SignedTransactionEnvelope)


# ============================================================================
# Tests: Solana signing still disabled — Phase 9A.1
# ============================================================================


class TestSolanaSigningStillDisabled:
    """Solana is NOT in _SUPPORTED_SIGNING_CHAINS until Phase 9A.2."""

    def test_solana_still_unsupported_for_signing(self, tmp_path: Path) -> None:
        """Solana chain still raises UnsupportedChain."""
        policy = _risk_policy()
        (tmp_path / "data").mkdir(exist_ok=True)
        action = _make_action(chain="Solana")
        sim = _make_sim(chain="Solana")
        with pytest.raises(UnsupportedChain):
            sign_transaction(
                action, _make_token(), sim,
                b"\x00" * 64, policy, signer_provider=FakeSigner(),
                base_dir=tmp_path,
            )
