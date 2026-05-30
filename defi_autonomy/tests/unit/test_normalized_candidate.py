"""Unit tests for defi_autonomy.schemas.normalized_candidate.

Phase 1.1 scope. These tests are deterministic and offline. No network calls.
No signing. No key loading.
"""

from __future__ import annotations

import dataclasses
import pytest

from defi_autonomy.schemas.normalized_candidate import (
    ALLOWED_CHAINS,
    ALLOWED_STRATEGY_TYPES,
    NORMALIZED_CANDIDATE_SCHEMA,
    NormalizedCandidate,
    NormalizedCandidateError,
    from_dict,
    validate_candidate,
)


def _valid_dict() -> dict:
    """A baseline fixture dict guaranteed to pass schema validation."""
    return {
        "chain": "Base",
        "protocol": "aave_v3",
        "venue": "aave_v3",
        "venue_id": "base:aave_v3:usdc",
        "pool_address": "0x" + "a" * 40,
        "token_addresses": ["0x" + "b" * 40, "0x" + "c" * 40],
        "strategy_type": "stablecoin_lending",
        "advertised_apy": 4.25,
        "fee_apr": 0.5,
        "reward_apr": 1.0,
        "tvl_usd": 12_500_000.0,
        "volume_24h_usd": 250_000.0,
        "liquidity_depth_usd": 1_000_000.0,
        "source_id": "defillama",
        "source_url": "https://example.invalid/yields/aave_v3_usdc",
        "source_timestamp_utc": "2026-05-26T00:00:00Z",
        "fetched_at_utc": "2026-05-26T00:00:01Z",
        "adapter_name": "defillama_adapter",
        "data_freshness_seconds": 30,
        "source_confidence_score": 0.8,
        "stale_data": False,
    }


def _valid_candidate() -> NormalizedCandidate:
    return from_dict(_valid_dict())


def test_schema_additional_properties_is_false() -> None:
    assert NORMALIZED_CANDIDATE_SCHEMA["additionalProperties"] is False


def test_schema_required_covers_every_field() -> None:
    field_names = {f.name for f in dataclasses.fields(NormalizedCandidate)}
    assert set(NORMALIZED_CANDIDATE_SCHEMA["required"]) == field_names


def test_schema_constants_align_with_module_constants() -> None:
    assert NORMALIZED_CANDIDATE_SCHEMA["properties"]["chain"]["enum"] == list(ALLOWED_CHAINS)
    assert NORMALIZED_CANDIDATE_SCHEMA["properties"]["strategy_type"]["enum"] == list(
        ALLOWED_STRATEGY_TYPES
    )


def test_valid_candidate_passes_validation() -> None:
    c = _valid_candidate()
    validate_candidate(c)  # must not raise


def test_token_addresses_is_tuple_after_construction() -> None:
    c = _valid_candidate()
    assert isinstance(c.token_addresses, tuple)


def test_to_dict_serializes_tuple_as_list() -> None:
    c = _valid_candidate()
    d = c.to_dict()
    assert isinstance(d["token_addresses"], list)
    assert d["token_addresses"] == ["0x" + "b" * 40, "0x" + "c" * 40]


def test_from_dict_rejects_unknown_field() -> None:
    bad = _valid_dict()
    bad["surprise_field"] = "nope"
    with pytest.raises(NormalizedCandidateError) as exc:
        from_dict(bad)
    assert "unknown fields" in str(exc.value)
    assert "surprise_field" in str(exc.value)


def test_from_dict_rejects_missing_field() -> None:
    bad = _valid_dict()
    del bad["chain"]
    with pytest.raises(NormalizedCandidateError) as exc:
        from_dict(bad)
    assert "missing required fields" in str(exc.value)
    assert "chain" in str(exc.value)


def test_invalid_chain_rejected_by_schema() -> None:
    bad = _valid_dict()
    bad["chain"] = "Ethereum"  # not in ALLOWED_CHAINS
    with pytest.raises(NormalizedCandidateError) as exc:
        from_dict(bad)
    assert "/chain" in str(exc.value)


def test_invalid_strategy_type_rejected_by_schema() -> None:
    bad = _valid_dict()
    bad["strategy_type"] = "leveraged_yield_farming"
    with pytest.raises(NormalizedCandidateError) as exc:
        from_dict(bad)
    assert "/strategy_type" in str(exc.value)


