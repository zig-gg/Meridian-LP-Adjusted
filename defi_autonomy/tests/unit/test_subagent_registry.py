"""Unit tests for defi_autonomy.subagent_registry."""

from defi_autonomy.subagent_registry import (
    ALLOWED_ROLES, PROPOSAL_ONLY_ROLES, get_role, list_roles, validate_role,
)


class TestRegistry:
    def test_all_roles_valid(self) -> None:
        for role in ALLOWED_ROLES:
            assert validate_role(role)

    def test_unknown_role_invalid(self) -> None:
        assert not validate_role("HackerAgent")
        assert not validate_role("AdminAgent")

    def test_get_role_returns_definition(self) -> None:
        role = get_role("RiskAgent")
        assert role is not None
        assert role.can_propose is True
        assert role.can_execute_directly is False

    def test_architect_is_proposal_only(self) -> None:
        role = get_role("ArchitectAgent")
        assert role is not None
        assert role.can_execute_directly is False
        assert role.requires_operator_approval_for_execution is True

    def test_execution_review_cannot_execute(self) -> None:
        role = get_role("ExecutionReviewAgent")
        assert role.can_execute_directly is False

    def test_no_role_can_execute_directly(self) -> None:
        for r in list_roles():
            assert r.can_execute_directly is False

    def test_list_roles_returns_all(self) -> None:
        roles = list_roles()
        assert len(roles) == len(ALLOWED_ROLES)
