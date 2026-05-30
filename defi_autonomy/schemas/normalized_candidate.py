"""NormalizedCandidate - schema for the External_Data_Ingestion boundary.

Phase 1.1 scaffolding. No network calls. No signing. No key loading.

This module declares:
- The frozen `NormalizedCandidate` dataclass that every source adapter must produce.
- A strict JSON Schema (`NORMALIZED_CANDIDATE_SCHEMA`, draft 2020-12,
  `additionalProperties: false`) that gates entry to the rest of the cycle.
- Deterministic helpers (`canonical_json`, `hash_sha256`) used by ledger records,
  policy approval tokens, and ingestion attestations downstream.
- A safe constructor (`from_dict`) that rejects unknown fields.

Authoritative requirements:
- R24.11: `NormalizedCandidate` JSON Schema; atomic batch persistence.
- R24.13: `stale_data` flag is set by the ingestion layer; this module only
  validates the shape, not the freshness policy.
- R-EXT.1 (Policy_Engine): policy rejects descriptors backed by stale candidates;
  schema validation alone does NOT reject `stale_data == true` here.
- design.md Testing Strategy items #3 (round-trip), #4 (hashing), #10 (validation).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, fields
from typing import Any

from jsonschema import Draft202012Validator


ALLOWED_CHAINS: tuple[str, ...] = ("Base", "BNB Chain", "Solana")
ALLOWED_STRATEGY_TYPES: tuple[str, ...] = (
    "stablecoin_lending",
    "stable_stable_lp",
    "xstocks_points",
    "xstocks_lp",
)


class NormalizedCandidateError(ValueError):
    """Raised when a NormalizedCandidate fails validation or construction."""


@dataclass(frozen=True, slots=True)
class NormalizedCandidate:
    """A single normalized yield candidate produced by a source adapter.

    All fields are required. The dataclass is frozen and uses slots so that no
    in-process code can mutate a candidate after the ingestion layer hands it
    off to the scanner / scorer / policy stages.
    """

    chain: str
    protocol: str
    venue: str
    venue_id: str
    pool_address: str | None
    token_addresses: tuple[str, ...]
    strategy_type: str
    advertised_apy: float
    fee_apr: float
    reward_apr: float
    tvl_usd: float
    volume_24h_usd: float
    liquidity_depth_usd: float
    source_id: str
    source_url: str
    source_timestamp_utc: str
    fetched_at_utc: str
    adapter_name: str
    data_freshness_seconds: int
    source_confidence_score: float
    stale_data: bool

    def to_dict(self) -> dict[str, Any]:
        """Return a plain dict representation suitable for JSON serialization.

        Tuples are converted to lists so the output is JSON-canonical.
        """
        out: dict[str, Any] = {}
        for f in fields(self):
            v = getattr(self, f.name)
            if isinstance(v, tuple):
                v = list(v)
            out[f.name] = v
        return out

    def canonical_json(self) -> bytes:
        """Return the canonical JSON encoding (sorted keys, compact separators)."""
        return json.dumps(
            self.to_dict(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")

    def hash_sha256(self) -> str:
        """Return the lowercase hex SHA-256 digest of canonical_json()."""
        return hashlib.sha256(self.canonical_json()).hexdigest()


NORMALIZED_CANDIDATE_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://hermes-defi.local/schemas/normalized_candidate.json",
    "title": "NormalizedCandidate",
    "type": "object",
    "additionalProperties": False,
    "required": [
        "chain",
        "protocol",
        "venue",
        "venue_id",
        "pool_address",
        "token_addresses",
        "strategy_type",
        "advertised_apy",
        "fee_apr",
        "reward_apr",
        "tvl_usd",
        "volume_24h_usd",
        "liquidity_depth_usd",
        "source_id",
        "source_url",
        "source_timestamp_utc",
        "fetched_at_utc",
        "adapter_name",
        "data_freshness_seconds",
        "source_confidence_score",
        "stale_data",
    ],
    "properties": {
        "chain": {"type": "string", "enum": list(ALLOWED_CHAINS)},
        "protocol": {"type": "string", "minLength": 1, "maxLength": 64},
        "venue": {"type": "string", "minLength": 1, "maxLength": 64},
        "venue_id": {"type": "string", "minLength": 1, "maxLength": 128},
        "pool_address": {
            "type": ["string", "null"],
            "minLength": 1,
            "maxLength": 128,
        },
        "token_addresses": {
            "type": "array",
            "items": {"type": "string", "minLength": 1, "maxLength": 128},
            "maxItems": 4,
            "uniqueItems": True,
        },
        "strategy_type": {
            "type": "string",
            "enum": list(ALLOWED_STRATEGY_TYPES),
        },
        "advertised_apy": {"type": "number", "minimum": -1, "maximum": 100},
        "fee_apr": {"type": "number", "minimum": 0, "maximum": 100},
        "reward_apr": {"type": "number", "minimum": 0, "maximum": 100},
        "tvl_usd": {"type": "number", "minimum": 0},
        "volume_24h_usd": {"type": "number", "minimum": 0},
        "liquidity_depth_usd": {"type": "number", "minimum": 0},
        "source_id": {"type": "string", "minLength": 1, "maxLength": 64},
        "source_url": {"type": "string", "minLength": 1, "maxLength": 2048},
        "source_timestamp_utc": {
            "type": "string",
            "minLength": 1,
            "maxLength": 64,
        },
        "fetched_at_utc": {"type": "string", "minLength": 1, "maxLength": 64},
        "adapter_name": {"type": "string", "minLength": 1, "maxLength": 64},
        "data_freshness_seconds": {"type": "integer", "minimum": 0},
        "source_confidence_score": {
            "type": "number",
            "minimum": 0,
            "maximum": 1,
        },
        "stale_data": {"type": "boolean"},
    },
}

# Validate the schema document itself once at import time. Any malformed
# constant here is a programmer error and must fail loudly before any
# candidate is processed.
Draft202012Validator.check_schema(NORMALIZED_CANDIDATE_SCHEMA)
_VALIDATOR: Draft202012Validator = Draft202012Validator(NORMALIZED_CANDIDATE_SCHEMA)


def validate_candidate(candidate: NormalizedCandidate) -> None:
    """Validate a NormalizedCandidate against NORMALIZED_CANDIDATE_SCHEMA.

    Raises NormalizedCandidateError on schema violation. The first error path
    is included in the message for diagnostics.
    """
    if not isinstance(candidate, NormalizedCandidate):
        raise NormalizedCandidateError(
            "validate_candidate expected NormalizedCandidate, got "
            f"{type(candidate).__name__}"
        )
    errors = list(_VALIDATOR.iter_errors(candidate.to_dict()))
    if errors:
        first = errors[0]
        path = (
            "/" + "/".join(str(p) for p in first.absolute_path)
            if first.absolute_path
            else "/"
        )
        raise NormalizedCandidateError(
            f"NormalizedCandidate schema violation at {path}: {first.message}"
        )


_FIELD_NAMES: frozenset[str] = frozenset(f.name for f in fields(NormalizedCandidate))


def from_dict(data: dict[str, Any]) -> NormalizedCandidate:
    """Construct a NormalizedCandidate from a plain dict.

    - Unknown keys raise NormalizedCandidateError (strict per R24.11).
    - Missing required keys raise NormalizedCandidateError.
    - `token_addresses` is converted from list to tuple (frozen invariant).
    - The constructed candidate is validated before being returned.
    """
    if not isinstance(data, dict):
        raise NormalizedCandidateError(
            f"from_dict expected dict, got {type(data).__name__}"
        )

    keys = set(data.keys())
    unknown = keys - _FIELD_NAMES
    if unknown:
        raise NormalizedCandidateError(
            "NormalizedCandidate.from_dict: unknown fields rejected: "
            f"{sorted(unknown)}"
        )
    missing = _FIELD_NAMES - keys
    if missing:
        raise NormalizedCandidateError(
            "NormalizedCandidate.from_dict: missing required fields: "
            f"{sorted(missing)}"
        )

    payload = dict(data)
    ta = payload.get("token_addresses")
    if isinstance(ta, list):
        payload["token_addresses"] = tuple(ta)
    elif isinstance(ta, tuple):
        pass
    else:
        raise NormalizedCandidateError(
            "token_addresses must be a list or tuple, got "
            f"{type(ta).__name__}"
        )

    candidate = NormalizedCandidate(**payload)
    validate_candidate(candidate)
    return candidate


__all__ = [
    "ALLOWED_CHAINS",
    "ALLOWED_STRATEGY_TYPES",
    "NormalizedCandidate",
    "NormalizedCandidateError",
    "NORMALIZED_CANDIDATE_SCHEMA",
    "from_dict",
    "validate_candidate",
]