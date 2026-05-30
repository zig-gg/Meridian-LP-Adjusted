"""OutcomeRecorder — Phase 5.4D.

Converts cycle artifacts (policy denials, simulation failures, source failures)
into validated OutcomeEvent records and appends them to outcome_events.jsonl.

Closes the learning loop so LearningMemory can consume auto-generated events.

No network calls. No signing. No broadcast. No policy/allowlist mutation.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from defi_autonomy.provenance import (
    OutcomeEvent,
    append_outcome_event,
    sanitize_evidence,
    sanitize_factor_key,
    sanitize_text,
    validate_outcome_event,
)

# ============================================================================
# Constants
# ============================================================================

_DEFAULT_OUTPUT_PATH = "data/outcome_events.jsonl"


# ============================================================================
# Helpers
# ============================================================================


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _gen_event_id(prefix: str = "auto") -> str:
    return f"{prefix}_{int(time.time() * 1000) % 1_000_000:06d}"


def _safe_evidence(raw: dict | None) -> dict:
    """Sanitize evidence, returning empty dict on failure."""
    if not raw or not isinstance(raw, dict):
        return {}
    try:
        return sanitize_evidence(raw)
    except Exception:
        return {}


# ============================================================================
# Event generators
# ============================================================================


def outcome_event_from_policy_denial(
    action_id: str | None = None,
    candidate_hash: str | None = None,
    source_id: str | None = None,
    protocol: str | None = None,
    strategy_type: str | None = None,
    denial_reasons: list[str] | tuple[str, ...] = (),
    risk_score: int | None = None,
    risk_decision: str | None = None,
    provenance_id: str | None = None,
) -> OutcomeEvent:
    """Generate an OutcomeEvent from a policy denial."""
    # Determine factor_key and category from denial reasons
    reasons_text = " ".join(str(r).lower() for r in denial_reasons)

    if any(kw in reasons_text for kw in ("max_tx", "daily_spend", "wallet_value", "slippage")):
        factor_category = "WALLET_RISK"
        factor_key = "policy_denial_cap_exceeded"
        confidence = 0.3
    elif any(kw in reasons_text for kw in ("risk_score", "skip")):
        factor_category = "YIELD_RISK"
        factor_key = "policy_denial_low_risk_score"
        confidence = 0.5
    elif any(kw in reasons_text for kw in ("chain", "strategy")):
        factor_category = "UNKNOWN_FACTOR"
        factor_key = "policy_denial_unsupported"
        confidence = 0.3
    elif "kill switch" in reasons_text or "halt" in reasons_text:
        factor_category = "EXECUTION_RISK"
        factor_key = "policy_denial_system_halt"
        confidence = 0.3
    else:
        factor_category = "UNKNOWN_FACTOR"
        factor_key = "policy_denial_other"
        confidence = 0.3

    evidence = _safe_evidence({
        "denial_reasons": [sanitize_text(str(r), max_len=100) for r in denial_reasons[:5]],
    })

    return OutcomeEvent(
        event_id=_gen_event_id("pol_deny"),
        provenance_id=provenance_id,
        provenance_source="PIPELINE" if provenance_id else "EXTERNAL_SIGNAL",
        outcome_type="POLICY_BLOCKED",
        factor_category=factor_category,
        factor_key=factor_key,
        factor_label=sanitize_text(str(denial_reasons[0]) if denial_reasons else "policy denial", max_len=200),
        impact_direction="NEGATIVE",
        impact_magnitude=0.3,
        confidence=confidence,
        evidence=evidence,
        notes="",
        created_at_utc=_now_utc(),
        action_id=action_id,
        candidate_hash=candidate_hash,
        source_id=source_id,
        protocol=protocol,
        strategy_type=strategy_type,
        risk_score_at_decision=risk_score,
        risk_decision_at_decision=risk_decision,
    )


def outcome_event_from_simulation_failure(
    action_id: str | None = None,
    candidate_hash: str | None = None,
    source_id: str | None = None,
    protocol: str | None = None,
    strategy_type: str | None = None,
    failure_reasons: list[str] | tuple[str, ...] = (),
    warnings: list[str] | tuple[str, ...] = (),
    slippage_bps: int | None = None,
    estimated_gas_usd: float | None = None,
    provenance_id: str | None = None,
) -> OutcomeEvent:
    """Generate an OutcomeEvent from a simulation failure."""
    reasons_text = " ".join(str(r).lower() for r in failure_reasons)

    if "slippage" in reasons_text:
        factor_key = "execution_risk_high_slippage"
        magnitude = 0.4
    elif "gas" in reasons_text or "tolerance" in reasons_text:
        factor_key = "execution_risk_high_gas"
        magnitude = 0.3
    elif "revert" in reasons_text:
        factor_key = "simulation_failure_revert"
        magnitude = 0.5
    elif "bridge" in reasons_text or "borrow" in reasons_text or "leverage" in reasons_text:
        factor_key = "simulation_failure_blocked_action"
        magnitude = 0.2
    else:
        factor_key = "simulation_failure_other"
        magnitude = 0.3

    evidence = _safe_evidence({
        "failure_reasons": [sanitize_text(str(r), max_len=100) for r in failure_reasons[:5]],
        "slippage_bps": slippage_bps,
        "estimated_gas_usd": estimated_gas_usd,
    })

    return OutcomeEvent(
        event_id=_gen_event_id("sim_fail"),
        provenance_id=provenance_id,
        provenance_source="PIPELINE" if provenance_id else "EXTERNAL_SIGNAL",
        outcome_type="SIMULATION_FAILED",
        factor_category="EXECUTION_RISK",
        factor_key=factor_key,
        factor_label=sanitize_text(str(failure_reasons[0]) if failure_reasons else "simulation failure", max_len=200),
        impact_direction="NEGATIVE",
        impact_magnitude=magnitude,
        confidence=0.6,
        evidence=evidence,
        notes="",
        created_at_utc=_now_utc(),
        action_id=action_id,
        candidate_hash=candidate_hash,
        source_id=source_id,
        protocol=protocol,
        strategy_type=strategy_type,
    )


def outcome_event_from_source_failure(
    source_id: str,
    error_type: str | None = None,
    error_message: str | None = None,
    provenance_id: str | None = None,
) -> OutcomeEvent:
    """Generate an OutcomeEvent from a source fetch failure.

    Sets protocol=None and strategy_type=None so bias applies only to source.
    """
    evidence = _safe_evidence({
        "error_type": sanitize_text(str(error_type or ""), max_len=100),
        "error_message": sanitize_text(str(error_message or ""), max_len=200),
    })

    return OutcomeEvent(
        event_id=_gen_event_id("src_fail"),
        provenance_id=provenance_id,
        provenance_source="PIPELINE" if provenance_id else "EXTERNAL_SIGNAL",
        outcome_type="SOURCE_DEGRADED",
        factor_category="SOURCE_RISK",
        factor_key="source_failure_fetch_error",
        factor_label=sanitize_text(f"source {source_id} fetch failed: {error_type or 'unknown'}", max_len=200),
        impact_direction="NEGATIVE",
        impact_magnitude=0.2,
        confidence=0.4,
        evidence=evidence,
        notes="",
        created_at_utc=_now_utc(),
        source_id=source_id,
        protocol=None,
        strategy_type=None,
    )


def outcome_event_from_source_degradation(
    source_id: str,
    consecutive_failures: int,
    provenance_id: str | None = None,
) -> OutcomeEvent:
    """Generate an OutcomeEvent for repeated source failures."""
    magnitude = min(0.5, 0.1 * consecutive_failures)

    evidence = _safe_evidence({
        "consecutive_failures": consecutive_failures,
    })

    return OutcomeEvent(
        event_id=_gen_event_id("src_deg"),
        provenance_id=provenance_id,
        provenance_source="PIPELINE" if provenance_id else "EXTERNAL_SIGNAL",
        outcome_type="SOURCE_DEGRADED",
        factor_category="SOURCE_RISK",
        factor_key="source_degradation_repeated_failures",
        factor_label=sanitize_text(f"source {source_id} degraded: {consecutive_failures} consecutive failures", max_len=200),
        impact_direction="NEGATIVE",
        impact_magnitude=magnitude,
        confidence=0.6,
        evidence=evidence,
        notes="",
        created_at_utc=_now_utc(),
        source_id=source_id,
        protocol=None,
        strategy_type=None,
    )


# ============================================================================
# Cycle-level recorder
# ============================================================================


def record_cycle_outcomes(
    base_dir: Path | str,
    policy_denials: list[dict] | None = None,
    simulation_failures: list[dict] | None = None,
    source_failures: list[dict] | None = None,
    source_health: dict | None = None,
) -> list[OutcomeEvent]:
    """Record all outcome events from a cycle.

    Args:
        base_dir: Path to defi_autonomy directory.
        policy_denials: List of dicts with denial info.
        simulation_failures: List of dicts with simulation failure info.
        source_failures: List of dicts with source failure info.
        source_health: Current source_health dict for degradation detection.

    Returns:
        List of generated OutcomeEvents.
    """
    base = Path(base_dir)
    output_path = base / _DEFAULT_OUTPUT_PATH
    generated: list[OutcomeEvent] = []

    # Policy denials
    if policy_denials:
        for denial in policy_denials:
            if not isinstance(denial, dict):
                continue
            try:
                event = outcome_event_from_policy_denial(
                    action_id=denial.get("action_id"),
                    candidate_hash=denial.get("candidate_hash"),
                    source_id=denial.get("source_id"),
                    protocol=denial.get("protocol"),
                    strategy_type=denial.get("strategy_type"),
                    denial_reasons=denial.get("denial_reasons", []),
                    risk_score=denial.get("risk_score"),
                    risk_decision=denial.get("risk_decision"),
                    provenance_id=denial.get("provenance_id"),
                )
                validate_outcome_event(event)
                append_outcome_event(output_path, event)
                generated.append(event)
            except Exception:
                continue

    # Simulation failures
    if simulation_failures:
        for sim_fail in simulation_failures:
            if not isinstance(sim_fail, dict):
                continue
            try:
                event = outcome_event_from_simulation_failure(
                    action_id=sim_fail.get("action_id"),
                    candidate_hash=sim_fail.get("candidate_hash"),
                    source_id=sim_fail.get("source_id"),
                    protocol=sim_fail.get("protocol"),
                    strategy_type=sim_fail.get("strategy_type"),
                    failure_reasons=sim_fail.get("failure_reasons", []),
                    warnings=sim_fail.get("warnings", []),
                    slippage_bps=sim_fail.get("slippage_bps"),
                    estimated_gas_usd=sim_fail.get("estimated_gas_usd"),
                    provenance_id=sim_fail.get("provenance_id"),
                )
                validate_outcome_event(event)
                append_outcome_event(output_path, event)
                generated.append(event)
            except Exception:
                continue

    # Source failures
    if source_failures:
        for src_fail in source_failures:
            if not isinstance(src_fail, dict):
                continue
            src_id = src_fail.get("source_id")
            if not src_id:
                continue
            try:
                event = outcome_event_from_source_failure(
                    source_id=src_id,
                    error_type=src_fail.get("error_type"),
                    error_message=src_fail.get("error_message"),
                    provenance_id=src_fail.get("provenance_id"),
                )
                validate_outcome_event(event)
                append_outcome_event(output_path, event)
                generated.append(event)
            except Exception:
                continue

    # Source degradation (from source_health)
    if source_health and isinstance(source_health, dict):
        for src_id, health in source_health.items():
            if not isinstance(health, dict):
                continue
            consecutive = health.get("consecutive_failures", 0)
            if consecutive >= 3:
                try:
                    event = outcome_event_from_source_degradation(
                        source_id=src_id,
                        consecutive_failures=consecutive,
                    )
                    validate_outcome_event(event)
                    append_outcome_event(output_path, event)
                    generated.append(event)
                except Exception:
                    continue

    return generated


__all__ = [
    "outcome_event_from_policy_denial",
    "outcome_event_from_simulation_failure",
    "outcome_event_from_source_failure",
    "outcome_event_from_source_degradation",
    "record_cycle_outcomes",
]
