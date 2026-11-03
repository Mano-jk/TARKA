"""Z3-backed verification for structured plan bindings and rules."""

import ast
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import cast

import z3

from tarka.rules import parse_rule, referenced_names
from tarka.translator import translate_unsat_core


class VerificationStatus(StrEnum):
    SAT = "SAT"
    UNSAT = "UNSAT"
    UNKNOWN = "UNKNOWN"


class RuleEvaluationError(ValueError):
    """Raised when a rule cannot be evaluated against the supplied bindings."""


@dataclass(frozen=True)
class VerificationResult:
    status: VerificationStatus
    message: str | None = None
    unsat_core: tuple[str, ...] = ()
    reason: str | None = None

    @property
    def is_valid(self) -> bool:
        return self.status is VerificationStatus.SAT


def _real_value(value: object) -> z3.ExprRef:
    if type(value) not in (int, float):
        raise RuleEvaluationError("Numeric values must be integers or floats.")
    if type(value) is float and not math.isfinite(value):
        raise RuleEvaluationError("Numeric bindings must be finite.")
    return z3.RealVal(str(value))


def _bound_value(value: object) -> z3.ExprRef:
    if type(value) is bool:
        return z3.BoolVal(value)
    return _real_value(value)


def _symbol(name: str, value: object) -> z3.ExprRef:
    if type(value) is bool:
        return z3.Bool(name)
    if type(value) in (int, float):
        _bound_value(value)
        return z3.Real(name)
    raise RuleEvaluationError(f"Binding {name!r} must be a finite number or boolean.")


def _literal(value: object) -> z3.ExprRef:
    return _bound_value(value)


def _arithmetic(expression: z3.ExprRef) -> z3.ArithRef:
    if not z3.is_arith(expression):
        raise RuleEvaluationError("Arithmetic operators require numeric values.")
    return cast(z3.ArithRef, expression)


def _boolean(expression: z3.ExprRef) -> z3.BoolRef:
    if not z3.is_bool(expression):
        raise RuleEvaluationError("Logical operators require boolean values.")
    return cast(z3.BoolRef, expression)


def _compile(
    node: ast.AST,
    symbols: Mapping[str, z3.ExprRef],
    divisors: list[z3.ArithRef],
) -> z3.ExprRef:
    if isinstance(node, ast.Expression):
        return _compile(node.body, symbols, divisors)
    if isinstance(node, ast.Constant):
        return _literal(node.value)
    if isinstance(node, ast.Name):
        return symbols[node.id]
    if isinstance(node, ast.BinOp):
        left = _arithmetic(_compile(node.left, symbols, divisors))
        right = _arithmetic(_compile(node.right, symbols, divisors))
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        if isinstance(node.op, ast.Div):
            divisors.append(right)
            return left / right
        raise RuleEvaluationError("Unsupported arithmetic operator.")
    if isinstance(node, ast.UnaryOp):
        operand = _compile(node.operand, symbols, divisors)
        if isinstance(node.op, ast.Not):
            return z3.Not(_boolean(operand))
        if isinstance(node.op, ast.USub):
            return -_arithmetic(operand)
        return _arithmetic(operand)
    if isinstance(node, ast.BoolOp):
        operands = [_boolean(_compile(value, symbols, divisors)) for value in node.values]
        return z3.And(*operands) if isinstance(node.op, ast.And) else z3.Or(*operands)
    if isinstance(node, ast.Compare):
        left = _compile(node.left, symbols, divisors)
        comparisons = []
        for operation, right_node in zip(node.ops, node.comparators, strict=True):
            right = _compile(right_node, symbols, divisors)
            if isinstance(operation, ast.Eq):
                comparison = cast(z3.BoolRef, left == right)
            elif isinstance(operation, ast.NotEq):
                comparison = cast(z3.BoolRef, left != right)
            else:
                left_number = _arithmetic(left)
                right_number = _arithmetic(right)
                if isinstance(operation, ast.Lt):
                    comparison = left_number < right_number
                elif isinstance(operation, ast.LtE):
                    comparison = left_number <= right_number
                elif isinstance(operation, ast.Gt):
                    comparison = left_number > right_number
                else:
                    comparison = left_number >= right_number
            comparisons.append(comparison)
            left = right
        return z3.And(*comparisons)
    raise RuleEvaluationError(f"Unsupported parsed expression node: {type(node).__name__}.")


def verify(
    bindings: Mapping[str, object],
    rules: Sequence[str],
    *,
    timeout_ms: int = 1000,
) -> VerificationResult:
    """Check whether structured bindings satisfy every declared rule."""
    if isinstance(rules, str):
        raise TypeError("rules must be a sequence of rule strings, not a single string.")
    if type(timeout_ms) is not int or timeout_ms <= 0:
        raise ValueError("timeout_ms must be a positive integer.")

    rule_list = tuple(rules)
    parsed_rules = tuple(parse_rule(rule) for rule in rule_list)
    names = sorted({name for expression in parsed_rules for name in referenced_names(expression)})
    missing = [name for name in names if name not in bindings]
    if missing:
        raise RuleEvaluationError(f"Missing bindings for: {', '.join(missing)}.")

    symbols = {name: _symbol(name, bindings[name]) for name in names}
    solver = z3.Solver()
    solver.set(timeout=timeout_ms)
    for name, symbol in symbols.items():
        solver.add(symbol == _bound_value(bindings[name]))

    tracked_rules: dict[str, str] = {}
    compiled_rules: list[tuple[z3.BoolRef, str, list[z3.ArithRef]]] = []
    try:
        for expression, rule in zip(parsed_rules, rule_list, strict=True):
            divisors: list[z3.ArithRef] = []
            compiled = _compile(expression, symbols, divisors)
            if not z3.is_bool(compiled):
                raise RuleEvaluationError("Each rule must evaluate to a boolean expression.")
            compiled_rules.append((_boolean(compiled), rule, divisors))

        for _, rule, divisors in compiled_rules:
            for divisor in divisors:
                solver.push()
                solver.add(divisor == 0)
                divisor_check = solver.check()
                reason = solver.reason_unknown() if divisor_check == z3.unknown else None
                solver.pop()
                if divisor_check == z3.sat:
                    raise RuleEvaluationError(f"Division by zero in rule: {rule!r}.")
                if divisor_check == z3.unknown:
                    return VerificationResult(VerificationStatus.UNKNOWN, reason=reason)

        for compiled, rule, _ in compiled_rules:
            tracker = z3.FreshBool("tarka_rule")
            tracked_rules[str(tracker)] = rule
            solver.assert_and_track(compiled, tracker)
    except z3.Z3Exception as exc:
        raise RuleEvaluationError(f"Rule type mismatch: {exc}.") from exc

    result = solver.check()
    if result == z3.sat:
        return VerificationResult(VerificationStatus.SAT)
    if result == z3.unsat:
        core_names = sorted(str(item) for item in solver.unsat_core())
        core = tuple(tracked_rules[name] for name in core_names)
        return VerificationResult(
            status=VerificationStatus.UNSAT,
            message=translate_unsat_core(core, bindings),
            unsat_core=core,
        )
    return VerificationResult(
        status=VerificationStatus.UNKNOWN,
        reason=solver.reason_unknown(),
    )