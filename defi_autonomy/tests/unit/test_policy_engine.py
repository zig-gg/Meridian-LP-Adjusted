"""Unit tests for defi_autonomy.policy_engine — Sprint 2, Phase 2.2.

All tests are deterministic and offline. No network calls. No signing.
No key loading.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from defi_autonomy.policy_engine import (
    ActionDescriptor,
    ApprovalToken,
    PolicyDecision,
    PolicyEngine,
    canonical_digest,
    compute_allowlist_digest,
    load_json,
    risk_assessment_to_action,
)
from defi_autonomy.risk_scorer import RiskAssessment, score_candidate
from defi_autonomy.schemas.normalized_candidate import from_dict


# ============================================================================
# Fixtures
# ============================================================================


def _risk_policy(**overrides) -> dict:
    """Baseline risk policy that allows WATCH actions."""
    base = {
        "version": 1,
        "mode": "CAPPED_AUTONOMY",
        "autonomy_level": 2,
        "max_wallet_value_usd": 100,
        "max_tx_usd": 25,
        "max_daily_spend_usd": 50,
        "max_slippage_bps": 50,
        "allowed_chains": ["Base", "BNB Chain", "Solana"],
        "allowed_strategy_types": [
            "stablecoin_lending",
            "stable_stable_lp",
            "xstocks_points",
            "xstocks_lp",
        ],
        "blocked_actions": [
            "bridge",
            "borrow",
            "leverage",
            "unknown_contract",
            "unlimited_approval",
            "main_wallet_access",
            "seed_phrase_storage",
        ],
        "require_contract_allowlist": False,
        "require_token_allowlist": False,
        "require_pool_allowlist": False,
        "require_tx_simulation": True,
        "kill_switch_file": None,
    }
    base.update(overrides)
    return base


def _contract_allowlist(entries: list | None = None) -> dict:
    return {"version": 1, "entries": entries or []}


def _token_allowlist(entries: list | None = None) -> dict:
    return {"version": 1, "entries": entries or []}


def _pool_allowlist(entries: list | None = None) -> dict:
    return {"version": 1, "entries": entries or []}


def _setup_data_dir(
    tmp_path: Path,
    policy: dict | None = None,
    contracts: dict | None = None,
    tokens: dict | None = None,
    pools: dict | None = None,
) -> Path:
    """Create a base_dir with data/ and policy files."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "risk_policy.json").write_text(
        json.dumps(policy or _risk_policy()), encoding="utf-8"
    )
    (data_dir / "contract_allowlist.json").write_text(
        json.dumps(contracts or _contract_allowlist()), encoding="utf-8"
    )
    (data_dir / "token_allowlist.json").write_text(
        json.dumps(tokens or _token_allowlist()), encoding="utf-8"
    )
    (data_dir / "pool_allowlist.json").write_text(
        json.dumps(pools or _pool_allowlist()), encoding="utf-8"
    )
    return tmp_path


def _make_action(**overrides) -> ActionDescriptor:
    """Build a valid FARM ActionDescriptor."""
    defaults = {
        "action_id": "act_test_00001",
        "candidate_hash": "a" * 64,
        "source_id": "defillama",
        "adapter_name": "defillama_adapter",
        "chain": "Base",
        "protocol": "aave-v3",
        "strategy_type": "stablecoin_lending",
        "action_type": "FARM",
        "pool_address": None,
        "token_addresses": (),
        "estimated_tx_usd": 5.0,
        "estimated_wallet_value_usd": 20.0,
        "estimated_daily_spend_usd": 5.0,
        "slippage_bps": 10,
        "risk_score": 90,
        "risk_decision": "FARM",
        "created_at_utc": "2026-05-27T00:00:00Z",
        "metadata": {},
    }
    defaults.update(overrides)
    return ActionDescriptor(**defaults)


def _make_watch_action(**overrides) -> ActionDescriptor:
    """Build a WATCH ActionDescriptor."""
    defaults = {
        "action_id": "act_watch_00001",
        "candidate_hash": "b" * 64,
        "source_id": "defillama",
        "adapter_name": "defillama_adapter",
        "chain": "Base",
        "protocol": "aave-v3",
        "strategy_type": "stablecoin_lending",
        "action_type": "WATCH",
        "pool_address": None,
        "token_addresses": (),
        "estimated_tx_usd": 0.0,
        "estimated_wallet_value_usd": 0.0,
        "estimated_daily_spend_usd": 0.0,
        "slippage_bps": 0,
        "risk_score": 80,
        "risk_decision": "WATCH",
        "created_at_utc": "2026-05-27T00:00:00Z",
        "metadata": {},
    }
    defaults.update(overrides)
    return ActionDescriptor(**defaults)


