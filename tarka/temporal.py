"""Finite-trace guards for tool calls that require prior successful actions."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

_MAX_TRACE_EVENTS = 1000
_MAX_WORKFLOW_RULES = 100
_MAX_TOOL_NAME_LENGTH = 256
_VALID_OUTCOMES = frozenset({"success", "failure"})


class WorkflowRuleError(ValueError):
    """Raised when a workflow rule or trusted execution trace is malformed."""


@dataclass(frozen=True)
class WorkflowResult:
    is_valid: bool
    violations: tuple[str, ...] = ()


def verify_workflow(
    history: Sequence[Mapping[str, object]],
    proposed_tool: str,
    rules: Sequence[Mapping[str, object]],
) -> WorkflowResult:
    """Require configured prerequisite tools to have succeeded earlier in a trace."""
    if (
        not isinstance(history, Sequence)
        or isinstance(history, (str, bytes))
        or len(history) > _MAX_TRACE_EVENTS
    ):
        raise WorkflowRuleError(
            f"Workflow history must contain at most {_MAX_TRACE_EVENTS} events."
        )
    if (
        not isinstance(rules, Sequence)
        or isinstance(rules, (str, bytes))
        or not rules
        or len(rules) > _MAX_WORKFLOW_RULES
    ):
        raise WorkflowRuleError(
            f"Workflow rules must contain between 1 and {_MAX_WORKFLOW_RULES} entries."
        )
    proposed_tool = _validate_tool_name(proposed_tool, "proposed_tool")

    events: list[tuple[str, str]] = []
    for index, event in enumerate(history):
        if not isinstance(event, Mapping):
            raise WorkflowRuleError(f"Workflow history event {index} must be an object.")
        tool = event.get("tool")
        outcome = event.get("outcome")
        if not isinstance(outcome, str) or outcome not in _VALID_OUTCOMES:
            raise WorkflowRuleError(
                f"Workflow history event {index} outcome must be 'success' or 'failure'."
            )
        events.append((_validate_tool_name(tool, f"history[{index}].tool"), outcome))

    requirements: dict[str, set[str]] = {}
    for index, rule in enumerate(rules):
        if not isinstance(rule, Mapping):
            raise WorkflowRuleError(f"Workflow rule {index} must be an object.")
        if rule.get("type") != "requires_prior_success":
            raise WorkflowRuleError(
                f"Workflow rule {index} type must be 'requires_prior_success'."
            )
        tool = rule.get("tool")
        prerequisite = rule.get("prior_tool")
        tool_name = _validate_tool_name(tool, f"rules[{index}].tool")
        prerequisite_name = _validate_tool_name(prerequisite, f"rules[{index}].prior_tool")
        requirements.setdefault(tool_name, set()).add(prerequisite_name)

    events.append((proposed_tool, "requested"))
    violations = []
    for index, (tool, outcome) in enumerate(events):
        if outcome != "requested":
            continue
        for prerequisite in sorted(requirements.get(tool, ())):
            if not any(
                earlier_tool == prerequisite and earlier_outcome == "success"
                for earlier_tool, earlier_outcome in events[:index]
            ):
                violations.append(
                    f"Tool {tool!r} requires a prior successful {prerequisite!r} action."
                )
    return WorkflowResult(not violations, tuple(violations))


def _validate_tool_name(value: object, field: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > _MAX_TOOL_NAME_LENGTH
    ):
        raise WorkflowRuleError(
            f"Workflow {field} must be a non-empty string of at most "
            f"{_MAX_TOOL_NAME_LENGTH} characters."
        )
    return value
