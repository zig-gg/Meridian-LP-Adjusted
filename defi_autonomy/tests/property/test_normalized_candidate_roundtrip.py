"""Property-based tests for NormalizedCandidate.

Phase 1.1 scope. No network calls. No signing. No key loading.

Properties verified:
- to_dict / from_dict round-trip preserves equality.
- canonical_json then JSON-decode preserves the field-wise content.
- canonical_json is deterministic across repeated calls.
- hash_sha256 is deterministic and content-sensitive.
"""

from __future__ import annotations

import json

from hypothesis import given, settings, strategies as st

from defi_autonomy.schemas.normalized_candidate import (
    ALLOWED_CHAINS,
    ALLOWED_STRATEGY_TYPES,
    NormalizedCandidate,
    from_dict,
)


# Bounded text strategy that satisfies the schema's minLength/maxLength bounds.
_text = st.text(
    alphabet=st.characters(min_codepoint=33, max_codepoint=126),
    min_size=1,
    max_size=32,
)


@st.composite
def candidates(draw: st.DrawFn) -> NormalizedCandidate:
    """A Hypothesis strategy that produces schema-valid NormalizedCandidate values."""
    n_tokens = draw(st.integers(min_value=1, max_value=4))
    token_pool = draw(
        st.lists(_text, min_size=n_tokens, max_size=n_tokens, unique=True)
    )
    return from_dict(
        {
            "chain": draw(st.sampled_from(ALLOWED_CHAINS)),
            "protocol": draw(_text),
            "venue": draw(_text),
            "venue_id": draw(_text),
            "pool_address": draw(st.one_of(st.none(), _text)),
            "token_addresses": token_pool,
            "strategy_type": draw(st.sampled_from(ALLOWED_STRATEGY_TYPES)),
            "advertised_apy": draw(
                st.floats(
                    min_value=-1.0,
                    max_value=100.0,
                    allow_nan=False,
                    allow_infinity=False,
                )
            ),
            "fee_apr": draw(
                st.floats(
                    min_value=0.0,
                    max_value=100.0,
                    allow_nan=False,
                    allow_infinity=False,
                )
            ),
            "reward_apr": draw(
                st.floats(
                    min_value=0.0,
                    max_value=100.0,
                    allow_nan=False,
                    allow_infinity=False,
                )
            ),
            "tvl_usd": draw(
                st.floats(
                    min_value=0.0,
                    max_value=1e12,
                    allow_nan=False,
                    allow_infinity=False,
                )
            ),
            "volume_24h_usd": draw(
                st.floats(
                    min_value=0.0,
                    max_value=1e12,
                    allow_nan=False,
                    allow_infinity=False,
                )
            ),
            "liquidity_depth_usd": draw(
                st.floats(
                    min_value=0.0,
                    max_value=1e12,
                    allow_nan=False,
                    allow_infinity=False,
                )
            ),
            "source_id": draw(_text),
            "source_url": draw(_text),
            "source_timestamp_utc": draw(_text),
            "fetched_at_utc": draw(_text),
            "adapter_name": draw(_text),
            "data_freshness_seconds": draw(
                st.integers(min_value=0, max_value=10**9)
            ),
            "source_confidence_score": draw(
                st.floats(
                    min_value=0.0,
                    max_value=1.0,
                    allow_nan=False,
                    allow_infinity=False,
                )
            ),
            "stale_data": draw(st.booleans()),
        }
    )


@given(candidates())
@settings(max_examples=100, deadline=None)
def test_to_dict_from_dict_roundtrip_equal(c: NormalizedCandidate) -> None:
    rebuilt = from_dict(c.to_dict())
    assert rebuilt == c


@given(candidates())
@settings(max_examples=100, deadline=None)
def test_canonical_json_decodes_to_to_dict(c: NormalizedCandidate) -> None:
    decoded = json.loads(c.canonical_json().decode("utf-8"))
    assert decoded == c.to_dict()


@given(candidates())
@settings(max_examples=100, deadline=None)
def test_canonical_json_is_deterministic(c: NormalizedCandidate) -> None:
    a = c.canonical_json()
    b = c.canonical_json()
    assert a == b


@given(candidates())
@settings(max_examples=100, deadline=None)
def test_hash_sha256_is_deterministic(c: NormalizedCandidate) -> None:
    assert c.hash_sha256() == c.hash_sha256()


@given(candidates(), candidates())
@settings(max_examples=50, deadline=None)
def test_distinct_candidates_distinct_hashes(
    c1: NormalizedCandidate, c2: NormalizedCandidate
) -> None:
    """If the canonical JSON differs, the SHA-256 must differ. (No claim about
    collisions across equal payloads.)"""
    if c1.canonical_json() != c2.canonical_json():
        assert c1.hash_sha256() != c2.hash_sha256()