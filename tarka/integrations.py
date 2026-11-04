"""Framework-neutral guards for tools that perform agent actions."""

import inspect
import math
from collections.abc import Awaitable, Callable, Mapping, Sequence
from functools import wraps
from typing import cast

from jsonschema.protocols import Validator

from tarka.rules import parse_rule
from tarka.schemas import build_schema_validator
from tarka.verifier import RuleEvaluationError, VerificationStatus, verify

_MAX_ARGUMENT_NODES = 10_000
_MAX_ARGUMENT_DEPTH = 64


class ToolValidationError(ValueError):
    """Raised when an agent tool call does not satisfy its declared contract."""


def guard_tool[F: Callable[..., object]](
    function: F | None = None,
    *,
    rules: Sequence[str] = (),
    schema: object | None = None,
    bindings: Mapping[str, object] | None = None,
) -> F | Callable[[F], F]:
    """Decorate a sync or async tool so validation occurs before its side effects.

    The tool's bound arguments are validated as one JSON object. Declared bindings
    are fixed context values and cannot be overwritten with a different value.
    """
    if isinstance(rules, (str, bytes)):
        raise TypeError("rules must be a sequence of rule strings.")
    rule_list = tuple(rules)
    if not all(isinstance(rule, str) for rule in rule_list):
        raise TypeError("rules must be a sequence of rule strings.")
    if not rule_list and schema is None:
        raise ValueError("guard_tool requires at least one rule or a JSON Schema.")
    for rule in rule_list:
        parse_rule(rule)
    validator: Validator | None = (
        build_schema_validator(schema) if schema is not None else None
    )
    fixed_bindings = dict(bindings or {})

    def decorate(target: F) -> F:
        signature = inspect.signature(target)

        def validate_call(args: tuple[object, ...], kwargs: dict[str, object]) -> None:
            try:
                bound = signature.bind(*args, **kwargs)
            except TypeError as exc:
                raise ToolValidationError(str(exc)) from exc
            bound.apply_defaults()
            arguments = dict(bound.arguments)
            plan = {**arguments, **fixed_bindings}
            _check_json_value_bounds(plan)
            for name, fixed_value in fixed_bindings.items():
                if name in arguments and not _same_json_value(arguments[name], fixed_value):
                    raise ToolValidationError(
                        f"Tool argument {name!r} cannot override a fixed binding."
                    )
            if validator is not None:
                errors = sorted(
                    validator.iter_errors(plan),
                    key=lambda error: (
                        tuple(str(part) for part in error.absolute_path),
                        error.message,
                    ),
                )
                if errors:
                    raise ToolValidationError(
                        "Tool arguments do not satisfy the declared JSON Schema."
                    )
            if rule_list:
                try:
                    result = verify(plan, rule_list)
                except RuleEvaluationError as exc:
                    raise ToolValidationError(str(exc)) from exc
                if result.status is not VerificationStatus.SAT:
                    raise ToolValidationError("Tool arguments violate declared rules.")

        if inspect.iscoroutinefunction(target):

            @wraps(target)
            async def async_wrapper(*args: object, **kwargs: object) -> object:
                validate_call(args, kwargs)
                result = target(*args, **kwargs)
                return await cast(Awaitable[object], result)

            return cast(F, async_wrapper)

        @wraps(target)
        def sync_wrapper(*args: object, **kwargs: object) -> object:
            validate_call(args, kwargs)
            return target(*args, **kwargs)

        return cast(F, sync_wrapper)

    if function is None:
        return decorate
    return decorate(function)


def _same_json_value(left: object, right: object) -> bool:
    if type(left) is not type(right):
        return False
    if isinstance(left, Mapping) and isinstance(right, Mapping):
        return left.keys() == right.keys() and all(
            _same_json_value(left[key], right[key]) for key in left
        )
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(
            _same_json_value(left_item, right_item)
            for left_item, right_item in zip(left, right, strict=True)
        )
    return left == right


def _check_json_value_bounds(value: object) -> None:
    pending: list[tuple[object, int, bool]] = [(value, 0, False)]
    active_containers: set[int] = set()
    nodes = 0

    while pending:
        current, depth, exiting = pending.pop()
        identity = id(current)
        if exiting:
            active_containers.remove(identity)
            continue
        nodes += 1
        if nodes > _MAX_ARGUMENT_NODES or depth > _MAX_ARGUMENT_DEPTH:
            raise ToolValidationError("Tool arguments exceed supported size limits.")
        if current is None or type(current) in (bool, int, str):
            continue
        if type(current) is float:
            if not math.isfinite(current):
                raise ToolValidationError("Tool arguments must contain finite numbers.")
            continue
        if isinstance(current, dict):
            if type(current) is not dict:
                raise ToolValidationError("Tool arguments must be JSON-compatible values.")
            if identity in active_containers:
                raise ToolValidationError("Tool arguments must not contain cycles.")
            if not all(type(key) is str for key in current):
                raise ToolValidationError("Tool argument object keys must be strings.")
            active_containers.add(identity)
            pending.append((current, depth, True))
            pending.extend((child, depth + 1, False) for child in current.values())
            continue
        if isinstance(current, list):
            if type(current) is not list:
                raise ToolValidationError("Tool arguments must be JSON-compatible values.")
            if identity in active_containers:
                raise ToolValidationError("Tool arguments must not contain cycles.")
            active_containers.add(identity)
            pending.append((current, depth, True))
            pending.extend((child, depth + 1, False) for child in current)
            continue
        raise ToolValidationError("Tool arguments must be JSON-compatible values.")