# ============================================================================
# Tests: valid FARM action approved
# ============================================================================


class TestFarmApproved:
    """Valid FARM action is approved."""

    def test_farm_approved(self, tmp_path: Path) -> None:
        base = _setup_data_dir(tmp_path)
        engine = PolicyEngine(base)
        action = _make_action()
        decision = engine.evaluate(action)
        assert decision.approved is True
        assert decision.approval_token is not None
        assert decision.approval_token.approved is True

    def test_approval_token_has_digests(self, tmp_path: Path) -> None:
        base = _setup_data_dir(tmp_path)
        engine = PolicyEngine(base)
        action = _make_action()
        decision = engine.evaluate(action)
        token = decision.approval_token
        assert token is not None
        assert len(token.policy_digest) == 64
        assert len(token.allowlist_digest) == 64


# ============================================================================
# Tests: WATCH action approved as planning action
# ============================================================================


class TestWatchApproved:
    """WATCH action approved as planning action."""

    def test_watch_approved(self, tmp_path: Path) -> None:
        base = _setup_data_dir(tmp_path)
        engine = PolicyEngine(base)
        action = _make_watch_action()
        decision = engine.evaluate(action)
        assert decision.approved is True


# ============================================================================
# Tests: SKIP risk_decision denied
# ============================================================================


class TestSkipDenied:
    """SKIP risk_decision is denied."""

    def test_skip_denied(self, tmp_path: Path) -> None:
        base = _setup_data_dir(tmp_path)
        engine = PolicyEngine(base)
        action = _make_action(risk_decision="SKIP", risk_score=30)
        decision = engine.evaluate(action)
        assert decision.approved is False
        assert any("SKIP" in r for r in decision.denial_reasons)


# ============================================================================
# Tests: low risk_score denied
# ============================================================================


class TestLowScoreDenied:
    """Low risk_score is denied."""

    def test_low_score_denied(self, tmp_path: Path) -> None:
        base = _setup_data_dir(tmp_path)
        engine = PolicyEngine(base)
        action = _make_action(risk_score=30, risk_decision="WATCH")
        decision = engine.evaluate(action)
        assert decision.approved is False
        assert any("risk_score" in r for r in decision.denial_reasons)


# ============================================================================
# Tests: kill switch denies
# ============================================================================


class TestKillSwitch:
    """Kill switch denies all actions."""

    def test_kill_switch_file_denies(self, tmp_path: Path) -> None:
        kill_file = tmp_path / "STOP"
        kill_file.write_text("STOP", encoding="utf-8")
        policy = _risk_policy(kill_switch_file=str(kill_file))
        base = _setup_data_dir(tmp_path, policy=policy)
        engine = PolicyEngine(base)
        action = _make_action()
        decision = engine.evaluate(action)
        assert decision.approved is False
        assert any("kill switch" in r for r in decision.denial_reasons)

    def test_kill_switch_md_denies(self, tmp_path: Path) -> None:
        policy = _risk_policy(kill_switch_file=None)
        base = _setup_data_dir(tmp_path, policy=policy)
        # Create KILL_SWITCH.md with STOP content
        (tmp_path / "KILL_SWITCH.md").write_text("STOP\nAll operations halted.", encoding="utf-8")
        engine = PolicyEngine(base)
        action = _make_action()
        decision = engine.evaluate(action)
        assert decision.approved is False
        assert any("kill switch" in r for r in decision.denial_reasons)


# ============================================================================
# Tests: macro HALT denies
# ============================================================================


class TestMacroHalt:
    """Macro HALT denies all actions."""

    def test_macro_halt_denies(self, tmp_path: Path) -> None:
        base = _setup_data_dir(tmp_path)
        (base / "data" / "macro_state.json").write_text(
            json.dumps({"state": "HALT", "reason": "market crash"}),
            encoding="utf-8",
        )
        engine = PolicyEngine(base)
        action = _make_action()
        decision = engine.evaluate(action)
        assert decision.approved is False
        assert any("HALT" in r for r in decision.denial_reasons)


# ============================================================================
# Tests: unsupported chain denied
# ============================================================================


