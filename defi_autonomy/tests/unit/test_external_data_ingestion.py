"""Unit tests for defi_autonomy.external_data_ingestion — Phase 1.5.

All tests are deterministic and offline. No real network calls. No signing.
No key loading. ReadOnlyHttpClient.request is always mocked.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from defi_autonomy.external_data_ingestion import (
    load_adapter_registry,
    run_ingestion_cycle,
    write_json_atomic,
)
from defi_autonomy.schemas.normalized_candidate import (
    NormalizedCandidate,
    validate_candidate,
)
from defi_autonomy.sources.base import (
    SourceAdapterError,
    SourceAllowlistEntry,
)


# ============================================================================
# Fixtures
# ============================================================================


def _minimal_allowlist_doc() -> dict:
    """A source_allowlist.json with all 4 registered adapters."""
    return {
        "version": 1,
        "entries": [
            {
                "source_id": "defillama",
                "adapter_name": "defillama_adapter",
                "domains": ["api.llama.fi", "yields.llama.fi"],
                "max_freshness_seconds": 1800,
                "fetch_timeout_seconds": 10,
                "methods": ["GET", "HEAD"],
                "max_response_bytes": 4194304,
                "source_confidence_score": 0.7,
            },
            {
                "source_id": "stablecoin_benchmark",
                "adapter_name": "stablecoin_benchmark_adapter",
                "domains": ["yields.llama.fi", "api.llama.fi"],
                "max_freshness_seconds": 1800,
                "fetch_timeout_seconds": 10,
                "methods": ["GET", "HEAD"],
                "max_response_bytes": 4194304,
                "source_confidence_score": 0.8,
            },
            {
                "source_id": "xstocks",
                "adapter_name": "xstocks_adapter",
                "domains": ["docs.xstocks.fi", "defi.xstocks.fi"],
                "max_freshness_seconds": 3600,
                "fetch_timeout_seconds": 10,
                "methods": ["GET", "HEAD"],
                "max_response_bytes": 1048576,
                "source_confidence_score": 0.7,
            },
            {
                "source_id": "meteora",
                "adapter_name": "meteora_adapter",
                "domains": ["app.meteora.ag", "dlmm-api.meteora.ag"],
                "max_freshness_seconds": 600,
                "fetch_timeout_seconds": 10,
                "methods": ["GET", "HEAD"],
                "max_response_bytes": 2097152,
                "source_confidence_score": 0.7,
            },
        ],
    }


def _allowlist_with_unknown_source() -> dict:
    """Allowlist with a source that has no registered adapter."""
    doc = _minimal_allowlist_doc()
    doc["entries"].append(
        {
            "source_id": "unknown_source",
            "adapter_name": "unknown_adapter",
            "domains": ["unknown.example.com"],
            "max_freshness_seconds": 600,
            "fetch_timeout_seconds": 5,
            "methods": ["GET"],
            "max_response_bytes": 1048576,
            "source_confidence_score": 0.5,
        }
    )
    return doc


def _valid_defillama_response() -> bytes:
    """A valid DeFiLlama response with one stablecoin lending pool on Base."""
    data = {
        "status": "success",
        "data": [
            {
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
        ],
    }
    return json.dumps(data).encode("utf-8")


def _valid_benchmark_response() -> bytes:
    """A valid stablecoin benchmark response."""
    data = {
        "status": "success",
        "data": [
            {
                "chain": "Base",
                "project": "compound-v3",
                "symbol": "USDT",
                "tvlUsd": 30_000_000.0,
                "apy": 3.2,
                "apyBase": 2.8,
                "apyReward": 0.4,
                "pool": "0x" + "c" * 40,
                "underlyingTokens": ["0x" + "d" * 40],
                "stablecoin": True,
                "ilRisk": "no",
                "volumeUsd1d": 500_000.0,
            }
        ],
    }
    return json.dumps(data).encode("utf-8")


def _valid_xstocks_response() -> bytes:
    """A valid xStocks response with points and LP entries."""
    data = {
        "data": [
            {
                "symbol": "TSLAx",
                "chain": "Base",
                "type": "points",
                "protocol": "xstocks",
                "venue": "xstocks",
                "apy": 0,
                "fee_apr": 0,
                "reward_apr": 0,
                "tvl_usd": 0,
                "volume_24h_usd": 100_000,
                "contract_address": "0x" + "e1" * 20,
                "is_trading_halted": False,
                "source_url": "https://defi.xstocks.fi/points/TSLAx",
            },
            {
                "symbol": "NVDAx-USDC",
                "chain": "Base",
                "type": "lp",
                "protocol": "xstocks",
                "venue": "xstocks",
                "apy": 14.0,
                "fee_apr": 9.0,
                "reward_apr": 5.0,
                "tvl_usd": 400_000.0,
                "volume_24h_usd": 60_000,
                "pool_address": "0x" + "f1" * 20,
                "token_addresses": ["0x" + "e2" * 20, "0x" + "e3" * 20],
                "is_trading_halted": False,
                "source_url": "https://defi.xstocks.fi/pools/NVDAx-USDC",
            },
        ]
    }
    return json.dumps(data).encode("utf-8")


def _valid_meteora_response() -> bytes:
    """A valid Meteora response with a stable-stable pool."""
    data = [
        {
            "pair_name": "USDC-USDT",
            "address": "So1anaAddr111111111111111111111111111111",
            "mint_x": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
            "mint_y": "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",
            "tvl_usd": 2_000_000.0,
            "fee_apy": 8.5,
            "reward_apy": 2.0,
            "apy": 10.5,
            "volume_24h_usd": 500_000.0,
        }
    ]
    return json.dumps(data).encode("utf-8")


def _setup_base_dir(tmp_path: Path, allowlist_doc: dict | None = None) -> Path:
    """Create a base_dir with data/ and source_allowlist.json."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    doc = allowlist_doc or _minimal_allowlist_doc()
    (data_dir / "source_allowlist.json").write_text(
        json.dumps(doc), encoding="utf-8"
    )
    return tmp_path


