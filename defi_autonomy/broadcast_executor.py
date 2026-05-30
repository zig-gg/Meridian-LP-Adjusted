"""BroadcastExecutor — Phase 3.2B.

Level 2 human-approved broadcast flow. Submits signed transaction envelopes
only after valid OperatorApprovalRecord from Telegram Guardian.

Level 3 autonomous broadcast is config-gated and NOT enabled in this phase.

No real RPC calls in tests. No private-key leakage. No policy/allowlist mutation.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from defi_autonomy.wallet_executor import (
    KillSwitchActive,
    MacroBlocked,
    SignedTransactionEnvelope,
    check_kill_switch,
    check_macro_gate,
)

# ============================================================================
# Exceptions
# ============================================================================


class BroadcastExecutorError(Exception):
    """Base for all broadcast executor errors."""


class OperatorApprovalMissing(BroadcastExecutorError):
    """No valid operator approval found."""


class OperatorApprovalExpired(BroadcastExecutorError):
    """Operator approval has expired."""


class OperatorApprovalMismatch(BroadcastExecutorError):
    """Operator approval does not match the envelope."""


class BroadcastNotAllowed(BroadcastExecutorError):
    """Broadcast is not allowed for this envelope or config."""


class BroadcastPolicyBlocked(BroadcastExecutorError):
    """Broadcast blocked by policy (kill switch, macro gate, caps)."""


class BroadcastProviderError(BroadcastExecutorError):
    """Broadcast provider returned an error."""


class DailySpendCapExceeded(BroadcastExecutorError):
    """Daily spend cap would be exceeded by this broadcast."""


# ============================================================================
# Dataclasses
# ============================================================================


@dataclass(frozen=True, slots=True)
class OperatorApprovalRecord:
    """Record of operator approval via Telegram."""

    approval_record_id: str
    action_id: str
    envelope_id: str
    simulation_id: str
    candidate_hash: str
    signed_payload_hash: str
    approved_by_chat_id: str
    approved_by_user_id: str | None
    approved_at_utc: str
    expires_at_utc: str
    approval_message: str


@dataclass(frozen=True, slots=True)
class BroadcastReceipt:
    """Receipt from a broadcast attempt."""

    receipt_id: str
    envelope_id: str
    action_id: str
    tx_hash: str | None
    chain: str
    protocol: str
    status: str  # SUBMITTED / CONFIRMED / FAILED
    block_number: int | None
    gas_used: float | None
    error_type: str | None
    error_message: str | None
    submitted_at_utc: str
    confirmed_at_utc: str | None


# ============================================================================
# Provider protocol
# ============================================================================


@runtime_checkable
class BroadcastProvider(Protocol):
    """Minimal broadcast provider interface. Tests use FakeBroadcastProvider."""

    def submit(self, envelope: SignedTransactionEnvelope) -> dict:
        """Submit a signed envelope. Returns result dict with tx_hash, status, etc."""
        ...


# ============================================================================
# Utility
# ============================================================================


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _gen_id(prefix: str) -> str:
    return f"{prefix}_{int(time.time() * 1000) % 1_000_000:06d}"


# ============================================================================
# Approval persistence
# ============================================================================


def operator_approval_to_dict(record: OperatorApprovalRecord) -> dict:
    return {
        "approval_record_id": record.approval_record_id,
        "action_id": record.action_id,
        "envelope_id": record.envelope_id,
        "simulation_id": record.simulation_id,
        "candidate_hash": record.candidate_hash,
        "signed_payload_hash": record.signed_payload_hash,
        "approved_by_chat_id": record.approved_by_chat_id,
        "approved_by_user_id": record.approved_by_user_id,
        "approved_at_utc": record.approved_at_utc,
        "expires_at_utc": record.expires_at_utc,
        "approval_message": record.approval_message,
    }


def append_operator_approval(path: Path | str, record: OperatorApprovalRecord) -> None:
    """Append an OperatorApprovalRecord to JSONL."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(operator_approval_to_dict(record), sort_keys=True, separators=(",", ":"))
    with open(p, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def load_operator_approvals(path: Path | str) -> list[dict]:
    """Load operator approvals from JSONL. Returns list of dicts."""
    p = Path(path)
    if not p.exists():
        return []
    results: list[dict] = []
    try:
        for line in p.read_text(encoding="utf-8").strip().split("\n"):
            if line.strip():
                results.append(json.loads(line))
    except (json.JSONDecodeError, OSError):
        pass
    return results


def find_valid_approval(
    envelope: SignedTransactionEnvelope,
    approvals: list[dict],
    now_utc: str | None = None,
) -> dict | None:
    """Find a valid, non-expired approval matching the envelope."""
    now = now_utc or _now_utc()
    for appr in reversed(approvals):  # Most recent first
        if appr.get("envelope_id") != envelope.envelope_id:
            continue
        if appr.get("signed_payload_hash") != envelope.signed_payload_hash:
            continue
        expires = appr.get("expires_at_utc", "")
        if expires and expires < now:
            continue
        return appr
    return None


# ============================================================================
# Validation
# ============================================================================


def validate_operator_approval(
    envelope: SignedTransactionEnvelope,
    approval_record: dict,
) -> None:
    """Validate that an approval record matches the envelope exactly."""
    if not approval_record:
        raise OperatorApprovalMissing("no approval record provided")

    if approval_record.get("envelope_id") != envelope.envelope_id:
        raise OperatorApprovalMismatch("envelope_id mismatch")

    if approval_record.get("action_id") != envelope.action_id:
        raise OperatorApprovalMismatch("action_id mismatch")

    if approval_record.get("candidate_hash") != envelope.candidate_hash:
        raise OperatorApprovalMismatch("candidate_hash mismatch")

    if approval_record.get("simulation_id") != envelope.simulation_id:
        raise OperatorApprovalMismatch("simulation_id mismatch")

    if approval_record.get("signed_payload_hash") != envelope.signed_payload_hash:
        raise OperatorApprovalMismatch("signed_payload_hash mismatch — approval invalidated")

    # Check expiry
    expires = approval_record.get("expires_at_utc", "")
    now = _now_utc()
    if expires and expires < now:
        raise OperatorApprovalExpired(f"approval expired at {expires}")


# ============================================================================
# Broadcast
# ============================================================================


def _compute_daily_spend(base_dir: Path) -> float:
    """Compute today's total spend from broadcast_ledger.jsonl."""
    ledger_path = base_dir / "data" / "broadcast_ledger.jsonl"
    if not ledger_path.exists():
        return 0.0
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    total = 0.0
    try:
        for line in ledger_path.read_text(encoding="utf-8").strip().split("\n"):
            if not line.strip():
                continue
            record = json.loads(line)
            if record.get("status") in ("SUBMITTED", "CONFIRMED"):
                submitted = record.get("submitted_at_utc", "")
                if submitted.startswith(today):
                    total += float(record.get("estimated_tx_usd", 0) or 0)
    except (json.JSONDecodeError, OSError):
        pass
    return total


def broadcast_envelope(
    envelope: SignedTransactionEnvelope,
    risk_policy: dict,
    base_dir: Path | str,
    provider: BroadcastProvider,
    approval_record: dict | None = None,
) -> BroadcastReceipt:
    """Broadcast a signed envelope after all safety checks.

    Level 2: requires valid OperatorApprovalRecord.
    Level 3: config-gated, not enabled in Phase 3.2B.
    """
    base = Path(base_dir)
    autonomy_level = int(risk_policy.get("autonomy_level", 0))

    # --- Envelope checks ---
    if not envelope.broadcast_allowed:
        raise BroadcastNotAllowed("envelope.broadcast_allowed is False")

    if envelope.broadcasted:
        raise BroadcastNotAllowed("envelope already broadcasted")

    # --- Policy checks ---
    # Kill switch
    try:
        check_kill_switch(risk_policy)
    except KillSwitchActive as e:
        raise BroadcastPolicyBlocked(str(e)) from e

    # Macro gate
    try:
        check_macro_gate(base, risk_policy)
    except MacroBlocked as e:
        raise BroadcastPolicyBlocked(str(e)) from e

    # --- Autonomy level + approval ---
    if autonomy_level < 2:
        raise BroadcastNotAllowed("autonomy_level < 2, broadcast not allowed")

    # Level 3 autonomous broadcast check
    allow_autonomous = risk_policy.get("allow_autonomous_broadcast", False)
    if autonomy_level >= 3 and allow_autonomous:
        # Level 3 autonomous — no approval needed (deferred, not enabled by default)
        pass
    else:
        # Level 2 — require operator approval
        if approval_record is None:
            raise OperatorApprovalMissing("Level 2 broadcast requires operator approval")
        validate_operator_approval(envelope, approval_record)

    # --- Daily spend cap re-check ---
    max_daily = float(risk_policy.get("max_daily_spend_usd", 0))
    if max_daily > 0:
        current_daily = _compute_daily_spend(base)
        # Estimate this tx cost (use envelope's action estimated_tx_usd from ledger context)
        # For now, use a conservative estimate from the envelope metadata
        estimated_tx = float(risk_policy.get("max_tx_usd", 0))  # worst case
        if current_daily + estimated_tx > max_daily:
            raise DailySpendCapExceeded(
                f"daily spend {current_daily} + tx {estimated_tx} > cap {max_daily}"
            )

    # --- Submit via provider ---
    now = _now_utc()
    try:
        result = provider.submit(envelope)
    except Exception as e:
        # Provider failure
        receipt = BroadcastReceipt(
            receipt_id=_gen_id("rcpt"),
            envelope_id=envelope.envelope_id,
            action_id=envelope.action_id,
            tx_hash=None,
            chain=envelope.chain,
            protocol=envelope.protocol,
            status="FAILED",
            block_number=None,
            gas_used=None,
            error_type=type(e).__name__,
            error_message=str(e)[:200],
            submitted_at_utc=now,
            confirmed_at_utc=None,
        )
        return receipt

    # Parse provider result
    if isinstance(result, dict):
        status = result.get("status", "SUBMITTED")
        tx_hash = result.get("tx_hash")
        block_number = result.get("block_number")
        gas_used = result.get("gas_used")
        error_type = result.get("error_type")
        error_message = result.get("error_message")
    else:
        status = "SUBMITTED"
        tx_hash = None
        block_number = None
        gas_used = None
        error_type = None
        error_message = None

    receipt = BroadcastReceipt(
        receipt_id=_gen_id("rcpt"),
        envelope_id=envelope.envelope_id,
        action_id=envelope.action_id,
        tx_hash=tx_hash,
        chain=envelope.chain,
        protocol=envelope.protocol,
        status=status,
        block_number=block_number,
        gas_used=gas_used,
        error_type=error_type,
        error_message=error_message[:200] if error_message else None,
        submitted_at_utc=now,
        confirmed_at_utc=result.get("confirmed_at_utc") if isinstance(result, dict) else None,
    )

    return receipt


# ============================================================================
# Ledger
# ============================================================================


def broadcast_receipt_to_dict(receipt: BroadcastReceipt) -> dict:
    return {
        "receipt_id": receipt.receipt_id,
        "envelope_id": receipt.envelope_id,
        "action_id": receipt.action_id,
        "tx_hash": receipt.tx_hash,
        "chain": receipt.chain,
        "protocol": receipt.protocol,
        "status": receipt.status,
        "block_number": receipt.block_number,
        "gas_used": receipt.gas_used,
        "error_type": receipt.error_type,
        "error_message": receipt.error_message,
        "submitted_at_utc": receipt.submitted_at_utc,
        "confirmed_at_utc": receipt.confirmed_at_utc,
    }


def write_broadcast_ledger(path: Path | str, receipt: BroadcastReceipt, estimated_tx_usd: float = 0.0) -> None:
    """Append a broadcast receipt to the broadcast ledger (JSONL)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    record = broadcast_receipt_to_dict(receipt)
    record["event_type"] = f"BROADCAST_{receipt.status}"
    record["estimated_tx_usd"] = estimated_tx_usd
    line = json.dumps(record, sort_keys=True, separators=(",", ":"))
    with open(p, "a", encoding="utf-8") as f:
        f.write(line + "\n")


__all__ = [
    "BroadcastExecutorError",
    "BroadcastNotAllowed",
    "BroadcastPolicyBlocked",
    "BroadcastProvider",
    "BroadcastProviderError",
    "BroadcastReceipt",
    "DailySpendCapExceeded",
    "OperatorApprovalExpired",
    "OperatorApprovalMismatch",
    "OperatorApprovalMissing",
    "OperatorApprovalRecord",
    "append_operator_approval",
    "broadcast_envelope",
    "broadcast_receipt_to_dict",
    "find_valid_approval",
    "load_operator_approvals",
    "operator_approval_to_dict",
    "validate_operator_approval",
    "write_broadcast_ledger",
]