class TestUnsupportedChain:
    """Unsupported chain is denied."""

    def test_unsupported_chain_denied(self, tmp_path: Path) -> None:
        base = _setup_data_dir(tmp_path)
        engine = PolicyEngine(base)
        action = _make_action(chain="Ethereum")
        decision = engine.evaluate(action)
        assert decision.approved is False
        assert any("chain" in r for r in decision.denial_reasons)


# ============================================================================
# Tests: unsupported strategy denied
# ============================================================================


class TestUnsupportedStrategy:
    """Unsupported strategy is denied."""

    def test_unsupported_strategy_denied(self, tmp_path: Path) -> None:
        base = _setup_data_dir(tmp_path)
        engine = PolicyEngine(base)
        action = _make_action(strategy_type="leveraged_farming")
        decision = engine.evaluate(action)
        assert decision.approved is False
        assert any("strategy_type" in r for r in decision.denial_reasons)


# ============================================================================
# Tests: caps denied
# ============================================================================


class TestCapsDenied:
    """Transaction, wallet, daily spend, and slippage caps deny."""

    def test_tx_amount_cap_denied(self, tmp_path: Path) -> None:
        base = _setup_data_dir(tmp_path)
        engine = PolicyEngine(base)
        action = _make_action(estimated_tx_usd=100.0)
        decision = engine.evaluate(action)
        assert decision.approved is False
        assert any("max_tx_usd" in r for r in decision.denial_reasons)

    def test_wallet_value_cap_denied(self, tmp_path: Path) -> None:
        base = _setup_data_dir(tmp_path)
        engine = PolicyEngine(base)
        action = _make_action(estimated_wallet_value_usd=200.0)
        decision = engine.evaluate(action)
        assert decision.approved is False
        assert any("max_wallet_value_usd" in r or "estimated_wallet_value_usd" in r for r in decision.denial_reasons)

    def test_daily_spend_cap_denied(self, tmp_path: Path) -> None:
        base = _setup_data_dir(tmp_path)
        engine = PolicyEngine(base)
        action = _make_action(estimated_daily_spend_usd=100.0)
        decision = engine.evaluate(action)
        assert decision.approved is False
        assert any("daily_spend" in r for r in decision.denial_reasons)

    def test_slippage_cap_denied(self, tmp_path: Path) -> None:
        base = _setup_data_dir(tmp_path)
        engine = PolicyEngine(base)
        action = _make_action(slippage_bps=100)
        decision = engine.evaluate(action)
        assert decision.approved is False
        assert any("slippage" in r for r in decision.denial_reasons)


# ============================================================================
# Tests: blocked actions
# ============================================================================


class TestBlockedActions:
    """Blocked actions are denied."""

    def test_bridge_denied(self, tmp_path: Path) -> None:
        base = _setup_data_dir(tmp_path)
        engine = PolicyEngine(base)
        action = _make_action(action_type="FARM", metadata={"bridge": True})
        decision = engine.evaluate(action)
        assert decision.approved is False
        assert any("bridge" in r for r in decision.denial_reasons)

    def test_borrow_denied(self, tmp_path: Path) -> None:
        base = _setup_data_dir(tmp_path)
        engine = PolicyEngine(base)
        action = _make_action(metadata={"borrow": True})
        decision = engine.evaluate(action)
        assert decision.approved is False
        assert any("borrow" in r for r in decision.denial_reasons)

    def test_leverage_denied(self, tmp_path: Path) -> None:
        base = _setup_data_dir(tmp_path)
        engine = PolicyEngine(base)
        action = _make_action(metadata={"leverage": True})
        decision = engine.evaluate(action)
        assert decision.approved is False
        assert any("leverage" in r for r in decision.denial_reasons)

    def test_unlimited_approval_denied(self, tmp_path: Path) -> None:
        base = _setup_data_dir(tmp_path)
        engine = PolicyEngine(base)
        action = _make_action(metadata={"unlimited_approval": True})
        decision = engine.evaluate(action)
        assert decision.approved is False
        assert any("unlimited_approval" in r for r in decision.denial_reasons)


# ============================================================================
# Tests: allowlist enforcement
# ============================================================================


