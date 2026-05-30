"""Unit tests for defi_autonomy.position_lifecycle — Phase 3.2C."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from defi_autonomy.position_lifecycle import (
    PositionCloseResult,
    PositionRecord,
    append_position,
    close_position,
    create_position_from_broadcast,
    get_open_positions,
    load_positions,
    position_close_result_to_dict,
    position_to_dict,
)
from defi_autonomy.wallet_executor import SignedTransactionEnvelope


def _envelope(**kw) -> SignedTransactionEnvelope:
    defaults = {
        "envelope_id": "env_001", "action_id": "act_001", "approval_id": "appr_001",
        "simulation_id": "sim_001", "candidate_hash": "c" * 64, "chain": "Base",
        "protocol": "aave-v3", "action_type": "FARM",
        "wallet_address": "0x" + "ab" * 20, "tx_hash_preview": "0x" + "ff" * 32,
        "signed_payload_hash": "s" * 64, "broadcast_allowed": True,
        "broadcasted": False, "warnings": (), "created_at_utc": "2026-05-27T00:00:00Z",
    }
    defaults.update(kw)
    return SignedTransactionEnvelope(**defaults)


class _FakeReceipt:
    def __init__(self, status="SUBMITTED", tx_hash="0x" + "ab" * 32, receipt_id="rcpt_001"):
        self.status = status
        self.tx_hash = tx_hash
        self.receipt_id = receipt_id


class TestCreatePosition:
    def test_creates_open_position(self) -> None:
        pos = create_position_from_broadcast(_FakeReceipt(), _envelope())
        assert pos is not None
        assert pos.status == "OPEN"
        assert pos.chain == "Base"
        assert pos.entry_envelope_id == "env_001"

    def test_failed_broadcast_returns_none(self) -> None:
        pos = create_position_from_broadcast(_FakeReceipt(status="FAILED"), _envelope())
        assert pos is None

    def test_position_has_all_entry_fields(self) -> None:
        pos = create_position_from_broadcast(_FakeReceipt(), _envelope())
        assert pos.entry_tx_hash == "0x" + "ab" * 32
        assert pos.entry_broadcast_receipt_id == "rcpt_001"
        assert pos.entry_action_id == "act_001"
        assert pos.entry_candidate_hash == "c" * 64


class TestAppendAndLoad:
    def test_append_and_load(self, tmp_path: Path) -> None:
        p = tmp_path / "positions.jsonl"
        pos = create_position_from_broadcast(_FakeReceipt(), _envelope())
        append_position(p, pos)
        loaded = load_positions(p)
        assert len(loaded) == 1
        assert loaded[0]["status"] == "OPEN"

    def test_get_open_positions(self, tmp_path: Path) -> None:
        p = tmp_path / "positions.jsonl"
        pos = create_position_from_broadcast(_FakeReceipt(), _envelope())
        append_position(p, pos)
        # Append a closed record
        with open(p, "a") as f:
            f.write(json.dumps({"position_id": "pos_closed", "status": "CLOSED"}) + "\n")
        open_pos = get_open_positions(p)
        assert len(open_pos) == 1
        assert open_pos[0]["status"] == "OPEN"


class TestClosePosition:
    def test_close_populates_exit_fields(self, tmp_path: Path) -> None:
        base = tmp_path
        (base / "data").mkdir()
        pos_path = base / "data" / "positions.jsonl"
        pos = create_position_from_broadcast(_FakeReceipt(), _envelope())
        append_position(pos_path, pos)
        result = close_position(base, pos.position_id, "APR_DECAY",
                                realized_pnl_usd=-1.5, gas_cost_usd=0.1)
        assert result.closed is True
        assert result.exit_reason == "APR_DECAY"
        assert result.net_pnl_usd is not None

    def test_close_generates_outcome_event(self, tmp_path: Path) -> None:
        base = tmp_path
        (base / "data").mkdir()
        pos_path = base / "data" / "positions.jsonl"
        pos = create_position_from_broadcast(_FakeReceipt(), _envelope())
        append_position(pos_path, pos)
        result = close_position(base, pos.position_id, "MANUAL",
                                realized_pnl_usd=-2.0, gas_cost_usd=0.05)
        assert result.outcome_event_id is not None
        # Check outcome_events.jsonl
        oe_path = base / "data" / "outcome_events.jsonl"
        assert oe_path.exists()
        events = json.loads(oe_path.read_text().strip().split("\n")[0])
        assert events["outcome_type"] == "POSITION_CLOSED"

    def test_realized_loss_negative_direction(self, tmp_path: Path) -> None:
        base = tmp_path
        (base / "data").mkdir()
        pos_path = base / "data" / "positions.jsonl"
        pos = create_position_from_broadcast(_FakeReceipt(), _envelope())
        append_position(pos_path, pos)
        close_position(base, pos.position_id, "STOP_LOSS", realized_pnl_usd=-5.0)
        oe_path = base / "data" / "outcome_events.jsonl"
        evt = json.loads(oe_path.read_text().strip().split("\n")[0])
        assert evt["impact_direction"] == "NEGATIVE"
        assert evt["factor_key"] == "realized_loss"

    def test_realized_gain_positive_direction(self, tmp_path: Path) -> None:
        base = tmp_path
        (base / "data").mkdir()
        pos_path = base / "data" / "positions.jsonl"
        pos = create_position_from_broadcast(_FakeReceipt(), _envelope())
        append_position(pos_path, pos)
        close_position(base, pos.position_id, "TAKE_PROFIT", realized_pnl_usd=3.0)
        oe_path = base / "data" / "outcome_events.jsonl"
        evt = json.loads(oe_path.read_text().strip().split("\n")[0])
        assert evt["impact_direction"] == "POSITIVE"
        assert evt["factor_key"] == "realized_gain"


class TestSafety:
    def test_no_key_leakage(self, tmp_path: Path) -> None:
        p = tmp_path / "positions.jsonl"
        pos = create_position_from_broadcast(_FakeReceipt(), _envelope())
        append_position(p, pos)
        content = p.read_text()
        assert "private_key" not in content.lower()
        assert "mnemonic" not in content.lower()
        assert "signed_payload" not in content

    def test_no_network_calls(self) -> None:
        import socket
        from unittest.mock import patch
        with patch.object(socket, "create_connection",
                          side_effect=AssertionError("network")):
            pos = create_position_from_broadcast(_FakeReceipt(), _envelope())
            assert pos is not None

    def test_no_signing_imports(self) -> None:
        import defi_autonomy.position_lifecycle as mod
        with open(mod.__file__, "r") as f:
            source = f.read()
        for term in ("from eth_account", "import solders", "broadcast_transaction"):
            assert term not in source

    def test_no_policy_mutation(self) -> None:
        import defi_autonomy.position_lifecycle as mod
        with open(mod.__file__, "r") as f:
            source = f.read()
        assert "risk_policy.json" not in source
        assert "write_json_atomic" not in source


class TestStrategyTypeFallback:
    """strategy_type uses correct fallback order."""

    def test_with_candidate_uses_candidate_strategy(self) -> None:
        class FakeCandidate:
            strategy_type = "stablecoin_lending"
            advertised_apy = 4.5
            fee_apr = 3.5
            reward_apr = 1.0
            tvl_usd = 50_000_000.0
            pool_address = None
            token_addresses = ()
            source_id = "defillama"
            adapter_name = "defillama_adapter"

        pos = create_position_from_broadcast(_FakeReceipt(), _envelope(), candidate=FakeCandidate())
        assert pos.strategy_type == "stablecoin_lending"

    def test_without_candidate_with_action_uses_action_strategy(self) -> None:
        class FakeAction:
            strategy_type = "stable_stable_lp"
            estimated_tx_usd = 5.0
            pool_address = None
            token_addresses = ()
            source_id = "meteora"

        pos = create_position_from_broadcast(_FakeReceipt(), _envelope(), action=FakeAction())
        assert pos.strategy_type == "stable_stable_lp"

    def test_without_candidate_and_action_uses_unknown(self) -> None:
        pos = create_position_from_broadcast(_FakeReceipt(), _envelope())
        assert pos.strategy_type == "unknown"

    def test_never_stores_farm_as_strategy_type(self) -> None:
        env = _envelope(action_type="FARM")
        pos = create_position_from_broadcast(_FakeReceipt(), env)
        assert pos.strategy_type != "FARM"
        assert pos.strategy_type == "unknown"