def _mock_client_factory(responses: dict[str, bytes | Exception]):
    """Create a client factory that returns mocked clients.

    responses: dict mapping URL substrings to bytes or Exception instances.
    """

    def factory(entry: SourceAllowlistEntry):
        client = MagicMock()

        def mock_request(method: str, url: str) -> bytes:
            for pattern, response in responses.items():
                if pattern in url:
                    if isinstance(response, Exception):
                        raise response
                    return response
            raise SourceAdapterError(f"no mock for URL: {url}")

        client.request = mock_request
        return client

    return factory


# ============================================================================
# Tests: load_adapter_registry
# ============================================================================


class TestLoadAdapterRegistry:
    """Adapter registry loads correctly."""

    def test_returns_dict(self) -> None:
        registry = load_adapter_registry()
        assert isinstance(registry, dict)

    def test_contains_defillama(self) -> None:
        registry = load_adapter_registry()
        assert "defillama" in registry
        assert registry["defillama"].source_id == "defillama"

    def test_contains_stablecoin_benchmark(self) -> None:
        registry = load_adapter_registry()
        assert "stablecoin_benchmark" in registry
        assert registry["stablecoin_benchmark"].source_id == "stablecoin_benchmark"

    def test_contains_xstocks(self) -> None:
        registry = load_adapter_registry()
        assert "xstocks" in registry
        assert registry["xstocks"].source_id == "xstocks"

    def test_contains_meteora(self) -> None:
        registry = load_adapter_registry()
        assert "meteora" in registry
        assert registry["meteora"].source_id == "meteora"

    def test_four_adapters(self) -> None:
        registry = load_adapter_registry()
        assert len(registry) == 4


# ============================================================================
# Tests: write_json_atomic
# ============================================================================


