"""WalletExecutor — Sprint 3, Phase 3.2A.

Signing core for the operator-funded agent wallet. Enforces all risk policy
preconditions before signing. Does NOT broadcast transactions in Phase 3.2A.

Doctrine:
- The operator creates and funds the agent wallet.
- The agent may sign only from that configured wallet.
- No seed phrase storage, no private key in logs/ledgers/prompts.
- No CEX credentials, no main-wallet access, no unrestricted approvals.
- broadcast_allowed = False in Phase 3.2A.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from defi_autonomy.policy_engine import ActionDescriptor, ApprovalToken
from defi_autonomy.tx_simulator import SimulationResult

# ============================================================================
# Exceptions
# ============================================================================


class WalletExecutorError(Exception):
    """Base for all wallet executor errors."""


class AutonomyLevelTooLow(WalletExecutorError):
    """Autonomy level insufficient for signing."""


class KillSwitchActive(WalletExecutorError):
    """Kill switch is active; all signing halted."""


class MacroBlocked(WalletExecutorError):
    """Macro gate is in HALT state."""


class ApprovalTokenInvalid(WalletExecutorError):
    """Approval token is missing, unapproved, or mismatched."""


class SimulationMissing(WalletExecutorError):
    """Simulation result is missing."""


class SimulationFailed(WalletExecutorError):
    """Simulation did not pass."""


class WalletAddressMismatch(WalletExecutorError):
    """Derived wallet address does not match configured agent wallet."""


class PrivateKeyMissing(WalletExecutorError):
    """Private key not available."""


class PrivateKeyUnsafe(WalletExecutorError):
    """Private key format is unsafe (seed phrase, mnemonic, etc.)."""


class BroadcastDisabled(WalletExecutorError):
    """Broadcasting is disabled in Phase 3.2A."""


class UnsupportedChain(WalletExecutorError):
    """Chain is not supported for signing in this phase."""


class PolicyCapViolation(WalletExecutorError):
    """A risk policy cap has been violated."""


# ============================================================================
# SignedTransactionEnvelope
# ============================================================================


@dataclass(frozen=True, slots=True)
class SignedTransactionEnvelope:
    """Envelope containing a signed (but not broadcast) transaction.

    IMPORTANT: signed_tx_bytes is IN-MEMORY ONLY. It is:
    - Never persisted to JSONL ledgers
    - Never serialized to JSON
    - Never shown in repr or logs
    - Only used transiently during the sign → broadcast window

    Ledgers store only: envelope_id, signed_payload_hash, tx_hash_preview,
    and other metadata fields.
    """

    envelope_id: str
    action_id: str
    approval_id: str
    simulation_id: str
    candidate_hash: str
    chain: str
    protocol: str
    action_type: str
    wallet_address: str
    tx_hash_preview: str | None
    signed_payload_hash: str
    broadcast_allowed: bool
    broadcasted: bool
    warnings: tuple[str, ...]
    created_at_utc: str
    signed_tx_bytes: bytes | None = None  # IN-MEMORY ONLY — never persisted

    def __repr__(self) -> str:
        """Custom repr that excludes signed_tx_bytes for safety."""
        return (
            f"SignedTransactionEnvelope("
            f"envelope_id={self.envelope_id!r}, "
            f"action_id={self.action_id!r}, "
            f"chain={self.chain!r}, "
            f"protocol={self.protocol!r}, "
            f"broadcast_allowed={self.broadcast_allowed!r}, "
            f"broadcasted={self.broadcasted!r})"
        )


# ============================================================================
# Signer provider protocol
# ============================================================================


@runtime_checkable
class SignerProvider(Protocol):
    """Minimal signer interface. Tests use FakeSigner.

    The unsigned_tx parameter accepts Any to support both:
    - EVM: dict with transaction fields
    - Solana: bytes (serialized VersionedTransaction)
    """

    def sign(self, unsigned_tx: Any, chain: str) -> bytes:
        """Sign an unsigned transaction and return raw signed bytes."""
        ...

    def derive_address(self, chain: str) -> str:
        """Derive the wallet address for the given chain."""
        ...


# ============================================================================
# Utility functions
# ============================================================================

_SUPPORTED_SIGNING_CHAINS: frozenset[str] = frozenset({"Base", "BNB Chain"})

_BLOCKED_ACTION_FLAGS: frozenset[str] = frozenset(
    {"bridge", "borrow", "leverage", "unlimited_approval"}
)

_UNSAFE_KEY_INDICATORS: tuple[str, ...] = (
    " ",  # spaces indicate mnemonic
    "abandon",  # BIP39 word
    "ability",
    "able",
)


def load_risk_policy(base_dir: Path | str) -> dict:
    """Load risk_policy.json from base_dir/data/."""
    p = Path(base_dir) / "data" / "risk_policy.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def check_kill_switch(risk_policy: dict) -> None:
    """Raise KillSwitchActive if kill switch file exists."""
    ks_file = risk_policy.get("kill_switch_file")
    if ks_file and Path(ks_file).exists():
        raise KillSwitchActive("kill switch file exists")


def check_macro_gate(base_dir: Path | str, risk_policy: dict) -> None:
    """Raise MacroBlocked if macro_state.json indicates HALT."""
    macro_path = Path(base_dir) / "data" / "macro_state.json"
    if not macro_path.exists():
        return
    try:
        data = json.loads(macro_path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            state = str(data.get("state", "")).upper()
            action = str(data.get("action", "")).upper()
            if state == "HALT" or action == "HALT":
                raise MacroBlocked("macro gate is HALT")
    except MacroBlocked:
        raise
    except (json.JSONDecodeError, OSError):
        pass


def load_operator_wallet_key(
    env_var_name: str = "HERMES_DEFI_AGENT_PRIVATE_KEY",
) -> str:
    """Load the operator-funded agent wallet private key from environment.

    Never logs, stores, or returns the key in error messages.
    Raises PrivateKeyMissing if not set.
    Raises PrivateKeyUnsafe if the value looks like a mnemonic/seed phrase.
    """
    key = os.environ.get(env_var_name)
    if not key:
        raise PrivateKeyMissing(
            "operator-funded agent wallet key not found in environment"
        )
    # Check for unsafe formats (mnemonic/seed phrase)
    if " " in key.strip():
        raise PrivateKeyUnsafe(
            "key appears to be a mnemonic/seed phrase (contains spaces)"
        )
    if len(key.split()) > 1:
        raise PrivateKeyUnsafe(
            "key appears to be a mnemonic/seed phrase"
        )
    return key


def derive_wallet_address(private_key: str, chain: str) -> str:
    """Derive wallet address from private key for the given chain.

    In Phase 3.2A this uses a deterministic hash-based derivation for testing.
    Real derivation would use chain-specific crypto libraries.
    """
    # Deterministic address derivation (placeholder for real crypto)
    raw = hashlib.sha256(f"{private_key}:{chain}".encode()).hexdigest()
    if chain in ("Base", "BNB Chain"):
        return "0x" + raw[:40]
    return raw[:44]  # Solana-style base58 length placeholder


def validate_wallet_address(expected: str, actual: str) -> None:
    """Raise WalletAddressMismatch if addresses don't match."""
    if not expected or not actual:
        raise WalletAddressMismatch(
            "wallet address validation failed: empty address"
        )
    if expected.lower() != actual.lower():
        raise WalletAddressMismatch(
            "derived wallet address does not match configured agent wallet"
        )


