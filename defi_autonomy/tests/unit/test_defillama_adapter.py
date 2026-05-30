"""Unit tests for defi_autonomy.sources.defillama_adapter — Phase 1.3.

All tests are deterministic and offline. No real network calls. No signing.
No key loading. Uses only mocked/static fixture data.
"""

from __future__ import annotations

from typing import Any

import pytest

from defi_autonomy.schemas.normalized_candidate import (
    NormalizedCandidate,
    validate_candidate,
)
from defi_autonomy.sources.base import SourceAllowlistEntry
from defi_autonomy.sources.defillama_adapter import DeFiLlamaAdapter


# ============================================================================
# Fixtures
# ============================================================================


def _make_entry(**overrides) -> SourceAllowlistEntry:
    """Build a valid DeFiLlama SourceAllowlistEntry."""
    defaults = {
        "source_id": "defillama",
        "adapter_name": "defillama_adapter",
        "domains": ("api.llama.fi", "yields.llama.fi"),
        "max_freshness_seconds": 1800,
        "fetch_timeout_seconds": 10,
        "methods": ("GET", "HEAD"),
        "max_response_bytes": 4194304,
        "source_confidence_score": 0.7,
    }
    defaults.update(overrides)
    return SourceAllowlistEntry(**defaults)


def _valid_stablecoin_lending_pool() -> dict[str, Any]:
    """A DeFiLlama pool that should classify as stablecoin_lending."""
    return {
        "chain": "Base",
        "project": "aave-v3",
        "symbol": "USDC",
        "tvlUsd": 50_000_000.0,
        "apy": 4.5,
        "apyBase": 3.0,
        "apyReward": 1.5,
        "pool": "0x" + "a" * 40,
        "underlyingTokens": ["0x" + "b" * 40],
        "stablecoin": True,
        "ilRisk": "no",
        "volumeUsd1d": 1_000_000.0,
    }


def _valid_stable_stable_lp_pool() -> dict[str, Any]:
    """A DeFiLlama pool that should classify as stable_stable_lp."""
    return {
        "chain": "BSC",
        "project": "pancakeswap",
        "symbol": "USDC-USDT",
        "tvlUsd": 20_000_000.0,
        "apy": 8.2,
        "apyBase": 5.0,
        "apyReward": 3.2,
        "pool": "0x" + "c" * 40,
        "underlyingTokens": ["0x" + "d" * 40, "0x" + "e" * 40],
        "stablecoin": True,
        "ilRisk": "yes",
        "volumeUsd1d": 500_000.0,
    }


def _adapter(confidence: float | None = None) -> DeFiLlamaAdapter:
    """Create a DeFiLlamaAdapter instance."""
    return DeFiLlamaAdapter(source_confidence_score=confidence)


# ============================================================================
# Tests: build_urls
# ============================================================================


class TestBuildUrls:
    """build_urls returns only allowlisted DeFiLlama endpoint URLs."""

    def test_returns_urls_for_llama_domains(self) -> None:
        entry = _make_entry()
        adapter = _adapter()
        urls = adapter.build_urls(entry)
        assert len(urls) == 2
        assert "https://api.llama.fi/pools" in urls
        assert "https://yields.llama.fi/pools" in urls

    def test_skips_non_llama_domains(self) -> None:
        entry = _make_entry(domains=("api.llama.fi", "example.com"))
        adapter = _adapter()
        urls = adapter.build_urls(entry)
        assert len(urls) == 1
        assert "https://api.llama.fi/pools" in urls

    def test_empty_domains_returns_empty(self) -> None:
        entry = _make_entry(domains=())
        adapter = _adapter()
        urls = adapter.build_urls(entry)
        assert urls == []


# ============================================================================
# Tests: normalize — input format acceptance
# ============================================================================


class TestNormalizeInputFormats:
    """normalize accepts bytes JSON, str JSON, dict/list objects."""

    def test_accepts_bytes_json(self) -> None:
        import json

        pool = _valid_stablecoin_lending_pool()
        raw = json.dumps({"data": [pool]}).encode("utf-8")
        adapter = _adapter()
        result = adapter.normalize(raw)
        assert len(result) == 1
        assert isinstance(result[0], NormalizedCandidate)

    def test_accepts_str_json(self) -> None:
        import json

        pool = _valid_stablecoin_lending_pool()
        raw = json.dumps({"data": [pool]})
        adapter = _adapter()
        result = adapter.normalize(raw)
        assert len(result) == 1
        assert isinstance(result[0], NormalizedCandidate)

    def test_accepts_dict_with_data_key(self) -> None:
        pool = _valid_stablecoin_lending_pool()
        raw = {"data": [pool]}
        adapter = _adapter()
        result = adapter.normalize(raw)
        assert len(result) == 1

    def test_accepts_list_directly(self) -> None:
        pool = _valid_stablecoin_lending_pool()
        adapter = _adapter()
        result = adapter.normalize([pool])
        assert len(result) == 1

    def test_accepts_single_pool_dict(self) -> None:
        pool = _valid_stablecoin_lending_pool()
        adapter = _adapter()
        result = adapter.normalize(pool)
        assert len(result) == 1


