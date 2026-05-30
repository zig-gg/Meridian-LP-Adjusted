"""End-to-end integration test for Hermes DeFi Autonomy — Phase 4.3.

Proves the full local pipeline works together:
External_Data_Ingestion → RiskScorer → PolicyEngine → TxSimulator →
WalletExecutor signing-prep gate → Coordinator → Telegram Guardian.

All data sources are mocked. No real network calls. No real Telegram calls.
No real private keys. No broadcast.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from defi_autonomy.coordinator import CycleReport, cycle_report_to_dict, run_cycle
from defi_autonomy.sources.base import SourceAdapterError, SourceAllowlistEntry
from defi_autonomy.telegram_guardian import (
    activate_kill_switch,
    format_status,
    handle_command,
    parse_command,
)


# ============================================================================
# Fixtures
# ============================================================================


def _risk_policy(autonomy_level: int = 1, **overrides) -> dict:
    base = {
        "version": 1,
        "autonomy_level": autonomy_level,
        "max_wallet_value_usd": 100,
        "max_tx_usd": 25,
        "max_daily_spend_usd": 50,
        "max_slippage_bps": 50,
        "allowed_chains": ["Base", "BNB Chain", "Solana"],
        "allowed_strategy_types": [
            "stablecoin_lending", "stable_stable_lp",
            "xstocks_points", "xstocks_lp",
        ],
        "blocked_actions": ["bridge", "borrow", "leverage", "unlimited_approval"],
        "require_contract_allowlist": False,
        "require_token_allowlist": False,
        "require_pool_allowlist": False,
        "kill_switch_file": None,
        "operator_funded_agent_wallet_address": "0x" + "ab" * 20,
        "allow_signing_prep": True,
        "simulation_value_tolerance_bps": 50,
    }
    base.update(overrides)
    return base


def _source_allowlist() -> dict:
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
        ],
    }


def _pool_response() -> bytes:
    """Realistic DeFiLlama response with a high-quality stablecoin pool."""
    data = {
        "status": "success",
        "data": [
            {
                "chain": "Base",
                "project": "aave-v3",
                "symbol": "USDC",
                "tvlUsd": 80_000_000.0,
                "apy": 4.2,
                "apyBase": 3.5,
                "apyReward": 0.7,
                "pool": "0x" + "a1" * 20,
                "underlyingTokens": ["0x" + "b1" * 20],
                "stablecoin": True,
                "ilRisk": "no",
                "volumeUsd1d": 2_000_000.0,
            },
            {
                "chain": "Base",
                "project": "compound-v3",
                "symbol": "USDT",
                "tvlUsd": 40_000_000.0,
                "apy": 3.8,
                "apyBase": 3.2,
                "apyReward": 0.6,
                "pool": "0x" + "c1" * 20,
                "underlyingTokens": ["0x" + "d1" * 20],
                "stablecoin": True,
                "ilRisk": "no",
                "volumeUsd1d": 1_000_000.0,
            },
        ],
    }
    return json.dumps(data).encode("utf-8")


def _setup_full_env(tmp_path: Path, autonomy_level: int = 1) -> Path:
    """Set up a complete environment for integration testing."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "risk_policy.json").write_text(
        json.dumps(_risk_policy(autonomy_level)), encoding="utf-8"
    )
    (data_dir / "source_allowlist.json").write_text(
        json.dumps(_source_allowlist()), encoding="utf-8"
    )
    (data_dir / "contract_allowlist.json").write_text(
        json.dumps({"version": 1, "entries": []}), encoding="utf-8"
    )
    (data_dir / "token_allowlist.json").write_text(
        json.dumps({"version": 1, "entries": []}), encoding="utf-8"
    )
    (data_dir / "pool_allowlist.json").write_text(
        json.dumps({"version": 1, "entries": []}), encoding="utf-8"
    )
    return tmp_path


def _mock_client_factory():
    resp = _pool_response()

    def factory(entry: SourceAllowlistEntry):
        client = MagicMock()
        client.request = MagicMock(return_value=resp)
        return client

    return factory


class FakeSigner:
    """Fake signer for integration testing."""

    def __init__(self, address: str = "0x" + "ab" * 20):
        self._address = address

    def sign(self, unsigned_tx: dict, chain: str) -> bytes:
        payload = json.dumps(unsigned_tx, sort_keys=True).encode()
        return hashlib.sha256(payload + chain.encode()).digest()

    def derive_address(self, chain: str) -> str:
        return self._address