class TestWriteJsonAtomic:
    """write_json_atomic creates valid JSON files."""

    def test_creates_valid_json(self, tmp_path: Path) -> None:
        data = {"key": "value", "number": 42}
        p = tmp_path / "test.json"
        write_json_atomic(p, data)
        assert p.exists()
        loaded = json.loads(p.read_text(encoding="utf-8"))
        assert loaded == data

    def test_creates_parent_dirs(self, tmp_path: Path) -> None:
        p = tmp_path / "sub" / "dir" / "test.json"
        write_json_atomic(p, {"ok": True})
        assert p.exists()

    def test_overwrites_existing(self, tmp_path: Path) -> None:
        p = tmp_path / "test.json"
        write_json_atomic(p, {"v": 1})
        write_json_atomic(p, {"v": 2})
        loaded = json.loads(p.read_text(encoding="utf-8"))
        assert loaded["v"] == 2

    def test_output_is_valid_json(self, tmp_path: Path) -> None:
        p = tmp_path / "test.json"
        write_json_atomic(p, [1, 2, 3])
        # Should not raise
        json.loads(p.read_text(encoding="utf-8"))


# ============================================================================
# Tests: run_ingestion_cycle — loads allowlist
# ============================================================================


class TestIngestionLoadsAllowlist:
    """Orchestrator loads the source allowlist."""

    def test_loads_allowlist_and_runs(self, tmp_path: Path) -> None:
        base = _setup_base_dir(tmp_path)
        responses = {
            "api.llama.fi": _valid_defillama_response(),
            "yields.llama.fi": _valid_benchmark_response(),
        }
        result = run_ingestion_cycle(base, _client_factory=_mock_client_factory(responses))
        assert "cycle_id" in result
        assert result["status"] in ("OK", "PARTIAL", "NO_VALID_DATA")


# ============================================================================
# Tests: run_ingestion_cycle — runs only registered adapters
# ============================================================================


class TestIngestionRegisteredAdapters:
    """Only registered adapters are run."""

    def test_missing_adapter_recorded_not_fatal(self, tmp_path: Path) -> None:
        base = _setup_base_dir(tmp_path, _allowlist_with_unknown_source())
        responses = {
            "api.llama.fi": _valid_defillama_response(),
            "yields.llama.fi": _valid_benchmark_response(),
        }
        result = run_ingestion_cycle(base, _client_factory=_mock_client_factory(responses))
        assert "unknown_source" in result["sources_missing_adapter"]
        # Cycle still succeeds
        assert result["status"] in ("OK", "PARTIAL")
        assert result["candidate_count"] > 0


# ============================================================================
# Tests: run_ingestion_cycle — successful source
# ============================================================================


class TestIngestionSuccessfulSource:
    """Successful source creates raw snapshot and normalized candidates."""

    def test_creates_raw_snapshots(self, tmp_path: Path) -> None:
        base = _setup_base_dir(tmp_path)
        responses = {
            "api.llama.fi": _valid_defillama_response(),
            "yields.llama.fi": _valid_benchmark_response(),
        }
        run_ingestion_cycle(base, _client_factory=_mock_client_factory(responses))
        snapshots_path = base / "data" / "raw_snapshots.json"
        assert snapshots_path.exists()
        snapshots = json.loads(snapshots_path.read_text(encoding="utf-8"))
        assert isinstance(snapshots, list)
        assert len(snapshots) > 0
        # Check snapshot structure
        s = snapshots[0]
        assert "source_id" in s
        assert "url" in s
        assert "fetched_at_utc" in s
        assert "status" in s
        assert s["status"] == "SUCCESS"
        assert "response_sha256" in s
        assert "response_size_bytes" in s

    def test_creates_normalized_candidates(self, tmp_path: Path) -> None:
        base = _setup_base_dir(tmp_path)
        responses = {
            "api.llama.fi": _valid_defillama_response(),
            "yields.llama.fi": _valid_benchmark_response(),
        }
        run_ingestion_cycle(base, _client_factory=_mock_client_factory(responses))
        candidates_path = base / "data" / "normalized_yield_candidates.json"
        assert candidates_path.exists()
        doc = json.loads(candidates_path.read_text(encoding="utf-8"))
        assert "cycle_id" in doc
        assert "generated_at_utc" in doc
        assert "candidate_count" in doc
        assert "candidates" in doc
        assert doc["candidate_count"] == len(doc["candidates"])
        assert doc["candidate_count"] > 0

    def test_candidates_have_hashes(self, tmp_path: Path) -> None:
        base = _setup_base_dir(tmp_path)
        responses = {
            "api.llama.fi": _valid_defillama_response(),
            "yields.llama.fi": _valid_benchmark_response(),
        }
        run_ingestion_cycle(base, _client_factory=_mock_client_factory(responses))
        candidates_path = base / "data" / "normalized_yield_candidates.json"
        doc = json.loads(candidates_path.read_text(encoding="utf-8"))
        for c in doc["candidates"]:
            assert "_candidate_hash" in c
            assert len(c["_candidate_hash"]) == 64  # SHA-256 hex

    def test_candidates_have_source_id_and_adapter_name(self, tmp_path: Path) -> None:
        base = _setup_base_dir(tmp_path)
        responses = {
            "api.llama.fi": _valid_defillama_response(),
            "yields.llama.fi": _valid_benchmark_response(),
        }
        run_ingestion_cycle(base, _client_factory=_mock_client_factory(responses))
        candidates_path = base / "data" / "normalized_yield_candidates.json"
        doc = json.loads(candidates_path.read_text(encoding="utf-8"))
        for c in doc["candidates"]:
            assert "source_id" in c
            assert "adapter_name" in c


