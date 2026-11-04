"""Precompiled, deterministic rule templates for common policy checks."""

from collections.abc import Mapping

_TEMPLATES: Mapping[str, tuple[str, ...]] = {
    "finance.discount": (
        "applied_discount >= 0",
        "applied_discount <= max_discount_percentage",
        "subtotal >= 0",
        "final_price == subtotal * (1 - (applied_discount / 100))",
    ),
    "supply_chain.allocation": (
        "allocated_quantity >= 0",
        "allocated_quantity <= requested_quantity",
        "allocated_quantity <= available_quantity",
    ),
    "healthcare.consent": (
        "consent_recorded == True",
        "patient_authorized == True",
    ),
}


class TemplateError(ValueError):
    """Raised when a requested policy template does not exist."""


def available_templates() -> tuple[str, ...]:
    """Return the names of built-in rule templates in stable order."""
    return tuple(sorted(_TEMPLATES))


def get_template_rules(name: str) -> tuple[str, ...]:
    """Return the rules for a built-in template without evaluating plan data."""
    try:
        return _TEMPLATES[name]
    except KeyError as exc:
        raise TemplateError(f"Unknown TARKA rule template: {name!r}.") from exc
