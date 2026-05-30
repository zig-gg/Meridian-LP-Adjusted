"""Position Lifecycle — Phase 3.2C.

Tracks open/closed positions after broadcast. Generates OutcomeEvents on close
so LearningMemory can learn from realized results.

No network calls. No signing. No broadcast. No policy/allowlist mutation.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from defi_autonomy.provenance import (
    OutcomeEvent,
    append_outcome_event,
    sanitize_text,
    validate_outcome_event,
)
from defi_autonomy.wallet_executor import SignedTransactionEnvelope

_POSITIONS_FILE = "data/positions.jsonl"


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _gen_id(prefix: str) -> str:
    return f"{prefix}_{int(time.time() * 1000) % 1_000_000:06d}"


# ============================================================================
# Dataclasses
# ============================================================================


@dataclass(frozen=True, slots=True)
class PositionRecord:
    position_id: str
    status: str
    chain: str
    protocol: str
    strategy_type: str
    pool_address: str | None
    token_addresses: tuple[str, ...]
    entry_tx_hash: str | None
    entry_broadcast_receipt_id: str
    entry_envelope_id: str
    entry_action_id: str
    entry_candidate_hash: str
    entry_estimated_tx_usd: float
    entry_apy_at_open: float
    entry_fee_apr_at_open: float
    entry_reward_apr_at_open: float
    entry_tvl_usd_at_open: float
    opened_at_utc: str
    exit_tx_hash: str | None
    exit_reason: str | None
    exit_estimated_tx_usd: float | None
    closed_at_utc: str | None
    realized_pnl_usd: float | None
    accrued_yield_usd: float | None
    impermanent_loss_usd: float | None
    gas_cost_usd: float | None
    net_pnl_usd: float | None
    risk_score_at_entry: int
    source_id: str
    adapter_name: str
    notes: str


@dataclass(frozen=True, slots=True)
class PositionCloseResult:
    position_id: str
    closed: bool
    exit_reason: str
    realized_pnl_usd: float | None
    accrued_yield_usd: float | None
    impermanent_loss_usd: float | None
    gas_cost_usd: float | None
    net_pnl_usd: float | None
    outcome_event_id: str | None
    created_at_utc: str


# ============================================================================
# Serialization
# ============================================================================


def position_to_dict(record: PositionRecord) -> dict:
    return {
        "position_id": record.position_id,
        "status": record.status,
        "chain": record.chain,
        "protocol": record.protocol,
        "strategy_type": record.strategy_type,
        "pool_address": record.pool_address,
        "token_addresses": list(record.token_addresses),
        "entry_tx_hash": record.entry_tx_hash,
        "entry_broadcast_receipt_id": record.entry_broadcast_receipt_id,
        "entry_envelope_id": record.entry_envelope_id,
        "entry_action_id": record.entry_action_id,
        "entry_candidate_hash": record.entry_candidate_hash,
        "entry_estimated_tx_usd": record.entry_estimated_tx_usd,
        "entry_apy_at_open": record.entry_apy_at_open,
        "entry_fee_apr_at_open": record.entry_fee_apr_at_open,
        "entry_reward_apr_at_open": record.entry_reward_apr_at_open,
        "entry_tvl_usd_at_open": record.entry_tvl_usd_at_open,
        "opened_at_utc": record.opened_at_utc,
        "exit_tx_hash": record.exit_tx_hash,
        "exit_reason": record.exit_reason,
        "exit_estimated_tx_usd": record.exit_estimated_tx_usd,
        "closed_at_utc": record.closed_at_utc,
        "realized_pnl_usd": record.realized_pnl_usd,
        "accrued_yield_usd": record.accrued_yield_usd,
        "impermanent_loss_usd": record.impermanent_loss_usd,
        "gas_cost_usd": record.gas_cost_usd,
        "net_pnl_usd": record.net_pnl_usd,
        "risk_score_at_entry": record.risk_score_at_entry,
        "source_id": record.source_id,
        "adapter_name": record.adapter_name,
        "notes": record.notes,
    }


def position_close_result_to_dict(result: PositionCloseResult) -> dict:
    return {
        "position_id": result.position_id,
        "closed": result.closed,
        "exit_reason": result.exit_reason,
        "realized_pnl_usd": result.realized_pnl_usd,
        "accrued_yield_usd": result.accrued_yield_usd,
        "impermanent_loss_usd": result.impermanent_loss_usd,
        "gas_cost_usd": result.gas_cost_usd,
        "net_pnl_usd": result.net_pnl_usd,
        "outcome_event_id": result.outcome_event_id,
        "created_at_utc": result.created_at_utc,
    }


# ============================================================================
# Persistence
# ============================================================================


def append_position(path: Path | str, record: PositionRecord) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(position_to_dict(record), sort_keys=True, separators=(",", ":"))
    with open(p, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def load_positions(path: Path | str) -> list[dict]:
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


def get_open_positions(path: Path | str) -> list[dict]:
    return [p for p in load_positions(path) if p.get("status") == "OPEN"]


# ============================================================================
# Position creation
# ============================================================================


def create_position_from_broadcast(
    receipt: Any,
    envelope: SignedTransactionEnvelope,
    action: Any = None,
    candidate: Any = None,
    risk_assessment: Any = None,
) -> PositionRecord | None:
    """Create an OPEN position from a successful broadcast receipt.

    Returns None if receipt status is FAILED.
    """
    status = getattr(receipt, "status", None) or (receipt.get("status") if isinstance(receipt, dict) else None)
    if status == "FAILED":
        return None

    tx_hash = getattr(receipt, "tx_hash", None)
    receipt_id = getattr(receipt, "receipt_id", "") or ""

    # Extract from action/candidate if available
    entry_apy = 0.0
    fee_apr = 0.0
    reward_apr = 0.0
    tvl_usd = 0.0
    strategy_type = "unknown"
    pool_address = None
    token_addresses: tuple[str, ...] = ()
    source_id = ""
    adapter_name = ""
    risk_score = 0
    estimated_tx_usd = 0.0

    if candidate is not None:
        entry_apy = getattr(candidate, "advertised_apy", 0.0)
        fee_apr = getattr(candidate, "fee_apr", 0.0)
        reward_apr = getattr(candidate, "reward_apr", 0.0)
        tvl_usd = getattr(candidate, "tvl_usd", 0.0)
        strategy_type = getattr(candidate, "strategy_type", strategy_type)
        pool_address = getattr(candidate, "pool_address", None)
        token_addresses = getattr(candidate, "token_addresses", ())
        source_id = getattr(candidate, "source_id", "")
        adapter_name = getattr(candidate, "adapter_name", "")

    if action is not None:
        estimated_tx_usd = getattr(action, "estimated_tx_usd", 0.0)
        if not pool_address:
            pool_address = getattr(action, "pool_address", None)
        if not token_addresses:
            token_addresses = getattr(action, "token_addresses", ())
        if not source_id:
            source_id = getattr(action, "source_id", "")
        if strategy_type == "unknown":
            strategy_type = getattr(action, "strategy_type", "unknown")

    if risk_assessment is not None:
        risk_score = getattr(risk_assessment, "score", 0)

    return PositionRecord(
        position_id=_gen_id("pos"),
        status="OPEN",
        chain=envelope.chain,
        protocol=envelope.protocol,
        strategy_type=strategy_type,
        pool_address=pool_address,
        token_addresses=token_addresses if isinstance(token_addresses, tuple) else tuple(token_addresses),
        entry_tx_hash=tx_hash,
        entry_broadcast_receipt_id=receipt_id,
        entry_envelope_id=envelope.envelope_id,
        entry_action_id=envelope.action_id,
        entry_candidate_hash=envelope.candidate_hash,
        entry_estimated_tx_usd=estimated_tx_usd,
        entry_apy_at_open=entry_apy,
        entry_fee_apr_at_open=fee_apr,
        entry_reward_apr_at_open=reward_apr,
        entry_tvl_usd_at_open=tvl_usd,
        opened_at_utc=_now_utc(),
        exit_tx_hash=None,
        exit_reason=None,
        exit_estimated_tx_usd=None,
        closed_at_utc=None,
        realized_pnl_usd=None,
        accrued_yield_usd=None,
        impermanent_loss_usd=None,
        gas_cost_usd=None,
        net_pnl_usd=None,
        risk_score_at_entry=risk_score,
        source_id=source_id,
        adapter_name=adapter_name,
        notes="",
    )


# ============================================================================
# Position close
# ============================================================================


def close_position(
    base_dir: Path | str,
    position_id: str,
    exit_reason: str,
    realized_pnl_usd: float | None = None,
    accrued_yield_usd: float | None = None,
    impermanent_loss_usd: float | None = None,
    gas_cost_usd: float | None = None,
    exit_tx_hash: str | None = None,
) -> PositionCloseResult:
    """Close a position and generate an OutcomeEvent."""
    base = Path(base_dir)
    positions_path = base / _POSITIONS_FILE
    outcome_path = base / "data" / "outcome_events.jsonl"

    # Compute net PnL
    net_pnl: float | None = None
    if realized_pnl_usd is not None:
        net_pnl = realized_pnl_usd
        if accrued_yield_usd:
            net_pnl += accrued_yield_usd
        if impermanent_loss_usd:
            net_pnl -= abs(impermanent_loss_usd)
        if gas_cost_usd:
            net_pnl -= abs(gas_cost_usd)

    # Find original position for context
    positions = load_positions(positions_path)
    original = next((p for p in positions if p.get("position_id") == position_id), None)

    # Append closed position record
    closed_record_dict = {
        "position_id": position_id,
        "status": "CLOSED",
        "exit_reason": exit_reason,
        "exit_tx_hash": exit_tx_hash,
        "closed_at_utc": _now_utc(),
        "realized_pnl_usd": realized_pnl_usd,
        "accrued_yield_usd": accrued_yield_usd,
        "impermanent_loss_usd": impermanent_loss_usd,
        "gas_cost_usd": gas_cost_usd,
        "net_pnl_usd": net_pnl,
    }
    positions_path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(closed_record_dict, sort_keys=True, separators=(",", ":"))
    with open(positions_path, "a", encoding="utf-8") as f:
        f.write(line + "\n")

    # Generate OutcomeEvent
    outcome_event_id: str | None = None
    try:
        if net_pnl is not None and net_pnl < 0:
            direction = "NEGATIVE"
            factor_key = "realized_loss"
            magnitude = min(1.0, abs(net_pnl) / 10.0)
        elif net_pnl is not None and net_pnl > 0:
            direction = "POSITIVE"
            factor_key = "realized_gain"
            magnitude = min(1.0, net_pnl / 10.0)
        else:
            direction = "NEUTRAL"
            factor_key = "position_closed_neutral"
            magnitude = 0.1

        evt = OutcomeEvent(
            event_id=_gen_id("pos_close"),
            provenance_id=None,
            provenance_source="OPERATOR_MANUAL",
            outcome_type="POSITION_CLOSED",
            factor_category="YIELD_RISK",
            factor_key=factor_key,
            factor_label=sanitize_text(f"Position {position_id} closed: {exit_reason}", max_len=200),
            impact_direction=direction,
            impact_magnitude=magnitude,
            confidence=0.9,
            evidence={"exit_reason": exit_reason, "net_pnl_usd": net_pnl},
            notes="",
            created_at_utc=_now_utc(),
            source_id=original.get("source_id") if original else None,
            protocol=original.get("protocol") if original else None,
            strategy_type=original.get("strategy_type") if original else None,
            candidate_hash=original.get("entry_candidate_hash") if original else None,
            risk_score_at_decision=original.get("risk_score_at_entry") if original else None,
            risk_decision_at_decision="FARM",
        )
        validate_outcome_event(evt)
        append_outcome_event(outcome_path, evt)
        outcome_event_id = evt.event_id
    except Exception:
        pass

    return PositionCloseResult(
        position_id=position_id,
        closed=True,
        exit_reason=exit_reason,
        realized_pnl_usd=realized_pnl_usd,
        accrued_yield_usd=accrued_yield_usd,
        impermanent_loss_usd=impermanent_loss_usd,
        gas_cost_usd=gas_cost_usd,
        net_pnl_usd=net_pnl,
        outcome_event_id=outcome_event_id,
        created_at_utc=_now_utc(),
    )


__all__ = [
    "PositionCloseResult",
    "PositionRecord",
    "append_position",
    "close_position",
    "create_position_from_broadcast",
    "get_open_positions",
    "load_positions",
    "position_close_result_to_dict",
    "position_to_dict",
]
