"""Unit tests for defi_autonomy.provenance — Phase 5.4A.

All tests are deterministic and offline. No network calls. No signing.
No key loading. No broadcast. No policy/allowlist mutation.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from defi_autonomy.provenance import (
    ALLOWED_FACTOR_CATEGORIES,
    ALLOWED_IMPACT_DIRECTIONS,
    ALLOWED_OUTCOME_TYPES,
    ALLOWED_PROVENANCE_SOURCES,
    EvidenceTooDeepError,
    EvidenceTooLargeError,
    InvalidFactorKeyError,
    InvalidOutcomeEventError,
    OutcomeEvent,
    ProvenanceRecord,
    append_outcome_event,
    append_provenance,
    canonical_digest,
    load_outcome_events,
    outcome_event_to_dict,
    provenance_to_dict,
    sanitize_evidence,
    sanitize_factor_key,
    sanitize_text,
    validate_outcome_event,
)


# ============================================================================
# Fixtures
# ============================================================================


def _valid_provenance(**overrides) -> ProvenanceRecord:
    defaults = {
        "provenance_id": "prov_001",
        "cycle_id": "cycle_001",
        "source_id": "defillama",
        "adapter_name": "defillama_adapter",
        "candidate_hash": "a" * 64,
        "created_at_utc": "2026-05-27T00:00:00Z",
    }
    defaults.update(overrides)
    return ProvenanceRecord(**defaults)


def _valid_outcome(**overrides) -> OutcomeEvent:
    defaults = {
        "event_id": "evt_001",
        "provenance_id": "prov_001",
        "provenance_source": "PIPELINE",
        "outcome_type": "REALIZED_PNL",
        "factor_category": "YIELD_RISK",
        "factor_key": "apr_decay",
        "factor_label": "APR decayed below threshold",
        "impact_direction": "NEGATIVE",
        "impact_magnitude": 0.6,
        "confidence": 0.8,
        "evidence": {"expected_apr": 4.5, "observed_apr": 2.1},
        "notes": "APR dropped significantly after 48h",
        "created_at_utc": "2026-05-27T00:00:00Z",
    }
    defaults.update(overrides)
    return OutcomeEvent(**defaults)


# ============================================================================
# Tests: append provenance record
# ============================================================================


class TestAppendProvenance:
    """Append provenance record to JSONL."""

    def test_appends_record(self, tmp_path: Path) -> None:
        p = tmp_path / "provenance_ledger.jsonl"
        record = _valid_provenance()
        append_provenance(p, record)
        assert p.exists()
        lines = p.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 1
        data = json.loads(lines[0])
        assert data["provenance_id"] == "prov_001"

    def test_appends_multiple(self, tmp_path: Path) -> None:
        p = tmp_path / "provenance_ledger.jsonl"
        append_provenance(p, _valid_provenance(provenance_id="p1"))
        append_provenance(p, _valid_provenance(provenance_id="p2"))
        lines = p.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 2


# ============================================================================
# Tests: append dynamic outcome event
# ============================================================================


class TestAppendOutcomeEvent:
    """Append dynamic outcome event to JSONL."""

    def test_appends_event(self, tmp_path: Path) -> None:
        p = tmp_path / "outcome_events.jsonl"
        event = _valid_outcome()
        append_outcome_event(p, event)
        assert p.exists()
        lines = p.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 1
        data = json.loads(lines[0])
        assert data["event_id"] == "evt_001"
        assert data["factor_key"] == "apr_decay"

    def test_appends_multiple(self, tmp_path: Path) -> None:
        p = tmp_path / "outcome_events.jsonl"
        append_outcome_event(p, _valid_outcome(event_id="e1"))
        append_outcome_event(p, _valid_outcome(event_id="e2"))
        lines = p.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 2


# ============================================================================
# Tests: load outcome events
# ============================================================================


class TestLoadOutcomeEvents:
    """Load outcome events from JSONL."""

    def test_loads_events(self, tmp_path: Path) -> None:
        p = tmp_path / "outcome_events.jsonl"
        append_outcome_event(p, _valid_outcome(event_id="e1"))
        append_outcome_event(p, _valid_outcome(event_id="e2"))
        events = load_outcome_events(p)
        assert len(events) == 2
        assert events[0]["event_id"] == "e1"

    def test_empty_file_returns_empty(self, tmp_path: Path) -> None:
        p = tmp_path / "nonexistent.jsonl"
        assert load_outcome_events(p) == []


# ============================================================================
# Tests: schema_version
# ============================================================================


class TestSchemaVersion:
    """schema_version present in serialized output."""

    def test_provenance_has_schema_version(self) -> None:
        record = _valid_provenance()
        d = provenance_to_dict(record)
        assert d["schema_version"] == 1

    def test_outcome_has_schema_version(self) -> None:
        event = _valid_outcome()
        d = outcome_event_to_dict(event)
        assert d["schema_version"] == 1


# ============================================================================
# Tests: dynamic factor validation
# ============================================================================


class TestDynamicFactorValidation:
    """Dynamic factor_key and factor_category validation."""

    def test_valid_dynamic_factor_accepted(self) -> None:
        event = _valid_outcome(factor_key="new_custom_factor")
        validate_outcome_event(event)  # must not raise

    def test_unknown_factor_key_accepted_if_valid(self) -> None:
        event = _valid_outcome(factor_key="completely_novel_risk_signal")
        validate_outcome_event(event)  # must not raise

    def test_factor_key_sanitized_to_snake_case(self) -> None:
        assert sanitize_factor_key("APR Decay") == "apr_decay"
        assert sanitize_factor_key("High-Risk Factor!") == "high_risk_factor"
        assert sanitize_factor_key("  UPPER_CASE  ") == "upper_case"

    def test_factor_key_max_length_enforced(self) -> None:
        long_key = "a" * 100
        result = sanitize_factor_key(long_key)
        assert len(result) <= 80

    def test_factor_key_empty_after_sanitize_raises(self) -> None:
        with pytest.raises(InvalidFactorKeyError):
            sanitize_factor_key("!!!")

    def test_factor_label_max_length_enforced(self) -> None:
        event = _valid_outcome(factor_label="x" * 201)
        with pytest.raises(InvalidOutcomeEventError, match="factor_label"):
            validate_outcome_event(event)


# ============================================================================
# Tests: provenance_source validation
# ============================================================================


class TestProvenanceSource:
    """provenance_source validation."""

    def test_valid_sources_accepted(self) -> None:
        for src in ALLOWED_PROVENANCE_SOURCES:
            event = _valid_outcome(provenance_source=src,
                                   provenance_id="p1" if src == "PIPELINE" else None)
            validate_outcome_event(event)

    def test_invalid_source_rejected(self) -> None:
        event = _valid_outcome(provenance_source="INVALID")
        with pytest.raises(InvalidOutcomeEventError, match="provenance_source"):
            validate_outcome_event(event)

    def test_provenance_id_none_accepted_for_operator_manual(self) -> None:
        event = _valid_outcome(provenance_id=None, provenance_source="OPERATOR_MANUAL")
        validate_outcome_event(event)  # must not raise

    def test_provenance_id_none_accepted_for_external_signal(self) -> None:
        event = _valid_outcome(provenance_id=None, provenance_source="EXTERNAL_SIGNAL")
        validate_outcome_event(event)  # must not raise

    def test_provenance_id_none_rejected_for_pipeline(self) -> None:
        event = _valid_outcome(provenance_id=None, provenance_source="PIPELINE")
        with pytest.raises(InvalidOutcomeEventError, match="provenance_id is required"):
            validate_outcome_event(event)


# ============================================================================
# Tests: outcome_type validation
# ============================================================================


class TestOutcomeType:
    """outcome_type validation."""

    def test_valid_outcome_types_accepted(self) -> None:
        for ot in ALLOWED_OUTCOME_TYPES:
            event = _valid_outcome(outcome_type=ot)
            validate_outcome_event(event)

    def test_invalid_outcome_type_rejected(self) -> None:
        event = _valid_outcome(outcome_type="INVALID_TYPE")
        with pytest.raises(InvalidOutcomeEventError, match="outcome_type"):
            validate_outcome_event(event)


# ============================================================================
# Tests: impact_direction validation
# ============================================================================


class TestImpactDirection:
    """impact_direction validation."""

    def test_invalid_direction_rejected(self) -> None:
        event = _valid_outcome(impact_direction="UP")
        with pytest.raises(InvalidOutcomeEventError, match="impact_direction"):
            validate_outcome_event(event)


# ============================================================================
# Tests: factor_category validation
# ============================================================================


class TestFactorCategory:
    """factor_category validation."""

    def test_invalid_category_rejected(self) -> None:
        event = _valid_outcome(factor_category="INVALID_CAT")
        with pytest.raises(InvalidOutcomeEventError, match="factor_category"):
            validate_outcome_event(event)

    def test_unknown_factor_category_accepted(self) -> None:
        event = _valid_outcome(factor_category="UNKNOWN_FACTOR")
        validate_outcome_event(event)  # must not raise


# ============================================================================
# Tests: magnitude and confidence bounds
# ============================================================================


class TestMagnitudeConfidence:
    """impact_magnitude and confidence bounds."""

    def test_magnitude_below_zero_rejected(self) -> None:
        event = _valid_outcome(impact_magnitude=-0.1)
        with pytest.raises(InvalidOutcomeEventError, match="impact_magnitude"):
            validate_outcome_event(event)

    def test_magnitude_above_one_rejected(self) -> None:
        event = _valid_outcome(impact_magnitude=1.1)
        with pytest.raises(InvalidOutcomeEventError, match="impact_magnitude"):
            validate_outcome_event(event)

    def test_confidence_below_zero_rejected(self) -> None:
        event = _valid_outcome(confidence=-0.1)
        with pytest.raises(InvalidOutcomeEventError, match="confidence"):
            validate_outcome_event(event)

    def test_confidence_above_one_rejected(self) -> None:
        event = _valid_outcome(confidence=1.1)
        with pytest.raises(InvalidOutcomeEventError, match="confidence"):
            validate_outcome_event(event)

    def test_boundaries_accepted(self) -> None:
        event = _valid_outcome(impact_magnitude=0.0, confidence=1.0)
        validate_outcome_event(event)
        event2 = _valid_outcome(impact_magnitude=1.0, confidence=0.0)
        validate_outcome_event(event2)


# ============================================================================
# Tests: risk_score_at_decision and risk_decision_at_decision
# ============================================================================


class TestRiskDecisionFields:
    """risk_score_at_decision and risk_decision_at_decision validation."""

    def test_risk_score_below_zero_rejected(self) -> None:
        event = _valid_outcome(risk_score_at_decision=-1)
        with pytest.raises(InvalidOutcomeEventError, match="risk_score_at_decision"):
            validate_outcome_event(event)

    def test_risk_score_above_100_rejected(self) -> None:
        event = _valid_outcome(risk_score_at_decision=101)
        with pytest.raises(InvalidOutcomeEventError, match="risk_score_at_decision"):
            validate_outcome_event(event)

    def test_invalid_risk_decision_rejected(self) -> None:
        event = _valid_outcome(risk_decision_at_decision="HOLD")
        with pytest.raises(InvalidOutcomeEventError, match="risk_decision_at_decision"):
            validate_outcome_event(event)

    def test_valid_risk_fields_accepted(self) -> None:
        event = _valid_outcome(risk_score_at_decision=85, risk_decision_at_decision="FARM")
        validate_outcome_event(event)

    def test_none_risk_fields_accepted(self) -> None:
        event = _valid_outcome(risk_score_at_decision=None, risk_decision_at_decision=None)
        validate_outcome_event(event)


# ============================================================================
# Tests: evidence validation
# ============================================================================


class TestEvidenceValidation:
    """Evidence size, depth, and secret sanitization."""

    def test_evidence_must_be_dict(self) -> None:
        event = _valid_outcome(evidence="not a dict")  # type: ignore
        with pytest.raises(InvalidOutcomeEventError, match="evidence must be a dict"):
            validate_outcome_event(event)

    def test_evidence_size_limit_enforced(self) -> None:
        big_evidence = {"data": "x" * 11_000}
        with pytest.raises(EvidenceTooLargeError):
            sanitize_evidence(big_evidence)

    def test_evidence_max_depth_enforced(self) -> None:
        deep = {"a": {"b": {"c": {"d": "too deep"}}}}
        with pytest.raises(EvidenceTooDeepError):
            sanitize_evidence(deep)

    def test_evidence_depth_3_accepted(self) -> None:
        ok = {"a": {"b": {"c": "fine"}}}
        result = sanitize_evidence(ok)
        assert result == ok

    def test_evidence_secret_redaction(self) -> None:
        evidence = {
            "key": "a" * 64,  # looks like a private key (64 hex chars)
            "normal": "hello",
        }
        result = sanitize_evidence(evidence)
        assert result["key"] == "[REDACTED]"
        assert result["normal"] == "hello"

    def test_evidence_bearer_token_redacted(self) -> None:
        evidence = {"auth": "Bearer sk-abc123xyz456"}
        result = sanitize_evidence(evidence)
        assert "[REDACTED]" in result["auth"]

    def test_valid_evidence_passes(self) -> None:
        evidence = {"apr": 4.5, "tvl": 1000000, "chain": "Base"}
        result = sanitize_evidence(evidence)
        assert result == evidence


# ============================================================================
# Tests: notes sanitization
# ============================================================================


class TestNotesSanitization:
    """Notes truncated and sanitized."""

    def test_notes_truncated(self) -> None:
        long_notes = "x" * 600
        result = sanitize_text(long_notes)
        assert len(result) <= 520  # 500 + truncation marker
        assert "[truncated]" in result

    def test_notes_secret_redacted(self) -> None:
        notes = f"Key was {'a' * 64} and it failed"
        result = sanitize_text(notes)
        assert "[REDACTED]" in result
        assert "a" * 64 not in result

    def test_normal_notes_unchanged(self) -> None:
        notes = "APR dropped from 4.5 to 2.1 after 48 hours"
        result = sanitize_text(notes)
        assert result == notes


# ============================================================================
# Tests: canonical digest
# ============================================================================


class TestCanonicalDigest:
    """Canonical digest is deterministic."""

    def test_deterministic(self) -> None:
        data = {"a": 1, "b": 2}
        d1 = canonical_digest(data)
        d2 = canonical_digest(data)
        assert d1 == d2
        assert len(d1) == 64

    def test_two_identical_events_same_digest(self) -> None:
        e1 = outcome_event_to_dict(_valid_outcome())
        e2 = outcome_event_to_dict(_valid_outcome())
        assert canonical_digest(e1) == canonical_digest(e2)

    def test_different_data_different_digest(self) -> None:
        d1 = canonical_digest({"x": 1})
        d2 = canonical_digest({"x": 2})
        assert d1 != d2


# ============================================================================
# Tests: no policy/allowlist mutation
# ============================================================================


class TestNoMutation:
    """No risk_policy or allowlist mutation."""

    def test_no_policy_mutation_in_module(self) -> None:
        import defi_autonomy.provenance as mod
        with open(mod.__file__, "r") as f:
            source = f.read()
        # The module should not import or write to policy/allowlist files
        # It only mentions them in the docstring as things it does NOT modify
        assert "import risk_policy" not in source
        assert "write_json_atomic" not in source
        assert ".write_text" not in source


# ============================================================================
# Tests: no network/signing/broadcast
# ============================================================================


class TestNoNetworkNoSigning:
    """No network calls, signing, or broadcast."""

    def test_no_network_calls(self, tmp_path: Path) -> None:
        import socket
        from unittest.mock import patch

        with patch.object(
            socket,
            "create_connection",
            side_effect=AssertionError("network call detected"),
        ):
            p = tmp_path / "test.jsonl"
            append_outcome_event(p, _valid_outcome())
            events = load_outcome_events(p)
            assert len(events) == 1

    def test_no_signing_imports(self) -> None:
        import defi_autonomy.provenance as mod
        with open(mod.__file__, "r") as f:
            source = f.read()
        # Check for actual imports of signing libraries (not comment mentions)
        forbidden_imports = (
            "from eth_account",
            "import eth_account",
            "from solders",
            "import solders",
            "from nacl",
            "import nacl",
            "import WalletExecutor",
            "import TxSimulator",
        )
        for term in forbidden_imports:
            assert term not in source, f"forbidden import {term!r} found"

    def test_no_daemon_modification(self) -> None:
        import defi_autonomy.provenance as mod
        with open(mod.__file__, "r") as f:
            source = f.read()
        assert "ecosystem.defi.cjs" not in source
        assert "pm2" not in source.lower()
