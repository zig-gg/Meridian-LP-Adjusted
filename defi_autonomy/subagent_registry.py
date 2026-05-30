"""SubagentRegistry — Safe role-based agent registry.

Defines subagent roles for the Hermes/OpenClaw agentic swarm.
Subagents can propose and review but cannot execute wallet/broadcast directly.

No execution. No signing. No broadcast. No policy mutation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

ALLOWED_ROLES: frozenset[str] = frozenset({
    "CoordinatorAgent",
    "ScannerAgent",
    "RiskAgent",
    "PolicyReviewAgent",
    "SimulationAgent",
    "ExecutionReviewAgent",
    "LearningAgent",
    "ResearchAgent",
    "ArchitectAgent",
    "KanbanPlannerAgent",
})

# Roles that can NEVER directly call execution functions
PROPOSAL_ONLY_ROLES: frozenset[str] = frozenset({
    "ArchitectAgent",
    "ResearchAgent",
    "KanbanPlannerAgent",
    "PolicyReviewAgent",
    "ExecutionReviewAgent",
})

# Tasks from these roles always require operator approval
OPERATOR_APPROVAL_REQUIRED_ROLES: frozenset[str] = frozenset({
    "ArchitectAgent",
    "ExecutionReviewAgent",
})


@dataclass(frozen=True, slots=True)
class SubagentRole:
    """Definition of a subagent role."""
    role_name: str
    can_propose: bool
    can_review: bool
    can_create_kanban_tasks: bool
    can_execute_directly: bool  # Always False for safety
    requires_operator_approval_for_execution: bool
    description: str


def get_role(role_name: str) -> SubagentRole | None:
    """Get a subagent role definition. Returns None for unknown roles."""
    if role_name not in ALLOWED_ROLES:
        return None
    return SubagentRole(
        role_name=role_name,
        can_propose=True,
        can_review=True,
        can_create_kanban_tasks=True,
        can_execute_directly=False,  # NEVER — safety invariant
        requires_operator_approval_for_execution=role_name in OPERATOR_APPROVAL_REQUIRED_ROLES,
        description=_ROLE_DESCRIPTIONS.get(role_name, ""),
    )


def list_roles() -> list[SubagentRole]:
    """List all allowed subagent roles."""
    return [get_role(r) for r in sorted(ALLOWED_ROLES)]


def validate_role(role_name: str) -> bool:
    """Check if a role name is valid."""
    return role_name in ALLOWED_ROLES


_ROLE_DESCRIPTIONS: dict[str, str] = {
    "CoordinatorAgent": "Orchestrates cycles and delegates tasks",
    "ScannerAgent": "Discovers and evaluates yield opportunities",
    "RiskAgent": "Assesses risk and produces scoring recommendations",
    "PolicyReviewAgent": "Reviews policy decisions (proposal-only)",
    "SimulationAgent": "Runs and reviews transaction simulations",
    "ExecutionReviewAgent": "Reviews planned execution (cannot execute)",
    "LearningAgent": "Analyzes outcomes and proposes learning adjustments",
    "ResearchAgent": "Researches protocols, markets, and strategies",
    "ArchitectAgent": "Proposes architecture patches (cannot self-deploy)",
    "KanbanPlannerAgent": "Creates and manages task board",
}


__all__ = [
    "ALLOWED_ROLES", "OPERATOR_APPROVAL_REQUIRED_ROLES", "PROPOSAL_ONLY_ROLES",
    "SubagentRole", "get_role", "list_roles", "validate_role",
]