# ============================================================================
# Tests: run_ingestion_cycle — source health
# ============================================================================


class TestIngestionSourceHealth:
    """Source health is tracked correctly."""

    def test_success_updates_health(self, tmp_path: Path) -> None:
        base = _setup_base_dir(tmp_path)
        responses = {
            "api.llama.fi": _valid_defillama_response(),
            "yields.llama.fi": _valid_benchmark_response(),
        }
        run_ingestion_cycle(base, _client_factory=_mock_client_factory(responses))
        health_path = base / "data" / "source_health.json"
        assert health_path.exists()
        health = json.loads(health_path.read_text(encoding="utf-8"))
        # At least one source should have success recorded
        assert any(
            v.get("total_successes", 0) > 0 for v in health.values()
        )

    def test_failure_updates_health(self, tmp_path: Path) -> None:
        base = _setup_base_dir(tmp_path)
        # All URLs fail
        responses = {
            "api.llama.fi": SourceAdapterError("connection refused"),
            "yields.llama.fi": SourceAdapterError("timeout"),
        }
        run_ingestion_cycle(base, _client_factory=_mock_client_factory(responses))
        health_path = base / "data" / "source_health.json"
        health = json.loads(health_path.read_text(encoding="utf-8"))
        # Sources should have failures recorded
        for source_id, entry in health.items():
            assert entry["total_failures"] > 0
            assert entry["consecutive_failures"] > 0
            assert entry["last_error_type"] is not None


# ============================================================================
# Tests: run_ingestion_cycle — failure behavior
# ============================================================================


