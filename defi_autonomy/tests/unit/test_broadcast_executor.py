"""Unit tests for defi_autonomy.broadcast_executor — Phase 3.2B.

All tests use FakeBroadcastProvider. No real RPC calls. No private-key leakage.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from defi_autonomy.broadcast_executor import (
    BroadcastNotAllowed,
    BroadcastPolicyBlocked,
    BroadcastReceipt,
    DailySpendCapExceeded,
    OperatorApprovalExpired,
    OperatorApprovalMismatch,
    OperatorApprovalMissing,
    OperatorApprovalRecord,
    append_operator_approval,
    broadcast_envelope,
    broadcast_receipt_to_dict,
    find_valid_approval,
    load_operator_approvals,
    validate_operator_approval,
    write_broadcast_ledger,
)
from defi_autonomy.wallet_executor import SignedTransactionEnvelope


# ============================================================================
# Fixtures
# ============================================================================


def _future(seconds: int = 300) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _past(seconds: int = 60) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _envelope(**overrides) -> SignedTransactionEnvelope:
    defaults = {
        "envelope_id": "env_001",
        "action_id": "act_001",
        "approval_id": "appr_001",
        "simulation_id": "sim_001",
        "candidate_hash": "c" * 64,
        "chain": "Base",
        "protocol": "aave-v3",
        "action_type": "FARM",
        "wallet_address": "0x" + "ab" * 20,
        "tx_hash_preview": "0x" + "ff" * 32,
        "signed_payload_hash": "s" * 64,
        "broadcast_allowed": True,
        "broadcasted": False,
        "warnings": (),
        "created_at_utc": "2026-05-27T00:00:00Z",
    }
    defaults.update(overrides)
    return SignedTransactionEnvelope(**defaults)


def _approval(**overrides) -> dict:
    defaults = {
        "approval_record_id": "appr_rec_001",
        "action_id": "act_001",
        "envelope_id": "env_001",
        "simulation_id": "sim_001",
        "candidate_hash": "c" * 64,
        "signed_payload_hash": "s" * 64,
        "approved_by_chat_id": "123",
        "approved_by_user_id": None,
        "approved_at_utc": _now(),
        "expires_at_utc": _future(300),
        "approval_message": "Approved",
    }
    defaults.update(overrides)
    return defaults


def _policy(**overrides) -> dict:
    base = {
        "autonomy_level": 2,
        "max_daily_spend_usd": 50,
        "max_tx_usd": 25,
        "kill_switch_file": None,
        "allow_autonomous_broadcast": False,
    }
    base.update(overrides)
    return base


def _setup_base(tmp_path: Path) -> Path:
    (tmp_path / "data").mkdir(exist_ok=True)
    return tmp_path


class FakeBroadcastProvider:
    """Fake provider that always succeeds."""
    def submit(self, envelope):
        return {"tx_hash": "0x" + "ab" * 32, "status": "SUBMITTED"}


class FakeFailProvider:
    """Fake provider that always fails."""
    def submit(self, envelope):
        raise RuntimeError("RPC connection refused")


# ============================================================================
# Tests: valid Level 2 broadcast
# ============================================================================


class TestValidBroadcast:
    """Valid Level 2 broadcast with approval succeeds."""

    def test_succeeds_with_approval(self, tmp_path: Path) -> None:
        base = _setup_base(tmp_path)
        env = _envelope()
        appr = _approval()
        receipt = broadcast_envelope(env, _policy(), base, FakeBroadcastProvider(), appr)
        assert isinstance(receipt, BroadcastReceipt)
        assert receipt.status == "SUBMITTED"
        assert receipt.tx_hash == "0x" + "ab" * 32

    def test_receipt_has_envelope_id(self, tmp_path: Path) -> None:
        base = _setup_base(tmp_path)
        receipt = broadcast_envelope(_envelope(), _policy(), base, FakeBroadcastProvider(), _approval())
        assert receipt.envelope_id == "env_001"


# ============================================================================
# Tests: approval validation
# ============================================================================


class TestApprovalValidation:
    """Approval validation catches invalid/missing/expired approvals."""

    def test_missing_approval_rejected(self, tmp_path: Path) -> None:
        base = _setup_base(tmp_path)
        with pytest.raises(OperatorApprovalMissing):
            broadcast_envelope(_envelope(), _policy(), base, FakeBroadcastProvider(), None)

    def test_expired_approval_rejected(self, tmp_path: Path) -> None:
        base = _setup_base(tmp_path)
        appr = _approval(expires_at_utc=_past(60))
        with pytest.raises(OperatorApprovalExpired):
            broadcast_envelope(_envelope(), _policy(), base, FakeBroadcastProvider(), appr)

    def test_mismatched_action_id_rejected(self, tmp_path: Path) -> None:
        base = _setup_base(tmp_path)
        appr = _approval(action_id="different")
        with pytest.raises(OperatorApprovalMismatch):
            broadcast_envelope(_envelope(), _policy(), base, FakeBroadcastProvider(), appr)

    def test_mismatched_envelope_id_rejected(self, tmp_path: Path) -> None:
        base = _setup_base(tmp_path)
        appr = _approval(envelope_id="different")
        with pytest.raises(OperatorApprovalMismatch):
            broadcast_envelope(_envelope(), _policy(), base, FakeBroadcastProvider(), appr)

    def test_mismatched_simulation_id_rejected(self, tmp_path: Path) -> None:
        base = _setup_base(tmp_path)
        appr = _approval(simulation_id="different")
        with pytest.raises(OperatorApprovalMismatch):
            broadcast_envelope(_envelope(), _policy(), base, FakeBroadcastProvider(), appr)

    def test_mismatched_candidate_hash_rejected(self, tmp_path: Path) -> None:
        base = _setup_base(tmp_path)
        appr = _approval(candidate_hash="d" * 64)
        with pytest.raises(OperatorApprovalMismatch):
            broadcast_envelope(_envelope(), _policy(), base, FakeBroadcastProvider(), appr)

    def test_mismatched_signed_payload_hash_rejected(self, tmp_path: Path) -> None:
        base = _setup_base(tmp_path)
        appr = _approval(signed_payload_hash="x" * 64)
        with pytest.raises(OperatorApprovalMismatch, match="signed_payload_hash"):
            broadcast_envelope(_envelope(), _policy(), base, FakeBroadcastProvider(), appr)

    def test_changed_payload_invalidates_approval(self, tmp_path: Path) -> None:
        base = _setup_base(tmp_path)
        env = _envelope(signed_payload_hash="new_hash_" + "a" * 55)
        appr = _approval(signed_payload_hash="s" * 64)  # old hash
        with pytest.raises(OperatorApprovalMismatch):
            broadcast_envelope(env, _policy(), base, FakeBroadcastProvider(), appr)


# ============================================================================
# Tests: policy blocks
# ============================================================================


class TestPolicyBlocks:
    """Kill switch, macro gate, daily cap block broadcast."""

    def test_kill_switch_blocks(self, tmp_path: Path) -> None:
        base = _setup_base(tmp_path)
        ks = tmp_path / "STOP"
        ks.write_text("STOP")
        policy = _policy(kill_switch_file=str(ks))
        with pytest.raises(BroadcastPolicyBlocked):
            broadcast_envelope(_envelope(), policy, base, FakeBroadcastProvider(), _approval())

    def test_macro_halt_blocks(self, tmp_path: Path) -> None:
        base = _setup_base(tmp_path)
        (base / "data" / "macro_state.json").write_text(json.dumps({"state": "HALT"}))
        with pytest.raises(BroadcastPolicyBlocked):
            broadcast_envelope(_envelope(), _policy(), base, FakeBroadcastProvider(), _approval())

    def test_daily_spend_cap_blocks(self, tmp_path: Path) -> None:
        base = _setup_base(tmp_path)
        # Write existing spend that exceeds cap
        today = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        ledger = base / "data" / "broadcast_ledger.jsonl"
        record = {"status": "SUBMITTED", "submitted_at_utc": today, "estimated_tx_usd": 45}
        ledger.write_text(json.dumps(record) + "\n")
        policy = _policy(max_daily_spend_usd=50, max_tx_usd=25)
        with pytest.raises(DailySpendCapExceeded):
            broadcast_envelope(_envelope(), policy, base, FakeBroadcastProvider(), _approval())

    def test_broadcast_allowed_false_rejected(self, tmp_path: Path) -> None:
        base = _setup_base(tmp_path)
        env = _envelope(broadcast_allowed=False)
        with pytest.raises(BroadcastNotAllowed):
            broadcast_envelope(env, _policy(), base, FakeBroadcastProvider(), _approval())

    def test_already_broadcasted_rejected(self, tmp_path: Path) -> None:
        base = _setup_base(tmp_path)
        env = _envelope(broadcasted=True)
        with pytest.raises(BroadcastNotAllowed):
            broadcast_envelope(env, _policy(), base, FakeBroadcastProvider(), _approval())

    def test_autonomy_level_1_rejected(self, tmp_path: Path) -> None:
        base = _setup_base(tmp_path)
        with pytest.raises(BroadcastNotAllowed):
            broadcast_envelope(_envelope(), _policy(autonomy_level=1), base, FakeBroadcastProvider(), _approval())


# ============================================================================
# Tests: provider failure
# ============================================================================


class TestProviderFailure:
    """Provider failure returns failed BroadcastReceipt."""

    def test_provider_failure_returns_failed_receipt(self, tmp_path: Path) -> None:
        base = _setup_base(tmp_path)
        receipt = broadcast_envelope(_envelope(), _policy(), base, FakeFailProvider(), _approval())
        assert receipt.status == "FAILED"
        assert receipt.error_type == "RuntimeError"

    def test_failed_receipt_has_error_message(self, tmp_path: Path) -> None:
        base = _setup_base(tmp_path)
        receipt = broadcast_envelope(_envelope(), _policy(), base, FakeFailProvider(), _approval())
        assert "connection refused" in receipt.error_message.lower()


# ============================================================================
# Tests: ledger
# ============================================================================


class TestBroadcastLedger:
    """Broadcast ledger appended correctly."""

    def test_ledger_appended(self, tmp_path: Path) -> None:
        base = _setup_base(tmp_path)
        receipt = broadcast_envelope(_envelope(), _policy(), base, FakeBroadcastProvider(), _approval())
        ledger_path = base / "data" / "broadcast_ledger.jsonl"
        write_broadcast_ledger(ledger_path, receipt, estimated_tx_usd=5.0)
        assert ledger_path.exists()
        lines = ledger_path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["event_type"] == "BROADCAST_SUBMITTED"
        assert record["envelope_id"] == "env_001"
        assert record["estimated_tx_usd"] == 5.0


# ============================================================================
# Tests: Level 3 config-gated
# ============================================================================


class TestLevel3Deferred:
    """Level 3 autonomous broadcast is config-gated."""

    def test_level_3_without_config_requires_approval(self, tmp_path: Path) -> None:
        base = _setup_base(tmp_path)
        policy = _policy(autonomy_level=3, allow_autonomous_broadcast=False)
        with pytest.raises(OperatorApprovalMissing):
            broadcast_envelope(_envelope(), policy, base, FakeBroadcastProvider(), None)

    def test_level_3_with_config_skips_approval(self, tmp_path: Path) -> None:
        base = _setup_base(tmp_path)
        policy = _policy(autonomy_level=3, allow_autonomous_broadcast=True)
        receipt = broadcast_envelope(_envelope(), policy, base, FakeBroadcastProvider(), None)
        assert receipt.status == "SUBMITTED"


# ============================================================================
# Tests: no network, no key leakage, no mutation
# ============================================================================


class TestSafety:
    """No real RPC, no key leakage, no policy mutation."""

    def test_no_network_calls(self, tmp_path: Path) -> None:
        import socket
        from unittest.mock import patch

        base = _setup_base(tmp_path)
        with patch.object(socket, "create_connection",
                          side_effect=AssertionError("network call")):
            receipt = broadcast_envelope(_envelope(), _policy(), base, FakeBroadcastProvider(), _approval())
            assert receipt.status == "SUBMITTED"

    def test_no_key_leakage_in_receipt(self, tmp_path: Path) -> None:
        base = _setup_base(tmp_path)
        receipt = broadcast_envelope(_envelope(), _policy(), base, FakeBroadcastProvider(), _approval())
        d = broadcast_receipt_to_dict(receipt)
        text = json.dumps(d)
        assert "private_key" not in text.lower()
        assert "mnemonic" not in text.lower()

    def test_no_signing_imports(self) -> None:
        import defi_autonomy.broadcast_executor as mod
        with open(mod.__file__, "r") as f:
            source = f.read()
        forbidden = ("from eth_account", "import eth_account", "from solders", "import solders")
        for term in forbidden:
            assert term not in source

    def test_no_daemon_modification(self) -> None:
        import defi_autonomy.broadcast_executor as mod
        with open(mod.__file__, "r") as f:
            source = f.read()
        assert "ecosystem.defi.cjs" not in source
        assert "pm2" not in source.lower()