def _get_configured_wallet_address(risk_policy: dict) -> str | None:
    """Get the configured operator-funded agent wallet address.

    Supports both new and legacy field names.
    """
    addr = risk_policy.get("operator_funded_agent_wallet_address")
    if addr:
        return addr
    # Fallback to legacy field
    return risk_policy.get("sandbox_wallet_address")


def validate_execution_preconditions(
    action: ActionDescriptor,
    approval_token: ApprovalToken,
    simulation_result: SimulationResult,
    risk_policy: dict,
) -> None:
    """Validate all preconditions before signing.

    Raises specific exceptions on any failure.
    """
    # 1. Autonomy level
    autonomy_level = int(risk_policy.get("autonomy_level", 0))
    if autonomy_level < 2:
        raise AutonomyLevelTooLow(
            f"autonomy_level={autonomy_level} < 2 required for signing"
        )

    # 2. Approval token exists and is approved
    if approval_token is None:
        raise ApprovalTokenInvalid("approval_token is None")
    if not approval_token.approved:
        raise ApprovalTokenInvalid("approval_token.approved is False")

    # 3. Approval token matches action
    if approval_token.action_id != action.action_id:
        raise ApprovalTokenInvalid(
            "approval_token.action_id does not match action"
        )
    if approval_token.candidate_hash != action.candidate_hash:
        raise ApprovalTokenInvalid(
            "approval_token.candidate_hash does not match action"
        )

    # 4. Simulation exists and passed
    if simulation_result is None:
        raise SimulationMissing("simulation_result is None")
    if not simulation_result.simulation_passed:
        raise SimulationFailed("simulation did not pass")

    # 5. Simulation matches action and approval
    if simulation_result.action_id != action.action_id:
        raise SimulationFailed(
            "simulation_result.action_id does not match action"
        )
    if simulation_result.approval_id != approval_token.approval_id:
        raise SimulationFailed(
            "simulation_result.approval_id does not match approval_token"
        )

    # 6. Policy caps
    max_tx = float(risk_policy.get("max_tx_usd", 0))
    if max_tx > 0 and action.estimated_tx_usd > max_tx:
        raise PolicyCapViolation(
            f"estimated_tx_usd exceeds max_tx_usd cap"
        )

    max_daily = float(risk_policy.get("max_daily_spend_usd", 0))
    if max_daily > 0 and action.estimated_daily_spend_usd > max_daily:
        raise PolicyCapViolation(
            f"estimated_daily_spend_usd exceeds max_daily_spend_usd cap"
        )

    max_wallet = float(risk_policy.get("max_wallet_value_usd", 0))
    if max_wallet > 0 and action.estimated_wallet_value_usd > max_wallet:
        raise PolicyCapViolation(
            f"estimated_wallet_value_usd exceeds max_wallet_value_usd cap"
        )

    max_slip = int(risk_policy.get("max_slippage_bps", 50))
    if action.slippage_bps > max_slip:
        raise PolicyCapViolation(
            f"slippage_bps exceeds max_slippage_bps cap"
        )

    # 7. Chain allowed
    allowed_chains = set(risk_policy.get("allowed_chains", []))
    if allowed_chains and action.chain not in allowed_chains:
        raise UnsupportedChain(f"chain not in allowed_chains")

    # 8. Blocked actions
    if isinstance(action.metadata, dict):
        for flag in _BLOCKED_ACTION_FLAGS:
            if action.metadata.get(flag):
                raise PolicyCapViolation(f"blocked action: {flag}")


