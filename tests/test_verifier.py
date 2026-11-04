import pytest

from tarka import (
    RuleEvaluationError,
    RuleSyntaxError,
    VerificationStatus,
    get_template_rules,
    verify,
)


def test_discount_above_cap_is_unsat_with_actionable_feedback() -> None:
    result = verify(
        {"applied_discount": 30, "max_discount_percentage": 20},
        rules=["applied_discount <= max_discount_percentage"],
    )

    assert result.status is VerificationStatus.UNSAT
    assert result.unsat_core == ("applied_discount <= max_discount_percentage",)
    assert result.message is not None
    assert "applied_discount=30" in result.message
    assert "max_discount_percentage=20" in result.message


def test_discount_at_cap_is_sat() -> None:
    result = verify(
        {"applied_discount": 20, "max_discount_percentage": 20},
        rules=["applied_discount <= max_discount_percentage"],
    )

    assert result.status is VerificationStatus.SAT
    assert result.is_valid


def test_boolean_operators_parentheses_and_arithmetic_are_supported() -> None:
    result = verify(
        {"discount": 10, "cap": 20},
        rules=["discount * 2 <= cap AND (discount = 10 OR NOT (discount > cap))"],
    )

    assert result.status is VerificationStatus.SAT


def test_unbound_names_are_rejected() -> None:
    with pytest.raises(RuleEvaluationError, match="Missing bindings"):
        verify({"cap": 20}, rules=["discount <= cap"])


def test_function_calls_are_rejected_by_rule_parser() -> None:
    with pytest.raises(RuleSyntaxError, match="Unsupported syntax"):
        verify({}, rules=["check(cap)"])


def test_division_by_zero_is_rejected() -> None:
    with pytest.raises(RuleEvaluationError, match="Division by zero"):
        verify({"divisor": 0}, rules=["1 / divisor == 0"])


def test_modulo_is_rejected_as_unsupported_syntax() -> None:
    with pytest.raises(RuleSyntaxError, match="Unsupported syntax"):
        verify({"amount": 5}, rules=["amount % 2 == 1"])


def test_rule_length_and_expression_complexity_are_bounded() -> None:
    with pytest.raises(RuleSyntaxError, match="4096 characters"):
        verify({}, rules=["1 == 1 " + " " * 4096])

    complex_rule = " + ".join(["1"] * 130) + " == 130"
    with pytest.raises(RuleSyntaxError, match="256 expression nodes"):
        verify({}, rules=[complex_rule])


def test_user_symbols_cannot_alias_solver_tracking_symbols() -> None:
    result = verify(
        {"__tarka_rule_0": False},
        rules=["__tarka_rule_0 == False"],
    )

    assert result.status is VerificationStatus.SAT


def test_finance_discount_template_verifies_price_and_cap() -> None:
    rules = get_template_rules("finance.discount")
    valid = verify(
        {
            "applied_discount": 20,
            "max_discount_percentage": 20,
            "subtotal": 500,
            "final_price": 400,
        },
        rules,
    )
    invalid = verify(
        {
            "applied_discount": 30,
            "max_discount_percentage": 20,
            "subtotal": 500,
            "final_price": 350,
        },
        rules,
    )

    assert valid.status is VerificationStatus.SAT
    assert invalid.status is VerificationStatus.UNSAT


def test_supply_chain_template_checks_allocation_bounds() -> None:
    result = verify(
        {
            "allocated_quantity": 11,
            "requested_quantity": 10,
            "available_quantity": 20,
        },
        get_template_rules("supply_chain.allocation"),
    )

    assert result.status is VerificationStatus.UNSAT