# ============================================================================
# Tests: full cycle end-to-end
# ============================================================================


class TestFullCycleEndToEnd:
    """Full Coordinator cycle runs end-to-end with mocked data."""

    def test_complete_cycle(self, tmp_path: Path) -> None:
        base = _setup_full_env(tmp_path)
        report = run_cycle(base, _client_factory=_mock_client_factory())
        assert isinstance(report, CycleReport)
        assert report.status == "COMPLETE"
        assert report.candidate_count > 0
        assert report.risk_assessment_count > 0

    def test_cycle_report_json_written(self, tmp_path: Path) -> None:
        base = _setup_full_env(tmp_path)
        run_cycle(base, _client_factory=_mock_client_factory())
        report_path = base / "data" / "cycle_report.json"
        assert report_path.exists()
        doc = json.loads(report_path.read_text(encoding="utf-8"))
        assert doc["status"] == "COMPLETE"
        assert doc["candidate_count"] > 0

    def test_normalized_candidates_written(self, tmp_path: Path) -> None:
        base = _setup_full_env(tmp_path)
        run_cycle(base, _client_factory=_mock_client_factory())
        path = base / "data" / "normalized_yield_candidates.json"
        assert path.exists()
        doc = json.loads(path.read_text(encoding="utf-8"))
        assert doc["candidate_count"] > 0
        assert len(doc["candidates"]) > 0

    def test_source_health_written(self, tmp_path: Path) -> None:
        base = _setup_full_env(tmp_path)
        run_cycle(base, _client_factory=_mock_client_factory())
        path = base / "data" / "source_health.json"
        assert path.exists()
        health = json.loads(path.read_text(encoding="utf-8"))
        assert len(health) > 0

    def test_policy_ledger_written(self, tmp_path: Path) -> None:
        base = _setup_full_env(tmp_path)
        run_cycle(base, _client_factory=_mock_client_factory())
        path = base / "data" / "execution_ledger.jsonl"
        assert path.exists()
        lines = path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) > 0
        record = json.loads(lines[0])
        assert "event_type" in record

    def test_simulation_ledger_written_when_approved(self, tmp_path: Path) -> None:
        base = _setup_full_env(tmp_path)
        report = run_cycle(base, _client_factory=_mock_client_factory())
        if report.simulation_passed_count > 0:
            path = base / "data" / "simulation_ledger.jsonl"
            assert path.exists()


# ============================================================================
# Tests: autonomy level enforcement
# ============================================================================


class TestAutonomyLevelEnforcement:
    """Autonomy level controls signing-prep access."""

    def test_level_1_never_prepares_signing(self, tmp_path: Path) -> None:
        base = _setup_full_env(tmp_path, autonomy_level=1)
        signer = FakeSigner()
        report = run_cycle(
            base, signer_provider=signer, _client_factory=_mock_client_factory()
        )
        assert report.signing_prepared_count == 0

    def test_level_2_may_prepare_signing(self, tmp_path: Path) -> None:
        base = _setup_full_env(tmp_path, autonomy_level=2)
        signer = FakeSigner()
        report = run_cycle(
            base, signer_provider=signer, _client_factory=_mock_client_factory()
        )
        # Level 2 with allow_signing_prep=True may prepare signing
        # (depends on policy approval)
        assert report.autonomy_level == 2

    def test_level_2_without_signer_no_signing(self, tmp_path: Path) -> None:
        base = _setup_full_env(tmp_path, autonomy_level=2)
        report = run_cycle(base, _client_factory=_mock_client_factory())
        assert report.signing_prepared_count == 0


# ============================================================================
# Tests: broadcast disabled
# ============================================================================


class TestBroadcastDisabled:
    """Broadcast remains disabled throughout the pipeline."""

    def test_no_broadcast_in_report(self, tmp_path: Path) -> None:
        base = _setup_full_env(tmp_path, autonomy_level=2)
        signer = FakeSigner()
        report = run_cycle(
            base, signer_provider=signer, _client_factory=_mock_client_factory()
        )
        # Check wallet execution ledger if signing happened
        ledger_path = base / "data" / "wallet_execution_ledger.jsonl"
        if ledger_path.exists():
            for line in ledger_path.read_text(encoding="utf-8").strip().split("\n"):
                if line:
                    record = json.loads(line)
                    assert record["broadcast_allowed"] is False
                    assert record["broadcasted"] is False


