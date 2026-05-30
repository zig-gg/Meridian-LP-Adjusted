"""Provenance + Dynamic OutcomeEvent schema — Phase 5.4A.

Provides traceable provenance records and dynamic outcome events for future
LearningMemory. Every lesson traces back through:
source snapshot → candidate → risk assessment → policy → simulation → signing.

Dynamic X-factors are advisory-only. This module NEVER modifies:
- risk_policy.json, allowlists, autonomy_level, wallet limits, blocked actions.

No network calls. No signing/key-loading. No broadcast.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ============================================================================
# Constants
# ============================================================================

ALLOWED_PROVENANCE_SOURCES: frozenset[str] = frozenset({
    "PIPELINE", "OPERATOR_MANUAL", "EXTERNAL_SIGNAL",
})

ALLOWED_OUTCOME_TYPES: frozenset[str] = frozenset({
    "REALIZED_PNL", "POSITION_CLOSED", "SIMULATION_FAILED",
    "SOURCE_DEGRADED", "MANUAL_OBSERVATION", "POLICY_BLOCKED", "INVALID_DATA",
})

ALLOWED_FACTOR_CATEGORIES: frozenset[str] = frozenset({
    "YIELD_RISK", "LIQUIDITY_RISK", "SOURCE_RISK", "EXECUTION_RISK",
    "PROTOCOL_RISK", "MARKET_RISK", "WALLET_RISK", "USER_BEHAVIOR",
    "UNKNOWN_FACTOR",
})

ALLOWED_IMPACT_DIRECTIONS: frozenset[str] = frozenset({
    "POSITIVE", "NEGATIVE", "NEUTRAL",
})

ALLOWED_RISK_DECISIONS: frozenset[str] = frozenset({
    "FARM", "WATCH", "SKIP",
})

_FACTOR_KEY_MAX_LEN = 80
_FACTOR_LABEL_MAX_LEN = 200
_EVIDENCE_MAX_BYTES = 10_240  # 10KB
_EVIDENCE_MAX_DEPTH = 3
_NOTES_MAX_LEN = 500

# Patterns that indicate secrets
_SECRET_PATTERNS: tuple[re.Pattern, ...] = (
    re.compile(r"[0-9a-fA-F]{64,}"),  # long hex (private keys)
    re.compile(r"\b(abandon|ability|able)\b.*\b\w+\b.*\b\w+\b"),  # mnemonic start
    re.compile(r"Bearer\s+\S+", re.IGNORECASE),
    re.compile(r"(api[_-]?key|apikey|secret[_-]?key)\s*[:=]\s*\S+", re.IGNORECASE),
    re.compile(r"sk[-_][a-zA-Z0-9]{20,}"),  # API secret keys
)


# ============================================================================
# Exceptions
# ============================================================================


class ProvenanceError(ValueError):
    """Base for provenance/outcome validation errors."""


class InvalidFactorKeyError(ProvenanceError):
    """factor_key failed validation."""


class InvalidOutcomeEventError(ProvenanceError):
    """OutcomeEvent failed validation."""


class EvidenceTooLargeError(ProvenanceError):
    """Evidence exceeds size limit."""


class EvidenceTooDeepError(ProvenanceError):
    """Evidence exceeds nesting depth limit."""


# ============================================================================
# Dataclasses
# ============================================================================


@dataclass(frozen=True, slots=True)
class ProvenanceRecord:
    """Traceable provenance linking pipeline stages."""

    provenance_id: str
    cycle_id: str | None = None
    source_id: str | None = None
    adapter_name: str | None = None
    raw_response_hash: str | None = None
    source_snapshot_hash: str | None = None
    candidate_hash: str | None = None
    risk_assessment_id: str | None = None
    policy_action_id: str | None = None
    approval_id: str | None = None
    simulation_id: str | None = None
    signing_envelope_id: str | None = None
    created_at_utc: str = ""
    schema_version: int = 1


@dataclass(frozen=True, slots=True)
class OutcomeEvent:
    """A dynamic outcome event for future LearningMemory."""

    event_id: str
    provenance_id: str | None
    provenance_source: str
    outcome_type: str
    factor_category: str
    factor_key: str
    factor_label: str
    impact_direction: str
    impact_magnitude: float
    confidence: float
    evidence: dict
    notes: str
    created_at_utc: str
    cycle_id: str | None = None
    candidate_hash: str | None = None
    action_id: str | None = None
    source_id: str | None = None
    protocol: str | None = None
    strategy_type: str | None = None
    risk_score_at_decision: int | None = None
    risk_decision_at_decision: str | None = None
    expected_value: float | None = None
    observed_value: float | None = None
    delta: float | None = None
    schema_version: int = 1


# ============================================================================
# Sanitization helpers
# ============================================================================


def sanitize_factor_key(value: str) -> str:
    """Sanitize factor_key to lowercase snake_case, max 80 chars.

    Replaces non-alphanumeric chars with underscores, collapses multiples,
    strips leading/trailing underscores, lowercases, and truncates.
    """
    if not isinstance(value, str):
        raise InvalidFactorKeyError("factor_key must be a string")
    # Lowercase
    result = value.lower().strip()
    # Replace non-alphanumeric with underscore
    result = re.sub(r"[^a-z0-9_]", "_", result)
    # Collapse multiple underscores
    result = re.sub(r"_+", "_", result)
    # Strip leading/trailing underscores
    result = result.strip("_")
    # Truncate
    if len(result) > _FACTOR_KEY_MAX_LEN:
        result = result[:_FACTOR_KEY_MAX_LEN]
    if not result:
        raise InvalidFactorKeyError("factor_key is empty after sanitization")
    return result


def sanitize_text(value: str, max_len: int = _NOTES_MAX_LEN) -> str:
    """Sanitize and truncate text, redacting secret-like patterns."""
    if not isinstance(value, str):
        return ""
    result = value
    # Redact secret patterns
    for pattern in _SECRET_PATTERNS:
        result = pattern.sub("[REDACTED]", result)
    # Truncate
    if len(result) > max_len:
        result = result[:max_len] + "...[truncated]"
    return result


def _check_depth(obj: Any, current: int = 0, max_depth: int = _EVIDENCE_MAX_DEPTH) -> None:
    """Check nesting depth of a JSON-serializable object."""
    if current > max_depth:
        raise EvidenceTooDeepError(
            f"evidence nesting depth exceeds {max_depth}"
        )
    if isinstance(obj, dict):
        for v in obj.values():
            _check_depth(v, current + 1, max_depth)
    elif isinstance(obj, (list, tuple)):
        for item in obj:
            _check_depth(item, current + 1, max_depth)


def _redact_secrets_recursive(obj: Any, depth: int = 0) -> Any:
    """Recursively redact secret-like values in evidence."""
    if depth > _EVIDENCE_MAX_DEPTH:
        return "[DEPTH_EXCEEDED]"
    if isinstance(obj, str):
        for pattern in _SECRET_PATTERNS:
            if pattern.search(obj):
                return "[REDACTED]"
        return obj
    elif isinstance(obj, dict):
        return {k: _redact_secrets_recursive(v, depth + 1) for k, v in obj.items()}
    elif isinstance(obj, (list, tuple)):
        return [_redact_secrets_recursive(item, depth + 1) for item in obj]
    return obj


def sanitize_evidence(evidence: dict) -> dict:
    """Sanitize evidence dict: check size, depth, redact secrets.

    Raises EvidenceTooLargeError or EvidenceTooDeepError on violation.
    """
    if not isinstance(evidence, dict):
        raise InvalidOutcomeEventError("evidence must be a dict")

    # Check depth
    _check_depth(evidence)

    # Check serializable and size
    try:
        blob = json.dumps(evidence, ensure_ascii=False)
    except (TypeError, ValueError) as e:
        raise InvalidOutcomeEventError(f"evidence not JSON-serializable: {e}")

    if len(blob.encode("utf-8")) > _EVIDENCE_MAX_BYTES:
        raise EvidenceTooLargeError(
            f"evidence exceeds {_EVIDENCE_MAX_BYTES} bytes"
        )

    # Redact secrets
    return _redact_secrets_recursive(evidence)


# ============================================================================
# Validation
# ============================================================================


def validate_outcome_event(event: OutcomeEvent) -> None:
    """Validate an OutcomeEvent. Raises InvalidOutcomeEventError on failure."""
    if not isinstance(event, OutcomeEvent):
        raise InvalidOutcomeEventError("expected OutcomeEvent instance")

    # provenance_source
    if event.provenance_source not in ALLOWED_PROVENANCE_SOURCES:
        raise InvalidOutcomeEventError(
            f"provenance_source={event.provenance_source!r} not in {sorted(ALLOWED_PROVENANCE_SOURCES)}"
        )

    # provenance_id requirement for PIPELINE
    if event.provenance_source == "PIPELINE" and event.provenance_id is None:
        raise InvalidOutcomeEventError(
            "provenance_id is required for PIPELINE provenance_source"
        )

    # outcome_type
    if event.outcome_type not in ALLOWED_OUTCOME_TYPES:
        raise InvalidOutcomeEventError(
            f"outcome_type={event.outcome_type!r} not in {sorted(ALLOWED_OUTCOME_TYPES)}"
        )

    # factor_category
    if event.factor_category not in ALLOWED_FACTOR_CATEGORIES:
        raise InvalidOutcomeEventError(
            f"factor_category={event.factor_category!r} not in {sorted(ALLOWED_FACTOR_CATEGORIES)}"
        )

    # impact_direction
    if event.impact_direction not in ALLOWED_IMPACT_DIRECTIONS:
        raise InvalidOutcomeEventError(
            f"impact_direction={event.impact_direction!r} not in {sorted(ALLOWED_IMPACT_DIRECTIONS)}"
        )

    # factor_key
    if not event.factor_key or len(event.factor_key) > _FACTOR_KEY_MAX_LEN:
        raise InvalidOutcomeEventError(
            f"factor_key must be 1-{_FACTOR_KEY_MAX_LEN} chars"
        )
    if event.factor_key != sanitize_factor_key(event.factor_key):
        raise InvalidOutcomeEventError(
            f"factor_key={event.factor_key!r} is not valid snake_case"
        )

    # factor_label
    if len(event.factor_label) > _FACTOR_LABEL_MAX_LEN:
        raise InvalidOutcomeEventError(
            f"factor_label exceeds {_FACTOR_LABEL_MAX_LEN} chars"
        )

    # impact_magnitude
    if not isinstance(event.impact_magnitude, (int, float)) or isinstance(event.impact_magnitude, bool):
        raise InvalidOutcomeEventError("impact_magnitude must be a number")
    if event.impact_magnitude < 0 or event.impact_magnitude > 1:
        raise InvalidOutcomeEventError(
            f"impact_magnitude={event.impact_magnitude} must be 0-1"
        )

    # confidence
    if not isinstance(event.confidence, (int, float)) or isinstance(event.confidence, bool):
        raise InvalidOutcomeEventError("confidence must be a number")
    if event.confidence < 0 or event.confidence > 1:
        raise InvalidOutcomeEventError(
            f"confidence={event.confidence} must be 0-1"
        )

    # risk_score_at_decision
    if event.risk_score_at_decision is not None:
        if not isinstance(event.risk_score_at_decision, int) or isinstance(event.risk_score_at_decision, bool):
            raise InvalidOutcomeEventError("risk_score_at_decision must be int")
        if event.risk_score_at_decision < 0 or event.risk_score_at_decision > 100:
            raise InvalidOutcomeEventError(
                f"risk_score_at_decision={event.risk_score_at_decision} must be 0-100"
            )

    # risk_decision_at_decision
    if event.risk_decision_at_decision is not None:
        if event.risk_decision_at_decision not in ALLOWED_RISK_DECISIONS:
            raise InvalidOutcomeEventError(
                f"risk_decision_at_decision={event.risk_decision_at_decision!r} "
                f"not in {sorted(ALLOWED_RISK_DECISIONS)}"
            )

    # evidence
    if not isinstance(event.evidence, dict):
        raise InvalidOutcomeEventError("evidence must be a dict")
    # Size and depth checked via sanitize_evidence
    sanitize_evidence(event.evidence)


# ============================================================================
# Serialization
# ============================================================================


def canonical_digest(data: Any) -> str:
    """Compute SHA-256 digest of canonical JSON representation."""
    blob = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def provenance_to_dict(record: ProvenanceRecord) -> dict:
    """Convert ProvenanceRecord to a plain dict."""
    return {
        "schema_version": record.schema_version,
        "provenance_id": record.provenance_id,
        "cycle_id": record.cycle_id,
        "source_id": record.source_id,
        "adapter_name": record.adapter_name,
        "raw_response_hash": record.raw_response_hash,
        "source_snapshot_hash": record.source_snapshot_hash,
        "candidate_hash": record.candidate_hash,
        "risk_assessment_id": record.risk_assessment_id,
        "policy_action_id": record.policy_action_id,
        "approval_id": record.approval_id,
        "simulation_id": record.simulation_id,
        "signing_envelope_id": record.signing_envelope_id,
        "created_at_utc": record.created_at_utc,
    }


def outcome_event_to_dict(event: OutcomeEvent) -> dict:
    """Convert OutcomeEvent to a plain dict."""
    return {
        "schema_version": event.schema_version,
        "event_id": event.event_id,
        "provenance_id": event.provenance_id,
        "provenance_source": event.provenance_source,
        "cycle_id": event.cycle_id,
        "candidate_hash": event.candidate_hash,
        "action_id": event.action_id,
        "source_id": event.source_id,
        "protocol": event.protocol,
        "strategy_type": event.strategy_type,
        "risk_score_at_decision": event.risk_score_at_decision,
        "risk_decision_at_decision": event.risk_decision_at_decision,
        "outcome_type": event.outcome_type,
        "factor_category": event.factor_category,
        "factor_key": event.factor_key,
        "factor_label": event.factor_label,
        "impact_direction": event.impact_direction,
        "impact_magnitude": event.impact_magnitude,
        "confidence": event.confidence,
        "expected_value": event.expected_value,
        "observed_value": event.observed_value,
        "delta": event.delta,
        "evidence": event.evidence,
        "notes": event.notes,
        "created_at_utc": event.created_at_utc,
    }


# ============================================================================
# Persistence (append-only JSONL)
# ============================================================================


def append_provenance(path: Path | str, record: ProvenanceRecord) -> None:
    """Append a ProvenanceRecord to the provenance ledger (JSONL)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(
        provenance_to_dict(record),
        sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    )
    with open(p, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def append_outcome_event(path: Path | str, event: OutcomeEvent) -> None:
    """Append an OutcomeEvent to the outcome events ledger (JSONL).

    Validates the event before appending.
    """
    validate_outcome_event(event)
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(
        outcome_event_to_dict(event),
        sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    )
    with open(p, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def load_outcome_events(path: Path | str) -> list[dict]:
    """Load all outcome events from a JSONL file. Returns list of dicts."""
    p = Path(path)
    if not p.exists():
        return []
    events: list[dict] = []
    try:
        for line in p.read_text(encoding="utf-8").strip().split("\n"):
            if line.strip():
                events.append(json.loads(line))
    except (json.JSONDecodeError, OSError):
        pass
    return events


__all__ = [
    "ALLOWED_FACTOR_CATEGORIES",
    "ALLOWED_IMPACT_DIRECTIONS",
    "ALLOWED_OUTCOME_TYPES",
    "ALLOWED_PROVENANCE_SOURCES",
    "ALLOWED_RISK_DECISIONS",
    "EvidenceTooDeepError",
    "EvidenceTooLargeError",
    "InvalidFactorKeyError",
    "InvalidOutcomeEventError",
    "OutcomeEvent",
    "ProvenanceError",
    "ProvenanceRecord",
    "append_outcome_event",
    "append_provenance",
    "canonical_digest",
    "load_outcome_events",
    "outcome_event_to_dict",
    "provenance_to_dict",
    "sanitize_evidence",
    "sanitize_factor_key",
    "sanitize_text",
    "validate_outcome_event",
]
