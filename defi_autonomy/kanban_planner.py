"""KanbanPlanner — Local fallback advisory task board.

Writes to data/kanban_board.json. Advisory only — cannot trigger execution.
Future integration: one-way sync to Hermes native Kanban when available.

No execution. No signing. No broadcast. No policy mutation.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ALLOWED_STATUSES: frozenset[str] = frozenset({
    "BACKLOG", "READY", "IN_PROGRESS", "BLOCKED",
    "NEEDS_OPERATOR_APPROVAL", "DONE", "REJECTED",
})

ALLOWED_PRIORITIES: frozenset[str] = frozenset({
    "critical", "high", "medium", "low",
})

# Keywords that indicate a task requires operator approval
_OPERATOR_APPROVAL_KEYWORDS: frozenset[str] = frozenset({
    "broadcast", "rpc", "private_key", "signing", "pm2", "deploy",
    "autonomy_level", "allowlist", "risk_policy", "wallet", "live",
})

_KANBAN_FILE = "data/kanban_board.json"


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True, slots=True)
class KanbanTask:
    task_id: str
    title: str
    description: str
    owner_agent: str
    priority: str
    status: str
    risk_level: str
    requires_operator_approval: bool
    blocked_reason: str
    created_at_utc: str
    updated_at_utc: str


def task_to_dict(task: KanbanTask) -> dict:
    return {
        "task_id": task.task_id,
        "title": task.title,
        "description": task.description,
        "owner_agent": task.owner_agent,
        "priority": task.priority,
        "status": task.status,
        "risk_level": task.risk_level,
        "requires_operator_approval": task.requires_operator_approval,
        "blocked_reason": task.blocked_reason,
        "created_at_utc": task.created_at_utc,
        "updated_at_utc": task.updated_at_utc,
    }


def _requires_approval(title: str, description: str) -> bool:
    """Check if task content indicates operator approval is needed."""
    combined = f"{title} {description}".lower()
    return any(kw in combined for kw in _OPERATOR_APPROVAL_KEYWORDS)


def create_task(
    title: str,
    description: str = "",
    owner_agent: str = "KanbanPlannerAgent",
    priority: str = "medium",
    risk_level: str = "low",
) -> KanbanTask:
    """Create a new Kanban task. Auto-detects if operator approval is needed."""
    status = "BACKLOG"
    requires_approval = _requires_approval(title, description)
    if requires_approval:
        status = "NEEDS_OPERATOR_APPROVAL"

    return KanbanTask(
        task_id=f"task_{int(time.time()*1000)%1_000_000:06d}",
        title=title[:200],
        description=description[:500],
        owner_agent=owner_agent,
        priority=priority if priority in ALLOWED_PRIORITIES else "medium",
        status=status,
        risk_level=risk_level,
        requires_operator_approval=requires_approval,
        blocked_reason="",
        created_at_utc=_now_utc(),
        updated_at_utc=_now_utc(),
    )


def load_board(base_dir: Path | str) -> dict:
    """Load the Kanban board from JSON."""
    p = Path(base_dir) / _KANBAN_FILE
    if not p.exists():
        return {"tasks": [], "updated_at_utc": _now_utc()}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"tasks": [], "updated_at_utc": _now_utc()}


def save_board(base_dir: Path | str, board: dict) -> None:
    """Save the Kanban board to JSON."""
    p = Path(base_dir) / _KANBAN_FILE
    p.parent.mkdir(parents=True, exist_ok=True)
    board["updated_at_utc"] = _now_utc()
    p.write_text(json.dumps(board, indent=2, ensure_ascii=False), encoding="utf-8")


def add_task(base_dir: Path | str, task: KanbanTask) -> dict:
    """Add a task to the board and save."""
    board = load_board(base_dir)
    board["tasks"].append(task_to_dict(task))
    save_board(base_dir, board)
    return board


__all__ = [
    "ALLOWED_PRIORITIES", "ALLOWED_STATUSES", "KanbanTask",
    "add_task", "create_task", "load_board", "save_board", "task_to_dict",
]