# ============================================================================
# Tests: normalize — valid pools
# ============================================================================


class TestNormalizeValidPools:
    """Valid pools normalize to NormalizedCandidate."""

    def test_stablecoin_lending_pool(self) -> None:
        pool = _valid_stablecoin_lending_pool()
        adapter = _adapter()
        result = adapter.normalize([pool])
        assert len(result) == 1
        c = result[0]
        assert c.chain == "Base"
        assert c.protocol == "aave-v3"
        assert c.strategy_type == "stablecoin_lending"
        assert c.advertised_apy == 4.5
        assert c.fee_apr == 3.0
        assert c.reward_apr == 1.5
        assert c.tvl_usd == 50_000_000.0
        assert c.volume_24h_usd == 1_000_000.0
        assert c.source_id == "defillama"
        assert c.adapter_name == "defillama_adapter"
        assert c.stale_data is False

    def test_stable_stable_lp_pool(self) -> None:
        pool = _valid_stable_stable_lp_pool()
        adapter = _adapter()
        result = adapter.normalize([pool])
        assert len(result) == 1
        c = result[0]
        assert c.chain == "BNB Chain"
        assert c.protocol == "pancakeswap"
        assert c.strategy_type == "stable_stable_lp"
        assert c.advertised_apy == 8.2
        assert c.tvl_usd == 20_000_000.0

    def test_solana_chain_mapped(self) -> None:
        pool = _valid_stablecoin_lending_pool()
        pool["chain"] = "Solana"
        pool["project"] = "solend"
        adapter = _adapter()
        result = adapter.normalize([pool])
        assert len(result) == 1
        assert result[0].chain == "Solana"

    def test_bsc_maps_to_bnb_chain(self) -> None:
        pool = _valid_stablecoin_lending_pool()
        pool["chain"] = "BSC"
        adapter = _adapter()
        result = adapter.normalize([pool])
        assert len(result) == 1
        assert result[0].chain == "BNB Chain"

    def test_source_confidence_from_constructor(self) -> None:
        pool = _valid_stablecoin_lending_pool()
        adapter = _adapter(confidence=0.8)
        result = adapter.normalize([pool])
        assert result[0].source_confidence_score == 0.8

    def test_default_confidence_is_conservative(self) -> None:
        pool = _valid_stablecoin_lending_pool()
        adapter = _adapter()
        result = adapter.normalize([pool])
        assert result[0].source_confidence_score == 0.5

    def test_apy_computed_from_base_plus_reward_when_apy_missing(self) -> None:
        pool = _valid_stablecoin_lending_pool()
        del pool["apy"]
        pool["apyBase"] = 2.0
        pool["apyReward"] = 1.0
        adapter = _adapter()
        result = adapter.normalize([pool])
        assert result[0].advertised_apy == 3.0

    def test_pool_address_preserved(self) -> None:
        pool = _valid_stablecoin_lending_pool()
        adapter = _adapter()
        result = adapter.normalize([pool])
        assert result[0].pool_address == "0x" + "a" * 40

    def test_token_addresses_preserved(self) -> None:
        pool = _valid_stablecoin_lending_pool()
        adapter = _adapter()
        result = adapter.normalize([pool])
        assert result[0].token_addresses == ("0x" + "b" * 40,)


# ============================================================================
# Tests: normalize — skipped pools
# ============================================================================


