"""Unit tests for defi_autonomy.transaction_builder — Phase 9A.1.

All tests are deterministic and offline. No network calls. No signing.
"""

from __future__ import annotations

import pytest

from defi_autonomy.policy_engine import ActionDescriptor
from defi_autonomy.transaction_builder import (
    FakeTransactionBuilder,
    TransactionBuilder,
)
from defi_autonomy.transaction_envelope import UnsignedTransactionEnvelope


# ============================================================================
# Fixtures
# ============================================================================


def _make_action(**overrides) -> ActionDescriptor:
    defaults = {
        "action_id": "act_tb_001",
        "candidate_hash": "c" * 64,
        "source_id": "defillama",
        "adapter_name": "defillama_adapter",
        "chain": "Base",
        "protocol": "aave-v3",
        "strategy_type": "stablecoin_lending",
        "action_type": "FARM",
        "pool_address": "0x" + "aa" * 20,
        "token_addresses": ("0x" + "bb" * 20,),
        "estimated_tx_usd": 5.0,
        "estimated_wallet_value_usd": 20.0,
        "estimated_daily_spend_usd": 5.0,
        "slippage_bps": 10,
        "risk_score": 90,
        "risk_decision": "FARM",
        "created_at_utc": "2026-05-28T00:00:00Z",
        "metadata": {},
    }
    defaults.update(overrides)
    return ActionDescriptor(**defaults)


# ============================================================================
# Tests: protocol compliance
# ============================================================================


class TestProtocolCompliance:
    """FakeTransactionBuilder satisfies TransactionBuilder protocol."""

    def test_fake_builder_is_transaction_builder(self) -> None:
        builder = FakeTransactionBuilder()
        assert isinstance(builder, TransactionBuilder)

    def test_fake_builder_has_build_method(self) -> None:
        builder = FakeTransactionBuilder()
        assert callable(getattr(builder, "build", None))


# ============================================================================
# Tests: EVM chain output
# ============================================================================


class TestEvmChainOutput:
    """FakeTransactionBuilder produces EVM-style envelopes for EVM chains."""

    def test_base_chain_produces_dict_payload(self) -> None:
        builder = FakeTransactionBuilder()
        action = _make_action(chain="Base")
        env = builder.build(action, "0x" + "ab" * 20)
        assert isinstance(env, UnsignedTransactionEnvelope)
        assert env.payload_is_dict is True
        assert env.chain == "Base"
        assert env.action_id == "act_tb_001"

    def test_bnb_chain_produces_dict_payload(self) -> None:
        builder = FakeTransactionBuilder()
        action = _make_action(chain="BNB Chain")
        env = builder.build(action, "0x" + "ab" * 20)
        assert env.payload_is_dict is True
        assert env.chain == "BNB Chain"

    def test_evm_payload_has_to_field(self) -> None:
        builder = FakeTransactionBuilder()
        action = _make_action(chain="Base", pool_address="0x" + "cc" * 20)
        env = builder.build(action, "0x" + "ab" * 20)
        assert "to" in env.tx_payload
        assert env.tx_payload["to"] == "0x" + "cc" * 20

    def test_evm_payload_has_from_field(self) -> None:
        builder = FakeTransactionBuilder()
        action = _make_action(chain="Base")
        wallet = "0x" + "dd" * 20
        env = builder.build(action, wallet)
        assert env.tx_payload["from"] == wallet


# ============================================================================
# Tests: Solana chain output
# ============================================================================


class TestSolanaChainOutput:
    """FakeTransactionBuilder produces Solana-style envelopes for Solana."""

    def test_solana_produces_bytes_payload(self) -> None:
        builder = FakeTransactionBuilder()
        action = _make_action(chain="Solana", protocol="meteora")
        env = builder.build(action, "FakeWa11etAddress1111111111111111111111111111")
        assert isinstance(env, UnsignedTransactionEnvelope)
        assert env.payload_is_bytes is True
        assert env.chain == "Solana"

    def test_solana_has_program_ids(self) -> None:
        builder = FakeTransactionBuilder()
        action = _make_action(chain="Solana", protocol="meteora")
        env = builder.build(action, "FakeWallet")
        assert len(env.program_ids) > 0
        assert "LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo" in env.program_ids

    def test_solana_has_token_mints(self) -> None:
        builder = FakeTransactionBuilder()
        action = _make_action(
            chain="Solana",
            protocol="meteora",
            token_addresses=(
                "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
                "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",
            ),
        )
        env = builder.build(action, "FakeWallet")
        assert "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v" in env.token_mints


# ============================================================================
# Tests: metadata and cost estimates
# ============================================================================


class TestMetadataAndCosts:
    """Builder passes through cost estimates and metadata."""

    def test_estimated_tx_usd_passed(self) -> None:
        builder = FakeTransactionBuilder()
        action = _make_action(estimated_tx_usd=4.5)
        env = builder.build(action, "0xwallet")
        assert env.estimated_tx_usd == 4.5

    def test_gas_usd_from_builder(self) -> None:
        builder = FakeTransactionBuilder(gas_usd=0.02)
        action = _make_action()
        env = builder.build(action, "0xwallet")
        assert env.estimated_gas_usd == 0.02

    def test_metadata_contains_builder_tag(self) -> None:
        builder = FakeTransactionBuilder()
        action = _make_action()
        env = builder.build(action, "0xwallet")
        assert env.metadata.get("builder") == "fake"

    def test_metadata_contains_wallet_address(self) -> None:
        builder = FakeTransactionBuilder()
        action = _make_action()
        env = builder.build(action, "0xMyWallet")
        assert env.metadata.get("wallet_address") == "0xMyWallet"


# ============================================================================
# Tests: no signing, no network, no keys
# ============================================================================


class TestSafety:
    """Builder does not sign, broadcast, or load keys."""

    def test_no_network_calls(self) -> None:
        import socket
        from unittest.mock import patch

        builder = FakeTransactionBuilder()
        action = _make_action()
        with patch.object(
            socket,
            "create_connection",
            side_effect=AssertionError("network call detected"),
        ):
            env = builder.build(action, "0xwallet")
            assert isinstance(env, UnsignedTransactionEnvelope)

    def test_no_signing_imports(self) -> None:
        import defi_autonomy.transaction_builder as mod

        with open(mod.__file__, "r") as f:
            source = f.read()
        forbidden = (
            "from eth_account",
            "import eth_account",
            "from solders",
            "import solders",
        )
        for term in forbidden:
            assert term not in source, f"forbidden import {term!r} found"
