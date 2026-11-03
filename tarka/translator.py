"""Human-readable feedback for unsatisfiable rule sets."""

from collections.abc import Mapping, Sequence

from tarka.rules import parse_rule, referenced_names


def translate_unsat_core(rules: Sequence[str], bindings: Mapping[str, object]) -> str:
    """Describe the conflicting rules and include bound values when available."""
    if not rules:
        return "Rule violation: the proposed plan does not satisfy the declared constraints."

    descriptions = []
    for rule in rules:
        names = referenced_names(parse_rule(rule))
        values = [f"{name}={bindings[name]!r}" for name in names if name in bindings]
        detail = f" ({', '.join(values)})" if values else ""
        descriptions.append(f"{rule}{detail}")

    return "Rule violation: no assignment satisfies " + "; ".join(descriptions) + "."