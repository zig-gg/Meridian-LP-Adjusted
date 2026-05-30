"""Unit tests for defi_autonomy.sources.stablecoin_benchmark_adapter — Phase 1.4.

All tests are deterministic and offline. No real network calls. No signing.
No key loading. Uses only static fixture data.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from defi_autonomy.schemas.normalized_candidate import (
    NormalizedCandidate,
    validate_candidate,
)
from defi_autonomy.sources.base import SourceAllowlistEntry
from defi_autonomy.sources.stablecoin_benchmark_adapter import (
    StablecoinBenchmarkAdapter,
)


# ============================================================================
# Fixtures
# ============================================================================


def _make_entry(**overrides) -> SourceAllowlistEntry:
    """Build a valid stablecoin_benchmark SourceAllowlistEntry."""
    defaults = {
        "source_id": "stablecoin_benchmark",
        "adapter_name": "stablecoin_benchmark_adapter",
        "domains": ("yields.llama.fi", "api.llama.fi"),
        "max_freshness_seconds": 1800,
        "fetch_timeout_seconds": 10,
        "methods": ("GET", "HEAD"),
        "max_response_bytes": 4194304,
        "source_confidence_score": 0.8,
    }
    defaults.update(overrides)
    return SourceAllowlistEntry(**defaults)


def _valid_stablecoin_lending_pool() -> dict[str, Any]:
    """A conservative stablecoin lending pool on Base."""
    return {
        "chain": "Base",
        "project": "aave-v3",
        "symbol": "USDC",
        "tvlUsd": 80_000_000.0,
        "apy": 3.8,
        "apyBase": 3.2,
        "apyReward": 0.6,
        "pool": "0x" + "a1" * 20,
        "underlyingTokens": ["0x" + "b1" * 20],
        "stablecoin": True,
        "ilRisk": "no",
        "volumeUsd1d": 2_000_000.0,
    }


def _valid_stable_stable_lp_pool() -> dict[str, Any]:
    """A conservative stable-stable LP pool on BNB Chain."""
    return {
        "chain": "BSC",
        "project": "pancakeswap",
        "symbol": "USDC-USDT",
        "tvlUsd": 30_000_000.0,
        "apy": 5.1,
        "apyBase": 4.0,
        "apyReward": 1.1,
        "pool": "0x" + "c1" * 20,
        "underlyingTokens": ["0x" + "d1" * 20, "0x" + "e1" * 20],
        "stablecoin": True,
        "ilRisk": "yes",
        "volumeUsd1d": 800_000.0,
    }


def _adapter(confidence: float | None = None) -> StablecoinBenchmarkAdapter:
    return StablecoinBenchmarkAdapter(source_confidence_score=confidence)


# ============================================================================
# Tests: build_urls
# ============================================================================


class TestBuildUrls:
    """build_urls returns only allowlisted benchmark URLs."""

    def test_returns_urls_for_llama_domains(self) -> None:
        entry = _make_entry()
        adapter = _adapter()
        urls = adapter.build_urls(entry)
        assert len(urls) == 2
        assert "https://yields.llama.fi/pools" in urls
        assert "https://api.llama.fi/pools" in urls

    def test_skips_non_llama_domains(self) -> None:
        entry = _make_entry(domains=("yields.llama.fi", "example.com"))
        adapter = _adapter()
        urls = adapter.build_urls(entry)
        assert len(urls) == 1
        assert "https://yields.llama.fi/pools" in urls

    def test_empty_domains_returns_empty(self) -> None:
        entry = _make_entry(domains=())
        adapter = _adapter()
        assert adapter.build_urls(entry) == []


# ============================================================================
# Tests: normalize — input format acceptance
# ============================================================================


class TestNormalizeInputFormats:
    """normalize accepts bytes JSON, str JSON, dict/list objects."""

    def test_accepts_bytes_json(self) -> None:
        pool = _valid_stablecoin_lending_pool()
        raw = json.dumps({"data": [pool]}).encode("utf-8")
        adapter = _adapter()
        result = adapter.normalize(raw)
        assert len(result) == 1
        assert isinstance(result[0], NormalizedCandidate)

    def test_accepts_str_json(self) -> None:
        pool = _valid_stablecoin_lending_pool()
        raw = json.dumps({"data": [pool]})
        adapter = _adapter()
        result = adapter.normalize(raw)
        assert len(result) == 1

    def test_accepts_dict_with_data_key(self) -> None:
        pool = _valid_stablecoin_lending_pool()
        adapter = _adapter()
        result = adapter.normalize({"data": [pool]})
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
    """Valid conservative stablecoin pools normalize correctly."""

    def test_stablecoin_lending_pool(self) -> None:
        pool = _valid_stablecoin_lending_pool()
        adapter = _adapter()
        result = adapter.normalize([pool])
        assert len(result) == 1
        c = result[0]
        assert c.chain == "Base"
        assert c.protocol == "aave-v3"
        assert c.strategy_type == "stablecoin_lending"
        assert c.advertised_apy == 3.8
        assert c.fee_apr == 3.2
        assert c.reward_apr == 0.6
        assert c.tvl_usd == 80_000_000.0
        assert c.source_id == "stablecoin_benchmark"
        assert c.adapter_name == "stablecoin_benchmark_adapter"
        assert c.stale_data is False

    def test_stable_stable_lp_pool(self) -> None:
        pool = _valid_stable_stable_lp_pool()
        adapter = _adapter()
        result = adapter.normalize([pool])
        assert len(result) == 1
        c = result[0]
        assert c.chain == "BNB Chain"
        assert c.strategy_type == "stable_stable_lp"
        assert c.advertised_apy == 5.1

    def test_dai_usdt_lp_on_solana(self) -> None:
        pool = _valid_stable_stable_lp_pool()
        pool["chain"] = "Solana"
        pool["symbol"] = "DAI-USDT"
        adapter = _adapter()
        result = adapter.normalize([pool])
        assert len(result) == 1
        assert result[0].chain == "Solana"
        assert result[0].strategy_type == "stable_stable_lp"

    def test_pyusd_lending(self) -> None:
        pool = _valid_stablecoin_lending_pool()
        pool["symbol"] = "PYUSD"
        adapter = _adapter()
        result = adapter.normalize([pool])
        assert len(result) == 1

    def test_fdusd_lending(self) -> None:
        pool = _valid_stablecoin_lending_pool()
        pool["symbol"] = "FDUSD"
        adapter = _adapter()
        result = adapter.normalize([pool])
        assert len(result) == 1

    def test_usds_lending(self) -> None:
        pool = _valid_stablecoin_lending_pool()
        pool["symbol"] = "USDS"
        adapter = _adapter()
        result = adapter.normalize([pool])
        assert len(result) == 1

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

    def test_apy_computed_from_base_plus_reward_when_total_missing(self) -> None:
        pool = _valid_stablecoin_lending_pool()
        del pool["apy"]
        pool["apyBase"] = 2.5
        pool["apyReward"] = 0.5
        adapter = _adapter()
        result = adapter.normalize([pool])
        assert result[0].advertised_apy == 3.0


# ============================================================================
# Tests: normalize — skipped pools
# ============================================================================


class TestNormalizeSkippedPools:
    """Non-conservative or invalid pools are skipped."""

    def test_non_stable_pool_skipped(self) -> None:
        pool = _valid_stablecoin_lending_pool()
        pool["symbol"] = "ETH"
        adapter = _adapter()
        result = adapter.normalize([pool])
        assert result == []

    def test_volatile_token_skipped(self) -> None:
        pool = _valid_stablecoin_lending_pool()
        pool["symbol"] = "WBTC"
        adapter = _adapter()
        result = adapter.normalize([pool])
        assert result == []

    def test_memecoin_skipped(self) -> None:
        pool = _valid_stablecoin_lending_pool()
        pool["symbol"] = "DOGE"
        adapter = _adapter()
        result = adapter.normalize([pool])
        assert result == []

    def test_lsd_pool_skipped(self) -> None:
        pool = _valid_stablecoin_lending_pool()
        pool["symbol"] = "USDC"
        pool["project"] = "lsd-protocol"
        adapter = _adapter()
        result = adapter.normalize([pool])
        assert result == []

    def test_restaking_pool_skipped(self) -> None:
        pool = _valid_stablecoin_lending_pool()
        pool["symbol"] = "USDC"
        pool["project"] = "restaking-vault"
        adapter = _adapter()
        result = adapter.normalize([pool])
        assert result == []

    def test_leveraged_pool_skipped(self) -> None:
        pool = _valid_stablecoin_lending_pool()
        pool["symbol"] = "USDC"
        pool["project"] = "leveraged-lending"
        adapter = _adapter()
        result = adapter.normalize([pool])
        assert result == []

    def test_recursive_pool_skipped(self) -> None:
        pool = _valid_stablecoin_lending_pool()
        pool["symbol"] = "USDC"
        pool["project"] = "recursive-yield"
        adapter = _adapter()
        result = adapter.normalize([pool])
        assert result == []

    def test_unsupported_chain_skipped(self) -> None:
        pool = _valid_stablecoin_lending_pool()
        pool["chain"] = "Ethereum"
        adapter = _adapter()
        result = adapter.normalize([pool])
        assert result == []

    def test_arbitrum_skipped(self) -> None:
        pool = _valid_stablecoin_lending_pool()
        pool["chain"] = "Arbitrum"
        adapter = _adapter()
        result = adapter.normalize([pool])
        assert result == []

    def test_missing_tvl_skipped(self) -> None:
        pool = _valid_stablecoin_lending_pool()
        del pool["tvlUsd"]
        adapter = _adapter()
        result = adapter.normalize([pool])
        assert result == []

    def test_zero_tvl_skipped(self) -> None:
        pool = _valid_stablecoin_lending_pool()
        pool["tvlUsd"] = 0
        adapter = _adapter()
        result = adapter.normalize([pool])
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

    def test_reward_only_unsafe_farm_skipped(self) -> None:
        """A pool with 0 base APY and high reward APY from a degen project."""
        pool = _valid_stablecoin_lending_pool()
        pool["apyBase"] = 0
        pool["apyReward"] = 50.0
        pool["apy"] = 50.0
        pool["project"] = "degen-farm"
        adapter = _adapter()
        result = adapter.normalize([pool])
        assert result == []

    def test_non_stablecoin_flag_skipped(self) -> None:
        pool = _valid_stablecoin_lending_pool()
        pool["stablecoin"] = False
        adapter = _adapter()
        result = adapter.normalize([pool])
        assert result == []

    def test_malformed_pool_skipped(self) -> None:
        adapter = _adapter()
        result = adapter.normalize(["not a dict", 42, None])
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

    def test_invalid_json_bytes_returns_empty(self) -> None:
        adapter = _adapter()
        result = adapter.normalize(b"not json {{{")
        assert result == []

    def test_invalid_json_str_returns_empty(self) -> None:
        adapter = _adapter()
        result = adapter.normalize("not json {{{")
        assert result == []

    def test_eth_usdc_lp_skipped(self) -> None:
        """Mixed volatile+stable LP should be skipped."""
        pool = _valid_stable_stable_lp_pool()
        pool["symbol"] = "ETH-USDC"
        adapter = _adapter()
        result = adapter.normalize([pool])
        assert result == []


# ============================================================================
# Tests: schema validation and hashing
# ============================================================================


class TestSchemaValidation:
    """Schema validation is applied; output has deterministic hashes."""

    def test_output_passes_validate_candidate(self) -> None:
        pool = _valid_stablecoin_lending_pool()
        adapter = _adapter()
        result = adapter.normalize([pool])
        for c in result:
            validate_candidate(c)  # must not raise

    def test_output_candidates_have_deterministic_hashes(self) -> None:
        pool = _valid_stablecoin_lending_pool()
        adapter = _adapter()
        r1 = adapter.normalize([pool])
        r2 = adapter.normalize([pool])
        assert len(r1) == 1
        assert len(r2) == 1
        assert r1[0].hash_sha256() == r2[0].hash_sha256()

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
        assert adapter.source_id == "stablecoin_benchmark"

    def test_adapter_name(self) -> None:
        adapter = _adapter()
        assert adapter.adapter_name == "stablecoin_benchmark_adapter"

    def test_is_source_adapter_subclass(self) -> None:
        from defi_autonomy.sources.base import SourceAdapter

        assert isinstance(_adapter(), SourceAdapter)


# ============================================================================
# Tests: no network calls, no signing
# ============================================================================


class TestNoNetworkNoSigning:
    """Confirm no network calls or signing imports."""

    def test_no_network_calls_in_normalize(self) -> None:
        import socket
        from unittest.mock import patch

        pool = _valid_stablecoin_lending_pool()
        adapter = _adapter()

        with patch.object(
            socket,
            "create_connection",
            side_effect=AssertionError("network call detected"),
        ):
            result = adapter.normalize([pool])
            assert len(result) == 1

    def test_no_signing_imports_in_module(self) -> None:
        import defi_autonomy.sources.stablecoin_benchmark_adapter as mod

        with open(mod.__file__, "r") as f:
            source = f.read()
        signing_modules = ("eth_account", "solders", "nacl", "cryptography.hazmat")
        for sm in signing_modules:
            assert sm not in source, f"signing module {sm!r} found in adapter source"
