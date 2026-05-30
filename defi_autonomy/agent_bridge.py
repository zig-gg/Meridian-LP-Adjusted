"""AgentBridge — Safe read-only and proposal-only interface for LLM agents.

Exposes tools for Hermes/OpenClaw/LLM agents to read system state and
submit advisory proposals. Cannot execute, sign, broadcast, or modify
policy/allowlists/caps.

All proposals are append-only to data/agent_proposals.jsonl.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_PROPOSALS_FILE = "data/agent_proposals.jsonl"


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_json(path: Path) -> Any:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


# ============================================================================
# Read-only tools
# ============================================================================


def read_last_cycle_report(base_dir: Path | str) -> dict:
    return _load_json(Path(base_dir) / "data" / "cycle_report.json")


def read_normalized_candidates(base_dir: Path | str) -> dict:
    return _load_json(Path(base_dir) / "data" / "normalized_yield_candidates.json")


def read_source_health(base_dir: Path | str) -> dict:
    return _load_json(Path(base_dir) / "data" / "source_health.json")


def read_open_positions(base_dir: Path | str) -> list[dict]:
    from defi_autonomy.position_lifecycle import get_open_positions
    return get_open_positions(Path(base_dir) / "data" / "positions.jsonl")


def read_outcome_events_summary(base_dir: Path | str, max_events: int = 20) -> dict:
    p = Path(base_dir) / "data" / "outcome_events.jsonl"
    if not p.exists():
        return {"count": 0, "recent": []}
    lines = p.read_text(encoding="utf-8").strip().split("\n")
    lines = [l for l in lines if l.strip()]
    recent = []
    for line in lines[-max_events:]:
        try:
            recent.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return {"count": len(lines), "recent": recent}


def read_risk_policy_summary_sanitized(base_dir: Path | str) -> dict:
    """Return non-secret policy fields only."""
    policy = _load_json(Path(base_dir) / "data" / "risk_policy.json")
    if not isinstance(policy, dict):
        return {}
    safe_keys = [
        "autonomy_level", "max_wallet_value_usd", "max_tx_usd",
        "max_daily_spend_usd", "max_slippage_bps", "allowed_chains",
        "allowed_strategy_types", "blocked_actions", "learning_bias_clamp_points",
    ]
    return {k: policy[k] for k in safe_keys if k in policy}


def read_phase_status(base_dir: Path | str) -> str:
    p = Path(base_dir) / "docs" / "PHASE_STATUS.md"
    if p.exists():
        return p.read_text(encoding="utf-8")[:2000]
    return "Phase status document not found."


def read_kanban_board(base_dir: Path | str) -> dict:
    return _load_json(Path(base_dir) / "data" / "kanban_board.json")


# ============================================================================
# Proposal-only tools
# ============================================================================


def _append_proposal(base_dir: Path | str, proposal: dict) -> dict:
    """Append a validated proposal to agent_proposals.jsonl."""
    p = Path(base_dir) / _PROPOSALS_FILE
    p.parent.mkdir(parents=True, exist_ok=True)
    proposal["created_at_utc"] = _now_utc()
    proposal["proposal_id"] = f"prop_{int(time.time()*1000)%1_000_000:06d}"
    line = json.dumps(proposal, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    with open(p, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    return proposal


def propose_candidate_review(base_dir: Path | str, candidate_hash: str, notes: str) -> dict:
    return _append_proposal(base_dir, {"type": "candidate_review", "candidate_hash": candidate_hash, "notes": notes[:500]})


def propose_risk_note(base_dir: Path | str, source_id: str, note: str) -> dict:
    return _append_proposal(base_dir, {"type": "risk_note", "source_id": source_id, "notes": note[:500]})


def propose_strategy_note(base_dir: Path | str, strategy_type: str, note: str) -> dict:
    return _append_proposal(base_dir, {"type": "strategy_note", "strategy_type": strategy_type, "notes": note[:500]})


def propose_new_factor_key(base_dir: Path | str, factor_key: str, rationale: str) -> dict:
    return _append_proposal(base_dir, {"type": "new_factor_key", "factor_key": factor_key[:80], "rationale": rationale[:500]})


def propose_action_descriptor_draft(base_dir: Path | str, draft: dict) -> dict:
    safe_draft = {k: v for k, v in draft.items() if k in (
        "chain", "protocol", "strategy_type", "action_type", "pool_address", "estimated_tx_usd"
    )}
    return _append_proposal(base_dir, {"type": "action_descriptor_draft", "draft": safe_draft})


def propose_subagent_task(base_dir: Path | str, agent_role: str, task_title: str, description: str) -> dict:
    return _append_proposal(base_dir, {"type": "subagent_task", "agent_role": agent_role, "title": task_title[:200], "description": description[:500]})


def propose_kanban_plan(base_dir: Path | str, tasks: list[dict]) -> dict:
    safe_tasks = [{"title": t.get("title", "")[:200], "priority": t.get("priority", "medium")} for t in tasks[:20]]
    return _append_proposal(base_dir, {"type": "kanban_plan", "tasks": safe_tasks})


def propose_architecture_patch_note(base_dir: Path | str, module: str, note: str) -> dict:
    return _append_proposal(base_dir, {"type": "architecture_patch_note", "module": module, "notes": note[:500]})


__all__ = [
    "propose_action_descriptor_draft", "propose_architecture_patch_note",
    "propose_candidate_review", "propose_kanban_plan", "propose_new_factor_key",
    "propose_risk_note", "propose_strategy_note", "propose_subagent_task",
    "read_kanban_board", "read_last_cycle_report", "read_normalized_candidates",
    "read_open_positions", "read_outcome_events_summary", "read_phase_status",
    "read_risk_policy_summary_sanitized", "read_source_health",
]