def test_apy_below_minus_one_rejected() -> None:
    bad = _valid_dict()
    bad["advertised_apy"] = -1.5
    with pytest.raises(NormalizedCandidateError):
        from_dict(bad)


def test_apy_above_hundred_rejected() -> None:
    bad = _valid_dict()
    bad["advertised_apy"] = 150.0
    with pytest.raises(NormalizedCandidateError):
        from_dict(bad)


def test_fee_apr_negative_rejected() -> None:
    bad = _valid_dict()
    bad["fee_apr"] = -0.1
    with pytest.raises(NormalizedCandidateError):
        from_dict(bad)


def test_reward_apr_above_hundred_rejected() -> None:
    bad = _valid_dict()
    bad["reward_apr"] = 250.0
    with pytest.raises(NormalizedCandidateError):
        from_dict(bad)


def test_source_confidence_above_one_rejected() -> None:
    bad = _valid_dict()
    bad["source_confidence_score"] = 1.5
    with pytest.raises(NormalizedCandidateError):
        from_dict(bad)


def test_source_confidence_below_zero_rejected() -> None:
    bad = _valid_dict()
    bad["source_confidence_score"] = -0.1
    with pytest.raises(NormalizedCandidateError):
        from_dict(bad)


def test_data_freshness_negative_rejected() -> None:
    bad = _valid_dict()
    bad["data_freshness_seconds"] = -1
    with pytest.raises(NormalizedCandidateError):
        from_dict(bad)


def test_token_addresses_max_items() -> None:
    bad = _valid_dict()
    bad["token_addresses"] = ["0x" + str(i) * 40 for i in range(5)]
    with pytest.raises(NormalizedCandidateError):
        from_dict(bad)


def test_token_addresses_unique() -> None:
    bad = _valid_dict()
    bad["token_addresses"] = ["0x" + "b" * 40, "0x" + "b" * 40]
    with pytest.raises(NormalizedCandidateError):
        from_dict(bad)


def test_pool_address_can_be_null() -> None:
    d = _valid_dict()
    d["pool_address"] = None
    c = from_dict(d)
    assert c.pool_address is None


def test_stale_data_true_still_validates() -> None:
    """R24.13 + R-EXT.1: schema must accept stale_data=True; rejection is the
    Policy_Engine's job, not the schema's."""
    d = _valid_dict()
    d["stale_data"] = True
    c = from_dict(d)
    assert c.stale_data is True


def test_canonical_json_is_deterministic() -> None:
    c = _valid_candidate()
    assert c.canonical_json() == c.canonical_json()


def test_canonical_json_uses_sorted_keys_and_compact_separators() -> None:
    c = _valid_candidate()
    blob = c.canonical_json()
    text = blob.decode("utf-8")
    # No spaces between separators (compact).
    assert ", " not in text
    assert ": " not in text
    # Top-level keys appear in lexicographic order.
    import json as _json
    parsed = _json.loads(text)
    keys = list(parsed.keys())
    assert keys == sorted(keys)


def test_hash_sha256_is_deterministic() -> None:
    c = _valid_candidate()
    assert c.hash_sha256() == c.hash_sha256()


def test_hash_sha256_changes_on_content_change() -> None:
    c1 = _valid_candidate()
    d2 = _valid_dict()
    d2["advertised_apy"] = 4.26  # one bp different
    c2 = from_dict(d2)
    assert c1.hash_sha256() != c2.hash_sha256()


def test_dataclass_is_frozen() -> None:
    c = _valid_candidate()
    with pytest.raises(dataclasses.FrozenInstanceError):
        c.advertised_apy = 99.0  # type: ignore[misc]


def test_from_dict_rejects_non_dict() -> None:
    with pytest.raises(NormalizedCandidateError):
        from_dict("not a dict")  # type: ignore[arg-type]


def test_from_dict_rejects_non_list_token_addresses() -> None:
    bad = _valid_dict()
    bad["token_addresses"] = "0xabc"
    with pytest.raises(NormalizedCandidateError):
        from_dict(bad)


def test_validate_candidate_rejects_non_candidate() -> None:
    with pytest.raises(NormalizedCandidateError):
        validate_candidate({"chain": "Base"})  # type: ignore[arg-type]