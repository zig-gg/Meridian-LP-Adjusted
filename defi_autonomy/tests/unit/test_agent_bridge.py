"""Unit tests for defi_autonomy.agent_bridge."""

from __future__ import annotations
import json
from pathlib import Path
import pytest
from defi_autonomy.agent_bridge import (
    read_last_cycle_report, read_risk_policy_summary_sanitized,
    read_source_health, read_open_positions, read_outcome_events_summary,
    propose_candidate_review, propose_risk_note, propose_subagent_task,
)


class TestReadFunctions:
    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        assert read_last_cycle_report(tmp_path) == {}
        assert read_source_health(tmp_path) == {}

    def test_reads_cycle_report(self, tmp_path: Path) -> None:
        (tmp_path / "data").mkdir()
        (tmp_path / "data" / "cycle_report.json").write_text(json.dumps({"status": "COMPLETE"}))
        assert read_last_cycle_report(tmp_path)["status"] == "COMPLETE"

    def test_sanitized_policy_hides_secrets(self, tmp_path: Path) -> None:
        (tmp_path / "data").mkdir()
        (tmp_path / "data" / "risk_policy.json").write_text(json.dumps({
            "autonomy_level": 1, "max_tx_usd": 5,
            "kill_switch_file": "/secret/path", "operator_funded_agent_wallet_address": "0xabc",
        }))
        result = read_risk_policy_summary_sanitized(tmp_path)
        assert "autonomy_level" in result
        assert "kill_switch_file" not in result
        assert "operator_funded_agent_wallet_address" not in result


class TestProposals:
    def test_proposal_is_append_only(self, tmp_path: Path) -> None:
        propose_candidate_review(tmp_path, "hash1", "looks good")
        propose_risk_note(tmp_path, "defillama", "source seems slow")
        p = tmp_path / "data" / "agent_proposals.jsonl"
        assert p.exists()
        lines = p.read_text().strip().split("\n")
        assert len(lines) == 2

    def test_proposal_has_id_and_timestamp(self, tmp_path: Path) -> None:
        result = propose_subagent_task(tmp_path, "RiskAgent", "Review APR", "Check decay")
        assert "proposal_id" in result
        assert "created_at_utc" in result


class TestSafety:
    def test_no_execution_imports(self) -> None:
        import defi_autonomy.agent_bridge as mod
        with open(mod.__file__, "r") as f:
            source = f.read()
        for term in ("WalletExecutor", "BroadcastExecutor", "sign_transaction",
                     "broadcast_envelope", "broadcast_transaction"):
            assert term not in source

    def test_cannot_mutate_policy(self) -> None:
        import defi_autonomy.agent_bridge as mod
        with open(mod.__file__, "r") as f:
            source = f.read()
        assert "risk_policy.json\", \"w" not in source
        assert "write_json_atomic" not in source

    def test_no_network_calls(self, tmp_path: Path) -> None:
        import socket
        from unittest.mock import patch
        with patch.object(socket, "create_connection", side_effect=AssertionError("net")):
            propose_candidate_review(tmp_path, "h", "n")