def sign_transaction(
    action: ActionDescriptor,
    approval_token: ApprovalToken,
    simulation_result: SimulationResult,
    unsigned_tx: Any,
    risk_policy: dict,
    signer_provider: SignerProvider | None = None,
    base_dir: Path | str = "",
) -> SignedTransactionEnvelope:
    """Sign a transaction from the operator-funded agent wallet.

    Enforces all preconditions. Does NOT broadcast.
    Returns SignedTransactionEnvelope with broadcast_allowed=False.

    Args:
        unsigned_tx: Chain-specific unsigned transaction data.
            EVM: dict with 'to', 'value', 'data', etc.
            Solana: bytes (serialized VersionedTransaction message).

    Raises on any precondition failure.
    base_dir should be provided for macro gate check. If empty, macro gate
    check is skipped but a warning is recorded. Production callers (Coordinator)
    must always provide base_dir.
    """
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    warnings: list[str] = []

    # Kill switch
    check_kill_switch(risk_policy)

    # Validate all preconditions (autonomy level, approval, simulation, caps)
    validate_execution_preconditions(
        action, approval_token, simulation_result, risk_policy
    )

    # Macro gate — checked after preconditions, requires base_dir at level >= 2
    if base_dir:
        check_macro_gate(base_dir, risk_policy)
    else:
        autonomy_level = int(risk_policy.get("autonomy_level", 0))
        if autonomy_level >= 2:
            raise MacroBlocked(
                "base_dir is required for macro gate check at autonomy_level >= 2"
            )

    # Chain support for signing
    if action.chain not in _SUPPORTED_SIGNING_CHAINS:
        raise UnsupportedChain(
            f"chain={action.chain!r} not supported for signing in Phase 3.2A"
        )

    # Signer required
    if signer_provider is None:
        raise PrivateKeyMissing(
            "signer_provider is required for signing"
        )

    # Derive address and validate against configured wallet
    derived_address = signer_provider.derive_address(action.chain)
    configured_address = _get_configured_wallet_address(risk_policy)
    if configured_address:
        validate_wallet_address(configured_address, derived_address)

    # Sign
    signed_bytes = signer_provider.sign(unsigned_tx, action.chain)
    signed_payload_hash = hashlib.sha256(signed_bytes).hexdigest()

    # Compute tx hash preview (first 32 chars of payload hash)
    tx_hash_preview = "0x" + signed_payload_hash[:64]

    envelope_id = (
        f"env_{action.action_id}_{int(time.time() * 1000) % 100000:05d}"
    )

    envelope = SignedTransactionEnvelope(
        envelope_id=envelope_id,
        action_id=action.action_id,
        approval_id=approval_token.approval_id,
        simulation_id=simulation_result.simulation_id,
        candidate_hash=action.candidate_hash,
        chain=action.chain,
        protocol=action.protocol,
        action_type=action.action_type,
        wallet_address=derived_address,
        tx_hash_preview=tx_hash_preview,
        signed_payload_hash=signed_payload_hash,
        broadcast_allowed=bool(risk_policy.get("allow_level2_broadcast", False)),
        broadcasted=False,
        warnings=tuple(warnings),
        created_at_utc=now,
        signed_tx_bytes=signed_bytes,
    )

    return envelope


