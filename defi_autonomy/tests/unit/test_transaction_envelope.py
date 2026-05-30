"""Unit tests for defi_autonomy.transaction_envelope — Phase 9A.1.

All tests are deterministic and offline. No network calls. No signing.
"""

from __future__ import annotations

import pytest

from defi_autonomy.transaction_envelope import (
    UnsignedTransactionEnvelope,
    create_unsigned_envelope,
)


# ============================================================================
# Tests: dataclass properties
# ============================================================================


class TestUnsignedEnvelopeBasics:
    """UnsignedTransactionEnvelope is frozen and has correct fields."""

    def test_is_frozen(self) -> None:
        env = create_unsigned_envelope(
            action_id="act_001",
            chain="Base",
            protocol="aave-v3",
            action_type="FARM",
            tx_payload={"to": "0x" + "ff" * 20, "value": 5000000},
        )
        with pytest.raises(AttributeError):
            env.chain = "Solana"  # type: ignore

    def test_has_tx_id(self) -> None:
        env = create_unsigned_envelope(
            action_id="act_002",
            chain="Base",
            protocol="aave-v3",
            action_type="FARM",
            tx_payload={"to": "0x00"},
        )
        assert env.tx_id.startswith("tx_act_002_")

    def test_has_created_at_utc(self) -> None:
        env = create_unsigned_envelope(
            action_id="act_003",
            chain="Solana",
            protocol="meteora",
            action_type="FARM",
            tx_payload=b"\x00" * 32,
        )
        assert "T" in env.created_at_utc
        assert env.created_at_utc.endswith("Z")


# ============================================================================
# Tests: EVM-style dict payload
# ============================================================================


class TestEvmPayload:
    """EVM-style dict payload handling."""

    def test_dict_payload_accepted(self) -> None:
        payload = {"to": "0x" + "ab" * 20, "value": 1000000, "data": "0x"}
        env = create_unsigned_envelope(
            action_id="act_evm_001",
            chain="Base",
            protocol="aave-v3",
            action_type="FARM",
            tx_payload=payload,
            estimated_tx_usd=5.0,
            estimated_gas_usd=0.01,
        )
        assert env.payload_is_dict is True
        assert env.payload_is_bytes is False
        assert env.tx_payload == payload
        assert env.chain == "Base"
        assert env.estimated_tx_usd == 5.0

    def test_evm_program_ids_empty(self) -> None:
        env = create_unsigned_envelope(
            action_id="act_evm_002",
            chain="BNB Chain",
            protocol="venus",
            action_type="FARM",
            tx_payload={"to": "0x00"},
            program_ids=(),
        )
        assert env.program_ids == ()


# ============================================================================
# Tests: Solana-style bytes payload
# ============================================================================


class TestSolanaPayload:
    """Solana-style bytes payload handling."""

    def test_bytes_payload_accepted(self) -> None:
        payload = b"\x01\x02\x03" * 20
        env = create_unsigned_envelope(
            action_id="act_sol_001",
            chain="Solana",
            protocol="meteora",
            action_type="FARM",
            tx_payload=payload,
            estimated_tx_usd=5.0,
            estimated_gas_usd=0.005,
            program_ids=("LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo",),
            token_mints=(
                "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
                "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",
            ),
        )
        assert env.payload_is_bytes is True
        assert env.payload_is_dict is False
        assert env.tx_payload == payload
        assert env.chain == "Solana"
        assert len(env.program_ids) == 1
        assert len(env.token_mints) == 2

    def test_empty_bytes_payload(self) -> None:
        env = create_unsigned_envelope(
            action_id="act_sol_002",
            chain="Solana",
            protocol="meteora",
            action_type="FARM",
            tx_payload=b"",
        )
        assert env.payload_is_bytes is True
        assert env.tx_payload == b""


# ============================================================================
# Tests: metadata
# ============================================================================


class TestMetadata:
    """Metadata dict handling."""

    def test_metadata_preserved(self) -> None:
        meta = {"bin_range": {"min": -10, "max": 10}, "strategy": "spot"}
        env = create_unsigned_envelope(
            action_id="act_meta_001",
            chain="Solana",
            protocol="meteora",
            action_type="FARM",
            tx_payload=b"\x00",
            metadata=meta,
        )
        assert env.metadata == meta

    def test_metadata_default_empty(self) -> None:
        env = create_unsigned_envelope(
            action_id="act_meta_002",
            chain="Base",
            protocol="aave-v3",
            action_type="FARM",
            tx_payload={"to": "0x00"},
        )
        assert env.metadata == {}

    def test_to_metadata_dict_excludes_tx_payload(self) -> None:
        env = create_unsigned_envelope(
            action_id="act_meta_003",
            chain="Solana",
            protocol="meteora",
            action_type="FARM",
            tx_payload=b"\xde\xad\xbe\xef" * 100,
            program_ids=("LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo",),
        )
        d = env.to_metadata_dict()
        assert "tx_payload" not in d
        assert d["action_id"] == "act_meta_003"
        assert d["chain"] == "Solana"
        assert d["program_ids"] == ["LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo"]


# ============================================================================
# Tests: tuple conversion
# ============================================================================


class TestTupleConversion:
    """Lists are converted to tuples for frozen invariant."""

    def test_program_ids_list_to_tuple(self) -> None:
        env = create_unsigned_envelope(
            action_id="act_tuple_001",
            chain="Solana",
            protocol="meteora",
            action_type="FARM",
            tx_payload=b"\x00",
            program_ids=["prog1", "prog2"],
        )
        assert isinstance(env.program_ids, tuple)
        assert env.program_ids == ("prog1", "prog2")

    def test_token_mints_list_to_tuple(self) -> None:
        env = create_unsigned_envelope(
            action_id="act_tuple_002",
            chain="Solana",
            protocol="meteora",
            action_type="FARM",
            tx_payload=b"\x00",
            token_mints=["mint1", "mint2"],
        )
        assert isinstance(env.token_mints, tuple)
        assert env.token_mints == ("mint1", "mint2")