class TestNormalizeSkippedPools:
    """Pools that should be skipped produce no candidates."""

    def test_unknown_chain_skipped(self) -> None:
        pool = _valid_stablecoin_lending_pool()
        pool["chain"] = "Ethereum"
        adapter = _adapter()
        result = adapter.normalize([pool])
        assert result == []

    def test_arbitrum_chain_skipped(self) -> None:
        pool = _valid_stablecoin_lending_pool()
        pool["chain"] = "Arbitrum"
        adapter = _adapter()
        result = adapter.normalize([pool])
        assert result == []

    def test_unsupported_strategy_skipped(self) -> None:
        pool = _valid_stablecoin_lending_pool()
        pool["stablecoin"] = False  # not stablecoin → no strategy match
        adapter = _adapter()
        result = adapter.normalize([pool])
        assert result == []

    def test_malformed_pool_skipped(self) -> None:
        adapter = _adapter()
        result = adapter.normalize(["not a dict", 42, None])
        assert result == []

    def test_invalid_apy_skipped(self) -> None:
        pool = _valid_stablecoin_lending_pool()
        pool["apy"] = "not_a_number"
        pool["apyBase"] = "bad"
        adapter = _adapter()
        result = adapter.normalize([pool])
        assert result == []

    def test_apy_above_100_skipped(self) -> None:
        pool = _valid_stablecoin_lending_pool()
        pool["apy"] = 150.0
        adapter = _adapter()
        result = adapter.normalize([pool])
        assert result == []

    def test_apy_below_minus_one_skipped(self) -> None:
        pool = _valid_stablecoin_lending_pool()
        pool["apy"] = -5.0
        adapter = _adapter()
        result = adapter.normalize([pool])
        assert result == []

    def test_missing_chain_skipped(self) -> None:
        pool = _valid_stablecoin_lending_pool()
        del pool["chain"]
        adapter = _adapter()
        result = adapter.normalize([pool])
        assert result == []

    def test_missing_project_skipped(self) -> None:
        pool = _valid_stablecoin_lending_pool()
        del pool["project"]
        adapter = _adapter()
        result = adapter.normalize([pool])
        assert result == []

    def test_missing_symbol_skipped(self) -> None:
        pool = _valid_stablecoin_lending_pool()
        del pool["symbol"]
        adapter = _adapter()
        result = adapter.normalize([pool])
        assert result == []

    def test_missing_tvl_skipped(self) -> None:
        pool = _valid_stablecoin_lending_pool()
        del pool["tvlUsd"]
        adapter = _adapter()
        result = adapter.normalize([pool])
        assert result == []

    def test_invalid_json_bytes_returns_empty(self) -> None:
        adapter = _adapter()
        result = adapter.normalize(b"not json at all {{{")
        assert result == []

    def test_invalid_json_str_returns_empty(self) -> None:
        adapter = _adapter()
        result = adapter.normalize("not json at all {{{")
        assert result == []


# ============================================================================
# Tests: schema validation applied
# ============================================================================


class TestSchemaValidation:
    """Schema validation is applied to output candidates."""

    def test_output_passes_validate_candidate(self) -> None:
        pool = _valid_stablecoin_lending_pool()
        adapter = _adapter()
        result = adapter.normalize([pool])
        for c in result:
            validate_candidate(c)  # must not raise

    def test_output_candidates_have_deterministic_hashes(self) -> None:
        from unittest.mock import patch
        from datetime import datetime, timezone

        pool = _valid_stablecoin_lending_pool()
        adapter = _adapter()
        fixed_time = datetime(2026, 5, 27, 0, 0, 0, tzinfo=timezone.utc)
        with patch("defi_autonomy.sources.defillama_adapter.datetime") as mock_dt:
            mock_dt.now.return_value = fixed_time
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result1 = adapter.normalize([pool])
            result2 = adapter.normalize([pool])
        assert len(result1) == 1
        assert len(result2) == 1
        assert result1[0].hash_sha256() == result2[0].hash_sha256()

    def test_different_pools_have_different_hashes(self) -> None:
        pool1 = _valid_stablecoin_lending_pool()
        pool2 = _valid_stablecoin_lending_pool()
        pool2["tvlUsd"] = 99_000_000.0
        adapter = _adapter()
        r1 = adapter.normalize([pool1])
        r2 = adapter.normalize([pool2])
        assert r1[0].hash_sha256() != r2[0].hash_sha256()


# ============================================================================
# Tests: adapter identity
# ============================================================================


class TestAdapterIdentity:
    """Adapter source_id and adapter_name are correct."""

    def test_source_id(self) -> None:
        adapter = _adapter()
        assert adapter.source_id == "defillama"

    def test_adapter_name(self) -> None:
        adapter = _adapter()
        assert adapter.adapter_name == "defillama_adapter"

    def test_is_source_adapter_subclass(self) -> None:
        from defi_autonomy.sources.base import SourceAdapter

        adapter = _adapter()
        assert isinstance(adapter, SourceAdapter)


# ============================================================================
# Tests: no network calls, no signing
# ============================================================================


class TestNoNetworkNoSigning:
    """Confirm no network calls or signing imports."""

    def test_no_network_calls_in_normalize(self) -> None:
        """normalize processes data without any network I/O."""
        import socket
        from unittest.mock import patch

        pool = _valid_stablecoin_lending_pool()
        adapter = _adapter()

        # Patch socket.create_connection to detect any network attempt
        with patch.object(
            socket, "create_connection", side_effect=AssertionError("network call detected")
        ):
            result = adapter.normalize([pool])
            assert len(result) == 1

    def test_no_signing_imports_in_module(self) -> None:
        """The adapter module does not import signing/key-loading code."""
        import defi_autonomy.sources.defillama_adapter as mod
        import sys

        module_source = mod.__file__
        # Check that no signing-related modules are imported by this module
        signing_modules = ("eth_account", "solders", "nacl", "cryptography.hazmat")
        for sm in signing_modules:
            # Check if the signing module was imported as a side effect
            if sm in sys.modules:
                # It might be in sys.modules from other tests, but it should
                # not be imported BY the adapter module
                pass
            # The real check: the adapter source should not reference these
            with open(module_source, "r") as f:
                source = f.read()
            assert sm not in source, f"signing module {sm!r} found in adapter source"