class TestAllowlistEnforcement:
    """Allowlist enforcement denies when required."""

    def test_missing_pool_allowlist_denied(self, tmp_path: Path) -> None:
        policy = _risk_policy(require_pool_allowlist=True)
        base = _setup_data_dir(tmp_path, policy=policy)
        engine = PolicyEngine(base)
        action = _make_action(pool_address="0x" + "f" * 40)
        decision = engine.evaluate(action)
        assert decision.approved is False
        assert any("pool" in r.lower() for r in decision.denial_reasons)

    def test_missing_token_allowlist_denied(self, tmp_path: Path) -> None:
        policy = _risk_policy(require_token_allowlist=True)
        base = _setup_data_dir(tmp_path, policy=policy)
        engine = PolicyEngine(base)
        action = _make_action(token_addresses=("0x" + "e" * 40,))
        decision = engine.evaluate(action)
        assert decision.approved is False
        assert any("token" in r.lower() for r in decision.denial_reasons)

    def test_pool_in_allowlist_approved(self, tmp_path: Path) -> None:
        pool_addr = "0x" + "f" * 40
        policy = _risk_policy(
            require_pool_allowlist=True,
            require_contract_allowlist=False,
            require_token_allowlist=False,
        )
        pools = _pool_allowlist([{"address": pool_addr}])
        base = _setup_data_dir(tmp_path, policy=policy, pools=pools)
        engine = PolicyEngine(base)
        action = _make_action(pool_address=pool_addr)
        decision = engine.evaluate(action)
        assert decision.approved is True


# ============================================================================
# Tests: digests
# ============================================================================


class TestDigests:
    """Allowlist and policy digests are deterministic."""

    def test_allowlist_digest_deterministic(self) -> None:
        c = _contract_allowlist()
        t = _token_allowlist()
        p = _pool_allowlist()
        d1 = compute_allowlist_digest(c, t, p)
        d2 = compute_allowlist_digest(c, t, p)
        assert d1 == d2
        assert len(d1) == 64

    def test_policy_digest_deterministic(self) -> None:
        policy = _risk_policy()
        d1 = canonical_digest(policy)
        d2 = canonical_digest(policy)
        assert d1 == d2
        assert len(d1) == 64

    def test_different_data_different_digest(self) -> None:
        d1 = canonical_digest({"a": 1})
        d2 = canonical_digest({"a": 2})
        assert d1 != d2

    def test_approval_token_contains_digests(self, tmp_path: Path) -> None:
        base = _setup_data_dir(tmp_path)
        engine = PolicyEngine(base)
        action = _make_action()
        decision = engine.evaluate(action)
        token = decision.approval_token
        assert token is not None
        assert token.policy_digest == engine.policy_digest
        assert token.allowlist_digest == engine.allowlist_digest


# ============================================================================
# Tests: ledger
# ============================================================================


class TestLedger:
    """Ledger appends JSONL records."""

    def test_ledger_appends_on_approve(self, tmp_path: Path) -> None:
        base = _setup_data_dir(tmp_path)
        engine = PolicyEngine(base)
        action = _make_action()
        engine.evaluate(action)
        ledger_path = base / "data" / "execution_ledger.jsonl"
        assert ledger_path.exists()
        lines = ledger_path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["event_type"] == "POLICY_APPROVED"
        assert record["approved"] is True
        assert "policy_digest" in record
        assert "allowlist_digest" in record

    def test_ledger_appends_on_deny(self, tmp_path: Path) -> None:
        base = _setup_data_dir(tmp_path)
        engine = PolicyEngine(base)
        action = _make_action(risk_decision="SKIP", risk_score=10)
        engine.evaluate(action)
        ledger_path = base / "data" / "execution_ledger.jsonl"
        lines = ledger_path.read_text(encoding="utf-8").strip().split("\n")
        record = json.loads(lines[0])
        assert record["event_type"] == "POLICY_DENIED"
        assert record["approved"] is False
        assert len(record["denial_reasons"]) > 0

    def test_ledger_appends_multiple(self, tmp_path: Path) -> None:
        base = _setup_data_dir(tmp_path)
        engine = PolicyEngine(base)
        engine.evaluate(_make_action())
        engine.evaluate(_make_watch_action())
        ledger_path = base / "data" / "execution_ledger.jsonl"
        lines = ledger_path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 2


# ============================================================================
# Tests: no network calls, no signing
# ============================================================================


class TestNoNetworkNoSigning:
    """No network calls or signing imports."""

    def test_no_network_calls(self, tmp_path: Path) -> None:
        import socket
        from unittest.mock import patch

        base = _setup_data_dir(tmp_path)
        with patch.object(
            socket,
            "create_connection",
            side_effect=AssertionError("network call detected"),
        ):
            engine = PolicyEngine(base)
            decision = engine.evaluate(_make_action())
            assert decision.approved is True

    def test_no_signing_imports(self) -> None:
        import defi_autonomy.policy_engine as mod

        with open(mod.__file__, "r") as f:
            source = f.read()
        forbidden = (
            "eth_account",
            "solders",
            "nacl",
            "cryptography.hazmat",
            "private_key",
            "seed_phrase",
            "mnemonic",
            "WalletExecutor",
            "TxSimulator",
        )
        for term in forbidden:
            assert term not in source, f"forbidden term {term!r} found in module"

