"""Parsing and validation for the deterministic Phase 1 rule language."""

import ast
import re


class RuleSyntaxError(ValueError):
    """Raised when a rule contains unsupported or invalid syntax."""


_MAX_RULE_LENGTH = 4096
_MAX_AST_NODES = 256
_LOGICAL_OPERATORS = re.compile(r"\b(and|or|not)\b", re.IGNORECASE)
_SINGLE_EQUALS = re.compile(r"(?<![<>=!])=(?!=)")
_ALLOWED_NODES = {
    ast.Expression,
    ast.BoolOp,
    ast.BinOp,
    ast.UnaryOp,
    ast.Compare,
    ast.Name,
    ast.Constant,
    ast.Load,
    ast.And,
    ast.Or,
    ast.Not,
    ast.UAdd,
    ast.USub,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.Eq,
    ast.NotEq,
    ast.Lt,
    ast.LtE,
    ast.Gt,
    ast.GtE,
}


def parse_rule(rule: str) -> ast.Expression:
    """Parse one rule and reject syntax outside the supported expression subset."""
    if not isinstance(rule, str) or not rule.strip():
        raise RuleSyntaxError("A rule must be a non-empty string.")
    if len(rule) > _MAX_RULE_LENGTH:
        raise RuleSyntaxError(f"Rules may not exceed {_MAX_RULE_LENGTH} characters.")

    normalized = _LOGICAL_OPERATORS.sub(lambda match: match.group(0).lower(), rule)
    normalized = _SINGLE_EQUALS.sub("==", normalized)
    try:
        expression = ast.parse(normalized, mode="eval")
    except (SyntaxError, RecursionError) as exc:
        raise RuleSyntaxError(f"Invalid rule syntax: {rule!r}.") from exc

    nodes = list(ast.walk(expression))
    if len(nodes) > _MAX_AST_NODES:
        raise RuleSyntaxError(f"Rules may not exceed {_MAX_AST_NODES} expression nodes.")

    for node in nodes:
        if type(node) not in _ALLOWED_NODES:
            raise RuleSyntaxError(f"Unsupported syntax in rule: {rule!r}.")
        if isinstance(node, ast.Constant) and type(node.value) not in (bool, int, float):
            raise RuleSyntaxError("Rules may only contain numeric and boolean literals.")

    return expression


def referenced_names(expression: ast.Expression) -> tuple[str, ...]:
    """Return variable names referenced by a parsed rule in stable order."""
    return tuple(sorted({node.id for node in ast.walk(expression) if isinstance(node, ast.Name)}))