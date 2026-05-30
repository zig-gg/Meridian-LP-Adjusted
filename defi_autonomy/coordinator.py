"""Coordinator — Sprint 4, Phase 4.1.

Central orchestration loop for the Hermes DeFi Autonomy swarm:
External_Data_Ingestion → RiskScorer → PolicyEngine → TxSimulator →
WalletExecutor (signing-prep only, no broadcast).

Respects kill switch, macro gate, risk policy caps, and autonomy levels.
No private key loading. No broadcast. No real network calls in tests.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from defi_autonomy.external_data_ingestion import (
    run_ingestion_cycle,
    write_json_atomic,
)
from defi_autonomy.outcome_recorder import record_cycle_outcomes
from defi_autonomy.policy_engine import (
    ActionDescriptor,
    PolicyEngine,
    risk_assessment_to_action,
)
from defi_autonomy.learning_memory import (
    get_bias_for_candidate,
    load_outcome_events,
)
from defi_autonomy.risk_scorer import (
    DECISION_FARM,
    DECISION_SKIP,
    DECISION_WATCH,
    RiskAssessment,
    extract_stablecoin_benchmark,
    score_candidate,
    score_candidates,
)
from defi_autonomy.schemas.normalized_candidate import (
    NormalizedCandidate,
    NormalizedCandidateError,
    from_dict,
)
from defi_autonomy.tx_simulator import (
    SimulationProvider,
    SimulationResult,
    simulate_action,
    write_simulation_ledger,
)
from defi_autonomy.wallet_executor import (
    BroadcastDisabled,
    SignedTransactionEnvelope,
    SignerProvider,
    WalletExecutorError,
    sign_transaction,
    write_execution_ledger,
)

logger = logging.getLogger(__name__)

# ============================================================================
# CycleReport
# ============================================================================


@dataclass(frozen=True, slots=True)
class CycleReport:
    """Summary of a single Coordinator cycle."""

    cycle_id: str
    started_at_utc: str
    finished_at_utc: str | None
    status: str  # COMPLETE / HALTED / FAILED / NO_VALID_DATA
    autonomy_level: int
    ingestion_status: str
    candidate_count: int
    risk_assessment_count: int
    approved_count: int
    denied_count: int
    simulation_passed_count: int
    simulation_failed_count: int
    signing_prepared_count: int
    errors: tuple[str, ...]
    warnings: tuple[str, ...]
    ledger_paths: dict
    learning_events_loaded: int = 0
    learning_bias_applied_count: int = 0


# ============================================================================
# Utility functions
# ============================================================================


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _generate_cycle_id() -> str:
    now = datetime.now(timezone.utc)
    return f"coord_{now.strftime('%Y%m%dT%H%M%SZ')}_{int(time.time() * 1000) % 100000:05d}"


def _sanitize_error(e: Exception) -> str:
    """Sanitize error message to avoid leaking secrets."""
    msg = str(e)
    # Remove anything that looks like a key or secret
    if len(msg) > 200:
        msg = msg[:200] + "...[truncated]"
    return f"{type(e).__name__}: {msg}"


def load_risk_policy(base_dir: Path | str) -> dict:
    """Load risk_policy.json from base_dir/data/."""
    p = Path(base_dir) / "data" / "risk_policy.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def load_normalized_candidates(base_dir: Path | str) -> list[NormalizedCandidate]:
    """Load normalized_yield_candidates.json and reconstruct NormalizedCandidate objects."""
    p = Path(base_dir) / "data" / "normalized_yield_candidates.json"
    if not p.exists():
        return []
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    if not isinstance(doc, dict):
        return []
    raw_candidates = doc.get("candidates", [])
    if not isinstance(raw_candidates, list):
        return []

    candidates: list[NormalizedCandidate] = []
    for raw in raw_candidates:
        if not isinstance(raw, dict):
            continue
        # Remove internal fields
        clean = {k: v for k, v in raw.items() if not k.startswith("_")}
        try:
            candidates.append(from_dict(clean))
        except (NormalizedCandidateError, Exception):
            continue
    return candidates


def _check_kill_switch(base_dir: Path, risk_policy: dict) -> bool:
    """Check if kill switch is active. Returns True if halted."""
    ks_file = risk_policy.get("kill_switch_file")
    if ks_file and Path(ks_file).exists():
        return True
    local_stop = base_dir / "KILL_SWITCH.md"
    if local_stop.exists():
        content = local_stop.read_text(encoding="utf-8").strip()
        if content.upper().startswith("STOP"):
            return True
    return False


def _check_macro_halt(base_dir: Path) -> bool:
    """Check if macro_state.json indicates HALT."""
    macro_path = base_dir / "data" / "macro_state.json"
    if not macro_path.exists():
        return False
    try:
        data = json.loads(macro_path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            state = str(data.get("state", "")).upper()
            action = str(data.get("action", "")).upper()
            return state == "HALT" or action == "HALT"
    except (json.JSONDecodeError, OSError):
        pass
    return False


# ============================================================================
# Coordinator cycle
# ============================================================================


def run_cycle(
    base_dir: Path | str,
    signer_provider: SignerProvider | None = None,
    simulation_provider: SimulationProvider | None = None,
    _client_factory: Any = None,
) -> CycleReport:
    """Run a full Coordinator cycle.

    Args:
        base_dir: Path to the defi_autonomy directory.
        signer_provider: Optional signer for WalletExecutor (Phase 3.2A).
        simulation_provider: Optional provider for TxSimulator.
        _client_factory: Optional mock client factory for ingestion.

    Returns:
        CycleReport summarizing the cycle outcome.
    """
    base = Path(base_dir)
    data_dir = base / "data"
    cycle_id = _generate_cycle_id()
    started_at = _now_utc()
    errors: list[str] = []
    warnings: list[str] = []

    # Load risk policy
    risk_policy = load_risk_policy(base)
    autonomy_level = int(risk_policy.get("autonomy_level", 0))

    # Ledger paths
    ledger_paths = {
        "execution_ledger": str(data_dir / "execution_ledger.jsonl"),
        "simulation_ledger": str(data_dir / "simulation_ledger.jsonl"),
        "wallet_execution_ledger": str(data_dir / "wallet_execution_ledger.jsonl"),
        "cycle_report": str(data_dir / "cycle_report.json"),
    }

    # --- Kill switch ---
    if _check_kill_switch(base, risk_policy):
        return CycleReport(
            cycle_id=cycle_id,
            started_at_utc=started_at,
            finished_at_utc=_now_utc(),
            status="HALTED",
            autonomy_level=autonomy_level,
            ingestion_status="NOT_RUN",
            candidate_count=0,
            risk_assessment_count=0,
            approved_count=0,
            denied_count=0,
            simulation_passed_count=0,
            simulation_failed_count=0,
            signing_prepared_count=0,
            errors=("kill switch active",),
            warnings=(),
            ledger_paths=ledger_paths,
        )

    # --- Macro gate ---
    if _check_macro_halt(base):
        return CycleReport(
            cycle_id=cycle_id,
            started_at_utc=started_at,
            finished_at_utc=_now_utc(),
            status="HALTED",
            autonomy_level=autonomy_level,
            ingestion_status="NOT_RUN",
            candidate_count=0,
            risk_assessment_count=0,
            approved_count=0,
            denied_count=0,
            simulation_passed_count=0,
            simulation_failed_count=0,
            signing_prepared_count=0,
            errors=("macro gate HALT",),
            warnings=(),
            ledger_paths=ledger_paths,
        )

    # --- Ingestion ---
    try:
        ingestion_result = run_ingestion_cycle(base, _client_factory=_client_factory)
        ingestion_status = ingestion_result.get("status", "UNKNOWN")
    except Exception as e:
        errors.append(_sanitize_error(e))
        return CycleReport(
            cycle_id=cycle_id,
            started_at_utc=started_at,
            finished_at_utc=_now_utc(),
            status="FAILED",
            autonomy_level=autonomy_level,
            ingestion_status="FAILED",
            candidate_count=0,
            risk_assessment_count=0,
            approved_count=0,
            denied_count=0,
            simulation_passed_count=0,
            simulation_failed_count=0,
            signing_prepared_count=0,
            errors=tuple(errors),
            warnings=tuple(warnings),
            ledger_paths=ledger_paths,
        )

    if ingestion_status == "NO_VALID_DATA":
        return CycleReport(
            cycle_id=cycle_id,
            started_at_utc=started_at,
            finished_at_utc=_now_utc(),
            status="NO_VALID_DATA",
            autonomy_level=autonomy_level,
            ingestion_status=ingestion_status,
            candidate_count=0,
            risk_assessment_count=0,
            approved_count=0,
            denied_count=0,
            simulation_passed_count=0,
            simulation_failed_count=0,
            signing_prepared_count=0,
            errors=(),
            warnings=("no valid data from any source",),
            ledger_paths=ledger_paths,
        )

    # --- Load candidates ---
    candidates = load_normalized_candidates(base)
    candidate_count = len(candidates)

    if candidate_count == 0:
        return CycleReport(
            cycle_id=cycle_id,
            started_at_utc=started_at,
            finished_at_utc=_now_utc(),
            status="NO_VALID_DATA",
            autonomy_level=autonomy_level,
            ingestion_status=ingestion_status,
            candidate_count=0,
            risk_assessment_count=0,
            approved_count=0,
            denied_count=0,
            simulation_passed_count=0,
            simulation_failed_count=0,
            signing_prepared_count=0,
            errors=(),
            warnings=("ingestion succeeded but no candidates loaded",),
            ledger_paths=ledger_paths,
        )

    # --- Risk scoring with LearningMemory ---
    learning_events_loaded = 0
    learning_bias_applied_count = 0

    # Load outcome events for learning bias
    outcome_events_path = data_dir / "outcome_events.jsonl"
    outcome_events: list[dict] = []
    if outcome_events_path.exists():
        try:
            outcome_events = load_outcome_events(outcome_events_path)
            learning_events_loaded = len(outcome_events)
        except Exception:
            pass

    clamp_points = int(risk_policy.get("learning_bias_clamp_points", 5))

    # Score candidates with optional learning bias
    try:
        benchmark_apy = extract_stablecoin_benchmark(candidates)
        assessments: list[RiskAssessment] = []
        for candidate in candidates:
            bias = None
            if outcome_events:
                try:
                    bias = get_bias_for_candidate(
                        candidate, outcome_events, clamp_points=clamp_points
                    )
                    if bias and bias.score_adjustment != 0:
                        learning_bias_applied_count += 1
                except Exception:
                    pass
            assessment = score_candidate(
                candidate, benchmark_apy=benchmark_apy, learning_bias=bias
            )
            assessments.append(assessment)
    except Exception as e:
        errors.append(_sanitize_error(e))
        assessments = []

    risk_assessment_count = len(assessments)

    # --- Policy evaluation ---
    approved_count = 0
    denied_count = 0
    simulation_passed_count = 0
    simulation_failed_count = 0
    signing_prepared_count = 0
    _policy_denials: list[dict] = []
    _simulation_failures: list[dict] = []

    try:
        policy_engine = PolicyEngine(base)
    except Exception as e:
        errors.append(_sanitize_error(e))
        return CycleReport(
            cycle_id=cycle_id,
            started_at_utc=started_at,
            finished_at_utc=_now_utc(),
            status="FAILED",
            autonomy_level=autonomy_level,
            ingestion_status=ingestion_status,
            candidate_count=candidate_count,
            risk_assessment_count=risk_assessment_count,
            approved_count=0,
            denied_count=0,
            simulation_passed_count=0,
            simulation_failed_count=0,
            signing_prepared_count=0,
            errors=tuple(errors),
            warnings=tuple(warnings),
            ledger_paths=ledger_paths,
        )

    for i, assessment in enumerate(assessments):
        if assessment.decision == DECISION_SKIP:
            denied_count += 1
            continue

        # Build ActionDescriptor
        try:
            candidate = candidates[i]
            action = risk_assessment_to_action(
                assessment, candidate, risk_policy
            )
        except Exception as e:
            errors.append(_sanitize_error(e))
            denied_count += 1
            continue

        # Policy evaluation
        try:
            policy_decision = policy_engine.evaluate(action)
        except Exception as e:
            errors.append(_sanitize_error(e))
            denied_count += 1
            continue

        if not policy_decision.approved:
            denied_count += 1
            _policy_denials.append({
                "action_id": action.action_id,
                "candidate_hash": action.candidate_hash,
                "source_id": action.source_id,
                "protocol": action.protocol,
                "strategy_type": action.strategy_type,
                "denial_reasons": list(policy_decision.denial_reasons),
                "risk_score": action.risk_score,
                "risk_decision": action.risk_decision,
                "provenance_id": None,
            })
            continue

        approved_count += 1

        # --- TxSimulator ---
        if policy_decision.approval_token is None:
            warnings.append(f"approved but no token for {action.action_id}")
            continue

        try:
            sim_result = simulate_action(
                action,
                policy_decision.approval_token,
                risk_policy,
                provider=simulation_provider,
            )
        except Exception as e:
            errors.append(_sanitize_error(e))
            simulation_failed_count += 1
            continue

        if sim_result.simulation_passed:
            simulation_passed_count += 1
        else:
            simulation_failed_count += 1
            _simulation_failures.append({
                "action_id": action.action_id,
                "candidate_hash": action.candidate_hash,
                "source_id": action.source_id,
                "protocol": action.protocol,
                "strategy_type": action.strategy_type,
                "failure_reasons": list(sim_result.failure_reasons),
                "warnings": list(sim_result.warnings),
                "slippage_bps": sim_result.slippage_bps,
                "estimated_gas_usd": sim_result.estimated_gas_usd,
                "provenance_id": None,
            })
            continue

        # Write simulation ledger
        try:
            write_simulation_ledger(
                data_dir / "simulation_ledger.jsonl", sim_result
            )
        except Exception:
            pass

        # --- WalletExecutor (signing-prep only) ---
        if (
            autonomy_level >= 2
            and signer_provider is not None
            and risk_policy.get("allow_signing_prep", False)
        ):
            try:
                unsigned_tx = {
                    "action_id": action.action_id,
                    "chain": action.chain,
                    "protocol": action.protocol,
                    "action_type": action.action_type,
                    "estimated_tx_usd": action.estimated_tx_usd,
                }
                envelope = sign_transaction(
                    action,
                    policy_decision.approval_token,
                    sim_result,
                    unsigned_tx,
                    risk_policy,
                    signer_provider=signer_provider,
                    base_dir=base,
                )
                signing_prepared_count += 1
                # Write wallet execution ledger
                try:
                    write_execution_ledger(
                        data_dir / "wallet_execution_ledger.jsonl", envelope
                    )
                except Exception:
                    pass
            except WalletExecutorError as e:
                warnings.append(f"signing-prep skipped: {_sanitize_error(e)}")
            except Exception as e:
                errors.append(_sanitize_error(e))

    # --- Determine final status ---
    if errors:
        status = "COMPLETE" if approved_count > 0 or simulation_passed_count > 0 else "FAILED"
    else:
        status = "COMPLETE"

    # --- Record outcome events for LearningMemory ---
    try:
        # Load source health for degradation detection
        source_health_path = data_dir / "source_health.json"
        _source_health = {}
        if source_health_path.exists():
            try:
                _source_health = json.loads(source_health_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass

        # Collect source failures from ingestion result
        _source_failures = []
        if ingestion_result.get("sources_failed"):
            for src_id in ingestion_result["sources_failed"]:
                _source_failures.append({"source_id": src_id, "error_type": "fetch_failure"})

        record_cycle_outcomes(
            base,
            policy_denials=_policy_denials,
            simulation_failures=_simulation_failures,
            source_failures=_source_failures,
            source_health=_source_health,
        )
    except Exception as e:
        warnings.append(f"outcome recording failed: {_sanitize_error(e)}")

    report = CycleReport(
        cycle_id=cycle_id,
        started_at_utc=started_at,
        finished_at_utc=_now_utc(),
        status=status,
        autonomy_level=autonomy_level,
        ingestion_status=ingestion_status,
        candidate_count=candidate_count,
        risk_assessment_count=risk_assessment_count,
        approved_count=approved_count,
        denied_count=denied_count,
        simulation_passed_count=simulation_passed_count,
        simulation_failed_count=simulation_failed_count,
        signing_prepared_count=signing_prepared_count,
        errors=tuple(errors),
        warnings=tuple(warnings),
        ledger_paths=ledger_paths,
        learning_events_loaded=learning_events_loaded,
        learning_bias_applied_count=learning_bias_applied_count,
    )

    # Write cycle report
    try:
        write_cycle_report(base, report)
    except Exception:
        pass

    return report


# ============================================================================
# Report serialization
# ============================================================================


def cycle_report_to_dict(report: CycleReport) -> dict:
    """Convert CycleReport to a plain dict."""
    return {
        "cycle_id": report.cycle_id,
        "started_at_utc": report.started_at_utc,
        "finished_at_utc": report.finished_at_utc,
        "status": report.status,
        "autonomy_level": report.autonomy_level,
        "ingestion_status": report.ingestion_status,
        "candidate_count": report.candidate_count,
        "risk_assessment_count": report.risk_assessment_count,
        "approved_count": report.approved_count,
        "denied_count": report.denied_count,
        "simulation_passed_count": report.simulation_passed_count,
        "simulation_failed_count": report.simulation_failed_count,
        "signing_prepared_count": report.signing_prepared_count,
        "learning_events_loaded": report.learning_events_loaded,
        "learning_bias_applied_count": report.learning_bias_applied_count,
        "errors": list(report.errors),
        "warnings": list(report.warnings),
        "ledger_paths": report.ledger_paths,
    }


def write_cycle_report(base_dir: Path | str, report: CycleReport) -> None:
    """Write cycle report to JSON and append to JSONL."""
    base = Path(base_dir)
    data_dir = base / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    report_dict = cycle_report_to_dict(report)

    # Write latest report
    write_json_atomic(data_dir / "cycle_report.json", report_dict)

    # Append to history
    line = json.dumps(
        report_dict, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    with open(data_dir / "cycle_report.jsonl", "a", encoding="utf-8") as f:
        f.write(line + "\n")


__all__ = [
    "CycleReport",
    "cycle_report_to_dict",
    "load_normalized_candidates",
    "load_risk_policy",
    "run_cycle",
    "write_cycle_report",
]
