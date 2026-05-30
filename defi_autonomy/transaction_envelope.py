"""TransactionEnvelope — Phase 9A.1.

Chain-neutral unsigned transaction envelope that carries transaction data
through the safety pipeline (PolicyEngine → TxSimulator → WalletExecutor).

Supports both EVM-style dict payloads and Solana-style bytes payloads.

No signing. No broadcast. No network calls. No private-key handling.
No mutation of risk_policy.json or allowlists.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


# ============================================================================
# UnsignedTransactionEnvelope
# ============================================================================


@dataclass(frozen=True, slots=True)
class UnsignedTransactionEnvelope:
    """Carries an unsigned transaction through the safety pipeline.

    The tx_payload field holds chain-specific transaction data:
    - EVM (Base, BNB Chain): dict with 'to', 'value', 'data', 'gas', etc.
    - Solana: bytes (serialized VersionedTransaction message)

    This dataclass is frozen and uses slots. It is never persisted to disk
    in its entirety — only metadata fields are written to ledgers.

    signed_tx_bytes does NOT exist here. Signing happens downstream in
    WalletExecutor and the signed bytes live only in memory.
    """

    tx_id: str
    action_id: str
    chain: str
    protocol: str
    action_type: str
    tx_payload: Any
    estimated_tx_usd: float
    estimated_gas_usd: float
    program_ids: tuple[str, ...]
    token_mints: tuple[str, ...]
    created_at_utc: str
    metadata: dict = field(default_factory=dict)

    def to_metadata_dict(self) -> dict:
        """Return a safe metadata-only dict for ledger records.

        NEVER includes tx_payload (which may contain raw transaction bytes).
        Only includes identifiers, chain info, and cost estimates.
        """
        return {
            "tx_id": self.tx_id,
            "action_id": self.action_id,
            "chain": self.chain,
            "protocol": self.protocol,
            "action_type": self.action_type,
            "estimated_tx_usd": self.estimated_tx_usd,
            "estimated_gas_usd": self.estimated_gas_usd,
            "program_ids": list(self.program_ids),
            "token_mints": list(self.token_mints),
            "created_at_utc": self.created_at_utc,
        }

    @property
    def payload_is_bytes(self) -> bool:
        """True if tx_payload is bytes (Solana-style)."""
        return isinstance(self.tx_payload, (bytes, bytearray, memoryview))

    @property
    def payload_is_dict(self) -> bool:
        """True if tx_payload is a dict (EVM-style)."""
        return isinstance(self.tx_payload, dict)


# ============================================================================
# Factory helpers
# ============================================================================


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _gen_tx_id(action_id: str) -> str:
    return f"tx_{action_id}_{int(time.time() * 1000) % 100000:05d}"


def create_unsigned_envelope(
    action_id: str,
    chain: str,
    protocol: str,
    action_type: str,
    tx_payload: Any,
    estimated_tx_usd: float = 0.0,
    estimated_gas_usd: float = 0.0,
    program_ids: tuple[str, ...] | list[str] = (),
    token_mints: tuple[str, ...] | list[str] = (),
    metadata: dict | None = None,
) -> UnsignedTransactionEnvelope:
    """Create an UnsignedTransactionEnvelope with generated tx_id and timestamp."""
    return UnsignedTransactionEnvelope(
        tx_id=_gen_tx_id(action_id),
        action_id=action_id,
        chain=chain,
        protocol=protocol,
        action_type=action_type,
        tx_payload=tx_payload,
        estimated_tx_usd=estimated_tx_usd,
        estimated_gas_usd=estimated_gas_usd,
        program_ids=tuple(program_ids),
        token_mints=tuple(token_mints),
        created_at_utc=_now_utc(),
        metadata=metadata or {},
    )


__all__ = [
    "UnsignedTransactionEnvelope",
    "create_unsigned_envelope",
]