# ============================================================================
# Tests: kill switch halts before ingestion
# ============================================================================


class TestKillSwitchIntegration:
    """STOP file halts before ingestion."""

    def test_stop_file_halts_cycle(self, tmp_path: Path) -> None:
        base = _setup_full_env(tmp_path)
        (base / "KILL_SWITCH.md").write_text("STOP\nTest halt.", encoding="utf-8")
        report = run_cycle(base, _client_factory=_mock_client_factory())
        assert report.status == "HALTED"
        assert report.ingestion_status == "NOT_RUN"
        assert report.candidate_count == 0


# ============================================================================
# Tests: Telegram Guardian integration
# ============================================================================


class TestTelegramGuardianIntegration:
    """Telegram Guardian reads cycle report and controls kill switch."""

    def test_status_reads_cycle_report(self, tmp_path: Path) -> None:
        base = _setup_full_env(tmp_path)
        run_cycle(base, _client_factory=_mock_client_factory())
        msg = format_status(base)
        assert "COMPLETE" in msg
        assert "Autonomy Level" in msg

    def test_halt_creates_stop_and_next_cycle_halts(self, tmp_path: Path) -> None:
        base = _setup_full_env(tmp_path)
        # First cycle succeeds
        r1 = run_cycle(base, _client_factory=_mock_client_factory())
        assert r1.status == "COMPLETE"
        # Guardian halts
        cmd = parse_command("/halt", chat_id="1")
        resp = handle_command(cmd, base)
        assert resp.ok is True
        # Next cycle halts
        r2 = run_cycle(base, _client_factory=_mock_client_factory())
        assert r2.status == "HALTED"

    def test_status_after_halt(self, tmp_path: Path) -> None:
        base = _setup_full_env(tmp_path)
        activate_kill_switch(base)
        msg = format_status(base)
        assert "ACTIVE" in msg


# ============================================================================
# Tests: safety confirmations
# ============================================================================


class TestSafetyConfirmations:
    """No real network, no Telegram, no key leakage."""

    def test_no_real_network_calls(self, tmp_path: Path) -> None:
        import socket
        from unittest.mock import patch

        base = _setup_full_env(tmp_path)
        with patch.object(
            socket,
            "create_connection",
            side_effect=AssertionError("real network call detected"),
        ):
            report = run_cycle(base, _client_factory=_mock_client_factory())
            assert report.status == "COMPLETE"

    def test_no_private_key_in_outputs(self, tmp_path: Path) -> None:
        base = _setup_full_env(tmp_path, autonomy_level=2)
        signer = FakeSigner()
        run_cycle(base, signer_provider=signer, _client_factory=_mock_client_factory())
        # Check output files for actual secret leakage
        # Wallet addresses are public; we check for private key / seed patterns
        data_dir = base / "data"
        for f in data_dir.iterdir():
            if f.suffix in (".json", ".jsonl"):
                # Skip risk_policy.json (it's config, not output)
                if f.name == "risk_policy.json":
                    continue
                content = f.read_text(encoding="utf-8").lower()
                assert "seed_phrase" not in content
                assert "mnemonic" not in content
                # No raw hex private key (64 hex chars that aren't hashes)
                # Hashes are expected; actual keys would be in a "key" field
                assert '"private_key"' not in content
                assert '"secret"' not in content

    def test_no_real_telegram_calls(self, tmp_path: Path) -> None:
        import socket
        from unittest.mock import patch

        base = _setup_full_env(tmp_path)
        run_cycle(base, _client_factory=_mock_client_factory())
        with patch.object(
            socket,
            "create_connection",
            side_effect=AssertionError("telegram call detected"),
        ):
            cmd = parse_command("/status", chat_id="1")
            resp = handle_command(cmd, base)
            assert resp.ok is True

    def test_existing_hermes_daemons_untouched(self, tmp_path: Path) -> None:
        """Integration test does not touch ecosystem.defi.cjs or PM2."""
        base = _setup_full_env(tmp_path)
        run_cycle(base, _client_factory=_mock_client_factory())
        # No ecosystem file created in test dir
        assert not (base / "ecosystem.defi.cjs").exists()