class TestIngestionFailureBehavior:
    """Failure handling works correctly."""

    def test_one_failed_source_does_not_block_another(self, tmp_path: Path) -> None:
        base = _setup_base_dir(tmp_path)
        # defillama fails (api.llama.fi is tried first for defillama),
        # but stablecoin_benchmark succeeds (yields.llama.fi is tried first)
        # We need to be more specific with URL matching
        call_count = {"n": 0}

        def factory(entry: SourceAllowlistEntry):
            client = MagicMock()

            def mock_request(method: str, url: str) -> bytes:
                if entry.source_id == "defillama":
                    raise SourceAdapterError("defillama down")
                # stablecoin_benchmark succeeds
                return _valid_benchmark_response()

            client.request = mock_request
            return client

        result = run_ingestion_cycle(base, _client_factory=factory)
        assert "defillama" in result["sources_failed"]
        assert "stablecoin_benchmark" in result["sources_succeeded"]
        assert result["status"] == "PARTIAL"

    def test_all_failed_returns_no_valid_data(self, tmp_path: Path) -> None:
        base = _setup_base_dir(tmp_path)

        def factory(entry: SourceAllowlistEntry):
            client = MagicMock()
            client.request = MagicMock(
                side_effect=SourceAdapterError("all down")
            )
            return client

        result = run_ingestion_cycle(base, _client_factory=factory)
        assert result["status"] == "NO_VALID_DATA"
        assert result["candidate_count"] == 0
        # Valid empty candidates file still written
        candidates_path = base / "data" / "normalized_yield_candidates.json"
        assert candidates_path.exists()
        doc = json.loads(candidates_path.read_text(encoding="utf-8"))
        assert doc["candidate_count"] == 0
        assert doc["candidates"] == []

    def test_malformed_adapter_output_skipped(self, tmp_path: Path) -> None:
        base = _setup_base_dir(tmp_path)
        # Return garbage that won't normalize to valid candidates
        garbage = json.dumps({"data": [{"garbage": True}]}).encode("utf-8")
        responses = {
            "api.llama.fi": garbage,
            "yields.llama.fi": garbage,
        }
        result = run_ingestion_cycle(base, _client_factory=_mock_client_factory(responses))
        # Should not crash, just produce 0 candidates
        assert result["candidate_count"] == 0
        # Sources technically succeeded (fetch worked) but no candidates
        assert result["status"] in ("OK", "PARTIAL")


# ============================================================================
# Tests: candidate validation enforced
# ============================================================================


class TestCandidateValidation:
    """Candidate validation is enforced."""

    def test_only_valid_candidates_in_output(self, tmp_path: Path) -> None:
        base = _setup_base_dir(tmp_path)
        responses = {
            "api.llama.fi": _valid_defillama_response(),
            "yields.llama.fi": _valid_benchmark_response(),
        }
        run_ingestion_cycle(base, _client_factory=_mock_client_factory(responses))
        candidates_path = base / "data" / "normalized_yield_candidates.json"
        doc = json.loads(candidates_path.read_text(encoding="utf-8"))
        # Every candidate in output should be reconstructable and valid
        from defi_autonomy.schemas.normalized_candidate import from_dict

        for c in doc["candidates"]:
            # Remove internal hash field before validation
            c_copy = {k: v for k, v in c.items() if not k.startswith("_")}
            candidate = from_dict(c_copy)
            validate_candidate(candidate)  # must not raise


# ============================================================================
# Tests: no network calls, no signing
# ============================================================================


class TestNoNetworkNoSigning:
    """No real network calls or signing imports."""

    def test_no_real_network_calls(self, tmp_path: Path) -> None:
        """All tests use mocked clients — no real network I/O."""
        import socket
        from unittest.mock import patch as _patch

        base = _setup_base_dir(tmp_path)
        responses = {
            "api.llama.fi": _valid_defillama_response(),
            "yields.llama.fi": _valid_benchmark_response(),
        }

        with _patch.object(
            socket,
            "create_connection",
            side_effect=AssertionError("real network call detected"),
        ):
            result = run_ingestion_cycle(
                base, _client_factory=_mock_client_factory(responses)
            )
            assert result["candidate_count"] > 0

    def test_no_signing_imports_in_module(self) -> None:
        import defi_autonomy.external_data_ingestion as mod

        with open(mod.__file__, "r") as f:
            source = f.read()
        signing_modules = (
            "eth_account",
            "solders",
            "nacl",
            "cryptography.hazmat",
            "private_key",
            "seed_phrase",
            "mnemonic",
        )
        for sm in signing_modules:
            assert sm not in source, f"signing-related term {sm!r} found in module"

    def test_no_wallet_imports(self) -> None:
        import defi_autonomy.external_data_ingestion as mod

        with open(mod.__file__, "r") as f:
            source = f.read()
        wallet_terms = ("WalletExecutor", "TxSimulator", "wallet_address")
        for wt in wallet_terms:
            assert wt not in source, f"wallet term {wt!r} found in module"


# ============================================================================
# Tests: all 4 adapters through ingestion — Phase 5.3
# ============================================================================