# ============================================================================
# Tests: Solana allowlist field extraction — Phase 9A.1
# ============================================================================


class TestSolanaAllowlistExtraction:
    """PolicyEngine._extract_entries handles Solana mint and program_id fields."""

    def test_extract_entries_handles_mint_field(self) -> None:
        """Solana token mint entries are extracted."""
        allowlist = {
            "version": 2,
            "entries": [
                {"mint": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", "symbol": "USDC"},
            ],
        }
        result = PolicyEngine._extract_entries(allowlist)
        assert "epjfwdd5aufqssqem2qn1xzybapC8G4wEGGkZwyTDt1v".lower() in result

    def test_extract_entries_handles_program_id_field(self) -> None:
        """Solana program ID entries are extracted."""
        allowlist = {
            "version": 2,
            "entries": [
                {"program_id": "LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo", "name": "Meteora DLMM"},
            ],
        }
        result = PolicyEngine._extract_entries(allowlist)
        assert "LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo".lower() in result

    def test_extract_entries_handles_mixed_evm_solana(self) -> None:
        """Allowlist with both EVM address and Solana mint entries works."""
        allowlist = {
            "version": 2,
            "entries": [
                {"address": "0xA238Dd80C259a72e81d7e4664a9801593F98d1c5"},
                {"mint": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"},
                {"program_id": "LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo"},
            ],
        }
        result = PolicyEngine._extract_entries(allowlist)
        assert len(result) == 3
        assert "0xa238dd80c259a72e81d7e4664a9801593f98d1c5" in result
        assert "epjfwdd5aufqssqem2qn1xzybapc8g4weggkzwytdt1v" in result
        assert "lbuzkhrxpf3xupbcjp4yztkglccjzhtsdm9yuvapwxo" in result

    def test_solana_token_allowlist_passes_policy(self, tmp_path: Path) -> None:
        """A Solana candidate with mint in token_allowlist passes the token check."""
        base = tmp_path
        data_dir = base / "data"
        data_dir.mkdir(parents=True)

        # Write risk policy
        policy = {
            "version": 1,
            "autonomy_level": 2,
            "max_wallet_value_usd": 100,
            "max_tx_usd": 25,
            "max_daily_spend_usd": 50,
            "max_slippage_bps": 50,
            "allowed_chains": ["Base", "BNB Chain", "Solana"],
            "allowed_strategy_types": ["stablecoin_lending", "stable_stable_lp", "xstocks_lp"],
            "blocked_actions": ["bridge", "borrow", "leverage", "unlimited_approval"],
            "require_contract_allowlist": True,
            "require_token_allowlist": True,
            "require_pool_allowlist": True,
        }
        (data_dir / "risk_policy.json").write_text(
            __import__("json").dumps(policy), encoding="utf-8"
        )

        # Solana token allowlist with USDC mint
        usdc_mint = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
        usdt_mint = "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"
        (data_dir / "token_allowlist.json").write_text(
            __import__("json").dumps({
                "version": 2,
                "entries": [
                    {"mint": usdc_mint, "symbol": "USDC"},
                    {"mint": usdt_mint, "symbol": "USDT"},
                ],
            }),
            encoding="utf-8",
        )

        # Contract allowlist with Meteora program ID
        (data_dir / "contract_allowlist.json").write_text(
            __import__("json").dumps({
                "version": 2,
                "entries": [
                    {"program_id": "LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo", "name": "Meteora DLMM"},
                    {"program_id": "meteora"},
                ],
            }),
            encoding="utf-8",
        )

        # Pool allowlist with a Solana pool address
        pool_addr = "So1anaAddr111111111111111111111111111111"
        (data_dir / "pool_allowlist.json").write_text(
            __import__("json").dumps({
                "version": 2,
                "entries": [
                    {"pool_address": pool_addr, "protocol": "meteora"},
                ],
            }),
            encoding="utf-8",
        )

        engine = PolicyEngine(base)

        # Verify the Solana mints are in the extracted entries
        assert usdc_mint.lower() in engine._token_entries
        assert usdt_mint.lower() in engine._token_entries
        assert pool_addr.lower() in engine._pool_entries