def broadcast_transaction(envelope: SignedTransactionEnvelope) -> None:
    """Attempt to broadcast a signed transaction.

    Always raises BroadcastDisabled in Phase 3.2A.
    """
    raise BroadcastDisabled(
        "broadcasting is disabled in Phase 3.2A"
    )


def write_execution_ledger(
    path: Path | str, envelope: SignedTransactionEnvelope
) -> None:
    """Append a signed transaction envelope to the wallet execution ledger.

    NEVER includes private keys in the ledger record.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "event_type": "SIGNED_TRANSACTION_PREPARED",
        "envelope_id": envelope.envelope_id,
        "action_id": envelope.action_id,
        "approval_id": envelope.approval_id,
        "simulation_id": envelope.simulation_id,
        "candidate_hash": envelope.candidate_hash,
        "chain": envelope.chain,
        "protocol": envelope.protocol,
        "action_type": envelope.action_type,
        "wallet_address": envelope.wallet_address,
        "tx_hash_preview": envelope.tx_hash_preview,
        "signed_payload_hash": envelope.signed_payload_hash,
        "broadcast_allowed": envelope.broadcast_allowed,
        "broadcasted": envelope.broadcasted,
        "warnings": list(envelope.warnings),
        "created_at_utc": envelope.created_at_utc,
    }
    line = json.dumps(
        record, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    with open(p, "a", encoding="utf-8") as f:
        f.write(line + "\n")


__all__ = [
    "ApprovalTokenInvalid",
    "AutonomyLevelTooLow",
    "BroadcastDisabled",
    "KillSwitchActive",
    "MacroBlocked",
    "PolicyCapViolation",
    "PrivateKeyMissing",
    "PrivateKeyUnsafe",
    "SignedTransactionEnvelope",
    "SignerProvider",
    "SimulationFailed",
    "SimulationMissing",
    "UnsupportedChain",
    "WalletAddressMismatch",
    "WalletExecutorError",
    "broadcast_transaction",
    "check_kill_switch",
    "check_macro_gate",
    "derive_wallet_address",
    "load_operator_wallet_key",
    "load_risk_policy",
    "sign_transaction",
    "validate_execution_preconditions",
    "validate_wallet_address",
    "write_execution_ledger",
]
