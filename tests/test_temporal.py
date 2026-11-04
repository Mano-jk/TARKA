import pytest

from tarka import WorkflowRuleError, verify_workflow

_RULES = [{"type": "requires_prior_success", "tool": "update_order", "prior_tool": "authorize"}]


def test_workflow_allows_action_after_successful_prerequisite() -> None:
    result = verify_workflow(
        [{"tool": "authorize", "outcome": "success"}],
        "update_order",
        _RULES,
    )

    assert result.is_valid
    assert result.violations == ()


@pytest.mark.parametrize(
    "history",
    [
        [],
        [{"tool": "authorize", "outcome": "failure"}],
        [{"tool": "update_order", "outcome": "success"}],
    ],
)
def test_workflow_rejects_action_without_prior_success(history: list[dict[str, object]]) -> None:
    result = verify_workflow(history, "update_order", _RULES)

    assert not result.is_valid
    assert result.violations == (
        "Tool 'update_order' requires a prior successful 'authorize' action.",
    )


def test_workflow_rejects_malformed_trace_and_rules() -> None:
    with pytest.raises(WorkflowRuleError, match="outcome"):
        verify_workflow([{"tool": "authorize", "outcome": "pending"}], "update_order", _RULES)

    with pytest.raises(WorkflowRuleError, match="prior_tool"):
        verify_workflow([], "update_order", [{"type": "requires_prior_success", "tool": "x"}])


def test_workflow_trace_and_rule_limits_are_enforced() -> None:
    with pytest.raises(WorkflowRuleError, match="at most"):
        verify_workflow(
            [{"tool": "authorize", "outcome": "success"}] * 1001,
            "update_order",
            _RULES,
        )

    with pytest.raises(WorkflowRuleError, match="between 1 and"):
        verify_workflow([], "update_order", [])
