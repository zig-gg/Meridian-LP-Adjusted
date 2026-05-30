"""TransactionBuilder — Phase 9A.1.

Abstract protocol for building unsigned transactions from ActionDescriptors.
Concrete implementations (MeteoraTransactionBuilder, EvmTransactionBuilder)
are deferred to later phases.

Tests use FakeTransactionBuilder which satisfies the protocol.

No signing. No broadcast. No network calls. No private-key handling.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from defi_autonomy.policy_engine import ActionDescriptor
from defi_autonomy.transaction_envelope import (
    UnsignedTransactionEnvelope,
    create_unsigned_envelope,
)


# ============================================================================
# TransactionBuilder protocol
# ============================================================================


@runtime_checkable
class TransactionBuilder(Protocol):
    """Builds unsigned transactions from ActionDescriptors.

    Implementations:
    - MeteoraTransactionBuilder (Phase 9B — Solana/Meteora DLMM)
    - EvmTransactionBuilder (deferred — EVM/Base/BNB Chain)
    - FakeTransactionBuilder (tests)

    The build method MUST NOT:
    - Sign transactions
    - Load private keys
    - Broadcast transactions
    - Mutate risk_policy.json or allowlists

    The build method MAY:
    - Make read-only network calls to fetch pool state (in production)
    - Return chain-specific payloads (dict for EVM, bytes for Solana)
    """

    def build(
        self,
        action: ActionDescriptor,
        wallet_address: str,
    ) -> UnsignedTransactionEnvelope:
        """Build an unsigned transaction envelope for the given action.

        Args:
            action: The policy-approved ActionDescriptor.
            wallet_address: The operator-funded agent wallet address.

        Returns:
            UnsignedTransactionEnvelope with chain-specific tx_payload.
        """
        ...


# ============================================================================
# FakeTransactionBuilder (for tests)
# ============================================================================


class FakeTransactionBuilder:
    """Fake builder for testing. Returns deterministic envelopes.

    Produces dict payloads for EVM chains and bytes payloads for Solana.
    No network calls. No signing. No private keys.
    """

    def __init__(self, gas_usd: float = 0.01) -> None:
        self._gas_usd = gas_usd

    def build(
        self,
        action: ActionDescriptor,
        wallet_address: str,
    ) -> UnsignedTransactionEnvelope:
        """Build a fake unsigned transaction envelope."""
        if action.chain == "Solana":
            # Solana-style: bytes payload
            tx_payload = b"\x00" * 64  # fake serialized VersionedTransaction
            program_ids = ("LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo",)
        else:
            # EVM-style: dict payload
            tx_payload = {
                "to": action.pool_address or "0x" + "00" * 20,
                "value": int(action.estimated_tx_usd * 1e6),
                "data": "0x",
                "from": wallet_address,
                "chain_id": 8453 if action.chain == "Base" else 56,
            }
            program_ids = ()

        return create_unsigned_envelope(
            action_id=action.action_id,
            chain=action.chain,
            protocol=action.protocol,
            action_type=action.action_type,
            tx_payload=tx_payload,
            estimated_tx_usd=action.estimated_tx_usd,
            estimated_gas_usd=self._gas_usd,
            program_ids=program_ids,
            token_mints=action.token_addresses,
            metadata={"builder": "fake", "wallet_address": wallet_address},
        )


__all__ = [
    "FakeTransactionBuilder",
    "TransactionBuilder",
]
