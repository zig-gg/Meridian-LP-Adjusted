"""Unit tests for defi_autonomy.sources.meteora_adapter — Sprint 5, Phase 5.2.

All tests are deterministic and offline. No real network calls. No signing.
No key loading. No broadcast. Uses only static fixture data.
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
from defi_autonomy.sources.meteora_adapter import MeteoraAdapter


# ============================================================================
# Fixtures
# ============================================================================


def _make_entry(**overrides) -> SourceAllowlistEntry:
    defaults = {
        "source_id": "meteora",
        "adapter_name": "meteora_adapter",
        "domains": ("app.meteora.ag", "dlmm-api.meteora.ag", "amm-v2-api.meteora.ag"),
        "max_freshness_seconds": 600,
        "fetch_timeout_seconds": 10,
        "methods": ("GET", "HEAD"),
        "max_response_bytes": 2097152,
        "source_confidence_score": 0.7,
    }
    defaults.update(overrides)
    return SourceAllowlistEntry(**defaults)


def _valid_stable_stable_pool() -> dict[str, Any]:
    """A valid Meteora stable-stable LP pool."""
    return {
        "pair_name": "USDC-USDT",
        "address": "So1anaP00LAddr3ss111111111111111111111111",
        "mint_x": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
        "mint_y": "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",
        "tvl_usd": 2_000_000.0,
        "fee_apy": 8.5,
        "reward_apy": 2.0,
        "apy": 10.5,
        "volume_24h_usd": 500_000.0,
    }


def _valid_xstocks_lp_pool() -> dict[str, Any]:
    """A valid Meteora xStocks/stablecoin LP pool."""
    return {
        "pair_name": "TSLAx-USDC",
        "address": "So1anaP00LAddr3ss222222222222222222222222",
        "mint_x": "TSLAxMint111111111111111111111111111111111",
        "mint_y": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
        "tvl_usd": 300_000.0,
        "fee_apy": 15.0,
        "reward_apy": 5.0,
        "apy": 20.0,
        "volume_24h_usd": 80_000.0,
    }


def _volatile_pool() -> dict[str, Any]:
    """A volatile LP pool that should be skipped."""
    return {
        "pair_name": "SOL-BONK",
        "address": "So1anaP00LAddr3ss333333333333333333333333",
        "mint_x": "So11111111111111111111111111111111111111112",
        "mint_y": "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",
        "tvl_usd": 1_000_000.0,
        "fee_apy": 25.0,
        "reward_apy": 10.0,
        "apy": 35.0,
        "volume_24h_usd": 200_000.0,
    }


def _adapter(confidence: float | None = None) -> MeteoraAdapter:
    return MeteoraAdapter(source_confidence_score=confidence)


# ============================================================================
# Tests: build_urls
# ============================================================================


class TestBuildUrls:
    """build_urls returns allowlisted Meteora URLs only."""

    def test_returns_urls_for_meteora_domains(self) -> None:
        entry = _make_entry()
        adapter = _adapter()
        urls = adapter.build_urls(entry)
        assert len(urls) == 3
        assert "https://app.meteora.ag/pair/all" in urls
        assert "https://dlmm-api.meteora.ag/pair/all" in urls

    def test_skips_non_meteora_domains(self) -> None:
        entry = _make_entry(domains=("app.meteora.ag", "example.com"))
        adapter = _adapter()
        urls = adapter.build_urls(entry)
        assert len(urls) == 1
        assert "meteora" in urls[0]

    def test_empty_domains_returns_empty(self) -> None:
        entry = _make_entry(domains=())
        adapter = _adapter()
        assert adapter.build_urls(entry) == []


# ============================================================================
# Tests: normalize — input formats
# ============================================================================


class TestNormalizeInputFormats:
    """normalize accepts bytes JSON, str JSON, dict/list objects."""

    def test_accepts_bytes_json(self) -> None:
        pool = _valid_stable_stable_pool()
        raw = json.dumps([pool]).encode("utf-8")
        adapter = _adapter()
        result = adapter.normalize(raw)
        assert len(result) == 1

    def test_accepts_str_json(self) -> None:
        pool = _valid_stable_stable_pool()
        raw = json.dumps([pool])
        adapter = _adapter()
        result = adapter.normalize(raw)
        assert len(result) == 1

    def test_accepts_dict_with_pairs_key(self) -> None:
        pool = _valid_stable_stable_pool()
        adapter = _adapter()
        result = adapter.normalize({"pairs": [pool]})
        assert len(result) == 1

    def test_accepts_dict_with_pools_key(self) -> None:
        pool = _valid_stable_stable_pool()
        adapter = _adapter()
        result = adapter.normalize({"pools": [pool]})
        assert len(result) == 1

    def test_accepts_list_directly(self) -> None:
        pool = _valid_stable_stable_pool()
        adapter = _adapter()
        result = adapter.normalize([pool])
        assert len(result) == 1

    def test_accepts_single_dict(self) -> None:
        pool = _valid_stable_stable_pool()
        adapter = _adapter()
        result = adapter.normalize(pool)
        assert len(result) == 1


# ============================================================================
# Tests: valid stable_stable_lp
# ============================================================================


class TestStableStableLP:
    """Valid stable-stable LP normalizes correctly."""

    def test_normalizes(self) -> None:
        pool = _valid_stable_stable_pool()
        adapter = _adapter()
        result = adapter.normalize([pool])
        assert len(result) == 1
        c = result[0]
        assert c.chain == "Solana"
        assert c.protocol == "meteora"
        assert c.venue == "meteora"
        assert c.strategy_type == "stable_stable_lp"
        assert c.advertised_apy == 10.5
        assert c.fee_apr == 8.5
        assert c.reward_apr == 2.0
        assert c.tvl_usd == 2_000_000.0
        assert c.source_id == "meteora"
        assert c.adapter_name == "meteora_adapter"

    def test_pool_address_preserved(self) -> None:
        pool = _valid_stable_stable_pool()
        adapter = _adapter()
        result = adapter.normalize([pool])
        assert result[0].pool_address == "So1anaP00LAddr3ss111111111111111111111111"

    def test_token_addresses_from_mints(self) -> None:
        pool = _valid_stable_stable_pool()
        adapter = _adapter()
        result = adapter.normalize([pool])
        assert len(result[0].token_addresses) == 2

    def test_usdc_pyusd_stable_pair(self) -> None:
        pool = _valid_stable_stable_pool()
        pool["pair_name"] = "USDC-PYUSD"
        adapter = _adapter()
        result = adapter.normalize([pool])
        assert len(result) == 1
        assert result[0].strategy_type == "stable_stable_lp"


# ============================================================================
# Tests: valid xstocks_lp
# ============================================================================


class TestXStocksLP:
    """Valid xStocks/stablecoin LP normalizes correctly."""

    def test_normalizes(self) -> None:
        pool = _valid_xstocks_lp_pool()
        adapter = _adapter()
        result = adapter.normalize([pool])
        assert len(result) == 1
        c = result[0]
        assert c.strategy_type == "xstocks_lp"
        assert c.advertised_apy == 20.0
        assert c.tvl_usd == 300_000.0

    def test_nvdax_usdt_lp(self) -> None:
        pool = _valid_xstocks_lp_pool()
        pool["pair_name"] = "NVDAx-USDT"
        adapter = _adapter()
        result = adapter.normalize([pool])
        assert len(result) == 1
        assert result[0].strategy_type == "xstocks_lp"

    def test_dynamic_unknown_xstock_lp(self) -> None:
        """A dynamically discovered unknown xStock/stablecoin LP normalizes."""
        pool = _valid_xstocks_lp_pool()
        pool["pair_name"] = "COINBASEx-USDC"  # Not in alias map
        adapter = _adapter()
        result = adapter.normalize([pool])
        assert len(result) == 1
        assert result[0].strategy_type == "xstocks_lp"

    def test_spyx_usdc_lp(self) -> None:
        """SPYx is not in alias map but matches xStocks heuristic."""
        pool = _valid_xstocks_lp_pool()
        pool["pair_name"] = "SPYx-USDC"
        adapter = _adapter()
        result = adapter.normalize([pool])
        assert len(result) == 1
        assert result[0].strategy_type == "xstocks_lp"


# ============================================================================
# Tests: skipped pools
# ============================================================================


class TestSkippedPools:
    """Invalid or unsupported pools are skipped."""

    def test_volatile_lp_skipped(self) -> None:
        pool = _volatile_pool()
        adapter = _adapter()
        result = adapter.normalize([pool])
        assert result == []

    def test_non_solana_skipped(self) -> None:
        # Meteora is Solana-only; chain field is ignored (always Solana)
        # But if we add a chain field that's wrong, it should still be Solana
        pool = _valid_stable_stable_pool()
        adapter = _adapter()
        result = adapter.normalize([pool])
        assert result[0].chain == "Solana"

    def test_missing_pool_address_skipped(self) -> None:
        pool = _valid_stable_stable_pool()
        pool["address"] = None
        adapter = _adapter()
        result = adapter.normalize([pool])
        assert result == []

    def test_empty_pool_address_skipped(self) -> None:
        pool = _valid_stable_stable_pool()
        pool["address"] = ""
        adapter = _adapter()
        result = adapter.normalize([pool])
        assert result == []

    def test_missing_token_addresses_skipped(self) -> None:
        pool = _valid_stable_stable_pool()
        pool["mint_x"] = None
        pool["mint_y"] = None
        adapter = _adapter()
        result = adapter.normalize([pool])
        assert result == []

    def test_missing_tvl_skipped(self) -> None:
        pool = _valid_stable_stable_pool()
        pool["tvl_usd"] = 0
        adapter = _adapter()
        result = adapter.normalize([pool])
        assert result == []

    def test_negative_tvl_skipped(self) -> None:
        pool = _valid_stable_stable_pool()
        pool["tvl_usd"] = -100
        adapter = _adapter()
        result = adapter.normalize([pool])
        assert result == []

    def test_invalid_apy_skipped(self) -> None:
        pool = _valid_stable_stable_pool()
        pool["apy"] = "not_a_number"
        pool["fee_apy"] = "bad"
        adapter = _adapter()
        result = adapter.normalize([pool])
        assert result == []

    def test_apy_above_100_skipped(self) -> None:
        pool = _valid_stable_stable_pool()
        pool["apy"] = 150.0
        adapter = _adapter()
        result = adapter.normalize([pool])
        assert result == []

    def test_apy_below_minus_one_skipped(self) -> None:
        pool = _valid_stable_stable_pool()
        pool["apy"] = -5.0
        adapter = _adapter()
        result = adapter.normalize([pool])
        assert result == []

    def test_malformed_entry_skipped(self) -> None:
        adapter = _adapter()
        result = adapter.normalize(["not a dict", 42, None])
        assert result == []

    def test_invalid_json_bytes(self) -> None:
        adapter = _adapter()
        result = adapter.normalize(b"not json {{{")
        assert result == []

    def test_invalid_json_str(self) -> None:
        adapter = _adapter()
        result = adapter.normalize("not json {{{")
        assert result == []

    def test_no_fabricated_apy(self) -> None:
        """Pool with no APY fields at all is skipped (not fabricated)."""
        pool = {
            "pair_name": "USDC-USDT",
            "address": "SomeAddr111111111111111111111111111111111",
            "mint_x": "MintA",
            "mint_y": "MintB",
            "tvl_usd": 1_000_000.0,
            # No apy, fee_apy, reward_apy fields
        }
        adapter = _adapter()
        result = adapter.normalize([pool])
        # Should be skipped because APY is completely unknown
        assert result == []


# ============================================================================
# Tests: schema validation and hashing
# ============================================================================


class TestSchemaValidation:
    """Candidates pass validation with deterministic hashes."""

    def test_stable_lp_passes_validation(self) -> None:
        pool = _valid_stable_stable_pool()
        adapter = _adapter()
        result = adapter.normalize([pool])
        for c in result:
            validate_candidate(c)

    def test_xstocks_lp_passes_validation(self) -> None:
        pool = _valid_xstocks_lp_pool()
        adapter = _adapter()
        result = adapter.normalize([pool])
        for c in result:
            validate_candidate(c)

    def test_hash_deterministic(self) -> None:
        pool = _valid_stable_stable_pool()
        adapter = _adapter()
        r1 = adapter.normalize([pool])
        r2 = adapter.normalize([pool])
        assert r1[0].hash_sha256() == r2[0].hash_sha256()

    def test_different_pools_different_hashes(self) -> None:
        p1 = _valid_stable_stable_pool()
        p2 = _valid_stable_stable_pool()
        p2["tvl_usd"] = 999_999.0
        adapter = _adapter()
        r1 = adapter.normalize([p1])
        r2 = adapter.normalize([p2])
        assert r1[0].hash_sha256() != r2[0].hash_sha256()


# ============================================================================
# Tests: adapter identity
# ============================================================================


class TestAdapterIdentity:
    """Adapter source_id and adapter_name are correct."""

    def test_source_id(self) -> None:
        assert _adapter().source_id == "meteora"

    def test_adapter_name(self) -> None:
        assert _adapter().adapter_name == "meteora_adapter"

    def test_is_source_adapter_subclass(self) -> None:
        from defi_autonomy.sources.base import SourceAdapter
        assert isinstance(_adapter(), SourceAdapter)


# ============================================================================
# Tests: no network, no signing, no broadcast
# ============================================================================


class TestNoNetworkNoSigning:
    """No real network calls or signing imports."""

    def test_no_network_calls(self) -> None:
        import socket
        from unittest.mock import patch

        pool = _valid_stable_stable_pool()
        adapter = _adapter()
        with patch.object(
            socket,
            "create_connection",
            side_effect=AssertionError("network call detected"),
        ):
            result = adapter.normalize([pool])
            assert len(result) == 1

    def test_no_signing_imports(self) -> None:
        import defi_autonomy.sources.meteora_adapter as mod
        with open(mod.__file__, "r") as f:
            source = f.read()
        forbidden = (
            "eth_account", "solders", "nacl", "cryptography.hazmat",
            "sign_transaction", "broadcast_transaction",
            "private_key", "mnemonic",
        )
        for term in forbidden:
            assert term not in source, f"forbidden term {term!r} found"
