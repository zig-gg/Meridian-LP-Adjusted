"""LearningMemory — Phase 5.4B.

Consumes dynamic OutcomeEvent records, aggregates factor signals, and exposes
advisory scoring bias to RiskScorer. Bias is clamped by
risk_policy["learning_bias_clamp_points"] (default 5).

Advisory-only. NEVER modifies:
- risk_policy.json, allowlists, autonomy_level, wallet caps, blocked actions,
  PolicyEngine hard rules, broadcast settings, or private-key handling.

No network calls. No signing. No broadcast.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from defi_autonomy.provenance import (
    OutcomeEvent,
    load_outcome_events as _load_raw_events,
    sanitize_text,
)
from defi_autonomy.schemas.normalized_candidate import NormalizedCandidate

# ============================================================================
# Constants
# ============================================================================

_DEFAULT_CLAMP_POINTS = 5


# ============================================================================
# LearningBias dataclass
# ============================================================================


@dataclass(frozen=True, slots=True)
class LearningBias:
    """Advisory scoring bias derived from historical outcome events."""

    source_id: str | None
    protocol: str | None
    strategy_type: str | None
    factor_key: str | None
    score_adjustment: int
    confidence_adjustment: float
    evidence_count: int
    reasons: tuple[str, ...]
    updated_at_utc: str


# ============================================================================
# Event loading
# ============================================================================


def load_outcome_events(path: Path | str) -> list[dict]:
    """Load outcome events from JSONL. Returns list of dicts."""
    return _load_raw_events(path)


# ============================================================================
# Bias derivation
# ============================================================================


def derive_learning_bias(
    events: list[dict],
    source_id: str | None = None,
    protocol: str | None = None,
    strategy_type: str | None = None,
    factor_key: str | None = None,
    clamp_points: int = _DEFAULT_CLAMP_POINTS,
) -> LearningBias:
    """Derive a LearningBias from filtered outcome events.

    Filters events by source_id, protocol, strategy_type, and factor_key
    (any None filter is ignored). Aggregates impact signals into a clamped
    score adjustment.
    """
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Filter events
    filtered = _filter_events(events, source_id, protocol, strategy_type, factor_key)

    if not filtered:
        return LearningBias(
            source_id=source_id,
            protocol=protocol,
            strategy_type=strategy_type,
            factor_key=factor_key,
            score_adjustment=0,
            confidence_adjustment=0.0,
            evidence_count=0,
            reasons=(),
            updated_at_utc=now,
        )

    # Aggregate signals
    total_signal = 0.0
    total_confidence = 0.0
    reasons: list[str] = []

    for evt in filtered:
        direction = evt.get("impact_direction", "NEUTRAL")
        magnitude = float(evt.get("impact_magnitude", 0) or 0)
        confidence = float(evt.get("confidence", 0) or 0)

        # Clamp inputs
        magnitude = max(0.0, min(1.0, magnitude))
        confidence = max(0.0, min(1.0, confidence))

        weighted = magnitude * confidence

        if direction == "NEGATIVE":
            total_signal -= weighted
        elif direction == "POSITIVE":
            total_signal += weighted * 0.5  # Positive bias is milder
        # NEUTRAL contributes nothing

        total_confidence += confidence

    # Convert signal to score adjustment
    # Scale: each event contributes up to ±0.5 points (magnitude * confidence)
    # Multiple events accumulate but are clamped
    evidence_count = len(filtered)
    raw_adjustment = total_signal

    # Round to integer
    score_adjustment = int(round(raw_adjustment))

    # Clamp
    score_adjustment = max(-clamp_points, min(clamp_points, score_adjustment))

    # Average confidence
    avg_confidence = total_confidence / evidence_count if evidence_count > 0 else 0.0

    # Build reasons (sanitized, no secrets)
    if score_adjustment < 0:
        reasons.append(
            f"learning: {evidence_count} negative signal(s), adjustment={score_adjustment}"
        )
    elif score_adjustment > 0:
        reasons.append(
            f"learning: {evidence_count} positive signal(s), adjustment=+{score_adjustment}"
        )

    return LearningBias(
        source_id=source_id,
        protocol=protocol,
        strategy_type=strategy_type,
        factor_key=factor_key,
        score_adjustment=score_adjustment,
        confidence_adjustment=round(avg_confidence, 4),
        evidence_count=evidence_count,
        reasons=tuple(reasons),
        updated_at_utc=now,
    )


def get_bias_for_candidate(
    candidate: NormalizedCandidate,
    events: list[dict],
    clamp_points: int = _DEFAULT_CLAMP_POINTS,
) -> LearningBias:
    """Get the combined learning bias for a specific candidate.

    Aggregates events matching the candidate's source_id, protocol, and
    strategy_type. Returns a single combined LearningBias.
    """
    # Collect all relevant events (matching any of the candidate's attributes)
    relevant = _filter_events(
        events,
        source_id=candidate.source_id,
        protocol=candidate.protocol,
        strategy_type=candidate.strategy_type,
        factor_key=None,  # Accept all factor_keys
    )

    if not relevant:
        return LearningBias(
            source_id=candidate.source_id,
            protocol=candidate.protocol,
            strategy_type=candidate.strategy_type,
            factor_key=None,
            score_adjustment=0,
            confidence_adjustment=0.0,
            evidence_count=0,
            reasons=(),
            updated_at_utc=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )

    return derive_learning_bias(
        relevant,
        source_id=candidate.source_id,
        protocol=candidate.protocol,
        strategy_type=candidate.strategy_type,
        factor_key=None,
        clamp_points=clamp_points,
    )


def _filter_events(
    events: list[dict],
    source_id: str | None = None,
    protocol: str | None = None,
    strategy_type: str | None = None,
    factor_key: str | None = None,
) -> list[dict]:
    """Filter events by optional criteria. None means no filter on that field."""
    filtered: list[dict] = []
    for evt in events:
        if not isinstance(evt, dict):
            continue
        if source_id and evt.get("source_id") != source_id:
            continue
        if protocol and evt.get("protocol") != protocol:
            continue
        if strategy_type and evt.get("strategy_type") != strategy_type:
            continue
        if factor_key and evt.get("factor_key") != factor_key:
            continue
        filtered.append(evt)
    return filtered


# ============================================================================
# Bias application
# ============================================================================


def apply_learning_bias(
    base_score: int,
    bias: LearningBias,
    clamp_points: int = _DEFAULT_CLAMP_POINTS,
) -> int:
    """Apply learning bias to a base score. Returns adjusted score clamped 0-100.

    The adjustment itself is clamped to ±clamp_points before application.
    """
    adjustment = max(-clamp_points, min(clamp_points, bias.score_adjustment))
    adjusted = base_score + adjustment
    return max(0, min(100, adjusted))


# ============================================================================
# Serialization
# ============================================================================


def learning_bias_to_dict(bias: LearningBias) -> dict:
    """Convert LearningBias to a plain dict."""
    return {
        "source_id": bias.source_id,
        "protocol": bias.protocol,
        "strategy_type": bias.strategy_type,
        "factor_key": bias.factor_key,
        "score_adjustment": bias.score_adjustment,
        "confidence_adjustment": bias.confidence_adjustment,
        "evidence_count": bias.evidence_count,
        "reasons": list(bias.reasons),
        "updated_at_utc": bias.updated_at_utc,
    }


__all__ = [
    "LearningBias",
    "apply_learning_bias",
    "derive_learning_bias",
    "get_bias_for_candidate",
    "learning_bias_to_dict",
    "load_outcome_events",
]
