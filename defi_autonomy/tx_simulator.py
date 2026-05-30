"""TxSimulator — Sprint 3, Phase 3.1.

Deterministic transaction simulation layer that validates a policy-approved
ActionDescriptor before any future wallet executor can sign.

This module does NOT:
- Sign transactions
- Load private keys
- Broadcast transactions
- Connect to real RPC in production paths
- Read environment secrets

It only simulates and returns a SimulationResult.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from defi_autonomy.policy_engine import ActionDescriptor, ApprovalToken
from defi_autonomy.schemas.normalized_candidate import ALLOWED_CHAINS

# ============================================================================
# Exceptions
# ============================================================================


class SimulationError(Exception):
    """Base for all simulation errors."""


class ApprovalTokenInvalidError(SimulationError):
    """ApprovalToken is missing, unapproved, or mismatched."""


class PolicyApprovalRequiredError(SimulationError):
    """Action has not been approved by PolicyEngine."""


class SimulationFailedError(SimulationError):
    """Simulation itself failed (value tolerance, slippage, etc.)."""


class UnsupportedChainSimulationError(SimulationError):
    """Chain is not supported for simulation."""


class ValueToleranceExceededError(SimulationFailedError):
    """Estimated total exceeds value tolerance."""


class SlippageExceededError(SimulationFailedError):
    """Slippage exceeds configured maximum."""


# ============================================================================
# SimulationResult dataclass
# ============================================================================


@dataclass(frozen=True, slots=True)
class SimulationResult:
    """Result of a transaction simulation."""

    simulation_id: str
    action_id: str
    approval_id: str
    candidate_hash: str
    approved_by_policy: bool
    simulation_passed: bool
    chain: str
    protocol: str
    action_type: str
    estimated_tx_usd: float
    estimated_gas_usd: float
    estimated_total_usd: float
    slippage_bps: int
    value_tolerance_bps: int
    expected_token_deltas: dict
    warnings: tuple[str, ...]
    failure_reasons: tuple[str, ...]
    created_at_utc: str


# ============================================================================
# Provider protocol
# ============================================================================


@runtime_checkable
class SimulationProvider(Protocol):
    """Minimal provider interface for transaction simulation."""

    def simulate(
        self, action: ActionDescriptor, tx_bytes: bytes | None = None
    ) -> dict:
        """Simulate an action and return result dict.

        Args:
            action: The ActionDescriptor describing the proposed action.
            tx_bytes: Optional serialized transaction bytes for chain-native
                simulation (e.g. Solana simulateTransaction). None for dry-run
                or EVM eth_call-style simulation that builds its own call.

        Expected keys in response:
        - gas_usd: float
        - expected_token_deltas: dict
        - warnings: list[str]
        - failure_reasons: list[str]
        - simulation_passed: bool
        """
        ...


# ============================================================================
# Validation
# ============================================================================


def validate_approval(
    action: ActionDescriptor, approval_token: ApprovalToken
) -> None:
    """Validate that the approval token is valid for this action.

    Raises ApprovalTokenInvalidError or PolicyApprovalRequiredError on failure.
    """
    if approval_token is None:
        raise PolicyApprovalRequiredError("approval_token is None")

    if not approval_token.approved:
        raise ApprovalTokenInvalidError("approval_token.approved is False")

    if approval_token.action_id != action.action_id:
        raise ApprovalTokenInvalidError(
            f"action_id mismatch: token={approval_token.action_id!r} != action={action.action_id!r}"
        )

    if approval_token.candidate_hash != action.candidate_hash:
        raise ApprovalTokenInvalidError(
            f"candidate_hash mismatch: token={approval_token.candidate_hash!r} != action={action.candidate_hash!r}"
        )

    # Check expiry
    try:
        expires = datetime.strptime(
            approval_token.expires_at_utc, "%Y-%m-%dT%H:%M:%SZ"
        ).replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        if now > expires:
            raise ApprovalTokenInvalidError(
                f"approval_token expired at {approval_token.expires_at_utc}"
            )
    except ValueError as e:
        raise ApprovalTokenInvalidError(
            f"invalid expires_at_utc format: {e}"
        ) from e


# ============================================================================
# Simulation
# ============================================================================

_SUPPORTED_SIMULATION_CHAINS: frozenset[str] = frozenset(ALLOWED_CHAINS)

_BLOCKED_METADATA_FLAGS: frozenset[str] = frozenset(
    {"bridge", "borrow", "leverage", "unlimited_approval"}
)


def _generate_simulation_id() -> str:
    now = datetime.now(timezone.utc)
    return f"sim_{now.strftime('%Y%m%dT%H%M%SZ')}_{int(time.time() * 1000) % 100000:05d}"


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def simulate_action(
    action: ActionDescriptor,
    approval_token: ApprovalToken,
    risk_policy: dict,
    provider: SimulationProvider | None = None,
    tx_bytes: bytes | None = None,
) -> SimulationResult:
    """Simulate an action and return SimulationResult.

    Validates approval token, checks policy caps, runs provider simulation.
    Returns SimulationResult with simulation_passed=False on soft failures.
    Raises on structural validation errors (missing token, mismatch, expired).

    Args:
        action: The ActionDescriptor to simulate.
        approval_token: Valid ApprovalToken from PolicyEngine.
        risk_policy: Loaded risk_policy.json dict.
        provider: Optional SimulationProvider for chain-native simulation.
        tx_bytes: Optional serialized transaction bytes passed to provider.
    """
    sim_id = _generate_simulation_id()
    now = _now_utc()

    # Validate approval token (raises on structural issues)
    validate_approval(action, approval_token)

    # Extract policy values
    max_tx_usd = float(risk_policy.get("max_tx_usd", 0))
    max_slippage_bps = int(risk_policy.get("max_slippage_bps", 50))
    value_tolerance_bps = int(risk_policy.get("simulation_value_tolerance_bps", 50))

    failure_reasons: list[str] = []
    warnings: list[str] = []

    # Check supported chain
    if action.chain not in _SUPPORTED_SIMULATION_CHAINS:
        raise UnsupportedChainSimulationError(
            f"chain={action.chain!r} not supported for simulation"
        )

    # Check max_tx_usd
    if max_tx_usd > 0 and action.estimated_tx_usd > max_tx_usd:
        failure_reasons.append(
            f"estimated_tx_usd={action.estimated_tx_usd} > max_tx_usd={max_tx_usd}"
        )

    # Check slippage
    if action.slippage_bps > max_slippage_bps:
        failure_reasons.append(
            f"slippage_bps={action.slippage_bps} > max_slippage_bps={max_slippage_bps}"
        )

    # Check blocked metadata flags
    if isinstance(action.metadata, dict):
        for flag in _BLOCKED_METADATA_FLAGS:
            if action.metadata.get(flag):
                failure_reasons.append(f"blocked action flag: {flag}")

    # Run provider simulation
    estimated_gas_usd = 0.0
    expected_token_deltas: dict = {}

    if provider is not None:
        try:
            provider_result = provider.simulate(action, tx_bytes=tx_bytes)
            if isinstance(provider_result, dict):
                estimated_gas_usd = float(provider_result.get("gas_usd", 0))
                expected_token_deltas = provider_result.get(
                    "expected_token_deltas", {}
                )
                provider_warnings = provider_result.get("warnings", [])
                if isinstance(provider_warnings, list):
                    warnings.extend(str(w) for w in provider_warnings)
                provider_failures = provider_result.get("failure_reasons", [])
                if isinstance(provider_failures, list):
                    failure_reasons.extend(str(f) for f in provider_failures)
                provider_passed = provider_result.get("simulation_passed", True)
                if not provider_passed:
                    if not any("provider" in r.lower() for r in failure_reasons):
                        failure_reasons.append("provider simulation failed")
        except Exception as e:
            failure_reasons.append(f"provider error: {type(e).__name__}: {e}")
    else:
        # Dry-run mode: deterministic pass for planning actions
        estimated_gas_usd = 0.01  # nominal gas estimate
        warnings.append("dry-run simulation (no provider)")

    # Compute total
    estimated_total_usd = action.estimated_tx_usd + estimated_gas_usd

    # Value tolerance check
    if value_tolerance_bps > 0 and max_tx_usd > 0:
        tolerance_usd = max_tx_usd * (value_tolerance_bps / 10000.0)
        if estimated_total_usd > max_tx_usd + tolerance_usd:
            failure_reasons.append(
                f"estimated_total_usd={estimated_total_usd:.4f} exceeds "
                f"max_tx_usd+tolerance={max_tx_usd + tolerance_usd:.4f}"
            )

    simulation_passed = len(failure_reasons) == 0

    result = SimulationResult(
        simulation_id=sim_id,
        action_id=action.action_id,
        approval_id=approval_token.approval_id,
        candidate_hash=action.candidate_hash,
        approved_by_policy=True,
        simulation_passed=simulation_passed,
        chain=action.chain,
        protocol=action.protocol,
        action_type=action.action_type,
        estimated_tx_usd=action.estimated_tx_usd,
        estimated_gas_usd=estimated_gas_usd,
        estimated_total_usd=round(estimated_total_usd, 6),
        slippage_bps=action.slippage_bps,
        value_tolerance_bps=value_tolerance_bps,
        expected_token_deltas=expected_token_deltas,
        warnings=tuple(warnings),
        failure_reasons=tuple(failure_reasons),
        created_at_utc=now,
    )

    return result


# ============================================================================
# Serialization and ledger
# ============================================================================


def simulation_result_to_dict(result: SimulationResult) -> dict:
    """Convert SimulationResult to a plain dict."""
    return {
        "simulation_id": result.simulation_id,
        "action_id": result.action_id,
        "approval_id": result.approval_id,
        "candidate_hash": result.candidate_hash,
        "approved_by_policy": result.approved_by_policy,
        "simulation_passed": result.simulation_passed,
        "chain": result.chain,
        "protocol": result.protocol,
        "action_type": result.action_type,
        "estimated_tx_usd": result.estimated_tx_usd,
        "estimated_gas_usd": result.estimated_gas_usd,
        "estimated_total_usd": result.estimated_total_usd,
        "slippage_bps": result.slippage_bps,
        "value_tolerance_bps": result.value_tolerance_bps,
        "expected_token_deltas": result.expected_token_deltas,
        "warnings": list(result.warnings),
        "failure_reasons": list(result.failure_reasons),
        "created_at_utc": result.created_at_utc,
    }


def write_simulation_ledger(path: Path | str, result: SimulationResult) -> None:
    """Append a simulation result to the simulation ledger (JSONL)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "event_type": "SIMULATION_PASSED" if result.simulation_passed else "SIMULATION_FAILED",
        "simulation_id": result.simulation_id,
        "action_id": result.action_id,
        "approval_id": result.approval_id,
        "candidate_hash": result.candidate_hash,
        "chain": result.chain,
        "protocol": result.protocol,
        "action_type": result.action_type,
        "estimated_tx_usd": result.estimated_tx_usd,
        "estimated_gas_usd": result.estimated_gas_usd,
        "estimated_total_usd": result.estimated_total_usd,
        "slippage_bps": result.slippage_bps,
        "warnings": list(result.warnings),
        "failure_reasons": list(result.failure_reasons),
        "created_at_utc": result.created_at_utc,
    }
    line = json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    with open(p, "a", encoding="utf-8") as f:
        f.write(line + "\n")


__all__ = [
    "ApprovalTokenInvalidError",
    "PolicyApprovalRequiredError",
    "SimulationError",
    "SimulationFailedError",
    "SimulationProvider",
    "SimulationResult",
    "SlippageExceededError",
    "UnsupportedChainSimulationError",
    "ValueToleranceExceededError",
    "simulate_action",
    "simulation_result_to_dict",
    "validate_approval",
    "write_simulation_ledger",
]
