"""Unit tests for defi_autonomy.sources.xstocks_adapter — Sprint 5, Phase 5.1 + 5.1b.

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
from defi_autonomy.sources.xstocks_adapter import (
    XStocksAdapter,
    rank_xstocks_assets,
)


# ============================================================================
# Fixtures
# ============================================================================


def _make_entry(**overrides) -> SourceAllowlistEntry:
    defaults = {
        "source_id": "xstocks",
        "adapter_name": "xstocks_adapter",
        "domains": ("docs.xstocks.fi", "defi.xstocks.fi"),
        "max_freshness_seconds": 3600,
        "fetch_timeout_seconds": 10,
        "methods": ("GET", "HEAD"),
        "max_response_bytes": 1048576,
        "source_confidence_score": 0.7,
    }
    defaults.update(overrides)
    return SourceAllowlistEntry(**defaults)


def _valid_points_entry() -> dict[str, Any]:
    """A valid xStocks points/holding opportunity."""
    return {
        "symbol": "TSLAx",
        "chain": "Base",
        "type": "points",
        "protocol": "xstocks",
        "venue": "xstocks",
        "apy": 0,
        "fee_apr": 0,
        "reward_apr": 0,
        "tvl_usd": 0,
        "volume_24h_usd": 0,
        "pool_address": None,
        "token_addresses": ["0x" + "a1" * 20],
        "source_url": "https://defi.xstocks.fi/points/TSLAx",
    }


def _valid_lp_entry() -> dict[str, Any]:
    """A valid xStocks LP opportunity."""
    return {
        "symbol": "TSLAx-USDC",
        "chain": "Base",
        "type": "lp",
        "protocol": "xstocks",
        "venue": "xstocks",
        "apy": 12.5,
        "fee_apr": 8.0,
        "reward_apr": 4.5,
        "tvl_usd": 500_000.0,
        "volume_24h_usd": 50_000.0,
        "pool_address": "0x" + "b1" * 20,
        "token_addresses": ["0x" + "c1" * 20, "0x" + "d1" * 20],
        "source_url": "https://defi.xstocks.fi/pools/TSLAx-USDC",
    }


def _dynamic_api_asset(symbol: str = "NEWx", **overrides) -> dict[str, Any]:
    """A dynamically discovered xStocks asset from API."""
    base = {
        "symbol": symbol,
        "chain": "Base",
        "type": "points",
        "protocol": "xstocks",
        "venue": "xstocks",
        "apy": 0,
        "fee_apr": 0,
        "reward_apr": 0,
        "tvl_usd": 0,
        "volume_24h_usd": 100_000.0,
        "market_cap_usd": 5_000_000.0,
        "contract_address": "0x" + "ff" * 20,
        "contract_addresses": ["0x" + "ff" * 20],
        "supported_networks": ["Base"],
        "is_trading_halted": False,
        "stablecoin_support": True,
        "source_url": "https://defi.xstocks.fi/assets/NEWx",
    }
    base.update(overrides)
    return base


def _adapter(confidence: float | None = None) -> XStocksAdapter:
    return XStocksAdapter(source_confidence_score=confidence)


# ============================================================================
# Tests: build_urls
# ============================================================================


class TestBuildUrls:
    """build_urls returns allowlisted xStocks URLs only."""

    def test_returns_urls_for_xstocks_domains(self) -> None:
        entry = _make_entry()
        adapter = _adapter()
        urls = adapter.build_urls(entry)
        assert len(urls) == 2
        assert "https://docs.xstocks.fi/api/v1/opportunities" in urls
        assert "https://defi.xstocks.fi/api/v1/opportunities" in urls

    def test_skips_non_xstocks_domains(self) -> None:
        entry = _make_entry(domains=("defi.xstocks.fi", "example.com"))
        adapter = _adapter()
        urls = adapter.build_urls(entry)
        assert len(urls) == 1
        assert "xstocks" in urls[0]

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
        entry = _valid_points_entry()
        raw = json.dumps({"data": [entry]}).encode("utf-8")
        adapter = _adapter()
        result = adapter.normalize(raw)
        assert len(result) == 1

    def test_accepts_str_json(self) -> None:
        entry = _valid_points_entry()
        raw = json.dumps({"data": [entry]})
        adapter = _adapter()
        result = adapter.normalize(raw)
        assert len(result) == 1

    def test_accepts_dict_with_data_key(self) -> None:
        entry = _valid_points_entry()
        adapter = _adapter()
        result = adapter.normalize({"data": [entry]})
        assert len(result) == 1

    def test_accepts_dict_with_opportunities_key(self) -> None:
        entry = _valid_points_entry()
        adapter = _adapter()
        result = adapter.normalize({"opportunities": [entry]})
        assert len(result) == 1

    def test_accepts_dict_with_assets_key(self) -> None:
        entry = _dynamic_api_asset("TSLAx")
        adapter = _adapter()
        result = adapter.normalize({"assets": [entry]})
        assert len(result) == 1

    def test_accepts_list_directly(self) -> None:
        entry = _valid_points_entry()
        adapter = _adapter()
        result = adapter.normalize([entry])
        assert len(result) == 1

    def test_accepts_single_dict(self) -> None:
        entry = _valid_points_entry()
        adapter = _adapter()
        result = adapter.normalize(entry)
        assert len(result) == 1


# ============================================================================
# Tests: valid xstocks_points candidate
# ============================================================================


class TestXStocksPoints:
    """Valid xstocks_points candidate normalizes correctly."""

    def test_points_normalizes(self) -> None:
        entry = _valid_points_entry()
        adapter = _adapter()
        result = adapter.normalize([entry])
        assert len(result) == 1
        c = result[0]
        assert c.strategy_type == "xstocks_points"
        assert c.chain == "Base"
        assert c.source_id == "xstocks"
        assert c.adapter_name == "xstocks_adapter"

    def test_points_does_not_fabricate_apy(self) -> None:
        entry = _valid_points_entry()
        adapter = _adapter()
        result = adapter.normalize([entry])
        c = result[0]
        assert c.advertised_apy == 0
        assert c.fee_apr == 0
        assert c.reward_apr == 0

    def test_points_tvl_zero_allowed(self) -> None:
        entry = _valid_points_entry()
        entry["tvl_usd"] = 0
        adapter = _adapter()
        result = adapter.normalize([entry])
        assert len(result) == 1
        assert result[0].tvl_usd == 0

    def test_nvdax_points(self) -> None:
        entry = _valid_points_entry()
        entry["symbol"] = "NVDAx"
        adapter = _adapter()
        result = adapter.normalize([entry])
        assert len(result) == 1

    def test_preopai_points(self) -> None:
        entry = _valid_points_entry()
        entry["symbol"] = "preOPAI"
        adapter = _adapter()
        result = adapter.normalize([entry])
        assert len(result) == 1

    def test_prespax_points(self) -> None:
        entry = _valid_points_entry()
        entry["symbol"] = "preSPAX"
        adapter = _adapter()
        result = adapter.normalize([entry])
        assert len(result) == 1

    def test_alias_normalization_tslaon(self) -> None:
        entry = _valid_points_entry()
        entry["symbol"] = "TSLAON"
        adapter = _adapter()
        result = adapter.normalize([entry])
        assert len(result) == 1
        # TSLAON should canonicalize to TSLAX in venue_id
        assert "TSLAX" in result[0].venue_id.upper()


# ============================================================================
# Tests: valid xstocks_lp candidate
# ============================================================================


class TestXStocksLP:
    """Valid xstocks_lp candidate normalizes correctly."""

    def test_lp_normalizes(self) -> None:
        entry = _valid_lp_entry()
        adapter = _adapter()
        result = adapter.normalize([entry])
        assert len(result) == 1
        c = result[0]
        assert c.strategy_type == "xstocks_lp"
        assert c.advertised_apy == 12.5
        assert c.fee_apr == 8.0
        assert c.reward_apr == 4.5
        assert c.tvl_usd == 500_000.0

    def test_lp_pool_address_preserved(self) -> None:
        entry = _valid_lp_entry()
        adapter = _adapter()
        result = adapter.normalize([entry])
        assert result[0].pool_address == "0x" + "b1" * 20

    def test_lp_token_addresses_preserved(self) -> None:
        entry = _valid_lp_entry()
        adapter = _adapter()
        result = adapter.normalize([entry])
        assert len(result[0].token_addresses) == 2

    def test_lp_requires_tvl(self) -> None:
        entry = _valid_lp_entry()
        entry["tvl_usd"] = 0
        adapter = _adapter()
        result = adapter.normalize([entry])
        assert result == []

    def test_lp_requires_valid_apy(self) -> None:
        entry = _valid_lp_entry()
        del entry["apy"]
        # Without apy, defaults to 0 which is valid
        adapter = _adapter()
        result = adapter.normalize([entry])
        if result:
            assert result[0].advertised_apy == 0


# ============================================================================
# Tests: dynamic discovery — Phase 5.1b
# ============================================================================


class TestDynamicDiscovery:
    """Dynamic xStocks API-style asset list normalizes without hardcoded rejection."""

    def test_unknown_valid_symbol_accepted(self) -> None:
        """An unknown but valid xStock symbol is accepted if API metadata is valid."""
        entry = _dynamic_api_asset("COINBASEx")
        adapter = _adapter()
        result = adapter.normalize([entry])
        assert len(result) == 1
        assert result[0].source_id == "xstocks"

    def test_completely_new_symbol_accepted(self) -> None:
        """A brand new tokenized asset not in any alias map is accepted."""
        entry = _dynamic_api_asset("SPYx")
        adapter = _adapter()
        result = adapter.normalize([entry])
        assert len(result) == 1
        assert "SPYx".upper() in result[0].venue_id.upper()

    def test_dynamic_asset_with_contract_address(self) -> None:
        entry = _dynamic_api_asset("QQQx", contract_address="0x" + "ee" * 20)
        adapter = _adapter()
        result = adapter.normalize([entry])
        assert len(result) == 1
        # contract_address should be used as token_addresses
        assert "0x" + "ee" * 20 in result[0].token_addresses

    def test_trading_halted_asset_skipped(self) -> None:
        entry = _dynamic_api_asset("HALTEDx", is_trading_halted=True)
        adapter = _adapter()
        result = adapter.normalize([entry])
        assert result == []

    def test_missing_contract_address_non_points_skipped(self) -> None:
        """Non-points entry without any contract/token reference is skipped."""
        entry = {
            "symbol": "NOCONTRACTx",
            "chain": "Base",
            "type": "lp",
            "apy": 5.0,
            "tvl_usd": 100_000.0,
            "pool_address": None,
            "token_addresses": None,
            "contract_address": None,
        }
        adapter = _adapter()
        result = adapter.normalize([entry])
        assert result == []

    def test_coingecko_style_category_response(self) -> None:
        """CoinGecko-style category response can be normalized."""
        assets = [
            {
                "symbol": "TSLAx",
                "chain": "Base",
                "type": "points",
                "market_cap_usd": 10_000_000,
                "volume_24h_usd": 500_000,
                "contract_address": "0x" + "aa" * 20,
                "is_trading_halted": False,
            },
            {
                "symbol": "NVDAx",
                "chain": "Base",
                "type": "points",
                "market_cap_usd": 8_000_000,
                "volume_24h_usd": 300_000,
                "contract_address": "0x" + "bb" * 20,
                "is_trading_halted": False,
            },
        ]
        adapter = _adapter()
        result = adapter.normalize({"assets": assets})
        assert len(result) == 2

    def test_dynamic_api_full_asset_list(self) -> None:
        """A full dynamic API response with mixed known/unknown assets."""
        assets = [
            _dynamic_api_asset("TSLAx"),
            _dynamic_api_asset("NVDAx"),
            _dynamic_api_asset("NEWTOKENx"),  # unknown but valid
            _dynamic_api_asset("FUTUREx"),  # unknown but valid
        ]
        adapter = _adapter()
        result = adapter.normalize(assets)
        assert len(result) == 4  # All accepted


# ============================================================================
# Tests: ranking — Phase 5.1b
# ============================================================================


class TestRanking:
    """rank_xstocks_assets ranks by quality signals."""

    def test_higher_volume_ranks_first(self) -> None:
        assets = [
            _dynamic_api_asset("LOWx", volume_24h_usd=1_000),
            _dynamic_api_asset("HIGHx", volume_24h_usd=10_000_000),
        ]
        ranked = rank_xstocks_assets(assets)
        assert ranked[0]["symbol"] == "HIGHx"

    def test_higher_market_cap_ranks_first(self) -> None:
        assets = [
            _dynamic_api_asset("SMALLx", market_cap_usd=100_000, volume_24h_usd=0),
            _dynamic_api_asset("BIGx", market_cap_usd=100_000_000, volume_24h_usd=0),
        ]
        ranked = rank_xstocks_assets(assets)
        assert ranked[0]["symbol"] == "BIGx"

    def test_halted_ranks_last(self) -> None:
        assets = [
            _dynamic_api_asset("HALTEDx", is_trading_halted=True, volume_24h_usd=999_999),
            _dynamic_api_asset("ACTIVEx", is_trading_halted=False, volume_24h_usd=1_000),
        ]
        ranked = rank_xstocks_assets(assets)
        assert ranked[0]["symbol"] == "ACTIVEx"

    def test_supported_chain_bonus(self) -> None:
        assets = [
            _dynamic_api_asset("UNKNOWNx", chain="Polygon", volume_24h_usd=100_000),
            _dynamic_api_asset("BASEx", chain="Base", volume_24h_usd=100_000),
        ]
        ranked = rank_xstocks_assets(assets)
        assert ranked[0]["symbol"] == "BASEx"

    def test_contract_address_bonus(self) -> None:
        assets = [
            _dynamic_api_asset("NOCONx", contract_address=None, contract_addresses=None,
                               volume_24h_usd=100_000, market_cap_usd=1_000_000),
            _dynamic_api_asset("CONx", contract_address="0x" + "ab" * 20,
                               volume_24h_usd=100_000, market_cap_usd=1_000_000),
        ]
        ranked = rank_xstocks_assets(assets)
        assert ranked[0]["symbol"] == "CONx"

    def test_empty_list_returns_empty(self) -> None:
        assert rank_xstocks_assets([]) == []

    def test_non_dict_entries_skipped(self) -> None:
        ranked = rank_xstocks_assets(["not a dict", 42])
        assert ranked == []


# ============================================================================
# Tests: skipped entries
# ============================================================================


class TestSkippedEntries:
    """Invalid or unsupported entries are skipped."""

    def test_unsupported_chain_skipped(self) -> None:
        entry = _valid_points_entry()
        entry["chain"] = "Ethereum"
        adapter = _adapter()
        result = adapter.normalize([entry])
        assert result == []

    def test_malformed_entry_skipped(self) -> None:
        adapter = _adapter()
        result = adapter.normalize(["not a dict", 42, None])
        assert result == []

    def test_missing_symbol_skipped(self) -> None:
        entry = _valid_points_entry()
        del entry["symbol"]
        adapter = _adapter()
        result = adapter.normalize([entry])
        assert result == []

    def test_lp_missing_tvl_skipped(self) -> None:
        entry = _valid_lp_entry()
        entry["tvl_usd"] = 0
        adapter = _adapter()
        result = adapter.normalize([entry])
        assert result == []

    def test_invalid_apy_skipped(self) -> None:
        entry = _valid_lp_entry()
        entry["apy"] = "not_a_number"
        adapter = _adapter()
        result = adapter.normalize([entry])
        assert result == []

    def test_apy_above_100_skipped(self) -> None:
        entry = _valid_lp_entry()
        entry["apy"] = 150.0
        adapter = _adapter()
        result = adapter.normalize([entry])
        assert result == []

    def test_invalid_json_bytes(self) -> None:
        adapter = _adapter()
        result = adapter.normalize(b"not json {{{")
        assert result == []

    def test_invalid_json_str(self) -> None:
        adapter = _adapter()
        result = adapter.normalize("not json {{{")
        assert result == []


# ============================================================================
# Tests: schema validation and hashing
# ============================================================================


class TestSchemaValidation:
    """Candidates pass NormalizedCandidate validation with deterministic hashes."""

    def test_points_passes_validation(self) -> None:
        entry = _valid_points_entry()
        adapter = _adapter()
        result = adapter.normalize([entry])
        for c in result:
            validate_candidate(c)

    def test_lp_passes_validation(self) -> None:
        entry = _valid_lp_entry()
        adapter = _adapter()
        result = adapter.normalize([entry])
        for c in result:
            validate_candidate(c)

    def test_dynamic_asset_passes_validation(self) -> None:
        entry = _dynamic_api_asset("NEWx")
        adapter = _adapter()
        result = adapter.normalize([entry])
        for c in result:
            validate_candidate(c)

    def test_hash_deterministic(self) -> None:
        entry = _valid_lp_entry()
        adapter = _adapter()
        r1 = adapter.normalize([entry])
        r2 = adapter.normalize([entry])
        assert r1[0].hash_sha256() == r2[0].hash_sha256()

    def test_different_entries_different_hashes(self) -> None:
        e1 = _valid_lp_entry()
        e2 = _valid_lp_entry()
        e2["tvl_usd"] = 999_999.0
        adapter = _adapter()
        r1 = adapter.normalize([e1])
        r2 = adapter.normalize([e2])
        assert r1[0].hash_sha256() != r2[0].hash_sha256()


# ============================================================================
# Tests: adapter identity
# ============================================================================


class TestAdapterIdentity:
    """Adapter source_id and adapter_name are correct."""

    def test_source_id(self) -> None:
        assert _adapter().source_id == "xstocks"

    def test_adapter_name(self) -> None:
        assert _adapter().adapter_name == "xstocks_adapter"

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

        entry = _valid_points_entry()
        adapter = _adapter()
        with patch.object(
            socket,
            "create_connection",
            side_effect=AssertionError("network call detected"),
        ):
            result = adapter.normalize([entry])
            assert len(result) == 1

    def test_no_signing_imports(self) -> None:
        import defi_autonomy.sources.xstocks_adapter as mod
        with open(mod.__file__, "r") as f:
            source = f.read()
        forbidden = (
            "eth_account", "solders", "nacl", "cryptography.hazmat",
            "sign_transaction", "broadcast_transaction",
            "private_key", "mnemonic",
        )
        for term in forbidden:
            assert term not in source, f"forbidden term {term!r} found"
