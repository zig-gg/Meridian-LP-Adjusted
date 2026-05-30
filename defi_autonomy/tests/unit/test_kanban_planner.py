"""Unit tests for defi_autonomy.kanban_planner."""

import json
from pathlib import Path
from defi_autonomy.kanban_planner import (
    KanbanTask, add_task, create_task, load_board, save_board, task_to_dict,
)


class TestCreateTask:
    def test_creates_valid_task(self) -> None:
        task = create_task("Scan DeFiLlama", "Check new pools")
        assert task.status == "BACKLOG"
        assert task.requires_operator_approval is False

    def test_risky_task_requires_approval(self) -> None:
        task = create_task("Enable broadcast for Level 2", "Set allow_level2_broadcast=true")
        assert task.requires_operator_approval is True
        assert task.status == "NEEDS_OPERATOR_APPROVAL"

    def test_rpc_task_requires_approval(self) -> None:
        task = create_task("Configure real RPC provider")
        assert task.requires_operator_approval is True

    def test_private_key_task_requires_approval(self) -> None:
        task = create_task("Load private_key for signing")
        assert task.requires_operator_approval is True

    def test_pm2_task_requires_approval(self) -> None:
        task = create_task("Restart PM2 with new config")
        assert task.requires_operator_approval is True

    def test_safe_task_no_approval(self) -> None:
        task = create_task("Review APR decay patterns", "Analyze outcome events")
        assert task.requires_operator_approval is False


class TestBoard:
    def test_load_empty_board(self, tmp_path: Path) -> None:
        board = load_board(tmp_path)
        assert board["tasks"] == []

    def test_add_and_load(self, tmp_path: Path) -> None:
        task = create_task("Test task")
        add_task(tmp_path, task)
        board = load_board(tmp_path)
        assert len(board["tasks"]) == 1
        assert board["tasks"][0]["title"] == "Test task"

    def test_board_is_json(self, tmp_path: Path) -> None:
        task = create_task("JSON test")
        add_task(tmp_path, task)
        p = tmp_path / "data" / "kanban_board.json"
        assert p.exists()
        json.loads(p.read_text())  # must not raise


class TestSafety:
    def test_no_execution_imports(self) -> None:
        import defi_autonomy.kanban_planner as mod
        with open(mod.__file__, "r") as f:
            source = f.read()
        for term in ("WalletExecutor", "BroadcastExecutor", "sign_transaction"):
            assert term not in source

    def test_no_policy_mutation(self) -> None:
        import defi_autonomy.kanban_planner as mod
        with open(mod.__file__, "r") as f:
            source = f.read()
        assert "risk_policy.json" not in source