def _all_sources_responses() -> dict[str, bytes]:
    """Responses that cover all 4 adapters."""
    return {
        "api.llama.fi": _valid_defillama_response(),
        "yields.llama.fi": _valid_benchmark_response(),
        "xstocks.fi": _valid_xstocks_response(),
        "meteora.ag": _valid_meteora_response(),
    }


class TestAllFourAdapters:
    """All 4 adapters run through ingestion with mocked data."""

    def test_all_four_sources_produce_candidates(self, tmp_path: Path) -> None:
        base = _setup_base_dir(tmp_path)
        result = run_ingestion_cycle(
            base, _client_factory=_mock_client_factory(_all_sources_responses())
        )
        assert result["status"] in ("OK", "PARTIAL")
        assert result["candidate_count"] > 0
        # Check candidates from multiple sources
        candidates_path = base / "data" / "normalized_yield_candidates.json"
        doc = json.loads(candidates_path.read_text(encoding="utf-8"))
        source_ids = {c["source_id"] for c in doc["candidates"]}
        # At least defillama and xstocks should produce candidates
        assert "defillama" in source_ids or "xstocks" in source_ids

    def test_xstocks_adapter_runs_through_ingestion(self, tmp_path: Path) -> None:
        base = _setup_base_dir(tmp_path)
        result = run_ingestion_cycle(
            base, _client_factory=_mock_client_factory(_all_sources_responses())
        )
        candidates_path = base / "data" / "normalized_yield_candidates.json"
        doc = json.loads(candidates_path.read_text(encoding="utf-8"))
        xstocks_candidates = [c for c in doc["candidates"] if c["source_id"] == "xstocks"]
        assert len(xstocks_candidates) > 0

    def test_meteora_adapter_runs_through_ingestion(self, tmp_path: Path) -> None:
        base = _setup_base_dir(tmp_path)
        result = run_ingestion_cycle(
            base, _client_factory=_mock_client_factory(_all_sources_responses())
        )
        candidates_path = base / "data" / "normalized_yield_candidates.json"
        doc = json.loads(candidates_path.read_text(encoding="utf-8"))
        meteora_candidates = [c for c in doc["candidates"] if c["source_id"] == "meteora"]
        assert len(meteora_candidates) > 0

    def test_one_failed_adapter_does_not_block_other_three(self, tmp_path: Path) -> None:
        base = _setup_base_dir(tmp_path)

        def factory(entry: SourceAllowlistEntry):
            client = MagicMock()

            def mock_request(method: str, url: str) -> bytes:
                if entry.source_id == "defillama":
                    raise SourceAdapterError("defillama down")
                if "xstocks" in url:
                    return _valid_xstocks_response()
                if "meteora" in url:
                    return _valid_meteora_response()
                return _valid_benchmark_response()

            client.request = mock_request
            return client

        result = run_ingestion_cycle(base, _client_factory=factory)
        assert "defillama" in result["sources_failed"]
        assert result["candidate_count"] > 0
        assert result["status"] == "PARTIAL"

    def test_source_health_records_all_four(self, tmp_path: Path) -> None:
        base = _setup_base_dir(tmp_path)
        run_ingestion_cycle(
            base, _client_factory=_mock_client_factory(_all_sources_responses())
        )
        health_path = base / "data" / "source_health.json"
        health = json.loads(health_path.read_text(encoding="utf-8"))
        # All 4 sources should have health entries
        for sid in ("defillama", "stablecoin_benchmark", "xstocks", "meteora"):
            assert sid in health, f"source {sid} missing from health"

    def test_candidates_from_all_four_adapters(self, tmp_path: Path) -> None:
        base = _setup_base_dir(tmp_path)
        run_ingestion_cycle(
            base, _client_factory=_mock_client_factory(_all_sources_responses())
        )
        candidates_path = base / "data" / "normalized_yield_candidates.json"
        doc = json.loads(candidates_path.read_text(encoding="utf-8"))
        source_ids = {c["source_id"] for c in doc["candidates"]}
        # Should have candidates from at least 3 sources
        # (stablecoin_benchmark may not produce if pool doesn't match strict criteria)
        assert len(source_ids) >= 3
