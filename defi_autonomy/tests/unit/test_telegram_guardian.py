"""Unit tests for defi_autonomy.telegram_guardian — Sprint 4, Phase 4.2.

All tests are deterministic and offline. No real Telegram API calls. No signing.
No key loading. No broadcast.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from defi_autonomy.telegram_guardian import (
    ApprovalFlowDisabled,
    GuardianCommand,
    GuardianResponse,
    InvalidCommandError,
    UnauthorizedChatError,
    activate_kill_switch,
    clear_kill_switch,
    format_cycle_report,
    format_status,
    handle_command,
    load_authorized_chat_ids,
    parse_command,
    pause_for_duration,
    resume_allowed,
)


# ============================================================================
# Fixtures
# ============================================================================


def _setup_base(tmp_path: Path, policy: dict | None = None) -> Path:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    pol = policy or {
        "autonomy_level": 1,
        "max_wallet_value_usd": 100,
        "max_tx_usd": 25,
        "max_daily_spend_usd": 50,
        "max_slippage_bps": 50,
        "allowed_chains": ["Base", "BNB Chain", "Solana"],
        "allowed_strategy_types": ["stablecoin_lending", "stable_stable_lp"],
        "blocked_actions": ["bridge", "borrow", "leverage"],
        "kill_switch_file": None,
    }
    (data_dir / "risk_policy.json").write_text(json.dumps(pol), encoding="utf-8")
    return tmp_path


def _setup_with_report(tmp_path: Path) -> Path:
    base = _setup_base(tmp_path)
    report = {
        "cycle_id": "coord_test_001",
        "started_at_utc": "2026-05-27T00:00:00Z",
        "finished_at_utc": "2026-05-27T00:00:05Z",
        "status": "COMPLETE",
        "autonomy_level": 1,
        "ingestion_status": "OK",
        "candidate_count": 3,
        "risk_assessment_count": 3,
        "approved_count": 1,
        "denied_count": 2,
        "simulation_passed_count": 1,
        "simulation_failed_count": 0,
        "signing_prepared_count": 0,
        "errors": [],
        "warnings": [],
    }
    (base / "data" / "cycle_report.json").write_text(
        json.dumps(report), encoding="utf-8"
    )
    return base


def _cmd(text: str, chat_id: str = "123") -> GuardianCommand:
    return parse_command(text, chat_id=chat_id)


# ============================================================================
# Tests: authorization
# ============================================================================


class TestAuthorization:
    """Unauthorized chat rejected."""

    def test_unauthorized_chat_rejected(self, tmp_path: Path) -> None:
        base = _setup_base(tmp_path)
        cmd = _cmd("/status", chat_id="999")
        with pytest.raises(UnauthorizedChatError):
            handle_command(cmd, base, authorized_chat_ids={"123", "456"})

    def test_authorized_chat_passes(self, tmp_path: Path) -> None:
        base = _setup_base(tmp_path)
        cmd = _cmd("/status", chat_id="123")
        resp = handle_command(cmd, base, authorized_chat_ids={"123"})
        assert resp.ok is True

    def test_no_auth_check_when_none(self, tmp_path: Path) -> None:
        base = _setup_base(tmp_path)
        cmd = _cmd("/status", chat_id="any")
        resp = handle_command(cmd, base, authorized_chat_ids=None)
        assert resp.ok is True


# ============================================================================
# Tests: command parsing
# ============================================================================


class TestParsing:
    """Command parsing."""

    def test_parse_status(self) -> None:
        cmd = parse_command("/status", chat_id="1")
        assert cmd.command == "/status"
        assert cmd.args == ()

    def test_parse_pause_with_arg(self) -> None:
        cmd = parse_command("/pause 30", chat_id="1")
        assert cmd.command == "/pause"
        assert cmd.args == ("30",)

    def test_invalid_command_rejected(self) -> None:
        with pytest.raises(InvalidCommandError):
            parse_command("/unknown_cmd", chat_id="1")

    def test_non_command_rejected(self) -> None:
        with pytest.raises(InvalidCommandError):
            parse_command("hello world", chat_id="1")


# ============================================================================
# Tests: /status
# ============================================================================


class TestStatus:
    """Status command returns useful summary."""

    def test_status_returns_summary(self, tmp_path: Path) -> None:
        base = _setup_with_report(tmp_path)
        cmd = _cmd("/status")
        resp = handle_command(cmd, base)
        assert resp.ok is True
        assert "Autonomy Level" in resp.message
        assert "Kill Switch" in resp.message

    def test_format_status_includes_cycle(self, tmp_path: Path) -> None:
        base = _setup_with_report(tmp_path)
        msg = format_status(base)
        assert "COMPLETE" in msg
        assert "Candidates" in msg


# ============================================================================
# Tests: /halt
# ============================================================================


class TestHalt:
    """Halt creates STOP file."""

    def test_halt_creates_stop_file(self, tmp_path: Path) -> None:
        base = _setup_base(tmp_path)
        cmd = _cmd("/halt")
        resp = handle_command(cmd, base)
        assert resp.ok is True
        ks = base / "KILL_SWITCH.md"
        assert ks.exists()
        content = ks.read_text(encoding="utf-8")
        assert content.startswith("STOP")


# ============================================================================
# Tests: /resume
# ============================================================================


class TestResume:
    """Resume removes STOP file when allowed."""

    def test_resume_clears_stop(self, tmp_path: Path) -> None:
        base = _setup_base(tmp_path)
        activate_kill_switch(base)
        assert (base / "KILL_SWITCH.md").read_text().startswith("STOP")
        cmd = _cmd("/resume")
        resp = handle_command(cmd, base)
        assert resp.ok is True
        content = (base / "KILL_SWITCH.md").read_text(encoding="utf-8")
        assert content.startswith("CLEARED")

    def test_resume_allowed_returns_true(self, tmp_path: Path) -> None:
        base = _setup_base(tmp_path)
        assert resume_allowed(base) is True


# ============================================================================
# Tests: /pause
# ============================================================================


class TestPause:
    """Pause creates pause metadata."""

    def test_pause_creates_stop_with_expiry(self, tmp_path: Path) -> None:
        base = _setup_base(tmp_path)
        cmd = parse_command("/pause 30", chat_id="1")
        resp = handle_command(cmd, base)
        assert resp.ok is True
        ks = base / "KILL_SWITCH.md"
        content = ks.read_text(encoding="utf-8")
        assert "STOP" in content
        assert "30 minutes" in content
        assert "Expires" in content

    def test_pause_invalid_duration(self, tmp_path: Path) -> None:
        base = _setup_base(tmp_path)
        cmd = parse_command("/pause abc", chat_id="1")
        resp = handle_command(cmd, base)
        assert resp.ok is False

    def test_pause_missing_duration(self, tmp_path: Path) -> None:
        base = _setup_base(tmp_path)
        cmd = parse_command("/pause", chat_id="1")
        resp = handle_command(cmd, base)
        assert resp.ok is False


# ============================================================================
# Tests: /cycle
# ============================================================================


class TestCycle:
    """Cycle formats latest cycle_report.json."""

    def test_cycle_with_report(self, tmp_path: Path) -> None:
        base = _setup_with_report(tmp_path)
        cmd = _cmd("/cycle")
        resp = handle_command(cmd, base)
        assert resp.ok is True
        assert "COMPLETE" in resp.message
        assert "coord_test_001" in resp.message

    def test_cycle_no_report(self, tmp_path: Path) -> None:
        base = _setup_base(tmp_path)
        cmd = _cmd("/cycle")
        resp = handle_command(cmd, base)
        assert resp.ok is True
        assert "No cycle report" in resp.message


# ============================================================================
# Tests: /policy
# ============================================================================


class TestPolicy:
    """Policy hides secrets and shows caps."""

    def test_policy_shows_caps(self, tmp_path: Path) -> None:
        base = _setup_base(tmp_path)
        cmd = _cmd("/policy")
        resp = handle_command(cmd, base)
        assert resp.ok is True
        assert "Max Wallet Value" in resp.message
        assert "Max TX" in resp.message
        assert "Allowed Chains" in resp.message

    def test_policy_hides_secrets(self, tmp_path: Path) -> None:
        base = _setup_base(tmp_path)
        cmd = _cmd("/policy")
        resp = handle_command(cmd, base)
        assert "private" not in resp.message.lower()
        assert "key" not in resp.message.lower()
        assert "secret" not in resp.message.lower()


# ============================================================================
# Tests: /positions
# ============================================================================


class TestPositions:
    """Positions safe when no positions exist."""

    def test_positions_empty(self, tmp_path: Path) -> None:
        base = _setup_base(tmp_path)
        cmd = _cmd("/positions")
        resp = handle_command(cmd, base)
        assert resp.ok is True
        assert "No active positions" in resp.message


# ============================================================================
# Tests: /approve and /deny
# ============================================================================


class TestApprovalFlow:
    """Approval flow disabled at autonomy_level 1."""

    def test_approve_disabled_level_1(self, tmp_path: Path) -> None:
        base = _setup_base(tmp_path)
        cmd = parse_command("/approve act_001", chat_id="1")
        resp = handle_command(cmd, base)
        assert resp.ok is False
        assert "disabled" in resp.message.lower() or "disabled" in " ".join(resp.errors).lower()

    def test_deny_disabled_level_1(self, tmp_path: Path) -> None:
        base = _setup_base(tmp_path)
        cmd = parse_command("/deny act_001", chat_id="1")
        resp = handle_command(cmd, base)
        assert resp.ok is False

    def test_approve_stub_level_2(self, tmp_path: Path) -> None:
        policy = {
            "autonomy_level": 2,
            "max_wallet_value_usd": 100,
            "max_tx_usd": 25,
            "max_daily_spend_usd": 50,
            "max_slippage_bps": 50,
            "allowed_chains": ["Base"],
            "allowed_strategy_types": ["stablecoin_lending"],
            "blocked_actions": [],
            "telegram_approval_timeout_seconds": 300,
        }
        base = _setup_base(tmp_path, policy=policy)
        # Create a wallet execution ledger entry for the envelope
        ledger_path = base / "data" / "wallet_execution_ledger.jsonl"
        ledger_path.write_text(json.dumps({
            "envelope_id": "act_001",
            "action_id": "act_001",
            "simulation_id": "sim_001",
            "candidate_hash": "c" * 64,
            "signed_payload_hash": "s" * 64,
            "broadcast_allowed": True,
            "broadcasted": False,
        }) + "\n", encoding="utf-8")
        cmd = parse_command("/approve act_001", chat_id="1")
        resp = handle_command(cmd, base)
        assert resp.ok is True


# ============================================================================
# Tests: no network, no signing, no broadcast
# ============================================================================


class TestNoNetworkNoSigning:
    """No real Telegram calls, no signing, no broadcast."""

    def test_no_network_calls(self, tmp_path: Path) -> None:
        import socket
        from unittest.mock import patch

        base = _setup_base(tmp_path)
        with patch.object(
            socket,
            "create_connection",
            side_effect=AssertionError("network call detected"),
        ):
            cmd = _cmd("/status")
            resp = handle_command(cmd, base)
            assert resp.ok is True

    def test_no_signing_imports(self) -> None:
        import defi_autonomy.telegram_guardian as mod
        with open(mod.__file__, "r") as f:
            source = f.read()
        forbidden = (
            "eth_account", "solders", "nacl", "sign_transaction",
            "broadcast_transaction", "private_key", "mnemonic",
        )
        for term in forbidden:
            assert term not in source, f"forbidden term {term!r} found"

    def test_no_daemon_modification(self) -> None:
        import defi_autonomy.telegram_guardian as mod
        with open(mod.__file__, "r") as f:
            source = f.read()
        assert "ecosystem.defi.cjs" not in source
        assert "pm2" not in source.lower()


# ============================================================================
# Tests: functional /approve — Phase 3.2B
# ============================================================================


class TestFunctionalApprove:
    """Functional /approve creates OperatorApprovalRecord."""

    def _setup_with_envelope(self, tmp_path: Path) -> Path:
        policy = {
            "autonomy_level": 2,
            "max_wallet_value_usd": 100,
            "max_tx_usd": 25,
            "max_daily_spend_usd": 50,
            "max_slippage_bps": 50,
            "allowed_chains": ["Base"],
            "allowed_strategy_types": ["stablecoin_lending"],
            "blocked_actions": [],
            "telegram_approval_timeout_seconds": 300,
            "allow_level2_broadcast": True,
        }
        base = _setup_base(tmp_path, policy=policy)
        # Write a wallet execution ledger entry
        ledger_path = base / "data" / "wallet_execution_ledger.jsonl"
        envelope_record = {
            "envelope_id": "env_test_001",
            "action_id": "act_001",
            "simulation_id": "sim_001",
            "candidate_hash": "c" * 64,
            "signed_payload_hash": "s" * 64,
            "broadcast_allowed": True,
            "broadcasted": False,
        }
        ledger_path.write_text(
            json.dumps(envelope_record) + "\n", encoding="utf-8"
        )
        return base

    def test_approve_creates_record(self, tmp_path: Path) -> None:
        base = self._setup_with_envelope(tmp_path)
        cmd = parse_command("/approve env_test_001", chat_id="1")
        resp = handle_command(cmd, base)
        assert resp.ok is True
        assert "approved" in resp.message.lower() or "Approved" in resp.message
        # Check approval file
        approvals_path = base / "data" / "operator_approvals.jsonl"
        assert approvals_path.exists()
        lines = approvals_path.read_text(encoding="utf-8").strip().split("\n")
        record = json.loads(lines[0])
        assert record["envelope_id"] == "env_test_001"
        assert record["signed_payload_hash"] == "s" * 64

    def test_approve_record_has_expires(self, tmp_path: Path) -> None:
        base = self._setup_with_envelope(tmp_path)
        cmd = parse_command("/approve env_test_001", chat_id="1")
        handle_command(cmd, base)
        approvals_path = base / "data" / "operator_approvals.jsonl"
        record = json.loads(approvals_path.read_text(encoding="utf-8").strip().split("\n")[0])
        assert "expires_at_utc" in record
        assert record["expires_at_utc"] > record["approved_at_utc"]

    def test_approve_rejects_unknown_envelope(self, tmp_path: Path) -> None:
        base = self._setup_with_envelope(tmp_path)
        cmd = parse_command("/approve unknown_env", chat_id="1")
        resp = handle_command(cmd, base)
        assert resp.ok is False
        assert "not found" in resp.message.lower()

    def test_approve_rejects_broadcast_not_allowed(self, tmp_path: Path) -> None:
        policy = {"autonomy_level": 2, "allow_level2_broadcast": True,
                  "telegram_approval_timeout_seconds": 300}
        base = _setup_base(tmp_path, policy=policy)
        ledger_path = base / "data" / "wallet_execution_ledger.jsonl"
        ledger_path.write_text(json.dumps({
            "envelope_id": "env_no_bc", "broadcast_allowed": False, "broadcasted": False,
        }) + "\n")
        cmd = parse_command("/approve env_no_bc", chat_id="1")
        resp = handle_command(cmd, base)
        assert resp.ok is False
        assert "broadcast_allowed" in resp.message.lower() or "broadcast_allowed" in " ".join(resp.errors)
